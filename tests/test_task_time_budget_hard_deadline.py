"""A time budget must bound the block even when the stream does not honour
cancellation.

Observed on run 5f83000a (2026-11-05): lanes under a 1,500 s budget ran for
2–3.5 h while the provider sat in its retry ladder on expired credentials;
the artifact recorded ``closing_turn: cut_off`` after ~8,200 s.  The budget
machinery in ``task_executor._pull_until`` used ``asyncio.wait_for``, which
on timeout cancels the in-flight ``__anext__`` and then WAITS for that
cancellation to complete -- so a stream that swallows or delays
CancelledError (a retry loop with a ``try/except Exception``, a ``finally``
that awaits a slow close) holds the deadline hostage for as long as it
likes.  A deadline that can be extended by the thing it bounds is not a
deadline.

Same harness as test_task_time_budget.py; the fake streams here refuse to
die on cancellation.
"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents import task_executor
from app.models.task_card import Block, TaskScope

CLOSING_OK = ('Findings: X holds.\n'
              '<self_assessment objective_met="true" rationale="wrote what I had" />')
STUCK_FOR = 20.0   # how long an uncooperative stream clings on after cancel


def _task(budget: int) -> Block:
    return Block(block_type="task", id="task-hd", name="T", instructions="do it",
                 scope=TaskScope(time_budget_secs=budget))


def _fake_executor(shape: str, closing_hangs: bool = False):
    """``shape``: how the FIRST stream misbehaves.

    - ``swallows_cancel``: catches CancelledError and keeps going, the shape
      of a retry ladder wrapped in ``except Exception`` or ``except BaseException``.
    - ``slow_finally``: honours the cancel but awaits a long teardown first.
    """
    calls = []

    class _Fake:
        def __init__(self, *a, **k):
            pass

        async def stream_with_tools(self, messages, tools=None, **kwargs):
            calls.append((list(messages), kwargs))
            if len(calls) == 1:
                yield {"type": "text", "content": "Started."}
                if shape == "swallows_cancel":
                    while True:
                        try:
                            await asyncio.sleep(STUCK_FOR)
                            break
                        except asyncio.CancelledError:
                            continue          # "retrying..."
                elif shape == "slow_finally":
                    try:
                        await asyncio.sleep(STUCK_FOR)
                    finally:
                        await asyncio.shield(asyncio.sleep(STUCK_FOR))
                yield {"type": "text", "content": " Done.\n<self_assessment objective_met=\"true\" rationale=\"r\" />"}
                yield {"type": "stream_end"}
                return
            if closing_hangs:
                while True:
                    try:
                        await asyncio.sleep(STUCK_FOR)
                        break
                    except asyncio.CancelledError:
                        continue
            yield {"type": "text", "content": CLOSING_OK}
            yield {"type": "stream_end"}
    return _Fake, calls


def _tools():
    return [SimpleNamespace(name="run_shell_command", description="", input_schema={}),
            SimpleNamespace(name="emit_artifact", description="", input_schema={})]


async def _run(block, fake, grace):
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
        patch.object(task_executor, "time_budget_grace_secs", lambda b: grace),
    ]
    for p in patches:
        p.start()
    try:
        t0 = time.time()
        artifact = await asyncio.wait_for(
            task_executor.execute_task_block(block, run_id="run-hd"),
            timeout=STUCK_FOR * 3)   # the test itself must not hang forever
        return artifact, events, time.time() - t0
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["swallows_cancel", "slow_finally"])
async def test_deadline_holds_when_the_stream_ignores_cancellation(shape):
    """Budget 1 s, grace 1 s: the block must return in well under STUCK_FOR,
    with the closing turn's findings, not after the stuck stream relents."""
    fake, calls = _fake_executor(shape)
    artifact, events, elapsed = await _run(_task(1), fake, grace=1)
    assert elapsed < STUCK_FOR / 2, f"deadline extended to {elapsed:.1f}s by an uncooperative stream"
    assert len(calls) == 2, "the closing turn must still run"
    assert "Findings: X holds" in artifact.summary
    assert any("time_budget: 1s exhausted" in d for d in artifact.decisions)


@pytest.mark.asyncio
async def test_closing_turn_that_ignores_cancellation_is_hard_stopped():
    """Both the main stream and the closing turn refuse to die, so the block
    pays the abandon wait twice.  The bound is budget + grace + 2 abandon
    windows -- a constant, not whatever the streams choose."""
    fake, calls = _fake_executor("swallows_cancel", closing_hangs=True)
    artifact, events, elapsed = await _run(_task(1), fake, grace=1)
    bound = 1 + 1 + 2 * task_executor.TIME_BUDGET_ABANDON_SECS + 2.0
    assert elapsed < bound, f"grace extended to {elapsed:.1f}s (bound {bound:.0f}s)"
    assert elapsed < STUCK_FOR, "the stuck closing turn dictated the exit"
    assert artifact.failed
    assert artifact.signature == task_executor.TIME_BUDGET_FAILURE_SIGNATURE
