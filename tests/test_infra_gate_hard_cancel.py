"""When the infra gate closes, in-flight siblings must END, not merely be
asked to.

Run 5f83000a (2026-11-05): two lanes failed instantly on expired AWS
credentials, the gate closed and the watcher cancelled every queued lane --
and the four lanes already in flight ran for another 3.3–3.6 h, because
the provider's retry ladder swallowed ``CancelledError``.  The run held at
06:00 for a credential that died at 03:00.

Cancellation at an iteration boundary is now hard: the iteration runs in
its own task behind ``asyncio.shield``; on cancellation that task is
cancelled, given ``HARD_CANCEL_GRACE_SECS`` to unwind, and then abandoned.
The Parallel block, which had no gate watcher at all, gets the same one
the parallel Repeat has.
"""
import asyncio
import time

import pytest

from app.agents import block_executor
from app.agents.block_executor import ExecutionContext, execute_block
from app.agents.task_executor import TaskInfraError
from app.models.task_card import Block

STUCK_FOR = 20.0


def _ctx(tmp_path):
    return ExecutionContext(run_id="r-hard", project_id="p1", project_root=str(tmp_path))


def _stuck_fanout(n_stuck: int, parallel_block: bool):
    """Iteration/child 0 dies on auth after 0.2 s; the rest sleep and refuse
    to be cancelled (a retry ladder under ``except Exception``)."""
    started = []

    async def fake(block, **kw):
        started.append(block.id)
        if block.id.endswith("-0") or (not parallel_block and len(started) == 1):
            await asyncio.sleep(0.2)
            raise TaskInfraError("expired", infra_kind="authentication_error", block_id=block.id)
        while True:
            try:
                await asyncio.sleep(STUCK_FOR)
                break
            except asyncio.CancelledError:
                continue
        from app.models.task_card import Artifact
        return Artifact(summary="too late")

    if parallel_block:
        blk = Block(id="par", block_type="parallel", name="p",
                    body=[Block(id=f"c-{i}", block_type="task", name=f"c{i}", instructions="go")
                          for i in range(n_stuck + 1)])
    else:
        blk = Block(id="rep", block_type="repeat", name="r", repeat_mode="count",
                    repeat_count=n_stuck + 1, repeat_parallel=True, repeat_propagate="none",
                    body=[Block(id="rep-t", block_type="task", name="w", instructions="go")])
    return blk, fake, started


@pytest.mark.asyncio
@pytest.mark.parametrize("parallel_block", [False, True],
                         ids=["repeat_parallel", "parallel_block"])
async def test_gate_close_ends_siblings_that_swallow_cancellation(tmp_path, monkeypatch, parallel_block):
    blk, fake, started = _stuck_fanout(3, parallel_block)
    monkeypatch.setattr("app.agents.block_executor.execute_task_block", fake)
    ctx = _ctx(tmp_path)
    t0 = time.time()
    with pytest.raises(TaskInfraError):
        await asyncio.wait_for(execute_block(blk, ctx), timeout=STUCK_FOR * 2)
    elapsed = time.time() - t0
    bound = 0.2 + block_executor.HARD_CANCEL_GRACE_SECS + 2.0
    assert elapsed < bound, f"gate close took {elapsed:.1f}s; stuck siblings dictated the exit (bound {bound:.0f}s)"
    assert len(started) == 4, "every sibling had started; the gate had something to cancel"
    assert ctx.infra_faults and ctx.infra_faults[0].kind == "authentication_error"
    assert ctx.infra_gated_reason


@pytest.mark.asyncio
async def test_cooperative_siblings_still_cancel_promptly(tmp_path, monkeypatch):
    """Positive control: a sibling that honours cancellation exits at once
    and the grace window is never paid."""
    async def fake(block, **kw):
        if block.id == "c-0":
            await asyncio.sleep(0.2)
            raise TaskInfraError("expired", infra_kind="authentication_error", block_id=block.id)
        await asyncio.sleep(STUCK_FOR)
    blk = Block(id="par", block_type="parallel", name="p",
                body=[Block(id=f"c-{i}", block_type="task", name=f"c{i}", instructions="go") for i in range(3)])
    monkeypatch.setattr("app.agents.block_executor.execute_task_block", fake)
    t0 = time.time()
    with pytest.raises(TaskInfraError):
        await execute_block(blk, _ctx(tmp_path))
    assert time.time() - t0 < 2.0
