"""Tests for app.tool_batch — deferred, partially concurrent execution of the
tool calls a single model turn emitted.

Covers:
- read-only classification (builtin names; conservative shell predicate)
- grouping: consecutive read-only calls run together; any other call is a
  barrier that preserves its position
- concurrency: reads in one group overlap; a write never overlaps a read
- results are emitted in ARRIVAL order regardless of completion order
- inter_tool_delay is applied once per batch, not once per tool
- a stop directive during one tool cancels its siblings and ends the batch
- non-stop feedback stubs every call that has not started yet
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import tool_batch
from app.tool_batch import (
    BatchOutcome, FEEDBACK_SKIP_MESSAGE, is_read_only_call, plan_groups,
    run_tool_batch, shell_command_is_read_only,
)


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

class TestShellReadOnly:
    @pytest.mark.parametrize("cmd", [
        "ls -la",
        "cat app/foo.py | grep -n def",
        "grep -rn 'x' app/ --include=*.py | head -20",
        "git log --oneline -5 && git status",
        "find . -name '*.md' -type f",
        "sed -n 10,20p file.py",
        "wc -l a.py b.py 2>/dev/null",
        "FOO=1 grep -i pattern file",
        "cd frontend && ls",
    ])
    def test_read_only_commands(self, cmd):
        assert shell_command_is_read_only(cmd) is True

    @pytest.mark.parametrize("cmd", [
        "echo hi > out.txt",
        "cat a >> b",
        "sed -i 's/a/b/' file.py",
        "sed --in-place 's/a/b/' file.py",
        "perl -pi -e 's/a/b/' file",
        "find . -name '*.pyc' -delete",
        "find . -exec rm {} \\;",
        "ls | xargs rm",
        "cat f | tee out",
        "git branch -D foo",
        "git log --output=x",
        "sort -o out in",
        "python3 -c 'open(\"x\",\"w\")'",
        "rm -rf build",
        "echo $(rm x)",
        "cat `whoami`",
        "/usr/bin/ls",
        "",
        "   ",
    ])
    def test_mutating_or_unknown_commands_are_not_read_only(self, cmd):
        assert shell_command_is_read_only(cmd) is False

    def test_grep_dash_i_is_not_treated_as_inplace(self):
        # The in-place flag check is scoped to sed/perl/awk; grep -i is
        # case-insensitive search and must remain read-only.
        assert shell_command_is_read_only("grep -i foo bar") is True


class TestIsReadOnlyCall:
    def test_builtin_read_only_names(self):
        for name in ("file_read", "file_list", "ast_search", "memory_search", "pdf_search"):
            assert is_read_only_call(name, {}) is True

    def test_write_and_unknown_tools_are_serial(self):
        for name in ("file_write", "context_add_file", "render_diagram", "some_external_tool"):
            assert is_read_only_call(name, {}) is False

    def test_shell_delegates_to_command_predicate(self):
        assert is_read_only_call("run_shell_command", {"command": "ls"}) is True
        assert is_read_only_call("run_shell_command", {"command": "rm x"}) is False
        assert is_read_only_call("run_shell_command", {}) is False


# --------------------------------------------------------------------------
# Batch execution
# --------------------------------------------------------------------------

def _ctx(tool_id, name, args=None):
    """A minimal stand-in for ToolExecContext: only the fields the batch
    runner reads."""
    return SimpleNamespace(
        tool_id=tool_id, tool_name=name, actual_tool_name=name, args=args or {},
        deferred_feedback=[], feedback_received=False, should_stop_stream=False,
    )


def _fake_executor(log, durations=None, on_start=None):
    """Build a fake execute_single_tool that records start/finish order and
    yields the same event shapes as the real one."""
    durations = durations or {}

    async def fake(ctx):
        log.append(("start", ctx.tool_id))
        if on_start:
            await on_start(ctx)
        yield {'type': 'tool_start', 'tool_id': ctx.tool_id, 'tool_name': ctx.tool_name}
        await asyncio.sleep(durations.get(ctx.tool_id, 0.01))
        log.append(("end", ctx.tool_id))
        yield {'type': 'tool_display', 'tool_id': ctx.tool_id, 'tool_name': ctx.tool_name, 'result': 'ok'}
        yield {'type': '_tool_result', 'tool_id': ctx.tool_id, 'tool_name': ctx.tool_name, 'result': f'r-{ctx.tool_id}'}
    return fake


def _delay():
    return {'current': 0.0, 'min': 0.0, 'max': 3.0, 'decay_factor': 0.6, 'growth_factor': 2.5, 'last_was_throttled': False}


async def _collect(ctxs, delay=None, **kw):
    outcome = BatchOutcome()
    events = []
    async for evt in run_tool_batch(ctxs, inter_tool_delay=delay or _delay(), outcome=outcome, **kw):
        events.append(evt)
    return events, outcome


def test_plan_groups_reads_together_writes_alone():
    ctxs = [_ctx("1", "file_read"), _ctx("2", "file_read"), _ctx("3", "file_write"),
            _ctx("4", "file_read"), _ctx("5", "file_write"), _ctx("6", "file_write")]
    groups = [[c.tool_id for c in g] for g in plan_groups(ctxs)]
    assert groups == [["1", "2"], ["3"], ["4"], ["5"], ["6"]]


@pytest.mark.asyncio
async def test_reads_overlap_and_write_waits_for_them():
    log = []
    # Read 1 is slow, read 2 fast: under concurrency 2 finishes before 1.
    fake = _fake_executor(log, durations={"1": 0.08, "2": 0.01})
    ctxs = [_ctx("1", "file_read"), _ctx("2", "file_read"), _ctx("3", "file_write")]
    with patch.object(tool_batch, "execute_single_tool", fake):
        events, outcome = await _collect(ctxs)

    starts = [tid for kind, tid in log if kind == "start"]
    # Both reads started before either finished (they overlapped).
    assert log.index(("start", "2")) < log.index(("end", "1"))
    assert log.index(("end", "2")) < log.index(("end", "1"))
    # The write did not start until both reads had ended.
    assert log.index(("start", "3")) > log.index(("end", "1"))
    assert log.index(("start", "3")) > log.index(("end", "2"))
    assert starts[-1] == "3"
    assert outcome.executed is True


@pytest.mark.asyncio
async def test_results_emitted_in_arrival_order_not_completion_order():
    log = []
    fake = _fake_executor(log, durations={"1": 0.08, "2": 0.01})
    ctxs = [_ctx("1", "file_read"), _ctx("2", "file_read")]
    with patch.object(tool_batch, "execute_single_tool", fake):
        events, _ = await _collect(ctxs)
    result_ids = [e['tool_id'] for e in events if e['type'] == '_tool_result']
    assert result_ids == ["1", "2"]
    # Display events are still live (2 displayed before 1).
    display_ids = [e['tool_id'] for e in events if e['type'] == 'tool_display']
    assert display_ids == ["2", "1"]


@pytest.mark.asyncio
async def test_inter_tool_delay_applied_once_per_batch():
    log = []
    fake = _fake_executor(log)
    ctxs = [_ctx("1", "file_read"), _ctx("2", "file_write"), _ctx("3", "file_read")]
    delay = _delay()
    delay['current'] = 0.5
    sleeps = []

    real_sleep = asyncio.sleep

    async def spy_sleep(t):
        if t >= 0.5:
            sleeps.append(t)
        await real_sleep(0)

    with patch.object(tool_batch, "execute_single_tool", fake), \
         patch.object(tool_batch.asyncio, "sleep", spy_sleep):
        await _collect(ctxs, delay=delay)

    assert sleeps == [0.5], "the adaptive delay must be paid once for the whole batch"
    assert delay['current'] == pytest.approx(0.3)  # decayed once (0.5 * 0.6)


@pytest.mark.asyncio
async def test_stop_in_one_tool_cancels_siblings_and_emits_no_results():
    log = []
    cancelled = []

    async def fake(ctx):
        log.append(("start", ctx.tool_id))
        yield {'type': 'tool_start', 'tool_id': ctx.tool_id, 'tool_name': ctx.tool_name}
        if ctx.tool_id == "1":
            await asyncio.sleep(0.01)
            ctx.should_stop_stream = True
            yield {'type': 'stream_end'}
            return
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(ctx.tool_id)
            raise
        yield {'type': '_tool_result', 'tool_id': ctx.tool_id, 'tool_name': ctx.tool_name, 'result': 'never'}

    ctxs = [_ctx("1", "file_read"), _ctx("2", "file_read"), _ctx("3", "file_write")]
    with patch.object(tool_batch, "execute_single_tool", fake):
        events, outcome = await _collect(ctxs)

    assert outcome.stop_requested is True
    assert cancelled == ["2"], "the sibling in flight must be cancelled, not awaited"
    assert ("start", "3") not in log, "the barrier after the stopped group must not run"
    assert not [e for e in events if e['type'] == '_tool_result']


@pytest.mark.asyncio
async def test_non_stop_feedback_stubs_unstarted_calls():
    log = []

    async def fake(ctx):
        log.append(("start", ctx.tool_id))
        yield {'type': 'tool_start', 'tool_id': ctx.tool_id, 'tool_name': ctx.tool_name}
        if ctx.tool_id == "1":
            ctx.feedback_received = True
            ctx.deferred_feedback.append("look at X first")
        yield {'type': '_tool_result', 'tool_id': ctx.tool_id, 'tool_name': ctx.tool_name, 'result': 'ok'}

    ctxs = [_ctx("1", "file_write"), _ctx("2", "file_read"), _ctx("3", "file_read")]
    with patch.object(tool_batch, "execute_single_tool", fake):
        events, outcome = await _collect(ctxs)

    assert [tid for _, tid in log] == ["1"]
    results = {e['tool_id']: e['result'] for e in events if e['type'] == '_tool_result'}
    assert results == {"1": "ok", "2": FEEDBACK_SKIP_MESSAGE, "3": FEEDBACK_SKIP_MESSAGE}
    # Every stubbed call also tells the model why, as the old skip path did.
    stubbed_for_model = [e for e in events if e['type'] == 'tool_result_for_model']
    assert {e['tool_use_id'] for e in stubbed_for_model} == {"2", "3"}
    assert outcome.feedback_received is True
    assert outcome.deferred_feedback == ["look at X first"]


@pytest.mark.asyncio
async def test_cancel_event_stops_between_groups():
    log = []
    fake = _fake_executor(log)
    cancel = asyncio.Event()
    cancel.set()
    ctxs = [_ctx("1", "file_write"), _ctx("2", "file_write")]
    with patch.object(tool_batch, "execute_single_tool", fake):
        events, outcome = await _collect(ctxs, cancel_event=cancel)
    assert [tid for kind, tid in log if kind == "start"] == ["1"]
    assert outcome.cancelled is True


@pytest.mark.asyncio
async def test_empty_batch_is_a_noop():
    events, outcome = await _collect([])
    assert events == [] and outcome.executed is False
