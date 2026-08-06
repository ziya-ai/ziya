"""
Tests for app.utils.resume_targets — resolving a user's click into the
block a resumed run actually starts at.

Central asymmetry under test: "retry from X" resumes AT X, while
"continue from X" resumes at X's SUCCESSOR — which is what makes X
itself replay from record instead of re-executing.  Getting the
successor wrong is not a cosmetic bug: pointing at a container's first
child instead of the block after the container would silently re-run
work the user explicitly chose to accept.
"""

import pytest

from app.utils.resume_targets import (
    find_resume_target,
    next_execution_target,
    resolve_resume_point,
    snapshot_contains,
)


def blk(bid, btype="task", body=None):
    return {"id": bid, "block_type": btype, "body": body or []}


# A 4-stage group: the common shape produced by a multi-stage card.
FLAT = blk("root", "group", [
    blk("s1"), blk("s2"), blk("s3"), blk("s4"),
])

# A loop in the middle, with two children inside its body.  Those
# children have no durable per-block state and must never be resume
# points.
WITH_LOOP = blk("root", "group", [
    blk("s1"),
    blk("loop", "repeat", [blk("inner1"), blk("inner2")]),
    blk("s3"),
])

# until nested inside repeat — the case that distinguishes "outermost"
# from "innermost" normalization.
NESTED_LOOPS = blk("root", "group", [
    blk("s1"),
    blk("outer", "repeat", [
        blk("mid", "until", [blk("deep")]),
    ]),
    blk("s3"),
])

# A nested group: continuing past the group must land on s9, not on the
# group's first child.
NESTED_GROUP = blk("root", "group", [
    blk("g", "group", [blk("g1"), blk("g2"), blk("g3")]),
    blk("s9"),
])


class TestSnapshotContains:
    def test_finds_self_and_descendants(self):
        assert snapshot_contains(WITH_LOOP, "root") is True
        assert snapshot_contains(WITH_LOOP, "inner2") is True
        assert snapshot_contains(WITH_LOOP, "nope") is False


class TestFindResumeTarget:
    def test_flat_block_resolves_to_itself(self):
        assert find_resume_target(FLAT, "s3") == "s3"

    def test_unknown_id_is_none(self):
        assert find_resume_target(FLAT, "ghost") is None

    def test_loop_body_child_normalizes_to_the_loop(self):
        # Only repeat/until push a binding frame, so a block inside a
        # loop body has no persisted state and cannot be re-entered.
        assert find_resume_target(WITH_LOOP, "inner1") == "loop"
        assert find_resume_target(WITH_LOOP, "inner2") == "loop"

    def test_loop_itself_resolves_to_itself(self):
        assert find_resume_target(WITH_LOOP, "loop") == "loop"

    def test_nested_loops_normalize_to_outermost(self):
        # The inner until is ITSELF inside the repeat's body, so it is
        # equally unpersisted — deferring to it would produce a target
        # the executor cannot resume at.
        assert find_resume_target(NESTED_LOOPS, "deep") == "outer"
        assert find_resume_target(NESTED_LOOPS, "mid") == "outer"

    def test_group_children_resolve_to_themselves(self):
        # group/parallel do not push bindings, so their children keep
        # their own durable state.
        assert find_resume_target(NESTED_GROUP, "g2") == "g2"


class TestNextExecutionTarget:
    def test_next_sibling_in_a_flat_sequence(self):
        assert next_execution_target(FLAT, "s2") == "s3"

    def test_last_block_has_no_successor(self):
        assert next_execution_target(FLAT, "s4") is None

    def test_skips_the_whole_subtree_of_a_container(self):
        # The user continuing past a group means "the block after the
        # group", not "the group's first child".
        assert next_execution_target(NESTED_GROUP, "g") == "s9"

    def test_skips_a_loops_body(self):
        assert next_execution_target(WITH_LOOP, "loop") == "s3"

    def test_successor_of_block_before_a_loop_is_the_loop(self):
        assert next_execution_target(WITH_LOOP, "s1") == "loop"

    def test_never_returns_a_block_inside_a_loop(self):
        # Continuing past s1 must yield the loop container, never
        # inner1 — an unresumable target would make the executor
        # replay everything and execute nothing.
        assert next_execution_target(WITH_LOOP, "s1") != "inner1"

    def test_unknown_id_is_none(self):
        assert next_execution_target(FLAT, "ghost") is None


class TestResolveResumePoint:
    def test_retry_points_at_the_block_itself(self):
        point, target, err = resolve_resume_point(FLAT, "s3", "retry")
        assert err is None
        assert point == "s3"
        assert target == "s3"

    def test_continue_points_at_the_successor(self):
        point, target, err = resolve_resume_point(FLAT, "s2", "continue")
        assert err is None
        assert point == "s3", "resume point must be the successor"
        # The USER pointed at s2; recording the successor here would make
        # the UI say "continued from Stage 3", which is not what happened.
        assert target == "s2", "recorded target must be what the user clicked"

    def test_continue_past_last_block_is_an_error_not_a_no_op(self):
        # Silently launching a run that replays everything and executes
        # nothing would look like a successful resume that did nothing.
        point, target, err = resolve_resume_point(FLAT, "s4", "continue")
        assert point is None
        assert target == "s4"
        assert err is not None and "nothing to continue" in err.lower()

    def test_continue_from_a_loop_skips_its_body(self):
        point, _, err = resolve_resume_point(WITH_LOOP, "loop", "continue")
        assert err is None
        assert point == "s3"

    def test_continue_from_inside_a_loop_normalizes_then_advances(self):
        # Two transformations compose: inner2 → loop (normalization),
        # then loop → s3 (successor).  The recorded target stays the
        # normalized loop, since that is the resumable unit the user
        # effectively pointed at.
        point, target, err = resolve_resume_point(WITH_LOOP, "inner2", "continue")
        assert err is None
        assert point == "s3"
        assert target == "loop"

    def test_unknown_block_is_an_error(self):
        point, target, err = resolve_resume_point(FLAT, "ghost", "retry")
        assert point is None and target is None
        assert err is not None and "not found" in err.lower()

    def test_unknown_mode_is_rejected(self):
        point, target, err = resolve_resume_point(FLAT, "s1", "sideways")
        assert point is None
        assert err is not None and "unknown resume mode" in err.lower()

    def test_retry_and_continue_differ_exactly_by_one_step(self):
        # The property that makes one mechanism serve both acts.
        r_point, _, _ = resolve_resume_point(FLAT, "s2", "retry")
        c_point, _, _ = resolve_resume_point(FLAT, "s2", "continue")
        assert r_point == "s2"
        assert c_point == next_execution_target(FLAT, "s2")
        assert r_point != c_point
