"""Infra faults must escape loop containers, not become iteration results.

The existing infra-hold tests cover the two ENDS of the path: that
task_executor raises TaskInfraError, and that storage.mark_held records a
held run.  Both passed while the middle was broken.

asyncio.gather(return_exceptions=True) in _execute_repeat / _execute_parallel
converted the exception into a failed Artifact, discarding the ``infra_kind``
attribute that api.task_cards reads off the live exception to choose
mark_held over update_status("failed").  A 20-wide fan-out on an expired
credential therefore burned every iteration against the same dead
dependency and recorded the result as a failure of the work.
"""

import pytest

from app.agents.task_executor import TaskInfraError
from app.models.task_card import Block


def _ctx(tmp_path):
    from app.agents.block_executor import ExecutionContext
    return ExecutionContext(
        run_id="r-loop-infra",
        project_id="p1",
        project_root=str(tmp_path),
    )


@pytest.fixture
def infra_raiser(monkeypatch):
    """Every task dispatch raises an auth-class infra fault."""
    calls = {"n": 0}

    async def _boom(block, **kwargs):
        calls["n"] += 1
        raise TaskInfraError(
            "Task execution failed (authentication_error): token expired",
            infra_kind="authentication_error",
            block_id=block.id or "b?",
        )

    monkeypatch.setattr(
        "app.agents.block_executor.execute_task_block", _boom,
    )
    return calls


def _fanout_block(n: int, parallel: bool) -> Block:
    return Block(
        id="rep1",
        block_type="repeat",
        name="fan-out",
        repeat_mode="count",
        repeat_count=n,
        repeat_parallel=parallel,
        repeat_propagate="none",
        body=[Block(id="t1", block_type="task", name="w", instructions="go")],
    )


class TestRepeatDoesNotSwallowInfraFaults:

    @pytest.mark.asyncio
    async def test_parallel_repeat_reraises_with_kind_intact(
        self, tmp_path, infra_raiser,
    ):
        from app.agents.block_executor import execute_block
        with pytest.raises(TaskInfraError) as ei:
            await execute_block(_fanout_block(5, True), _ctx(tmp_path))
        assert ei.value.infra_kind == "authentication_error"

    @pytest.mark.asyncio
    async def test_serial_repeat_reraises(self, tmp_path, infra_raiser):
        from app.agents.block_executor import execute_block
        with pytest.raises(TaskInfraError) as ei:
            await execute_block(_fanout_block(5, False), _ctx(tmp_path))
        assert ei.value.infra_kind == "authentication_error"

    @pytest.mark.asyncio
    async def test_does_not_grind_through_every_iteration(
        self, tmp_path, infra_raiser,
    ):
        """The regression: 11 cycles against a dead dependency.

        Serial must stop at the first fault.  Parallel has already
        dispatched concurrently, so the bound is the fan-out width — the
        contract is that it does not ITERATE past it.
        """
        from app.agents.block_executor import execute_block
        with pytest.raises(TaskInfraError):
            await execute_block(_fanout_block(1, False), _ctx(tmp_path))
        assert infra_raiser["n"] == 1

    @pytest.mark.asyncio
    async def test_serial_stops_at_first_not_after_all(
        self, tmp_path, infra_raiser,
    ):
        from app.agents.block_executor import execute_block
        with pytest.raises(TaskInfraError):
            await execute_block(_fanout_block(11, False), _ctx(tmp_path))
        assert infra_raiser["n"] == 1, (
            f"serial loop ran {infra_raiser['n']} iterations against a "
            f"known-dead dependency; expected to stop at the first"
        )


class TestOrdinaryFailuresStillLoop:
    """The guard must not turn every failure into a run-ending abort."""

    @pytest.mark.asyncio
    async def test_work_failure_does_not_abort_the_fan_out(
        self, tmp_path, monkeypatch,
    ):
        from app.agents.block_executor import execute_block
        from app.agents.task_executor import TaskExecutorError
        calls = {"n": 0}

        async def _fail(block, **kwargs):
            calls["n"] += 1
            raise TaskExecutorError("the work failed on its merits")

        monkeypatch.setattr(
            "app.agents.block_executor.execute_task_block", _fail,
        )
        art = await execute_block(_fanout_block(4, True), _ctx(tmp_path))
        assert art.failed
        assert calls["n"] == 4, (
            "a plain work failure must not short-circuit the fan-out"
        )


class TestParallelBlockDoesNotSwallowInfraFaults:

    @pytest.mark.asyncio
    async def test_parallel_block_reraises(self, tmp_path, infra_raiser):
        from app.agents.block_executor import execute_block
        blk = Block(
            id="par1",
            block_type="parallel",
            name="two-up",
            body=[
                Block(id="a", block_type="task", name="a", instructions="go"),
                Block(id="b", block_type="task", name="b", instructions="go"),
            ],
        )
        with pytest.raises(TaskInfraError) as ei:
            await execute_block(blk, _ctx(tmp_path))
        assert ei.value.infra_kind == "authentication_error"
