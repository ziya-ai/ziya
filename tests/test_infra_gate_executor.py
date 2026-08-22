"""Infra gate enforcement inside a fan-out, end to end.

The policy itself is unit-tested in test_infra_gate_policy.py.  This
file tests the WIRING, which is where the two real defects lived:

  1. ``record_infra_fault`` was defined but never called, so
     ``ctx.infra_faults`` stayed empty and the gate could never fire.
     A test that only exercised the policy would have passed.

  2. ``_saved_width`` was restored on the success path only, so the
     infra re-raise leaked a stale denominator into an enclosing loop.

Both are invisible unless a test drives a real fan-out through a real
fault, so that is what these do.
"""

import asyncio
import pytest

from app.agents.task_executor import TaskInfraError, TaskExecutorError
from app.models.task_card import Block


def _ctx(tmp_path):
    from app.agents.block_executor import ExecutionContext
    return ExecutionContext(
        run_id="r-gate",
        project_id="p1",
        project_root=str(tmp_path),
    )


def _fanout(n: int, parallel: bool = True, block_id: str = "rep") -> Block:
    return Block(
        id=block_id,
        block_type="repeat",
        name="fan-out",
        repeat_mode="count",
        repeat_count=n,
        repeat_parallel=parallel,
        repeat_propagate="none",
        body=[Block(id=f"{block_id}-t", block_type="task",
                    name="w", instructions="go")],
    )


def _install(monkeypatch, fn):
    monkeypatch.setattr("app.agents.block_executor.execute_task_block", fn)


class TestFaultsAreRecorded:
    """Defect 1: the accumulator must actually be fed."""

    @pytest.mark.asyncio
    async def test_auth_fault_is_recorded_on_the_context(
        self, tmp_path, monkeypatch,
    ):
        async def _boom(block, **kw):
            raise TaskInfraError(
                "expired", infra_kind="authentication_error",
                block_id=block.id or "b?",
            )
        _install(monkeypatch, _boom)
        from app.agents.block_executor import execute_block
        ctx = _ctx(tmp_path)
        with pytest.raises(TaskInfraError):
            await execute_block(_fanout(4), ctx)
        assert ctx.infra_faults, (
            "no faults recorded — record_infra_fault is not being called, "
            "so the gate can never fire and the hold cannot report breadth"
        )
        assert ctx.infra_faults[0].kind == "authentication_error"

    @pytest.mark.asyncio
    async def test_plain_work_failure_is_not_recorded(
        self, tmp_path, monkeypatch,
    ):
        """Only infra kinds count: a work failure must not gate a fleet."""
        async def _fail(block, **kw):
            raise TaskExecutorError("the work failed on its merits")
        _install(monkeypatch, _fail)
        from app.agents.block_executor import execute_block
        ctx = _ctx(tmp_path)
        art = await execute_block(_fanout(4), ctx)
        assert art.failed
        assert ctx.infra_faults == []
        assert ctx.infra_gated_reason is None


class TestGateCancelsInFlightSiblings:
    """The gate must bound the collapse, not merely relabel it."""

    @pytest.mark.asyncio
    async def test_auth_fault_cancels_slow_siblings(
        self, tmp_path, monkeypatch,
    ):
        """One dead credential must not cost N full task durations.

        Iteration 0 fails fast; the rest would each sleep well past the
        watcher's 0.25 s poll.  With the gate working, they are cancelled
        and never reach their completion marker.
        """
        completed: list = []

        async def _mixed(block, **kw):
            from app.context import get_task_iteration_context
            _ic = get_task_iteration_context() or {}
            idx = _ic.get("index")
            if idx == 0:
                raise TaskInfraError(
                    "expired", infra_kind="authentication_error",
                    block_id=block.id or "b?",
                )
            await asyncio.sleep(3.0)
            completed.append(idx)
            from app.models.task_card import Artifact
            return Artifact(summary=f"iter {idx} finished")

        _install(monkeypatch, _mixed)
        from app.agents.block_executor import execute_block
        ctx = _ctx(tmp_path)
        with pytest.raises(TaskInfraError):
            await execute_block(_fanout(6), ctx)
        assert completed == [], (
            f"siblings ran to completion despite the gate: {completed}. "
            f"The collapse is unbounded — every subagent burns a full "
            f"task against the same dead dependency."
        )
        assert ctx.infra_gated_reason, "gate never latched a reason"

    @pytest.mark.asyncio
    async def test_gate_is_fast_not_fan_out_wide(self, tmp_path, monkeypatch):
        """Wall clock must reflect the cancel, not the slowest sibling."""
        async def _mixed(block, **kw):
            from app.context import get_task_iteration_context
            _ic = get_task_iteration_context() or {}
            idx = _ic.get("index")
            if idx == 0:
                raise TaskInfraError(
                    "expired", infra_kind="authentication_error",
                    block_id=block.id or "b?",
                )
            await asyncio.sleep(5.0)
            from app.models.task_card import Artifact
            return Artifact(summary="never")

        _install(monkeypatch, _mixed)
        from app.agents.block_executor import execute_block
        t0 = asyncio.get_event_loop().time()
        with pytest.raises(TaskInfraError):
            await execute_block(_fanout(8), _ctx(tmp_path))
        elapsed = asyncio.get_event_loop().time() - t0
        assert elapsed < 2.0, (
            f"took {elapsed:.2f}s; the gate should cancel within a poll "
            f"interval rather than awaiting a 5 s sibling"
        )


class TestThrottleDoesNotGateAHealthyFanOut:
    """Kind-dependence, enforced at the executor and not just in policy."""

    @pytest.mark.asyncio
    async def test_single_throttle_lets_siblings_finish(
        self, tmp_path, monkeypatch,
    ):
        completed: list = []

        async def _one_throttle(block, **kw):
            from app.context import get_task_iteration_context
            _ic = get_task_iteration_context() or {}
            idx = _ic.get("index")
            if idx == 0:
                raise TaskInfraError(
                    "throttled", infra_kind="throttling_error",
                    block_id=block.id or "b?",
                )
            completed.append(idx)
            from app.models.task_card import Artifact
            return Artifact(summary=f"iter {idx} ok")

        _install(monkeypatch, _one_throttle)
        from app.agents.block_executor import execute_block
        ctx = _ctx(tmp_path)
        # One throttle in 10 is below the proportional threshold, so the
        # run still holds (the fault is real) but the fleet is not gated.
        with pytest.raises(TaskInfraError):
            await execute_block(_fanout(10), ctx)
        assert len(completed) == 9, (
            f"only {len(completed)}/9 healthy siblings completed — a single "
            f"transient throttle wrongly aborted a healthy fan-out"
        )
        assert ctx.infra_gated_reason is None


class TestWidthIsNotLeaked:
    """Defect 2: the denominator must survive the raise, and be restored."""

    @pytest.mark.asyncio
    async def test_widest_fanout_survives_for_the_summary(
        self, tmp_path, monkeypatch,
    ):
        """The hold surface reads its denominator after every loop exits.

        If it read the LIVE width it would see 0 and report fleet_wide
        False for a credential that killed the whole fleet.
        """
        async def _boom(block, **kw):
            raise TaskInfraError(
                "expired", infra_kind="authentication_error",
                block_id=block.id or "b?",
            )
        _install(monkeypatch, _boom)
        from app.agents.block_executor import execute_block
        ctx = _ctx(tmp_path)
        with pytest.raises(TaskInfraError):
            await execute_block(_fanout(7), ctx)
        # Live width restored to its pre-fan-out value...
        assert ctx.infra_fanout_width == 0
        # ...but the summary still knows how wide the fleet was.
        summary = ctx.infra_summary()
        assert summary["fanout_width"] == 7, (
            f"summary lost the denominator: {summary}. fleet_wide becomes "
            f"unreliable and the hold cannot distinguish a dead credential "
            f"from one throttled sibling."
        )
        assert summary["primary_kind"] == "authentication_error"
        assert summary["fleet_wide"] is True
