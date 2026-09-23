"""An ``until`` block that runs out of ``until_max`` with its explicit
condition still unmet must report failure, not success.

Found on GFX Stage 2 run 3068d3d0 (2026-09-20): the verify block's
condition was "every non-deferred defect is verified or wont-fix",
until_max=3.  Three iterations ran, the model evaluator said "no" each
time, the loop fell out of its ``for`` and returned
``failed=bool(last_artifact.failed)`` -- the BODY's last outcome, which
was a clean agent turn -- so the run went ``done`` with 8 regressions and
85 still-broken defects on the ledger.  Nothing in the artifact said the
condition was unmet.

Exhaustion is a verdict on the goal.  When the card author wrote an
explicit condition, hitting the cap without satisfying it is the block
failing at what it was asked to do, and must surface as ``failed`` with
a decision that names the cause, so ``classify_terminal_status`` can make
the run ``partial`` / ``failed`` and ``on_failure=stop`` can hold the
siblings.

The no-condition ("goal card") path is deliberately NOT changed here: its
exits are the agent's own self-assessment and the convergence backstop,
and its docstring names until_max as a safety net rather than the stop.
"""

import pytest
from unittest.mock import patch

from app.models.task_card import Block, Artifact
from app.models.task_run import TaskRunCreate
from app.storage.task_runs import TaskRunStorage
from app.agents.block_executor import execute_block, ExecutionContext


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def run(storage):
    r = storage.create(TaskRunCreate(card_id="card-1"))
    storage.update_status(r.id, "running")
    return storage.get(r.id)


def _until(condition: str, n_max: int) -> Block:
    return Block(
        block_type="until", id="until-1", name="verify",
        until_mode="model", until_condition=condition, until_max=n_max,
        body=[Block(block_type="task", id="inner", name="t", instructions="do")],
    )


def _clean_body(n):
    """A body whose every iteration is a clean, distinct agent turn."""
    calls = [0]

    async def stub(b, project_root=None, project_id=None, run_id=None):
        calls[0] += 1
        return Artifact(summary=f"cycle {calls[0]}: deferred the rest", tokens=5)
    return stub, calls


class TestUntilExhaustion:
    @pytest.mark.asyncio
    async def test_unmet_condition_at_until_max_is_a_failure(self, storage, run):
        block = _until("no defect is still-broken", n_max=3)
        stub, calls = _clean_body(3)

        async def never(condition, artifact):
            return False

        ctx = ExecutionContext(run_id=run.id, storage=storage)
        with patch("app.agents.block_executor.execute_task_block", stub), \
             patch("app.agents.block_executor._evaluate_until_condition_with_model", never):
            result = await execute_block(block, ctx)

        assert calls[0] == 3, "ran the body to until_max"
        assert result.failed is True, (
            "the body's last iteration was clean, but the CONDITION was never "
            "met -- that is the block failing, not succeeding"
        )
        joined = " ".join(result.decisions)
        assert "not satisfied" in joined and "3" in joined, result.decisions

    @pytest.mark.asyncio
    async def test_met_condition_is_not_a_failure(self, storage, run):
        """Positive control: the same body with a satisfiable condition."""
        block = _until("no defect is still-broken", n_max=3)
        stub, calls = _clean_body(3)
        answers = iter([False, True])

        async def eventually(condition, artifact):
            return next(answers)

        ctx = ExecutionContext(run_id=run.id, storage=storage)
        with patch("app.agents.block_executor.execute_task_block", stub), \
             patch("app.agents.block_executor._evaluate_until_condition_with_model", eventually):
            result = await execute_block(block, ctx)

        assert calls[0] == 2
        assert result.failed is False
        assert any("satisfied at iter 1" in d for d in result.decisions)

    @pytest.mark.asyncio
    async def test_exhaustion_reaches_the_run_status(self, storage, run):
        """The seam: an exhausted until inside a group makes the run
        outcome non-``done`` when the launch path reclassifies it."""
        from app.utils.run_outcome import classify_terminal_status
        group = Block(
            block_type="group", id="g", name="pipeline", on_failure="stop",
            body=[_until("all verified", n_max=2)],
        )
        stub, _ = _clean_body(2)

        async def never(condition, artifact):
            return False

        ctx = ExecutionContext(run_id=run.id, storage=storage)
        with patch("app.agents.block_executor.execute_task_block", stub), \
             patch("app.agents.block_executor._evaluate_until_condition_with_model", never):
            result = await execute_block(group, ctx)

        assert result.failed is True
        fresh = storage.get(run.id)
        final = classify_terminal_status(
            "failed" if result.failed else "done", fresh.block_states)
        assert final != "done", final
