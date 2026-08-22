"""Resume must reach a block INSIDE a called card, not stop at the Call.

The reported failure: a six-phase study whose Phase 1 was a ``Call`` to
another card held on iteration 19 of a 20-wide fan-out inside that callee.
The hold surface offered "↻ Retry b-cf96c4e2", and clicking it returned
``404 — Block b-cf96c4e2 not found in this run's card snapshot``.

Two distinct defects produced that, and this file covers the resolution
half of both (the executor half is in
``test_resume_call_descent_execution.py``):

1. **The callee's ids are in no tree the resolver searched.**  A Call runs
   its target inline, so the callee's block tree exists only in
   ``run.call_snapshots`` — keyed by the Call block's own id, carrying the
   callee card's OWN block ids (``task_call._resolve_card`` returns the
   stored card's root verbatim).  ``held_at_block_id`` therefore names a
   block that ``find_resume_target`` cannot see, and the request 404s.

2. **Substituting the Call block is not a fix.**  An earlier attempt
   resolved a callee block id UP to the enclosing Call and resumed there.
   That returns 200 and re-enters the callee from its own start, re-running
   every banked iteration — 14 hours of completed work on the reported
   study, discarded by a control labelled "resume".  So the tests here
   assert the resume point is the CALLEE block itself, plus a descent chain
   telling the executor which Calls to walk through.

The id-space disjointness these tests rely on is a real invariant, not an
assumption: a Call block's id is a KEY of ``call_snapshots`` and never a
node inside one, so membership in a recorded callee tree is decisive.
"""

import pytest

from app.utils.resume_targets import (
    enclosing_call,
    enclosing_call_block,
    locate_block,
    parallel_replay_indices,
    resolve_iteration_resume,
    resolve_resume_point,
    resume_call_chain,
)


# ---------------------------------------------------------------- fixtures

def _task(bid: str, name: str = "t") -> dict:
    return {"id": bid, "block_type": "task", "name": name,
            "instructions": "go", "body": []}


def _call(bid: str, name: str, target: str) -> dict:
    """A Call block as the caller's snapshot stores it — EMPTY body.

    The empty body is the whole point: it is why ``_subtree_contains``
    cannot find a callee target and why a descent hint is required.
    """
    return {"id": bid, "block_type": "call", "name": name,
            "call_target": target, "call_target_kind": "card", "body": []}


#: Caller deck, mirroring the reported card: a State block then six Calls.
CALLER = {
    "id": "root", "block_type": "group", "name": "CL0",
    "body": [
        {"id": "b-params", "block_type": "state", "name": "Study parameters",
         "state_context": "given", "body": []},
        _call("call-p1", "Phase 1 — Ziya ground truth", "CL1"),
        _call("call-p2", "Phase 2 — competitive field survey", "CL2"),
    ],
}

#: CL1's own tree, as ``call_snapshots['call-p1']['root']`` records it.
CALLEE_P1 = {
    "id": "b-cl1-root", "block_type": "group", "name": "CL1",
    "body": [
        _task("b-recon", "Stage 1: Recon"),
        {"id": "b-cf96c4e2", "block_type": "repeat",
         "name": "Stage 2: Parallel subsystem auditors",
         "repeat_mode": "count", "repeat_count": 20,
         "repeat_parallel": True,
         "body": [_task("b-auditor", "Audit subsystem {{item}}")]},
        _task("b-merge", "Stage 3: Merge into the ledger"),
    ],
}

SNAPS = {
    "call-p1": {"key": "card:cl1", "target": "CL1", "root": CALLEE_P1},
    "call-p2": {"key": "card:cl2", "target": "CL2",
                "root": {"id": "b-cl2-root", "block_type": "group",
                         "name": "CL2", "body": [_task("b-survey")]}},
}


# --------------------------------------------------------------- locating

class TestLocateBlock:
    def test_callers_own_block_has_no_chain(self):
        tree, chain = locate_block(CALLER, SNAPS, "call-p1")
        assert tree is CALLER
        assert chain == []

    def test_callee_block_resolves_to_the_callee_tree(self):
        tree, chain = locate_block(CALLER, SNAPS, "b-cf96c4e2")
        assert tree is CALLEE_P1
        assert chain == ["call-p1"], (
            "the executor must be told to descend through call-p1; without "
            "the chain a Call is replayed whole and the target is "
            "unreachable"
        )

    def test_unknown_id_is_located_nowhere(self):
        assert locate_block(CALLER, SNAPS, "ghost") == (None, [])

    def test_no_snapshots_means_callee_ids_are_invisible(self):
        """Reproduces the 404: this is the pre-fix information state."""
        assert locate_block(CALLER, None, "b-cf96c4e2") == (None, [])

    def test_nested_calls_yield_an_outermost_first_chain(self):
        inner = {"id": "b-inner-root", "block_type": "group", "name": "CL1a",
                 "body": [_task("b-deep")]}
        mid = {"id": "b-mid-root", "block_type": "group", "name": "CL1",
               "body": [_call("call-inner", "sub", "CL1a")]}
        snaps = {
            "call-p1": {"key": "card:cl1", "target": "CL1", "root": mid},
            "call-inner": {"key": "card:cl1a", "target": "CL1a",
                           "root": inner},
        }
        tree, chain = locate_block(CALLER, snaps, "b-deep")
        assert tree is inner
        assert chain == ["call-p1", "call-inner"], (
            "descent order must be outermost-first: the gate meets call-p1 "
            "before call-inner exists in any tree it can see"
        )

    def test_a_cycle_in_a_corrupt_record_terminates(self):
        a = {"id": "a-root", "block_type": "group", "body": [_call("ca", "a", "B")]}
        b = {"id": "b-root", "block_type": "group", "body": [_call("cb", "b", "A")]}
        snaps = {"ca": {"root": b}, "cb": {"root": a}}
        assert locate_block(CALLER, snaps, "ca") == (None, [])


class TestEnclosingCall:
    def test_returns_id_and_root_together(self):
        got = enclosing_call(SNAPS, "b-cf96c4e2")
        assert got is not None
        assert got[0] == "call-p1"
        assert got[1] is CALLEE_P1

    def test_wrapper_still_returns_just_the_id(self):
        assert enclosing_call_block(SNAPS, "b-recon") == "call-p1"
        assert enclosing_call_block(SNAPS, "call-p1") is None


# ------------------------------------------------------- resolve / resume

class TestResolveResumePointThroughCall:
    def test_the_reported_404_is_gone(self):
        point, target, err = resolve_resume_point(
            CALLER, "b-cf96c4e2", "retry", call_snapshots=SNAPS,
        )
        assert err is None
        assert point == "b-cf96c4e2"
        assert target == "b-cf96c4e2"

    def test_resume_point_is_not_substituted_up_to_the_call(self):
        """The regression guard for the expensive wrong fix."""
        point, _t, _e = resolve_resume_point(
            CALLER, "b-cf96c4e2", "retry", call_snapshots=SNAPS,
        )
        assert point != "call-p1", (
            "resuming at the Call re-enters the callee from its own start "
            "and re-runs every banked iteration"
        )

    def test_the_chain_accompanies_the_point(self):
        assert resume_call_chain(CALLER, SNAPS, "b-cf96c4e2") == ["call-p1"]

    def test_a_task_inside_the_callee_loop_normalizes_to_the_loop(self):
        """Loop normalization still applies, but WITHIN the callee tree."""
        point, target, err = resolve_resume_point(
            CALLER, "b-auditor", "retry", call_snapshots=SNAPS,
        )
        assert err is None
        assert point == "b-cf96c4e2"
        assert target == "b-cf96c4e2"

    def test_continue_stays_inside_the_callee(self):
        point, target, err = resolve_resume_point(
            CALLER, "b-cf96c4e2", "continue", call_snapshots=SNAPS,
        )
        assert err is None
        assert target == "b-cf96c4e2"
        assert point == "b-merge", (
            "continuing past the fan-out means the callee's next stage, "
            "not the caller's next phase"
        )

    def test_continue_past_the_callees_last_block_walks_out(self):
        point, target, err = resolve_resume_point(
            CALLER, "b-merge", "continue", call_snapshots=SNAPS,
        )
        assert err is None
        assert target == "b-merge"
        assert point == "call-p2", (
            "nothing follows b-merge inside CL1, so continue must resume at "
            "the caller's next phase rather than refusing"
        )

    def test_without_snapshots_it_still_404s_honestly(self):
        point, target, err = resolve_resume_point(
            CALLER, "b-cf96c4e2", "retry", call_snapshots={},
        )
        assert point is None and target is None
        assert err is not None and "not found" in err

    def test_callers_own_blocks_are_unaffected(self):
        point, target, err = resolve_resume_point(
            CALLER, "call-p1", "retry", call_snapshots=SNAPS,
        )
        assert (point, target, err) == ("call-p1", "call-p1", None)


# -------------------------------------------------- parallel banked set

class TestParallelReplayIndices:
    """The 19-of-20 case: which iterations a retry may skip."""

    @staticmethod
    def _summaries(passed: int, failed_at: int, width: int = 20):
        out = []
        for i in range(width):
            if i == failed_at:
                out.append({"index": i, "status": "failed",
                            "has_artifact": True})
            elif i < passed or (i > failed_at and i < width):
                out.append({"index": i, "status": "passed",
                            "has_artifact": True})
        return out

    def test_nineteen_of_twenty_are_banked(self):
        sums = [
            {"index": i, "status": "failed" if i == 19 else "passed",
             "has_artifact": True}
            for i in range(20)
        ]
        got = parallel_replay_indices(CALLER, "b-cf96c4e2", sums, SNAPS)
        assert got == list(range(19)), (
            "the 19 completed subagents must be replayed; re-running them "
            "is the 14-hour regression this exists to prevent"
        )

    def test_the_failed_iteration_is_not_banked(self):
        sums = [{"index": 0, "status": "failed", "has_artifact": True}]
        assert parallel_replay_indices(CALLER, "b-cf96c4e2", sums, SNAPS) == []

    def test_an_unretained_pass_is_not_banked(self):
        """Beyond the 50-pass cap there is no artifact to replay."""
        sums = [{"index": 0, "status": "passed", "has_artifact": False},
                {"index": 1, "status": "passed", "has_artifact": True}]
        assert parallel_replay_indices(CALLER, "b-cf96c4e2", sums, SNAPS) == [1]

    def test_not_applicable_to_a_serial_loop(self):
        serial = {"id": "root", "block_type": "group", "body": [
            {"id": "loop", "block_type": "repeat", "repeat_mode": "count",
             "repeat_count": 3, "repeat_parallel": False,
             "body": [_task("t")]}]}
        sums = [{"index": i, "status": "passed", "has_artifact": True}
                for i in range(3)]
        assert parallel_replay_indices(serial, "loop", sums, None) is None, (
            "serial loops resume by PREFIX; returning a set here would "
            "let a later iteration run without its predecessor"
        )

    def test_not_applicable_to_a_task(self):
        assert parallel_replay_indices(CALLER, "b-recon", [], SNAPS) is None

    def test_unknown_block_is_not_applicable(self):
        assert parallel_replay_indices(CALLER, "ghost", [], SNAPS) is None


# ----------------------------------------------- mid-loop through a call

class TestIterationResumeThroughCall:
    def test_a_serial_callee_loop_is_now_resumable(self):
        callee = {
            "id": "b-cl1-root", "block_type": "group", "body": [
                {"id": "b-serial", "block_type": "repeat",
                 "repeat_mode": "count", "repeat_count": 5,
                 "repeat_parallel": False, "body": [_task("t")]}]}
        snaps = {"call-p1": {"key": "card:cl1", "target": "CL1",
                             "root": callee}}
        sums = [{"index": i, "status": "passed", "has_artifact": True}
                for i in range(5)]
        start, err = resolve_iteration_resume(
            CALLER, "b-serial", 3, sums, "retry_iteration",
            call_snapshots=snaps,
        )
        assert err is None, (
            "a loop inside a called card is reachable now that the gate "
            "descends through the Call"
        )
        assert start == 3

    def test_a_parallel_callee_loop_points_at_the_cheap_remedy(self):
        sums = [{"index": i, "status": "passed", "has_artifact": True}
                for i in range(20)]
        start, err = resolve_iteration_resume(
            CALLER, "b-cf96c4e2", 19, sums, "retry_iteration",
            call_snapshots=SNAPS,
        )
        assert start is None
        assert err is not None
        # Still refused (an index has no meaning without ordering), but the
        # named alternative must no longer imply re-running everything.
        assert "replayed from record" in err
        assert "re-entered from its own start" not in err
