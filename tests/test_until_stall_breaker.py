"""The Until loop's stall breaker must not become a lower ``until_max``.

The breaker exists for a real incident: run 2e1fbe76 burned 35 consecutive
iterations against a condition that was unsatisfiable by construction (it
demanded visual verification; the deploy step that made verification possible
was broken).  Its own in-source comment states the constraint it has to respect:

    THREE in a row are required, so a loop that is genuinely advancing is
    untouched -- the breaker must not degenerate into a lower until_max.

As first written it did exactly that, and broke two PRE-EXISTING committed
tests in tests/test_block_executor.py::TestUntilExitConditions:

    test_self_assessment_false_continues_iterating          assert 3 == 4
    test_self_assessment_takes_priority_over_until_condition assert 3 == 5

Both failed because each of its three stall signals fires on a shape that is
NOT a stall:

  * ``artifact.failed`` alone -- a fix-until-green loop fails on every
    intermediate iteration BY DESIGN. Counting that as non-progress makes
    ``until_max > 3`` unreachable for the canonical Until use case.
  * a repeated summary alone -- a terse agent, or a stub returning a constant,
    reports the same summary while genuinely working. And when an explicit
    condition is set the documented contract is that ONLY the evaluator
    decides (that is what the second test above pins), so overriding it with a
    summary heuristic contradicts layer 3.

Neither signal alone is sufficient, because each has a legitimate reading.
The corrected rule requires evidence that is unambiguous:

    stalled = objective_met == "partial"          # the agent itself reports an
                                                  # obstacle, explicitly
              OR (failed AND summary unchanged)   # it failed AND told us
                                                  # nothing new

This file pins BOTH directions.  The first class would pass against a breaker
that had simply been deleted, so the second class -- proving the breaker still
fires -- is what makes this suite non-vacuous.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from app.models.task_card import Artifact, Block
from app.models.task_run import TaskRunCreate, TaskRunBlockState
from app.storage.task_runs import TaskRunStorage
from app.agents.block_executor import ExecutionContext, execute_block

EVALUATOR = "app.agents.block_executor._evaluate_until_condition_with_model"
BODY = "app.agents.block_executor.execute_task_block"


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def until_run(storage):
    r = storage.create(TaskRunCreate(card_id="card-stall"))
    storage.set_block_state(r.id, TaskRunBlockState(
        block_id="until-1", block_type="until",
    ))
    return r


def _block(until_max: int, condition: str = "") -> Block:
    return Block(
        block_type="until", id="until-1", name="u",
        until_mode="model", until_condition=condition, until_max=until_max,
        body=[Block(block_type="task", id="inner", name="inner",
                    instructions="do it")],
    )


def _artifact(summary: str, objective_met: str, failed: bool = False) -> Artifact:
    return Artifact(
        summary=summary, failed=failed, tokens=5, duration_ms=1,
        self_assessment={"objective_met": objective_met, "rationale": ""},
    )


async def _run(block, ctx, *, summary, objective_met, failed,
               condition_result=False):
    """Execute the loop, returning (body_call_count, artifact).

    ``summary`` is a callable of the 1-based iteration number so a test can
    choose progressing (unique) or non-progressing (constant) summaries.
    """
    calls = [0]

    async def body(b, project_root=None, project_id=None, run_id=None):
        calls[0] += 1
        return _artifact(summary(calls[0]), objective_met, failed)

    async def evaluator(*a, **k):
        return condition_result

    with patch(BODY, body), patch(EVALUATOR, new=evaluator):
        art = await execute_block(block, ctx)
    return calls[0], art


UNIQUE = lambda n: f"attempt {n}"      # noqa: E731 - progressing
CONSTANT = lambda n: "done"            # noqa: E731 - no new information


# ---------------------------------------------------------------------------
# direction 1: the breaker must NOT shorten a deliberate budget
# ---------------------------------------------------------------------------

class TestExplicitBudgetIsHonoured:
    @pytest.mark.asyncio
    async def test_failing_but_progressing_runs_to_until_max(
        self, storage, until_run,
    ):
        """A fix-until-green loop fails every intermediate iteration.

        Mirrors the shape of the pre-existing
        test_self_assessment_false_continues_iterating, which regressed to
        ``assert 3 == 4``.
        """
        ctx = ExecutionContext(run_id=until_run.id, storage=storage)
        calls, _ = await _run(_block(6), ctx, summary=UNIQUE,
                              objective_met="false", failed=True)
        assert calls == 6, (
            "failure alone is not a stall -- an Until loop whose body fails "
            "while producing new work each pass is the canonical use case"
        )

    @pytest.mark.asyncio
    async def test_repeated_summary_alone_runs_to_until_max(
        self, storage, until_run,
    ):
        """With a condition set, only the evaluator may decide.

        Mirrors test_self_assessment_takes_priority_over_until_condition,
        which regressed to ``assert 3 == 5``. A constant summary is a terse
        agent, not a stuck one, and the layer-3 contract is explicit that a
        set condition owns the exit.
        """
        ctx = ExecutionContext(run_id=until_run.id, storage=storage)
        calls, _ = await _run(_block(5, condition="some condition"), ctx,
                              summary=CONSTANT, objective_met="true",
                              failed=False)
        assert calls == 5, (
            "a repeated summary on a non-failing iteration must not override "
            "an explicit until_condition's budget"
        )

    @pytest.mark.asyncio
    async def test_objective_met_false_is_not_an_obstacle_report(
        self, storage, until_run,
    ):
        """'false' means 'not done yet'; only 'partial' reports an obstacle."""
        ctx = ExecutionContext(run_id=until_run.id, storage=storage)
        calls, _ = await _run(_block(4, condition="c"), ctx, summary=UNIQUE,
                              objective_met="false", failed=False)
        assert calls == 4

    @pytest.mark.asyncio
    async def test_a_healthy_loop_is_untouched(self, storage, until_run):
        """Control: nothing stall-like, so the budget is spent in full."""
        ctx = ExecutionContext(run_id=until_run.id, storage=storage)
        calls, art = await _run(_block(4, condition="c"), ctx, summary=UNIQUE,
                                objective_met="false", failed=False)
        assert calls == 4
        assert not any("stall breaker" in d for d in art.decisions)


# ---------------------------------------------------------------------------
# direction 2: the breaker must STILL fire -- without these, deleting it passes
# ---------------------------------------------------------------------------

class TestBreakerStillFires:
    @pytest.mark.asyncio
    async def test_three_partial_reports_stop_the_loop(
        self, storage, until_run,
    ):
        """The agent itself reporting an obstacle is unambiguous evidence."""
        ctx = ExecutionContext(run_id=until_run.id, storage=storage)
        calls, art = await _run(_block(20, condition="c"), ctx,
                                summary=UNIQUE, objective_met="partial",
                                failed=False)
        assert calls == 3, "three consecutive obstacle reports must stop it"
        assert any("stall breaker" in d for d in art.decisions)

    @pytest.mark.asyncio
    async def test_failing_with_no_new_information_stops_the_loop(
        self, storage, until_run,
    ):
        """Failed AND nothing new -- both signals, so it is a real stall.

        Four iterations, not three: "unchanged" needs a predecessor, so the
        first pass can never contribute to the streak. An assertion of 3 here
        would pass against the OLD rule (where bare failure counted from
        iteration 1) and so would certify the defect.
        """
        ctx = ExecutionContext(run_id=until_run.id, storage=storage)
        calls, art = await _run(_block(20, condition="c"), ctx,
                                summary=CONSTANT, objective_met="false",
                                failed=True)
        assert calls == 4
        assert any("stall breaker" in d for d in art.decisions)

    @pytest.mark.asyncio
    async def test_the_decision_names_the_reason_and_the_budget(
        self, storage, until_run,
    ):
        """A run cut short must say so, and against what it was cut."""
        ctx = ExecutionContext(run_id=until_run.id, storage=storage)
        _, art = await _run(_block(20, condition="c"), ctx, summary=CONSTANT,
                            objective_met="false", failed=True)
        d = next(x for x in art.decisions if "stall breaker" in x)
        assert "20" in d, "the decision must name the max it stopped short of"
        assert "3" in d

    @pytest.mark.asyncio
    async def test_a_recovering_loop_resets_the_streak(
        self, storage, until_run,
    ):
        """Two stalls then progress must not accumulate toward the limit.

        Without the reset, any loop with intermittent trouble dies on its
        third bad iteration however far apart they are.
        """
        ctx = ExecutionContext(run_id=until_run.id, storage=storage)
        calls = [0]

        async def body(b, project_root=None, project_id=None, run_id=None):
            calls[0] += 1
            n = calls[0]
            # stall, stall, progress, stall, stall, progress, ...
            if n % 3 == 0:
                return _artifact(f"progress {n}", "false", failed=False)
            return _artifact("stuck", "partial", failed=True)

        async def evaluator(*a, **k):
            return False

        with patch(BODY, body), patch(EVALUATOR, new=evaluator):
            art = await execute_block(_block(9, condition="c"), ctx)
        assert calls[0] == 9, (
            "an interrupted stall streak must reset; got "
            f"{calls[0]} iterations"
        )
        assert not any("stall breaker" in d for d in art.decisions)
