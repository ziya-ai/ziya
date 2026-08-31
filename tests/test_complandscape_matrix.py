"""Full-grid contract for the competitive-landscape capability matrix.

The defect these tests exist for, measured on the shipped v1 matrix:

    capability's vendor_aliases NAMES the tool  -> cell exists  380/404 = 94%
    vendor_aliases does NOT name the tool       -> cell exists 1681/12726 = 13%

CL3 transcribed what 26 dossier authors volunteered instead of interrogating
a grid, so 11069 of 13130 (capability, tool) pairs were never assessed and a
missing cell was indistinguishable from a verified absence.  Downstream, 117
of the 205 "only Ziya has this" claims rested on ZERO competitor assessment.

Nothing about that was detectable: every v1 artifact was well-formed JSON and
its self-reported coverage block was accurate about what it contained.  The
assertions below are therefore about COMPLETENESS and STATUS EXPLICITNESS
rather than schema shape:

  * grid completeness is an error, not a warning (TestGridCompleteness)
  * every non-scored status must be self-justifying, so "unresolvable" and
    "unexamined" cannot collapse into one another (TestStatusDiscipline)
  * capability ids frozen by the CL5 dimension registry cannot be renamed
    (TestSpacePreservation)
  * coverage names WHOSE zero-cells it counts (TestCoverageDisaggregation) --
    v1's ``zero_cells: 507`` was correct but unnamed, and was misread as a
    competitor-only figure during the analysis that produced this module

Each block carries a positive control so a broken helper cannot make the
suite vacuous.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

matrix_mod = pytest.importorskip(
    "complandscape_matrix",
    reason="scripts/complandscape_matrix.py not present",
)

TODAY = date.today().isoformat()
REAL_ROOT = os.path.join(os.path.dirname(__file__), "..", ".ziya", "complandscape")


# --------------------------------------------------------------------------
# fixtures: a tiny but complete world
# --------------------------------------------------------------------------

def _space(n_caps: int = 4, tools=("alpha", "beta")) -> dict:
    return {
        "generated": "2026-01-01",
        "domains": ["d1", "d2"],
        "capabilities": [
            {"id": f"cap-{i}", "name": f"C{i}", "domain": "d1" if i % 2 else "d2",
             "origin": "ziya-ledger", "functional_description": "x",
             "vendor_aliases": []}
            for i in range(n_caps)
        ],
        "tools": ["ziya", *tools],
        "cells": [
            {"capability_id": f"cap-{i}", "tool": "ziya", "score": 3,
             "evidence_tier": "A", "citation": "a.py:1", "note": ""}
            for i in range(n_caps)
        ],
        "coverage": {},
    }


def _write_space(root: str, space: dict) -> None:
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "30-matrix.json"), "w") as f:
        json.dump(space, f)


def _cell(cap: str, status: str = "present", **over) -> dict:
    base = {"capability_id": cap, "status": status, "as_of": TODAY}
    if status == "present":
        base.update(score=4, evidence_tier="C", citation="docs/x", note="n")
    elif status == "absent":
        base.update(score=0, evidence_tier="C", citation="docs/x", note="n")
    else:
        base.update(score=None)
        if status == "unknown":
            base["what_would_resolve"] = "hands-on trial"
        if status == "not_applicable":
            base["rationale"] = "CLI-only; no browser surface"
    base.update(over)
    return base


def _write_slices(cells_dir: str, space: dict, *, omit=()) -> None:
    """Write one complete slice file per (tool, domain), minus `omit` pairs."""
    os.makedirs(cells_dir, exist_ok=True)
    tools = [t for t in space["tools"] if t != "ziya"]
    by_domain: dict = {}
    for c in space["capabilities"]:
        by_domain.setdefault(c["domain"], []).append(c["id"])
    for tool in tools:
        for domain, caps in by_domain.items():
            rows = [_cell(c) for c in caps if (c, tool) not in omit]
            with open(os.path.join(cells_dir, f"{tool}__{domain}.json"), "w") as f:
                json.dump({"schema_version": "2.0", "tool": tool,
                           "domain": domain, "generated_at": TODAY,
                           "cells": rows}, f)


@pytest.fixture
def world(tmp_path):
    root = str(tmp_path / "cl")
    cells = str(tmp_path / "cells")
    space = _space()
    _write_space(root, space)
    return {"root": root, "cells": cells, "space": space}


# --------------------------------------------------------------------------
# the defect
# --------------------------------------------------------------------------

class TestGridCompleteness:
    def test_complete_grid_validates(self, world):
        """Positive control: without this every assertion below is vacuous."""
        _write_slices(world["cells"], world["space"])
        res = matrix_mod.validate_cells(world["root"], world["cells"])
        assert res["ok"], res["errors"]
        assert res["stats"]["missing_pairs"] == 0
        assert res["stats"]["completeness_pct"] == 100.0
        assert res["stats"]["cells"] == 4 * 2

    def test_a_single_missing_pair_is_an_error(self, world):
        _write_slices(world["cells"], world["space"], omit={("cap-0", "alpha")})
        res = matrix_mod.validate_cells(world["root"], world["cells"])
        assert not res["ok"]
        assert any("GRID INCOMPLETE" in e for e in res["errors"])
        assert res["stats"]["missing_pairs"] == 1

    def test_transcription_shaped_sparsity_is_rejected(self, tmp_path):
        """The actual v1 failure: cells only where a dossier named the tool.

        Reproduces the shape rather than the scale -- one tool volunteered for
        one capability, everything else silent. v1 called this a finished
        matrix and reported accurate coverage for what it held.
        """
        root = str(tmp_path / "cl")
        cells = str(tmp_path / "cells")
        space = _space(n_caps=6, tools=("alpha", "beta", "gamma"))
        _write_space(root, space)
        os.makedirs(cells, exist_ok=True)
        with open(os.path.join(cells, "alpha__d1.json"), "w") as f:
            json.dump({"tool": "alpha", "domain": "d1",
                       "cells": [_cell("cap-1")]}, f)
        res = matrix_mod.validate_cells(root, cells)
        assert not res["ok"], "a dossier transcription must not pass as a grid"
        assert res["stats"]["missing_pairs"] == 6 * 3 - 1
        assert res["stats"]["completeness_pct"] < 10

    def test_incompleteness_names_the_worst_tools(self, world):
        """The error must be actionable: which tool needs re-dispatch."""
        _write_slices(world["cells"], world["space"],
                      omit={(f"cap-{i}", "beta") for i in range(4)})
        res = matrix_mod.validate_cells(world["root"], world["cells"])
        err = next(e for e in res["errors"] if "GRID INCOMPLETE" in e)
        assert "beta" in err

    def test_check_matrix_rejects_an_incomplete_merge(self, world):
        _write_slices(world["cells"], world["space"], omit={("cap-2", "beta")})
        m = matrix_mod.merge(world["root"], world["cells"])
        problems = matrix_mod.check_matrix(m)
        assert any("grid incomplete" in p for p in problems)

    def test_the_real_v1_matrix_fails_the_full_grid_check(self):
        """Anchor on the shipped artifact, not a reconstruction.

        If this ever passes, either v1 was backfilled (good) or the check
        stopped checking (bad) -- both need a human to look.
        """
        path = os.path.join(REAL_ROOT, "30-matrix.json")
        if not os.path.exists(path):
            pytest.skip("v1 matrix not present in this checkout")
        m = matrix_mod._read_json(path)
        problems = matrix_mod.check_matrix(m)
        assert problems, "the v1 matrix is 84% unassessed and must not validate"
        assert any("grid incomplete" in p for p in problems)


# --------------------------------------------------------------------------
# status discipline
# --------------------------------------------------------------------------

class TestStatusDiscipline:
    def _one(self, world, cell):
        os.makedirs(world["cells"], exist_ok=True)
        _write_slices(world["cells"], world["space"])
        p = os.path.join(world["cells"], "alpha__d2.json")
        data = json.load(open(p))
        data["cells"] = [c for c in data["cells"]
                         if c["capability_id"] != cell["capability_id"]] + [cell]
        json.dump(data, open(p, "w"))
        return matrix_mod.validate_cells(world["root"], world["cells"])

    def test_every_documented_status_is_accepted(self, world):
        """Positive control over the whole vocabulary."""
        for status in ("present", "absent", "not_applicable", "unknown"):
            res = self._one(world, _cell("cap-0", status))
            assert res["ok"], (status, res["errors"])

    def test_unknown_without_a_resolution_path_is_refused(self, world):
        res = self._one(world, _cell("cap-0", "unknown", what_would_resolve=""))
        assert not res["ok"]
        assert any("what_would_resolve" in e for e in res["errors"])

    def test_not_applicable_without_rationale_is_refused(self, world):
        res = self._one(world, _cell("cap-0", "not_applicable", rationale=""))
        assert not res["ok"]
        assert any("rationale" in e for e in res["errors"])

    def test_unscored_status_may_not_carry_a_score(self, world):
        """A score on an unknown cell reads as a measurement it is not."""
        res = self._one(world, _cell("cap-0", "unknown", score=0))
        assert not res["ok"]
        assert any("score null" in e for e in res["errors"])

    def test_present_requires_a_score_in_range(self, world):
        assert not self._one(world, _cell("cap-0", "present", score=0))["ok"]
        assert not self._one(world, _cell("cap-0", "present", score=6))["ok"]

    def test_absent_requires_score_zero(self, world):
        assert not self._one(world, _cell("cap-0", "absent", score=2))["ok"]

    def test_scored_cell_requires_tier_and_citation(self, world):
        assert not self._one(world, _cell("cap-0", evidence_tier=None))["ok"]
        assert not self._one(world, _cell("cap-0", citation=""))["ok"]

    def test_as_of_is_required_on_every_cell(self, world):
        res = self._one(world, _cell("cap-0", as_of=""))
        assert not res["ok"]
        assert any("as_of" in e for e in res["errors"])

    def test_unknown_capability_id_is_refused(self, world):
        res = self._one(world, _cell("cap-does-not-exist"))
        assert not res["ok"]
        assert any("not in the capability space" in e for e in res["errors"])

    def test_off_roster_tool_is_refused(self, world):
        _write_slices(world["cells"], world["space"])
        p = os.path.join(world["cells"], "rogue__d1.json")
        json.dump({"tool": "not-a-real-tool", "domain": "d1",
                   "cells": [_cell("cap-1")]}, open(p, "w"))
        res = matrix_mod.validate_cells(world["root"], world["cells"])
        assert any("not in the roster" in e for e in res["errors"])


class TestDuplicateCells:
    def test_two_determinations_for_one_pair_is_an_error(self, world):
        _write_slices(world["cells"], world["space"])
        p = os.path.join(world["cells"], "alpha__extra.json")
        json.dump({"tool": "alpha", "domain": "d1",
                   "cells": [_cell("cap-1", score=2)]}, open(p, "w"))
        res = matrix_mod.validate_cells(world["root"], world["cells"])
        assert not res["ok"]
        assert any("duplicate cell" in e for e in res["errors"])

    def test_no_duplicate_in_the_clean_case(self, world):
        """Positive control: the detector is not firing on everything."""
        _write_slices(world["cells"], world["space"])
        res = matrix_mod.validate_cells(world["root"], world["cells"])
        assert not any("duplicate" in e for e in res["errors"])


# --------------------------------------------------------------------------
# id preservation (the frozen registry depends on it)
# --------------------------------------------------------------------------

class TestSpacePreservation:
    def _space_file(self, root, ids):
        with open(os.path.join(root, "29-capability-space.json"), "w") as f:
            json.dump({"space_version": "2", "domains": ["d1"], "tools": ["ziya", "alpha"],
                       "capabilities": [{"id": i, "name": i, "domain": "d1",
                                         "origin": "ziya-ledger",
                                         "functional_description": "",
                                         "vendor_aliases": []} for i in ids]}, f)

    def test_identical_ids_are_accepted(self, world):
        """Positive control."""
        self._space_file(world["root"], [f"cap-{i}" for i in range(4)])
        assert matrix_mod.check_space(world["root"]) == []

    def test_added_capability_is_allowed(self, world):
        self._space_file(world["root"], [f"cap-{i}" for i in range(4)] + ["cap-new"])
        assert matrix_mod.check_space(world["root"]) == []

    def test_dropped_id_is_refused(self, world):
        self._space_file(world["root"], [f"cap-{i}" for i in range(3)])
        problems = matrix_mod.check_space(world["root"])
        assert any("cap-3" in p for p in problems)

    def test_renamed_id_is_refused(self, world):
        """A rename is a drop plus an add, and orphans the frozen schema."""
        self._space_file(world["root"],
                         ["cap-0", "cap-1", "cap-2", "cap-3-renamed"])
        problems = matrix_mod.check_space(world["root"])
        assert any("cap-3" in p for p in problems)

    def test_registry_frozen_id_is_protected(self, world):
        with open(os.path.join(world["root"], "25-dimension-registry.json"), "w") as f:
            json.dump({"registry_version": "1.0.0",
                       "capabilities": {"cap-1": {}, "cap-9": {}}}, f)
        self._space_file(world["root"], [f"cap-{i}" for i in range(4)])
        problems = matrix_mod.check_space(world["root"])
        assert any("cap-9" in p and "frozen" in p for p in problems)


# --------------------------------------------------------------------------
# coverage honesty
# --------------------------------------------------------------------------

class TestCoverageDisaggregation:
    def test_zero_cells_are_reported_per_owner(self, world):
        """v1 reported one unnamed 'zero_cells: 507' and it was misread.

        507 was 471 competitor + 36 Ziya. The number was right; the name did
        not say whose, so a competitor-filtered recount looked like a 36-cell
        discrepancy in the field a reader uses to size the gap.
        """
        _write_slices(world["cells"], world["space"])
        p = os.path.join(world["cells"], "alpha__d1.json")
        d = json.load(open(p))
        d["cells"] = [_cell(c["capability_id"], "absent") for c in d["cells"]]
        json.dump(d, open(p, "w"))
        cov = matrix_mod.merge(world["root"], world["cells"])["coverage"]
        assert cov["zero_cells_competitor"] == 2
        assert cov["zero_cells_ziya"] == 0
        assert cov["zero_cells_all"] == 2
        assert "zero_cells" not in cov, "the ambiguous name must not return"

    def test_grid_block_states_completeness(self, world):
        _write_slices(world["cells"], world["space"])
        g = matrix_mod.merge(world["root"], world["cells"])["coverage"]["grid"]
        assert g["expected_competitor_cells"] == 8
        assert g["actual_competitor_cells"] == 8
        assert g["never_assessed"] == 0
        assert g["completeness_pct"] == 100.0

    def test_status_totals_partition_the_grid(self, world):
        _write_slices(world["cells"], world["space"])
        cov = matrix_mod.merge(world["root"], world["cells"])["coverage"]
        assert sum(cov["competitor_status_totals"].values()) == \
            cov["grid"]["actual_competitor_cells"]

    def test_contender_count_uses_the_threshold(self, world):
        _write_slices(world["cells"], world["space"])
        p = os.path.join(world["cells"], "beta__d1.json")
        d = json.load(open(p))
        d["cells"] = [_cell(c["capability_id"], "present", score=1)
                      for c in d["cells"]]
        json.dump(d, open(p, "w"))
        cov = matrix_mod.merge(world["root"], world["cells"])["coverage"]
        # score-1 cells are present but below the contender threshold
        assert cov["contender_cells"] == 6


class TestZiyaCellCarryForward:
    # Placeholder-vs-finding is decided by the EVIDENCE TIER, not by the
    # capability's origin.  The origin form of this rule was correct only
    # until CL4 ran: it treated every competitor-sourced 0 as unverified,
    # which after CL4 discarded 34 tier-A confirmed-absent findings and
    # re-queued them for audit.  See
    # tests/test_complandscape_cl4_preservation.py for the measured impact.
    def test_unevidenced_zero_stays_unresolved(self, world):
        """Writing 0 for Ziya here would launder an unverified assumption."""
        space = world["space"]
        space["capabilities"][0]["origin"] = "competitor-sourced"
        space["cells"][0]["score"] = 0
        space["cells"][0]["evidence_tier"] = None      # nobody stands behind it
        _write_space(world["root"], space)
        _write_slices(world["cells"], space)
        m = matrix_mod.merge(world["root"], world["cells"])
        cell = next(c for c in m["cells"]
                    if c["tool"] == "ziya" and c["capability_id"] == "cap-0")
        assert cell["status"] == "unresolved"
        assert cell["score"] is None

    def test_evidenced_zero_is_carried_as_a_finding(self, world):
        """The complement: a 0 someone audited is a determination.

        Without this the suite would pass against a rule that treats every
        zero as a placeholder -- which is the defect that discarded CL4's
        confirmed absences.
        """
        space = world["space"]
        space["capabilities"][0]["origin"] = "competitor-sourced"
        space["cells"][0]["score"] = 0
        space["cells"][0]["evidence_tier"] = "A"
        space["cells"][0]["citation"] = "app/x.py:1-40"
        _write_space(world["root"], space)
        _write_slices(world["cells"], space)
        m = matrix_mod.merge(world["root"], world["cells"])
        cell = next(c for c in m["cells"]
                    if c["tool"] == "ziya" and c["capability_id"] == "cap-0")
        assert cell["status"] == "absent"
        assert cell["score"] == 0
        assert cell["evidence_tier"] == "A"

    def test_ziya_scores_are_carried_not_recomputed(self, world):
        _write_slices(world["cells"], world["space"])
        m = matrix_mod.merge(world["root"], world["cells"])
        z = {c["capability_id"]: c for c in m["cells"] if c["tool"] == "ziya"}
        assert z["cap-1"]["score"] == 3 and z["cap-1"]["evidence_tier"] == "A"
        assert z["cap-1"]["status"] == "present"

    def test_every_capability_gets_a_ziya_cell(self, world):
        _write_slices(world["cells"], world["space"])
        m = matrix_mod.merge(world["root"], world["cells"])
        assert len([c for c in m["cells"] if c["tool"] == "ziya"]) == 4


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------

class TestPlanning:
    def test_one_slice_per_tool_and_domain(self, world):
        slices = matrix_mod.plan_slices(world["space"])
        assert len(slices) == 2 * 2  # 2 tools x 2 domains
        assert {s["slice_id"] for s in slices} == {
            "alpha::d1", "alpha::d2", "beta::d1", "beta::d2"}

    def test_slices_partition_the_grid_exactly(self, world):
        """No pair may be planned twice or left unplanned."""
        slices = matrix_mod.plan_slices(world["space"])
        pairs = [(c, s["tool"]) for s in slices for c in s["capability_ids"]]
        assert len(pairs) == len(set(pairs)) == 4 * 2

    def test_largest_slices_come_first(self, world):
        space = _space(n_caps=8)
        space["capabilities"][0]["domain"] = "d1"
        sizes = [s["cells"] for s in matrix_mod.plan_slices(space)]
        assert sizes == sorted(sizes, reverse=True)

    def test_ziya_is_not_planned_as_a_competitor(self, world):
        assert all(s["tool"] != "ziya"
                   for s in matrix_mod.plan_slices(world["space"]))

    def test_real_space_plans_the_full_grid(self):
        """The number the run is sized against."""
        path = os.path.join(REAL_ROOT, "30-matrix.json")
        if not os.path.exists(path):
            pytest.skip("v1 matrix not present")
        space = matrix_mod.load_space(REAL_ROOT)
        slices = matrix_mod.plan_slices(space)
        assert len(slices) == 364
        assert sum(s["cells"] for s in slices) == 13130


class TestTolerantReader:
    def test_duplicated_close_punctuation_tail_is_tolerated(self, tmp_path):
        """33-unique-queue.json shipped with a repeated '\\n ]\\n}\\n' tail."""
        p = tmp_path / "x.json"
        p.write_text('{"a": [1, 2]}\n\n ]\n}\n')
        assert matrix_mod._read_json(str(p)) == {"a": [1, 2]}

    def test_a_tail_carrying_content_still_raises(self, tmp_path):
        """Real corruption must not be silently truncated."""
        p = tmp_path / "y.json"
        p.write_text('{"a": 1}\n{"b": 2}\n')
        with pytest.raises(json.JSONDecodeError):
            matrix_mod._read_json(str(p))

    def test_the_repaired_unique_queue_parses_strictly(self):
        path = os.path.join(REAL_ROOT, "33-unique-queue.json")
        if not os.path.exists(path):
            pytest.skip("unique queue not present")
        data = json.load(open(path))  # strict json.load, not the tolerant reader
        assert len(data["unique"]) == 205


class TestProtocolDocMatchesValidator:
    """The seam: 364 agents follow the doc, the validator judges them.

    A doc and a validator that disagree would each pass their own tests while
    every agent's output was silently discarded -- the costliest possible
    failure here, since it is only discoverable after the whole run is paid
    for.  So parse the schema example out of the protocol and validate it.
    """

    PROTOCOL = os.path.join(REAL_ROOT, "24-matrix-protocol.md")

    def _example(self):
        import re
        if not os.path.exists(self.PROTOCOL):
            pytest.skip("matrix protocol not present")
        blocks = re.findall(r"```json\n(.*?)```",
                            open(self.PROTOCOL).read(), re.S)
        assert blocks, "the protocol must carry a JSON schema example"
        return json.loads(blocks[0])

    def test_the_documented_example_is_valid_json(self):
        assert self._example()["schema_version"] == matrix_mod.SCHEMA_VERSION

    def test_the_example_demonstrates_every_competitor_status(self):
        """A vocabulary member with no worked example gets used wrongly."""
        shown = {c["status"] for c in self._example()["cells"]}
        expected = set(matrix_mod.STATUSES) - {"unresolved"}  # Ziya-only
        assert shown == expected, f"undemonstrated: {expected - shown}"

    def test_the_documented_shape_passes_the_validator(self, tmp_path):
        """Bound to REAL capability ids, so this is not a self-consistent toy."""
        example = self._example()
        if not os.path.exists(os.path.join(REAL_ROOT, "30-matrix.json")):
            pytest.skip("capability space not present")
        space = matrix_mod.load_space(REAL_ROOT)
        slices = [s for s in matrix_mod.plan_slices(space)
                  if s["tool"] == space["tools"][1]]
        shapes = example["cells"]
        cells_dir = str(tmp_path / "cells")
        os.makedirs(cells_dir)
        for s in slices:
            rows = []
            for i, cid in enumerate(s["capability_ids"]):
                c = dict(shapes[i % len(shapes)])
                c["capability_id"] = cid
                rows.append(c)
            with open(os.path.join(cells_dir,
                                   f"{s['tool']}__{s['domain']}.json"), "w") as f:
                json.dump({"schema_version": "2.0", "tool": s["tool"],
                           "domain": s["domain"], "cells": rows}, f)
        res = matrix_mod.validate_cells(REAL_ROOT, cells_dir)
        per_cell = [e for e in res["errors"] if "GRID INCOMPLETE" not in e]
        assert per_cell == [], f"the documented shape is rejected: {per_cell[:3]}"
        # Positive control: it really did score the whole tool, so an empty
        # error list cannot be an artifact of nothing having been checked.
        assert res["stats"]["cells"] == len(space["capabilities"])

    def test_the_protocol_states_the_measured_defect(self):
        """A contract that does not say WHY gets optimised away by its readers."""
        text = open(self.PROTOCOL).read()
        for token in ("13130", "11069", "94%", "13%", "117"):
            assert token in text, f"protocol omits the measurement {token}"
