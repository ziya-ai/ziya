"""
Per-block time budget (``scope.time_budget_secs``), option B: at the deadline
the model stream is cut off and ONE closing turn runs with tools withheld so
the task writes what it has; a closing turn that overruns its grace fails
the block with signature ``time_budget_exceeded``.

Motivating run (chat 3f831376): a lane spent 2 h 20 min on one hypothesis
query and ended "cause not identified".  It was inside its iteration budget
the whole time -- the only ceiling was a count, not a clock.

Driven through the real ``execute_task_block`` with a faked
StreamingToolExecutor (pattern from test_task_executor_finished_verdict.py).
The first stream SLEEPS instead of ending, which is what a hunt that never
converges looks like from the executor's side.
"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents import task_executor
from app.models.task_card import Block, TaskScope, merge_scopes

CLOSING_OK = ('Findings: X holds, Y unknown.\n'
              '<self_assessment objective_met="true" rationale="wrote what I had" />')


def _task(budget=None) -> Block:
    scope = TaskScope(time_budget_secs=budget) if budget is not None else None
    return Block(block_type="task", id="task-tb", name="T", instructions="do it",
                 scope=scope)


def _fake_executor(first_hangs: bool, closing_hangs: bool = False,
                   closing_text: str = CLOSING_OK):
    calls = []

    class _Fake:
        def __init__(self, *a, **k):
            pass

        async def stream_with_tools(self, messages, tools=None, **kwargs):
            calls.append((list(messages), kwargs))
            if len(calls) == 1:
                yield {"type": "text", "content": "Started pulling."}
                if first_hangs:
                    await asyncio.sleep(30)       # the hunt that never converges
                yield {"type": "text", "content": " Done.\n<self_assessment objective_met=\"true\" rationale=\"r\" />"}
                yield {"type": "stream_end"}
                return
            if closing_hangs:
                await asyncio.sleep(30)
            yield {"type": "text", "content": closing_text}
            yield {"type": "stream_end"}
    return _Fake, calls


def _tools():
    # Two tools the block could reach: the closing turn must withhold the
    # first and keep the second.
    return [SimpleNamespace(name="run_shell_command", description="", input_schema={}),
            SimpleNamespace(name="emit_artifact", description="", input_schema={})]


async def _run(block, fake, grace=None):
    events = []

    async def _push(run_id, event):
        events.append(event)

    patches = [
        patch("app.streaming_tool_executor.StreamingToolExecutor", fake),
        patch("app.agents.models.ModelManager.get_state",
              return_value={"aws_region": "us-east-1", "aws_profile": "x",
                            "current_model": "fake"}),
        patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=_tools()),
        patch("app.agents.task_run_stream_relay.safe_push", _push),
    ]
    if grace is not None:
        patches.append(patch.object(task_executor, "time_budget_grace_secs",
                                    lambda b: grace))
    for p in patches:
        p.start()
    try:
        t0 = time.time()
        artifact = await task_executor.execute_task_block(block, run_id="run-tb")
        return artifact, events, time.time() - t0
    finally:
        for p in patches:
            p.stop()


def _finished(events):
    fin = [e for e in events if e.get("type") == "task_finished"]
    assert len(fin) == 1, fin
    return fin[0]


# ---------------------------------------------------------------------------
# Scope field and merge
# ---------------------------------------------------------------------------

def test_time_budget_merges_as_min_not_max():
    """A budget is a bound, not a grant: an inner layer may tighten it, never
    widen it (the opposite of shell_timeout_secs)."""
    outer, inner = TaskScope(time_budget_secs=7200), TaskScope(time_budget_secs=600)
    assert merge_scopes(outer, inner).time_budget_secs == 600
    assert merge_scopes(inner, outer).time_budget_secs == 600
    assert merge_scopes(outer, TaskScope()).time_budget_secs == 7200
    assert merge_scopes(TaskScope(), TaskScope()).time_budget_secs is None
    # Positive control for the contrast: the grant field still takes the MAX.
    assert merge_scopes(TaskScope(shell_timeout_secs=60),
                        TaskScope(shell_timeout_secs=1200)).shell_timeout_secs == 1200


# ---------------------------------------------------------------------------
# Executor behaviour, on the outermost surface (artifact + task_finished)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deadline_cuts_the_hunt_and_runs_one_closing_turn_with_tools_withheld():
    fake, calls = _fake_executor(first_hangs=True)
    artifact, events, elapsed = await _run(_task(budget=1), fake)

    assert elapsed < 10, "the 30 s hunt must have been cut off at the 1 s budget"
    assert len(calls) == 2, "exactly one closing turn"
    first_kwargs, closing_kwargs = calls[0][1], calls[1][1]
    # The hunt ran with the normal tool set; the closing turn withheld every
    # tool but emit_artifact.
    assert not first_kwargs.get("tool_denylist")
    assert closing_kwargs["tool_denylist"] == ["run_shell_command"]
    # The closing turn is asked the way the contract-correction turn is: the
    # model's turn so far closed, then the budget message.
    msgs = calls[1][0]
    assert isinstance(msgs[-2], AIMessage) and "Started pulling." in msgs[-2].content
    assert isinstance(msgs[-1], HumanMessage)
    assert msgs[-1].content == task_executor.TIME_BUDGET_CLOSING_MESSAGE

    # What the block passes forward is the closing turn's findings.
    assert "Findings: X holds" in artifact.summary
    assert artifact.failed is False, "a deliberate partial report is not a failure"
    assert artifact.time_budget == {"budget_secs": 1,
                                    "exhausted_after_ms": artifact.time_budget["exhausted_after_ms"],
                                    "closing_turn": "completed"}
    assert artifact.time_budget["exhausted_after_ms"] >= 1000
    assert any(d.startswith("time_budget:") for d in artifact.decisions)
    fin = _finished(events)
    assert fin["ok"] is True
    assert fin["time_budget"]["closing_turn"] == "completed"
    assert any(e.get("type") == "task_progress" and "time budget" in e.get("note", "")
               for e in events)


@pytest.mark.asyncio
async def test_closing_turn_past_grace_fails_with_signature():
    fake, calls = _fake_executor(first_hangs=True, closing_hangs=True)
    artifact, events, elapsed = await _run(_task(budget=1), fake, grace=1)

    assert elapsed < 10
    assert len(calls) == 2
    assert artifact.failed is True
    assert artifact.signature == task_executor.TIME_BUDGET_FAILURE_SIGNATURE
    assert artifact.time_budget["closing_turn"] == "cut_off"
    fin = _finished(events)
    assert fin["ok"] is False
    assert fin["failure_signature"] == "time_budget_exceeded"


@pytest.mark.asyncio
async def test_under_budget_block_is_untouched():
    fake, calls = _fake_executor(first_hangs=False)
    artifact, events, _ = await _run(_task(budget=60), fake)
    assert len(calls) == 1
    assert artifact.failed is False
    assert not hasattr(artifact, "time_budget")
    assert "time_budget" not in _finished(events)
    assert not any("time_budget" in d for d in artifact.decisions)


@pytest.mark.asyncio
async def test_no_budget_means_no_deadline():
    # Negative control for the cut-off: the same hanging stream with no
    # budget is only stopped by the test's own timeout.
    fake, calls = _fake_executor(first_hangs=True)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_run(_task(), fake), timeout=3)
    assert len(calls) == 1


def test_grace_is_ten_percent_clamped():
    f = task_executor.time_budget_grace_secs
    assert f(60) == 60          # floor
    assert f(1200) == 120       # 10%
    assert f(7200) == 300       # ceiling
