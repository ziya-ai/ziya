"""Iteration identity on the live-observation stream for PARALLEL loops.

A parallel Repeat runs N iterations concurrently under ONE loop block_id.
Every consumer that keys per-iteration output on block_id alone therefore
cannot tell the iterations apart, and resolves all N to a single bucket —
the observable symptom being a fan-out that renders as one active block
with one blinking indicator and output for only one iteration.

Two independent losses of identity are covered here:

  1. ``execute_task_block`` emits the per-iteration deltas re-tagged with
     the loop's block_id (so they bucket under the loop rather than the
     inner task) but dropped the iteration ordinal that
     ``set_task_iteration_context`` had already established — so the
     ordinal never reached the wire at all.

  2. ``task_run_stream_relay._record`` folds ADJACENT same-block
     ``task_text_delta`` events into one entry.  Concurrent iterations
     interleave their deltas, so folding on block_id alone concatenated
     several iterations' text into a single replay entry — discarding on
     replay a distinction the live events carried.

Both are asserted against a live/replay surface, not an intermediate:
(1) through the real ``execute_task_block`` with a faked streaming
executor, per the harness in test_task_executor_progress_events.py, and
(2) through the relay's public ``_record`` / ``history`` pair.
"""

from unittest.mock import patch

import pytest

from app.agents import task_executor
from app.agents import task_run_stream_relay as relay
from app.context import (
    set_task_iteration_context, reset_task_iteration_context,
)
from app.models.task_card import Block


def _task(block_id: str = "inner-task") -> Block:
    return Block(block_type="task", id=block_id, name="T",
                 instructions="do it")


class _FakeExecutorTextThenTool:
    """Text delta, tool call, second text delta — exercises the
    task_text_delta, task_tool_call and task_progress emission sites in
    one pass."""

    def __init__(self, *args, **kwargs):
        pass

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        yield {"type": "text", "content": "first"}
        yield {
            "type": "tool_display",
            "tool_name": "run_shell_command",
            "tool_id": "tc-1",
            "args": {"command": "git status"},
            "result": "clean",
        }
        yield {"type": "text", "content": "second"}
        yield {"type": "stream_end"}


@pytest.fixture
def captured_events():
    events = []

    async def _fake_safe_push(run_id, event):
        events.append(event)

    with patch("app.streaming_tool_executor.StreamingToolExecutor",
               _FakeExecutorTextThenTool), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x",
                             "current_model": "fake"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools",
               return_value=[]), \
         patch("app.agents.task_run_stream_relay.safe_push", _fake_safe_push):
        yield events


# Event types re-tagged with the iteration owner's block_id, and which
# therefore need the ordinal to stay attributable.
_DELTA_SCOPED = {"task_text_delta", "task_tool_call", "task_progress"}


class TestDeltaCarriesIterationOrdinal:
    @pytest.mark.asyncio
    async def test_delta_scoped_events_carry_the_iteration_index(
        self, captured_events,
    ):
        """Inside a loop iteration, every event re-tagged to the loop's
        block_id must also carry that iteration's ordinal — otherwise
        (block_id, index) cannot identify which of N concurrent
        iterations produced it."""
        token = set_task_iteration_context("loop-1", 2)
        try:
            await task_executor.execute_task_block(
                _task(), run_id="run-idx",
            )
        finally:
            reset_task_iteration_context(token)

        scoped = [e for e in captured_events
                  if e.get("type") in _DELTA_SCOPED]
        assert scoped, "no delta-scoped events were emitted"
        # Precondition: these really were re-tagged to the loop, which is
        # what makes the missing ordinal load-bearing rather than cosmetic.
        assert all(e.get("block_id") == "loop-1" for e in scoped), \
            "expected delta-scoped events re-tagged to the loop block_id"
        for e in scoped:
            assert e.get("index") == 2, (
                f"{e['type']} lost the iteration ordinal — cannot be "
                f"attributed to one of N concurrent iterations: {e}"
            )

    @pytest.mark.asyncio
    async def test_task_started_and_finished_keep_the_inner_block_id(
        self, captured_events,
    ):
        """Guard against over-reach: the ordinal is for the re-tagged
        deltas.  task_started / task_finished describe the inner task
        itself and must keep its own id."""
        token = set_task_iteration_context("loop-1", 2)
        try:
            await task_executor.execute_task_block(
                _task("inner-task"), run_id="run-idx-2",
            )
        finally:
            reset_task_iteration_context(token)

        lifecycle = [e for e in captured_events
                     if e.get("type") in ("task_started", "task_finished")]
        assert lifecycle, "no task lifecycle events were emitted"
        for e in lifecycle:
            assert e.get("block_id") == "inner-task"

    @pytest.mark.asyncio
    async def test_outside_a_loop_no_ordinal_is_claimed(self, captured_events):
        """A bare task has no iteration; the ordinal must be absent or
        None so consumers fall back to block_id-only routing rather than
        matching a phantom iteration 0."""
        await task_executor.execute_task_block(
            _task(), run_id="run-bare",
        )
        scoped = [e for e in captured_events
                  if e.get("type") in _DELTA_SCOPED]
        assert scoped, "no delta-scoped events were emitted"
        for e in scoped:
            assert e.get("index") is None, (
                f"bare task claimed iteration ordinal {e.get('index')!r}: {e}"
            )


class TestRelayFoldKeepsIterationsApart:
    """``_record`` collapses adjacent same-block deltas.  The fold key
    must include the ordinal, or interleaved concurrent iterations merge
    into one replay entry."""

    def setup_method(self):
        relay._history.clear()

    def _delta(self, block_id: str, index, content: str):
        return {"type": "task_text_delta", "block_id": block_id,
                "index": index, "content": content}

    def test_interleaved_iterations_are_not_folded_together(self):
        run = "run-fold"
        # The interleave a parallel fan-out actually produces: three
        # iterations of one loop block, alternating.
        for pass_no in ("a", "b"):
            for i in range(3):
                relay._record(run, self._delta("loop-1", i, f"{i}{pass_no} "))

        runs = [e for e in list(relay._history.get(run, []))
                if e.get("type") == "task_text_delta_run"]
        by_index = {}
        for e in runs:
            by_index.setdefault(e.get("index"), []).append(e.get("content", ""))

        assert set(by_index) == {0, 1, 2}, (
            f"iterations were not kept apart on replay: {by_index}"
        )
        # Each iteration's own text, and nothing from a sibling.
        for i in range(3):
            joined = "".join(by_index[i])
            assert f"{i}a" in joined and f"{i}b" in joined
            for other in range(3):
                if other != i:
                    assert f"{other}a" not in joined, (
                        f"iteration {i} absorbed iteration {other}'s text: "
                        f"{joined!r}"
                    )

    def test_same_iteration_still_folds(self):
        """Positive control — the fold is the point of _record and must
        still happen for consecutive deltas of ONE iteration."""
        run = "run-fold-same"
        for n in range(4):
            relay._record(run, self._delta("loop-1", 0, f"{n}"))
        runs = [e for e in list(relay._history.get(run, []))
                if e.get("type") == "task_text_delta_run"]
        assert len(runs) == 1, f"expected one folded run, got {runs}"
        assert runs[0]["count"] == 4
        assert runs[0]["content"] == "0123"

    def test_serial_loop_without_ordinal_still_folds(self):
        """Back-compat: a pre-fix producer emits no ``index``.  Those
        deltas must fold exactly as before rather than each becoming its
        own entry."""
        run = "run-fold-legacy"
        for n in range(3):
            relay._record(run, {"type": "task_text_delta",
                                "block_id": "loop-1", "content": f"{n}"})
        runs = [e for e in list(relay._history.get(run, []))
                if e.get("type") == "task_text_delta_run"]
        assert len(runs) == 1, f"expected one folded run, got {runs}"
        assert runs[0]["content"] == "012"
