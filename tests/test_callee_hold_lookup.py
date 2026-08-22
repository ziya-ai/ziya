"""Reverse lookup: finding the run that holds a card called as a callee.

The motivating case: CL0 calls CL1..CL6, so ONE run exists and it belongs
to CL0.  Opening CL1 in the deck showed nothing — ``list(card_id="CL1")``
filters on ``run.card_id`` and CL1 owns no runs.  Yet CL1 is the card
actually holding the study.

The load-bearing fact these tests pin is that a callee's block ids in a
caller's ``call_snapshots`` are the callee's OWN ids (``_resolve_card``
returns ``card.root`` verbatim; ``_assign_block_ids`` is fill-only).  If
that ever stops being true, ``held_in_callee`` silently goes false
everywhere and the feature degrades to "a hold exists somewhere" — so it
is tested directly rather than assumed.
"""

import pytest

from app.utils.callee_hold_lookup import (
    callee_key, find_callee_holds, primary_callee_hold, INTERESTING_STATUSES,
)


class _Run:
    """Minimal stand-in exposing the TaskRun attributes the lookup reads."""

    def __init__(self, run_id, card_id, status="held", call_snapshots=None,
                 held_at_block_id=None, held_reason=None, held_faults=None,
                 held_gate_reason=None, updated_at=0):
        self.id = run_id
        self.card_id = card_id
        self.status = status
        self.call_snapshots = call_snapshots or {}
        self.held_at_block_id = held_at_block_id
        self.held_reason = held_reason
        self.held_faults = held_faults
        self.held_gate_reason = held_gate_reason
        self.updated_at = updated_at


def _cl1_tree():
    """CL1's own tree, with CL1's own persisted block ids."""
    return {
        "id": "cl1-root", "block_type": "group", "name": "CL1", "body": [
            {"id": "cl1-recon", "block_type": "task", "body": []},
            {"id": "cl1-fanout", "block_type": "repeat", "body": [
                {"id": "cl1-auditor", "block_type": "task", "body": []},
            ]},
            {"id": "cl1-merge", "block_type": "task", "body": []},
        ],
    }


def _cl2_tree():
    return {
        "id": "cl2-root", "block_type": "group", "body": [
            {"id": "cl2-roster", "block_type": "task", "body": []},
        ],
    }


def _cl0_run(held_block="cl1-fanout", status="held", updated_at=100):
    return _Run(
        "run-cl0", "card-CL0", status=status,
        call_snapshots={
            "cl0-call1": {"target": "CL1", "kind": "card",
                          "key": "card:card-CL1", "root": _cl1_tree()},
            "cl0-call2": {"target": "CL2", "kind": "card",
                          "key": "card:card-CL2", "root": _cl2_tree()},
        },
        held_at_block_id=held_block,
        held_reason="authentication_error",
        held_faults={"fault_count": 18, "fanout_width": 20,
                     "fleet_wide": True, "kinds": {"authentication_error": 18},
                     "call_path": ["CL0", "CL1"], "block_ids": ["cl1-auditor"],
                     "primary_kind": "authentication_error"},
        held_gate_reason="session-level fault",
        updated_at=updated_at,
    )


class TestKeyFormat:

    def test_matches_resolve_card(self):
        # task_call._resolve_card builds key=f"card:{card.id}"
        assert callee_key("card-CL1") == "card:card-CL1"


class TestFindingTheCallerRun:

    def test_finds_the_run_that_called_this_card(self):
        hits = find_callee_holds([_cl0_run()], "card-CL1")
        assert len(hits) == 1
        assert hits[0]["run_id"] == "run-cl0"
        assert hits[0]["caller_card_id"] == "card-CL0"
        assert hits[0]["call_block_id"] == "cl0-call1"

    def test_a_card_never_called_yields_nothing(self):
        assert find_callee_holds([_cl0_run()], "card-CL9") == []

    def test_ignores_runs_with_no_call_snapshots(self):
        plain = _Run("r2", "card-X", status="held")
        assert find_callee_holds([plain], "card-CL1") == []

    def test_survives_a_corrupt_snapshot_entry(self):
        bad = _Run("r3", "card-Y", status="held",
                   call_snapshots={"c": "not-a-dict"})
        assert find_callee_holds([bad], "card-CL1") == []

    def test_survives_call_snapshots_of_wrong_type(self):
        bad = _Run("r4", "card-Y", status="held", call_snapshots=["nope"])
        assert find_callee_holds([bad], "card-CL1") == []


class TestHeldInCallee:
    """The discriminator that stops a wrong-card report."""

    def test_hold_inside_this_callee_is_flagged(self):
        hit = find_callee_holds([_cl0_run("cl1-fanout")], "card-CL1")[0]
        assert hit["held_in_callee"] is True

    def test_nested_block_of_the_callee_counts(self):
        hit = find_callee_holds([_cl0_run("cl1-auditor")], "card-CL1")[0]
        assert hit["held_in_callee"] is True

    def test_hold_in_a_DIFFERENT_callee_is_not_claimed(self):
        """A hold in CL2 must not be reported as CL1's.

        Showing it would be worse than showing nothing: it points the user
        at a card that is fine.
        """
        hit = find_callee_holds([_cl0_run("cl2-roster")], "card-CL1")[0]
        assert hit["held_in_callee"] is False
        # Still returned as context — CL1 did participate in a held run.
        assert hit["held_at_block_id"] == "cl2-roster"

    def test_hold_in_the_CALLER_is_not_claimed(self):
        hit = find_callee_holds([_cl0_run("cl0-call2")], "card-CL1")[0]
        assert hit["held_in_callee"] is False

    def test_no_hold_at_all_is_not_claimed(self):
        run = _cl0_run(held_block=None, status="running")
        hit = find_callee_holds([run], "card-CL1")[0]
        assert hit["held_in_callee"] is False


class TestCalleeIdsAreTheCalleesOwn:
    """The fact the whole feature rests on.

    ``_resolve_card`` returns ``card.root`` verbatim and ``_assign_block_ids``
    is fill-only, so a caller's ``held_at_block_id`` is directly meaningful
    in the callee's own frame.  If ids were regenerated per invocation this
    would fail and the surface could only ever say "held somewhere".
    """

    def test_the_recorded_tree_carries_the_callees_own_ids(self):
        hit = find_callee_holds([_cl0_run()], "card-CL1")[0]
        root = hit["callee_root"]
        assert root["id"] == "cl1-root"
        names = {b["id"] for b in root["body"]}
        assert {"cl1-recon", "cl1-fanout", "cl1-merge"} <= names

    def test_the_recorded_tree_is_usable_for_hold_derivation(self):
        """held_at_block_id resolves against the callee's own tree.

        This is precisely what lets the deck run holdChain over CL1's own
        card and mark CL1's blocks, with no per-callee run record.
        """
        hit = find_callee_holds([_cl0_run("cl1-fanout")], "card-CL1")[0]

        def ids(node):
            out = [node["id"]]
            for c in node.get("body") or []:
                out += ids(c)
            return out

        assert hit["held_at_block_id"] in ids(hit["callee_root"])


class TestStatusFiltering:

    def test_a_done_run_is_not_surfaced_by_default(self):
        run = _cl0_run(status="done")
        assert find_callee_holds([run], "card-CL1") == []

    def test_running_is_surfaced_so_the_view_is_not_error_only(self):
        run = _cl0_run(status="running", held_block=None)
        assert len(find_callee_holds([run], "card-CL1")) == 1

    def test_paused_is_surfaced(self):
        run = _cl0_run(status="paused", held_block=None)
        assert len(find_callee_holds([run], "card-CL1")) == 1

    def test_interesting_statuses_excludes_terminal_success(self):
        assert "done" not in INTERESTING_STATUSES
        assert "failed" not in INTERESTING_STATUSES
        assert "held" in INTERESTING_STATUSES

    def test_explicit_statuses_override(self):
        run = _cl0_run(status="done")
        hits = find_callee_holds([run], "card-CL1", statuses=("done",))
        assert len(hits) == 1

    def test_empty_statuses_disables_filtering(self):
        run = _cl0_run(status="failed")
        hits = find_callee_holds([run], "card-CL1", statuses=())
        assert len(hits) == 1


class TestMultipleCallSites:

    def test_one_entry_per_call_site(self):
        """The same card called twice is two distinct invocations."""
        run = _Run(
            "r", "card-CL0", status="held",
            call_snapshots={
                "callA": {"key": "card:card-CL1", "root": _cl1_tree()},
                "callB": {"key": "card:card-CL1", "root": _cl1_tree()},
            },
            held_at_block_id="cl1-merge",
        )
        hits = find_callee_holds([run], "card-CL1")
        assert len(hits) == 2
        assert {h["call_block_id"] for h in hits} == {"callA", "callB"}


class TestOrderingAndPrimary:

    def test_held_sorts_ahead_of_running(self):
        held = _cl0_run(status="held", updated_at=50)
        running = _Run(
            "run-other", "card-CLX", status="running",
            call_snapshots={"c": {"key": "card:card-CL1",
                                  "root": _cl1_tree()}},
            updated_at=50,
        )
        hits = find_callee_holds([running, held], "card-CL1")
        assert hits[0]["run_status"] == "held"

    def test_primary_prefers_a_hold_inside_this_callee(self):
        elsewhere = _cl0_run(held_block="cl2-roster", updated_at=200)
        elsewhere.id = "run-elsewhere"
        mine = _cl0_run(held_block="cl1-fanout", updated_at=10)
        got = primary_callee_hold([elsewhere, mine], "card-CL1")
        assert got is not None
        # The older run wins because its hold is actually in THIS callee.
        assert got["held_in_callee"] is True
        assert got["held_at_block_id"] == "cl1-fanout"

    def test_primary_falls_back_to_context_when_none_are_ours(self):
        elsewhere = _cl0_run(held_block="cl2-roster")
        got = primary_callee_hold([elsewhere], "card-CL1")
        assert got is not None
        assert got["held_in_callee"] is False

    def test_primary_is_none_when_uninvolved(self):
        assert primary_callee_hold([_cl0_run()], "card-CL9") is None


class TestBreadthPassThrough:

    def test_faults_and_gate_reason_reach_the_caller(self):
        hit = find_callee_holds([_cl0_run()], "card-CL1")[0]
        assert hit["held_faults"]["fault_count"] == 18
        assert hit["held_faults"]["fleet_wide"] is True
        assert hit["held_reason"] == "authentication_error"
        assert hit["held_gate_reason"] == "session-level fault"
