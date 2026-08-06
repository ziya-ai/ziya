"""
Mid-loop resume: continuing a loop at an iteration index (E2).

A loop was only ever resumable at iteration 0 — ``find_resume_target``
normalises a loop-body click up to the loop, and the loop then re-plans
from scratch.  A five-iteration campaign that lost iteration 5 to an
expired credential therefore had to re-pay all five, discarding four
banked passes.  That is the most expensive lost work in the task system,
because a long loop is exactly where a run outlives a credential.

These pin the resolution rules and the two refusals, each of which
prevents a resume that would LOOK successful while producing wrong
results.
"""

import pytest

from app.utils.resume_targets import (
    find_block, is_loop_node, resolve_iteration_resume,
)


def _summary(index, status="passed", has_artifact=True):
    return {"index": index, "status": status, "has_artifact": has_artifact}


def _loop_card(parallel=False, block_type="repeat"):
    """A Group root wrapping one loop with a two-block body."""
    return {
        "id": "root", "block_type": "group", "body": [
            {"id": "prep", "block_type": "task", "body": []},
            {
                "id": "loop", "block_type": block_type,
                "repeat_parallel": parallel,
                "body": [
                    {"id": "body1", "block_type": "task", "body": []},
                    {"id": "body2", "block_type": "task", "body": []},
                ],
            },
        ],
    }


FIVE_PASSES = [_summary(i) for i in range(5)]


class TestFindBlock:
    def test_finds_a_nested_node(self):
        assert find_block(_loop_card(), "body2")["block_type"] == "task"

    def test_returns_none_for_unknown(self):
        assert find_block(_loop_card(), "nope") is None

    def test_identifies_loop_types(self):
        card = _loop_card()
        assert is_loop_node(find_block(card, "loop"))
        assert not is_loop_node(find_block(card, "prep"))
        assert not is_loop_node(find_block(card, "root"))

    def test_until_is_a_loop(self):
        card = _loop_card(block_type="until")
        assert is_loop_node(find_block(card, "loop"))

    def test_none_is_not_a_loop(self):
        assert not is_loop_node(None)


class TestRetryIteration:
    def test_retry_starts_at_that_iteration(self):
        start, err = resolve_iteration_resume(
            _loop_card(), "loop", 3, FIVE_PASSES, "retry_iteration",
        )
        assert err is None
        assert start == 3

    def test_retry_iteration_zero_replays_nothing(self):
        start, err = resolve_iteration_resume(
            _loop_card(), "loop", 0, FIVE_PASSES, "retry_iteration",
        )
        assert err is None
        assert start == 0

    def test_retrying_the_last_recorded_iteration_is_allowed(self):
        # The observed case: five iterations passed, the fifth's follow-up
        # work was lost to the credential expiry.  Re-running index 4 must
        # be possible without discarding 0-3.
        start, err = resolve_iteration_resume(
            _loop_card(), "loop", 4, FIVE_PASSES, "retry_iteration",
        )
        assert err is None
        assert start == 4


class TestContinueIteration:
    def test_continue_starts_after_that_iteration(self):
        start, err = resolve_iteration_resume(
            _loop_card(), "loop", 3, FIVE_PASSES, "continue_iteration",
        )
        assert err is None
        assert start == 4

    def test_continue_past_the_last_recorded_iteration(self):
        # Legitimate: the loop's plan may be longer than what ran, so
        # continuing past the last record means "run the rest".
        start, err = resolve_iteration_resume(
            _loop_card(), "loop", 4, FIVE_PASSES, "continue_iteration",
        )
        assert err is None
        assert start == 5

    def test_continue_from_a_failed_iteration_is_allowed(self):
        # "I fixed it by hand, move on" — the same intent the block-level
        # continue serves.
        sums = [_summary(0), _summary(1, status="failed")]
        start, err = resolve_iteration_resume(
            _loop_card(), "loop", 1, sums, "continue_iteration",
        )
        assert err is None
        assert start == 2


class TestRefusals:
    def test_parallel_loop_is_refused(self):
        # Iterations cannot see each other, so 0..2 were not
        # prerequisites of 3; "resume at 3" would just run fewer
        # iterations than the card asks for and report the loop complete.
        start, err = resolve_iteration_resume(
            _loop_card(parallel=True), "loop", 3, FIVE_PASSES,
        )
        assert start is None
        assert "parallel" in err

    def test_non_loop_block_is_refused(self):
        start, err = resolve_iteration_resume(
            _loop_card(), "prep", 0, [],
        )
        assert start is None
        assert "Repeat or Until" in err

    def test_unknown_block_is_refused(self):
        start, err = resolve_iteration_resume(_loop_card(), "nope", 0, [])
        assert start is None
        assert "not found" in err

    def test_unrecorded_iteration_is_refused(self):
        start, err = resolve_iteration_resume(
            _loop_card(), "loop", 9, FIVE_PASSES,
        )
        assert start is None
        assert "never recorded" in err

    def test_negative_index_is_refused(self):
        start, err = resolve_iteration_resume(
            _loop_card(), "loop", -1, FIVE_PASSES,
        )
        assert start is None
        assert "zero or greater" in err

    def test_unknown_mode_is_refused(self):
        start, err = resolve_iteration_resume(
            _loop_card(), "loop", 1, FIVE_PASSES, mode="retry",
        )
        assert start is None
        assert "unknown iteration resume mode" in err

    def test_missing_predecessor_artifact_is_refused(self):
        # Past the 50-pass retention cap there is no artifact file, so
        # {{previous}} would replay as empty and the executed iteration
        # would run against nothing — a failure that looks like a defect
        # in the card rather than a missing input.
        sums = [_summary(0), _summary(1, has_artifact=False), _summary(2)]
        start, err = resolve_iteration_resume(
            _loop_card(), "loop", 2, sums, "retry_iteration",
        )
        assert start is None
        assert "not retained" in err

    def test_only_the_immediate_predecessor_must_be_retained(self):
        # {{previous}} binds one iteration back; {{all}} degrades to a
        # shorter history, which is visible rather than silently wrong.
        sums = [_summary(0, has_artifact=False), _summary(1), _summary(2)]
        start, err = resolve_iteration_resume(
            _loop_card(), "loop", 2, sums, "retry_iteration",
        )
        assert err is None
        assert start == 2

    def test_missing_predecessor_record_entirely_is_refused(self):
        sums = [_summary(0), _summary(2)]  # index 1 absent
        start, err = resolve_iteration_resume(
            _loop_card(), "loop", 2, sums, "retry_iteration",
        )
        assert start is None
        assert "no prior result" in err


class TestResumeKindVocabulary:
    def test_both_iteration_kinds_are_accepted_statuses(self):
        # The run model must accept them or the launch would 500 after
        # the resolution has already succeeded.
        from app.models.task_run import TaskRun
        for kind in ("retry_iteration", "continue_iteration"):
            run = TaskRun(card_id="c1", resume_kind=kind)
            assert run.resume_kind == kind

    def test_iteration_fields_default_to_absent(self):
        # A plain launch must be indistinguishable from before, or every
        # existing run would be treated as a mid-loop resume at index 0.
        from app.models.task_run import TaskRun
        run = TaskRun(card_id="c1")
        assert run.resume_from_iteration is None
        assert run.resume_iteration_artifacts == {}

    def test_create_model_carries_the_fields_through(self):
        # storage.create() names fields one by one, so an unnamed field is
        # silently dropped and the resume would restart the loop at 0.
        from app.models.task_run import TaskRunCreate
        c = TaskRunCreate(card_id="c1", resume_from_iteration=3)
        assert c.resume_from_iteration == 3


class TestExecutionContextWiring:
    def test_context_accepts_the_resume_position(self):
        from app.agents.block_executor import ExecutionContext
        ctx = ExecutionContext(run_id="r1", resume_from_iteration=2)
        assert ctx.resume_from_iteration == 2
        assert ctx.resume_iteration_artifacts == {}

    def test_context_defaults_leave_loops_untouched(self):
        # None means "start at 0" — every pre-existing resume and plain
        # launch must behave exactly as before.
        from app.agents.block_executor import ExecutionContext
        ctx = ExecutionContext(run_id="r1")
        assert ctx.resume_from_iteration is None
