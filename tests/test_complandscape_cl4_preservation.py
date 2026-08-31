"""A CL3 re-run must not discard CL4's settled Ziya determinations.

CL4 (reintegration) exists to answer one question per apparent gap: does
Ziya actually implement this under different vocabulary?  Measured on the
real corpus, it answered 112 of them -- 78 moved off zero (FOUND/PARTIAL,
so the ledger was wrong) and 34 were confirmed genuinely absent at tier A
by mechanism search, each carrying a citation and the note "confirmed
absent by mechanism search across code (not a terminology artifact)".

Those 34 zeros are FINDINGS.  ``_ziya_cells`` originally decided
placeholder-vs-finding from the capability's ``origin``::

    if cap["origin"] == "competitor-sourced" and not score:
        status = "unresolved"

which was right before CL4 ran (every such cell was then an unverified 0,
and forwarding it as "absent" would have laundered an assumption) and
wrong after.  ``not 0`` is true, so the rule reset precisely CL4's 34
confirmed-absent findings back to "unresolved".  The cost is not a bad
number in a file: an "unresolved" Ziya cell re-enters the gap queue, so
CL4 would re-audit 34 capabilities it had already settled and pay full
price to reach the same answer -- and nothing anywhere would report that
the work was repeated.

The fix keys on the EVIDENCE TIER rather than the origin: a 0 with a tier
is a determination, a 0 without one is a placeholder.  That rule is
correct both before and after CL4, and it removes the asymmetry whereby a
ledger-sourced 0 was believed while a competitor-sourced 0 was not.

Assertions run in both directions, because a fix that simply believed
every 0 would pass the preservation tests while re-introducing the
laundering the origin test was written to prevent:

  * a tier-bearing 0 survives as ``absent``          (preservation)
  * a tier-less 0 still degrades to ``unresolved``    (no laundering)
  * a real-corpus check that all 34 survive           (end-to-end)
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
matrix_mod = pytest.importorskip(
    "complandscape_matrix",
    reason="complandscape_matrix.py not present",
)

REAL_ROOT = os.path.join(
    os.path.dirname(__file__), "..", ".ziya", "complandscape",
)


def _space(caps):
    return {"schema_version": "2.0", "capabilities": caps,
            "tools": ["ziya", "cursor"], "domains": ["d"]}


def _legacy(tmp_path, cells):
    p = tmp_path / "30-matrix.json"
    p.write_text(json.dumps({"capabilities": [], "tools": [], "cells": cells}))
    return str(tmp_path)


def _by_id(rows):
    return {r["capability_id"]: r for r in rows}


class TestSettledAbsenceSurvives:
    """CL4's confirmed-absent findings must reach the new matrix intact."""

    def test_tier_bearing_zero_on_competitor_sourced_cap_stays_absent(self, tmp_path):
        # The exact shape CL4 writes for an ABSENT verdict.
        root = _legacy(tmp_path, [{
            "capability_id": "fast-apply-merge-model", "tool": "ziya",
            "score": 0, "evidence_tier": "A", "citation": "app/",
            "note": "CL4 reintegration (ABSENT): confirmed absent by "
                    "mechanism search across code (not a terminology artifact).",
        }])
        space = _space([{"id": "fast-apply-merge-model", "domain": "d",
                         "origin": "competitor-sourced"}])
        got = _by_id(matrix_mod._ziya_cells(root, space))["fast-apply-merge-model"]
        assert got["status"] == "absent", (
            "a tier-A CL4 ABSENT determination was reset to unresolved, which "
            "re-queues a capability CL4 already settled"
        )
        assert got["score"] == 0
        assert got["evidence_tier"] == "A", "the evidence must travel with it"

    def test_moved_off_zero_correction_survives(self, tmp_path):
        """The FOUND/PARTIAL half of CL4's work (78 cells)."""
        root = _legacy(tmp_path, [{
            "capability_id": "activity-timeline-memory", "tool": "ziya",
            "score": 3, "evidence_tier": "A", "citation": "app/x.py:1",
            "corrected": True,
        }])
        space = _space([{"id": "activity-timeline-memory", "domain": "d",
                         "origin": "competitor-sourced"}])
        got = _by_id(matrix_mod._ziya_cells(root, space))["activity-timeline-memory"]
        assert got["status"] == "present" and got["score"] == 3


class TestNoLaundering:
    """The guarantee the original origin test existed to provide."""

    def test_tierless_zero_stays_unresolved(self, tmp_path):
        # A 0 nobody stood behind: no evidence_tier.  Believing it would
        # assert Ziya lacks the capability on no evidence, which is the
        # error the origin test was written to prevent.
        root = _legacy(tmp_path, [{
            "capability_id": "some-gap", "tool": "ziya",
            "score": 0, "evidence_tier": None, "citation": None,
        }])
        space = _space([{"id": "some-gap", "domain": "d",
                         "origin": "competitor-sourced"}])
        got = _by_id(matrix_mod._ziya_cells(root, space))["some-gap"]
        assert got["status"] == "unresolved", (
            "an unevidenced 0 must not be forwarded as a finding"
        )
        assert got["score"] is None

    def test_string_unresolved_stays_unresolved(self, tmp_path):
        root = _legacy(tmp_path, [{
            "capability_id": "x", "tool": "ziya", "score": "unresolved",
            "evidence_tier": "A",
        }])
        space = _space([{"id": "x", "domain": "d", "origin": "ziya-ledger"}])
        got = _by_id(matrix_mod._ziya_cells(root, space))["x"]
        assert got["status"] == "unresolved" and got["score"] is None

    def test_missing_cell_is_unresolved_not_absent(self, tmp_path):
        root = _legacy(tmp_path, [])
        space = _space([{"id": "brand-new", "domain": "d",
                         "origin": "competitor-sourced"}])
        got = _by_id(matrix_mod._ziya_cells(root, space))["brand-new"]
        assert got["status"] == "unresolved"


class TestOriginNoLongerDecides:
    """Evidence decides, not provenance — the asymmetry is gone."""

    def test_ledger_and_competitor_sourced_zeros_are_treated_alike(self, tmp_path):
        root = _legacy(tmp_path, [
            {"capability_id": "a", "tool": "ziya", "score": 0,
             "evidence_tier": "A", "citation": "c"},
            {"capability_id": "b", "tool": "ziya", "score": 0,
             "evidence_tier": "A", "citation": "c"},
        ])
        space = _space([
            {"id": "a", "domain": "d", "origin": "ziya-ledger"},
            {"id": "b", "domain": "d", "origin": "competitor-sourced"},
        ])
        rows = _by_id(matrix_mod._ziya_cells(root, space))
        assert rows["a"]["status"] == rows["b"]["status"] == "absent", (
            "identical evidence must yield identical status regardless of "
            "which phase first named the capability"
        )


@pytest.mark.skipif(
    not os.path.exists(os.path.join(REAL_ROOT, "30-matrix.json")),
    reason="real corpus not present",
)
class TestAgainstTheRealCorpus:
    """End-to-end on the artifact CL4 actually produced.

    A unit test on synthetic cells can pass while the real corpus uses a
    shape the rule does not recognise, so the count is checked against the
    matrix's own record of what CL4 did.
    """

    def test_all_cl4_confirmed_absences_survive(self):
        m = json.load(open(os.path.join(REAL_ROOT, "30-matrix.json")))
        caps = {c["id"]: c for c in m["capabilities"]}
        z = {c["capability_id"]: c for c in m["cells"] if c["tool"] == "ziya"}
        settled = [
            cid for cid, c in caps.items()
            if c.get("origin") == "competitor-sourced"
            and z.get(cid, {}).get("score") == 0
            and z.get(cid, {}).get("evidence_tier")
        ]
        # Positive control: the corpus really does carry these, so the
        # assertion below cannot pass by finding nothing.
        expected = (m["coverage"].get("reintegration_CL4") or {}).get("stayed_zero")
        assert expected, "matrix does not record CL4's stayed_zero count"
        assert len(settled) == expected, (
            f"found {len(settled)} tier-bearing competitor-sourced zeros but "
            f"the matrix records {expected} CL4 confirmed-absent cells"
        )

        space = matrix_mod.load_space(REAL_ROOT)
        rows = _by_id(matrix_mod._ziya_cells(REAL_ROOT, space))
        lost = [cid for cid in settled if rows[cid]["status"] != "absent"]
        assert not lost, (
            f"{len(lost)} of CL4's {len(settled)} confirmed-absent findings "
            f"would be discarded by a CL3 re-run and re-queued for audit: "
            f"{lost[:6]}"
        )
