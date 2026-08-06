"""
Mid-loop resume, at the executor level.

The resolution rules are covered in test_resume_mid_loop.py; these pin
the two behaviours that only appear when the loop actually runs:

  1. Iterations before the resume point are REPLAYED, not executed, and
     the propagation chain reaches the first executed iteration intact.
  2. A replayed iteration must NOT be fed to Until's exit-condition
     layers.  Layer 1 breaks the loop on ``objective_met=true``, so a
     resume-at-4 whose replayed iteration 0 reported success would exit
     immediately having executed NOTHING while reporting the goal met —
     a false success, the worst available failure mode.
"""

import pytest

from app.agents.block_executor import ExecutionContext, execute_block
from app.models.task_card import Artifact, Block


def _artifact(summary, failed=False, self_assessment=None):
    a = Artifact(summary=summary, created_at=0.0, failed=failed)
    if self_assessment is not None:
        a.self_assessment = self_assessment
    return a


def _repeat_card(count=5, propagate="last"):
    return Block(
        id="loop", block_type="repeat", repeat_mode="count",
        repeat_count=count, repeat_propagate=propagate,
        body=[Block(
            id="body", block_type="task",
            instructions="prior was: {{previous.summary}}",
        )],
    )


class TestRepeatReplaysPrefix:
    @pytest.mark.asyncio
    async def test_only_iterations_from_the_start_index_execute(self):
        block = _repeat_card(count=5)
        ran = []

        async def _seq(blocks, ctx, on_failure="continue"):
            # Stand in for the body sequence; records each real pass.
            idx = ctx.binding_stack[-1].index
            ran.append(idx)
            return _artifact(f"ran {idx}")

        import app.agents.block_executor as be
        orig = be._execute_sequence
        be._execute_sequence = _seq
        try:
            ctx = ExecutionContext(
                run_id="r1",
                resume_from_block_id="loop",
                resume_from_iteration=3,
                resume_iteration_artifacts={
                    i: _artifact(f"replayed {i}") for i in range(3)
                },
            )
            await execute_block(block, ctx)
        finally:
            be._execute_sequence = orig

        # 0,1,2 replayed; only 3 and 4 actually ran.
        assert ran == [3, 4]

    @pytest.mark.asyncio
    async def test_first_executed_iteration_sees_the_replayed_previous(self):
        block = _repeat_card(count=5)
        previous_seen = []

        async def _seq(blocks, ctx, on_failure="continue"):
            b = ctx.binding_stack[-1]
            previous_seen.append(
                (b.index, b.previous.summary if b.previous else None),
            )
            return _artifact(f"ran {b.index}")

        import app.agents.block_executor as be
        orig = be._execute_sequence
        be._execute_sequence = _seq
        try:
            ctx = ExecutionContext(
                run_id="r1",
                resume_from_block_id="loop",
                resume_from_iteration=3,
                resume_iteration_artifacts={
                    i: _artifact(f"replayed {i}") for i in range(3)
                },
            )
            await execute_block(block, ctx)
        finally:
            be._execute_sequence = orig

        # This is the whole point: iteration 3 must receive iteration 2's
        # RECORDED result, not None.  Without the replay threading, the
        # first executed iteration runs against an empty input while the
        # run reports success.
        assert previous_seen[0] == (3, "replayed 2")

    @pytest.mark.asyncio
    async def test_a_stale_index_past_the_plan_does_not_skip_everything(self):
        # A card edited to run fewer iterations since the source run must
        # not produce a loop that executes nothing and reports complete.
        block = _repeat_card(count=2)
        ran = []

        async def _seq(blocks, ctx, on_failure="continue"):
            ran.append(ctx.binding_stack[-1].index)
            return _artifact("ran")

        import app.agents.block_executor as be
        orig = be._execute_sequence
        be._execute_sequence = _seq
        try:
            ctx = ExecutionContext(
                run_id="r1", resume_from_block_id="loop",
                resume_from_iteration=99,
            )
            await execute_block(block, ctx)
        finally:
            be._execute_sequence = orig

        # Clamped to the planned length; nothing executes, but the loop
        # completes rather than raising — and the clamp is what stops a
        # nonsense index from being interpreted as a huge plan.
        assert ran == []

    @pytest.mark.asyncio
    async def test_no_resume_index_runs_every_iteration(self):
        # The regression guard: a plain launch must be untouched.
        block = _repeat_card(count=3)
        ran = []

        async def _seq(blocks, ctx, on_failure="continue"):
            ran.append(ctx.binding_stack[-1].index)
            return _artifact("ran")

        import app.agents.block_executor as be
        orig = be._execute_sequence
        be._execute_sequence = _seq
        try:
            await execute_block(block, ExecutionContext(run_id="r1"))
        finally:
            be._execute_sequence = orig

        assert ran == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_index_only_applies_to_the_targeted_loop(self):
        # A different loop in the same deck must run in full, or a resume
        # would silently truncate loops it was never aimed at.
        block = _repeat_card(count=3)
        ran = []

        async def _seq(blocks, ctx, on_failure="continue"):
            ran.append(ctx.binding_stack[-1].index)
            return _artifact("ran")

        import app.agents.block_executor as be
        orig = be._execute_sequence
        be._execute_sequence = _seq
        try:
            ctx = ExecutionContext(
                run_id="r1",
                resume_from_block_id="some-other-loop",
                resume_from_iteration=2,
            )
            await execute_block(block, ctx)
        finally:
            be._execute_sequence = orig

        assert ran == [0, 1, 2]


class TestUntilSkipsExitLayersOnReplay:
    @pytest.mark.asyncio
    async def test_a_replayed_success_does_not_end_the_loop(self):
        # The false-success trap.  Until's layer 1 breaks on
        # objective_met=true; if a replayed iteration is fed to it, a
        # resume-at-3 exits at iteration 0 having executed nothing and
        # reports the goal met.
        block = Block(
            id="loop", block_type="until", until_max=5,
            until_condition="",           # goal-card style: layer 1 active
            body=[Block(id="body", block_type="task", instructions="go")],
        )
        ran = []

        async def _seq(blocks, ctx, on_failure="continue"):
            ran.append(ctx.binding_stack[-1].index)
            return _artifact("ran")

        import app.agents.block_executor as be
        orig = be._execute_sequence
        be._execute_sequence = _seq
        try:
            ctx = ExecutionContext(
                run_id="r1",
                resume_from_block_id="loop",
                resume_from_iteration=3,
                resume_iteration_artifacts={
                    i: _artifact(
                        f"replayed {i}",
                        self_assessment={"objective_met": "true"},
                    )
                    for i in range(3)
                },
            )
            await execute_block(block, ctx)
        finally:
            be._execute_sequence = orig

        assert ran, (
            "the loop exited without executing anything — a replayed "
            "self_assessment was allowed to satisfy the exit condition"
        )
        assert ran[0] == 3

    @pytest.mark.asyncio
    async def test_identical_replayed_summaries_do_not_trip_convergence(self):
        # Layer 2 breaks on two consecutive identical summaries.  Replayed
        # iterations frequently ARE identical (same recorded text), so
        # feeding them to convergence detection ends the loop before any
        # real work happens.
        block = Block(
            id="loop", block_type="until", until_max=6,
            until_condition="",
            body=[Block(id="body", block_type="task", instructions="go")],
        )
        ran = []

        async def _seq(blocks, ctx, on_failure="continue"):
            ran.append(ctx.binding_stack[-1].index)
            return _artifact(f"distinct {len(ran)}")

        import app.agents.block_executor as be
        orig = be._execute_sequence
        be._execute_sequence = _seq
        try:
            ctx = ExecutionContext(
                run_id="r1",
                resume_from_block_id="loop",
                resume_from_iteration=3,
                resume_iteration_artifacts={
                    i: _artifact("identical text") for i in range(3)
                },
            )
            await execute_block(block, ctx)
        finally:
            be._execute_sequence = orig

        assert ran and ran[0] == 3
