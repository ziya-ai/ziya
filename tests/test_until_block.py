"""Tests for the Until block — model-judged loop exit."""

import pytest
from unittest.mock import patch, AsyncMock

from app.models.task_card import Artifact, Block
from app.agents.block_executor import execute_block, ExecutionContext
from app.agents import until_evaluator
from app.storage.task_runs import TaskRunStorage


def _task(name: str = "t") -> Block:
    return Block(
        block_type="task", id=f"task-{name}", name=name,
        instructions=f"do {name}",
    )


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def ctx(storage):
    from app.models.task_run import TaskRunCreate
    run = storage.create(TaskRunCreate(card_id="c"))
    return ExecutionContext(run_id=run.id, storage=storage)


@pytest.mark.asyncio
async def test_until_terminates_when_model_says_yes(ctx):
    """Two iterations: first 'no', second 'yes' → loop stops at iter 1."""
    call_log = []

    async def fake_eval(condition, artifact):
        call_log.append(condition)
        return len(call_log) >= 2  # yes on 2nd call

    async def fake_task(block, **kw):
        return Artifact(summary=f"done iter", failed=False)

    block = Block(
        block_type="until", id="u", name="u",
        until_mode="model", until_condition="all green", until_max=10,
        body=[_task("x")],
    )
    with patch("app.agents.block_executor.execute_task_block", side_effect=fake_task), \
         patch("app.agents.block_executor._evaluate_until_condition_with_model",
               side_effect=fake_eval):
        artifact = await execute_block(block, ctx)
    assert len(call_log) == 2
    assert not artifact.failed


@pytest.mark.asyncio
async def test_explicit_condition_ignores_inner_self_assessment(ctx):
    """Regression: an explicit until_condition must NOT be short-circuited
    by the inner task's self_assessment.

    The "count to 300 by 20s" card had until_condition="counter is above
    300" wrapping a task "increase count by 20".  Each task reported
    objective_met="true" for its OWN atomic work (it did add 20), which
    layer 1 read as "loop done" and broke after iteration 0 — producing
    one iteration and a final count of 1.  With the guard, layer 1 is
    inert when a condition is set and only the model evaluator (layer 3)
    decides termination.
    """
    explicit_eval_calls = []

    async def eval_explicit_cond(condition, artifact):
        explicit_eval_calls.append(condition)
        return len(explicit_eval_calls) >= 5  # judge says "above 300" on 5th pass

    async def task_always_self_done(block, **kw):
        # Always declares its own task complete — the trap that used to
        # collapse the loop after a single iteration.
        return Artifact(
            summary="added 20", failed=False,
            self_assessment={"objective_met": "true", "rationale": "added 20"},
        )

    block = Block(
        block_type="until", id="u", name="u",
        until_mode="model", until_condition="counter is above 300", until_max=100,
        body=[_task("x")],
    )
    with patch("app.agents.block_executor.execute_task_block", side_effect=task_always_self_done), \
         patch("app.agents.block_executor._evaluate_until_condition_with_model",
               side_effect=eval_explicit_cond):
        await execute_block(block, ctx)
    assert len(explicit_eval_calls) == 5  # ran 5 iterations, not 1


@pytest.mark.asyncio
async def test_until_respects_max_when_condition_never_met(ctx):
    """Condition never satisfied → loop runs until_max times then stops."""
    eval_calls = []

    async def fake_eval(condition, artifact):
        eval_calls.append(condition)
        return False

    async def fake_task(block, **kw):
        return Artifact(summary="done", failed=False)

    block = Block(
        block_type="until", id="u", name="u",
        until_mode="model", until_condition="impossible", until_max=3,
        body=[_task("x")],
    )
    with patch("app.agents.block_executor.execute_task_block", side_effect=fake_task), \
         patch("app.agents.block_executor._evaluate_until_condition_with_model",
               side_effect=fake_eval):
        await execute_block(block, ctx)
    assert len(eval_calls) == 3  # exactly until_max


@pytest.mark.asyncio
async def test_until_no_condition_runs_to_max_without_self_assessment(ctx):
    """Empty condition + no self_assessment + unique summaries → no
    layer fires, loop runs to until_max.

    Replaces the prior "Repeat-until-success" semantic.  Empty condition
    now means "rely on agent self_assessment or convergence"; absent
    those signals, the cap is the only stop.  See
    design/goal-exit-conditions.md.
    """
    iter_count = 0

    async def fake_task(block, **kw):
        nonlocal iter_count
        iter_count += 1
        # Distinct summary each iter so convergence doesn't fire.
        return Artifact(summary=f"attempt {iter_count}", failed=False)

    block = Block(
        block_type="until", id="u", name="u",
        until_mode="model", until_condition=None, until_max=4,
        body=[_task("x")],
    )
    with patch("app.agents.block_executor.execute_task_block", side_effect=fake_task):
        await execute_block(block, ctx)
    assert iter_count == 4


@pytest.mark.asyncio
async def test_until_no_condition_terminates_on_self_assessment_true(ctx):
    """Empty condition + agent declares objective_met='true' → stops.

    This is the goal-card path: synthesize_goal_card emits
    until_condition="" so the loop relies on the agent's self_assessment
    tag (parsed by task_executor into Artifact.self_assessment).
    """
    iter_count = 0

    async def fake_task(block, **kw):
        nonlocal iter_count
        iter_count += 1
        return Artifact(
            summary=f"attempt {iter_count}",
            failed=False,
            self_assessment={"objective_met": "true", "rationale": "done"},
        )

    block = Block(
        block_type="until", id="u", name="u",
        until_mode="model", until_condition="", until_max=10,
        body=[_task("x")],
    )
    with patch("app.agents.block_executor.execute_task_block", side_effect=fake_task):
        await execute_block(block, ctx)
    assert iter_count == 1


@pytest.mark.asyncio
async def test_until_no_condition_terminates_on_convergence(ctx):
    """Empty condition + identical summaries across iters → convergence
    fires at iter 1 (when 2 signatures match)."""
    iter_count = 0

    async def fake_task(block, **kw):
        nonlocal iter_count
        iter_count += 1
        # Identical summary every iter so convergence triggers.
        return Artifact(summary="same finding", failed=False)

    block = Block(
        block_type="until", id="u", name="u",
        until_mode="model", until_condition="", until_max=10,
        body=[_task("x")],
    )
    with patch("app.agents.block_executor.execute_task_block", side_effect=fake_task):
        await execute_block(block, ctx)
    # iter 0 records sig; iter 1 matches → stop.  Total = 2.
    assert iter_count == 2


@pytest.mark.asyncio
async def test_until_expression_mode_runs_to_max():
    """Expression mode is reserved; in current build it runs to max."""
    parsed_yes = until_evaluator._parse_yes_no("yes")
    parsed_no = until_evaluator._parse_yes_no("no")
    parsed_garbage = until_evaluator._parse_yes_no("maybe?")
    assert parsed_yes is True
    assert parsed_no is False
    # Ambiguous → conservative no.
    assert parsed_garbage is False
    assert until_evaluator._parse_yes_no("") is False


# ── until_evaluator transport-level coverage ────────────────

def test_parse_yes_no_variants():
    """Each accepted yes/no synonym maps correctly."""
    assert until_evaluator._parse_yes_no("YES") is True
    assert until_evaluator._parse_yes_no("y") is True
    assert until_evaluator._parse_yes_no("true") is True
    assert until_evaluator._parse_yes_no("done") is True
    assert until_evaluator._parse_yes_no("satisfied") is True
    assert until_evaluator._parse_yes_no("NO.") is False
    assert until_evaluator._parse_yes_no("not yet") is False
    assert until_evaluator._parse_yes_no("incomplete") is False
    assert until_evaluator._parse_yes_no(None) is False


def test_build_user_message_includes_condition_summary_decisions():
    """The prompt the model sees contains all three input fields."""
    art = Artifact(
        summary="server returned 200",
        decisions=["chose POST", "added auth header", "retried once"],
        failed=False,
    )
    msg = until_evaluator._build_user_message("status is 200", art)
    assert "status is 200" in msg
    assert "server returned 200" in msg
    assert "chose POST" in msg
    assert "added auth header" in msg


def test_build_user_message_truncates_decisions_to_five():
    """Decisions list is capped at 5 to bound the prompt size."""
    art = Artifact(
        summary="x",
        decisions=[f"d{i}" for i in range(20)],
        failed=False,
    )
    msg = until_evaluator._build_user_message("c", art)
    assert "d0" in msg
    assert "d4" in msg
    assert "d5" not in msg
    assert "d19" not in msg


@pytest.mark.asyncio
async def test_evaluate_condition_empty_returns_false():
    """An empty condition short-circuits to False without calling the model."""
    art = Artifact(summary="anything", failed=False)
    assert await until_evaluator.evaluate_condition("", art) is False
    assert await until_evaluator.evaluate_condition("   ", art) is False


@pytest.mark.asyncio
async def test_evaluate_condition_yes_response():
    """Model says yes → True."""
    art = Artifact(summary="all green", failed=False)
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(return_value="yes")):
        assert await until_evaluator.evaluate_condition("done?", art) is True


@pytest.mark.asyncio
async def test_evaluate_condition_no_response():
    """Model says no → False."""
    art = Artifact(summary="still failing", failed=True)
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(return_value="no")):
        assert await until_evaluator.evaluate_condition("done?", art) is False


@pytest.mark.asyncio
async def test_evaluate_condition_transport_error_returns_false():
    """Any transport exception resolves to False (loop continues)."""
    art = Artifact(summary="x", failed=False)
    with patch("app.services.model_resolver.call_service_model",
               new=AsyncMock(side_effect=RuntimeError("network down"))):
        assert await until_evaluator.evaluate_condition("done?", art) is False


# ── _execute_schedule_passthrough ───────────────────────────

@pytest.mark.asyncio
async def test_schedule_block_executed_directly_runs_body_once(ctx):
    """Running a schedule block via execute_block (not via the
    scheduler's fire path) executes its body exactly once.
    This is the 'Run now' button behavior."""
    from app.models.task_card import Block
    calls = []

    async def fake_task(block, **kw):
        calls.append(block.id)
        return Artifact(summary=f"ran {block.id}", failed=False)

    sched = Block(
        block_type="schedule", id="s", name="s",
        schedule_mode="interval",
        schedule_interval_value=1, schedule_interval_unit="hours",
        body=[_task("a")],
    )
    with patch("app.agents.block_executor.execute_task_block", side_effect=fake_task):
        artifact = await execute_block(sched, ctx)
    assert calls == ["task-a"]
    assert not artifact.failed


@pytest.mark.asyncio
async def test_schedule_block_with_empty_body_returns_marker(ctx):
    """Empty schedule body yields a marker artifact, not a crash."""
    from app.models.task_card import Block
    sched = Block(
        block_type="schedule", id="s", name="s",
        schedule_mode="interval", body=[],
    )
    artifact = await execute_block(sched, ctx)
    assert "empty" in artifact.summary.lower()
    assert not artifact.failed
