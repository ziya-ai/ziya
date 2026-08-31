"""Re-audit deltas must be partitioned by CAUSE, not just reported.

A score moving 3 -> 4 has three possible causes needing opposite reactions:
the tool changed (REAL), our evidence improved (EVIDENCE), or the cell came or
went (COVERAGE).  This matters quantitatively, not just conceptually: 71% of
the first run's competitor cells were C or D tier and 35% were D, so on any
re-audit the EVIDENCE class will be large.  Unpartitioned it swamps REAL, and
the report reads as competitive churn that is not happening.

A fourth class is a refusal rather than a delta: when the two runs do not share
a dimension -- absent from one registry, or present in both under a stable id
whose ``name_hash`` changed -- the cell is INCOMPARABLE and must not be diffed.
Silently aligning a reworded axis is how a re-audit invents findings.

Every assertion here targets the classification, and each class carries a
negative control so a classifier that answered one label for everything would
fail rather than pass.
"""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

diff_mod = pytest.importorskip(
    "complandscape_diff", reason="scripts/complandscape_diff.py not present")
registry_mod = pytest.importorskip(
    "complandscape_registry", reason="scripts/complandscape_registry.py not present")

REAL = diff_mod.REAL
EVIDENCE = diff_mod.EVIDENCE
COVERAGE = diff_mod.COVERAGE
SCHEMA = diff_mod.SCHEMA
UNCHANGED = diff_mod.UNCHANGED

DIM = "cap-a::tier-count"
AXIS_NAME = "tier count"


def registry(dim_id=DIM, axis_name=AXIS_NAME):
    return {
        "registry_version": "1.0.0",
        "capabilities": {
            "cap-a": {
                "capability_id": "cap-a",
                "dimensions": [{
                    "dimension_id": dim_id,
                    "name": axis_name,
                    "name_hash": registry_mod.name_hash(axis_name),
                }],
                "contenders": [{"tool": "claude-code", "matrix_score": 3}],
            }
        },
    }


def run(*, score=3, tier="C", status="scored", ziya_score=4, verdict="PARITY",
        confidence="medium", tool="claude-code", dim_id=DIM):
    cell = {"tool": tool, "status": status}
    if status == "scored":
        cell.update({"score": score, "evidence_tier": tier, "as_of": "2026-08-25"})
    return {
        "cap-a": {
            "capability_id": "cap-a",
            "registry_version": "1.0.0",
            "verdict": verdict,
            "confidence": confidence,
            "dimensions": [{
                "dimension_id": dim_id,
                "ziya": {"score": ziya_score, "evidence_tier": "A",
                         "as_of": "2026-08-25"},
                "competitors": [cell],
            }],
        }
    }


def only(report, kind, tool="claude-code"):
    """The single row of ``kind`` for ``tool``; asserts there is exactly one."""
    rows = [r for r in report[kind] if r["tool"] == tool]
    assert len(rows) == 1, f"expected one {kind} row for {tool}, got {rows}"
    return rows[0]


class TestNothingChanged:
    def test_identical_runs_produce_no_deltas(self):
        """Negative control. Without it every classifier below could be a stub
        that labels everything REAL and still pass its own test."""
        reg = registry()
        report = diff_mod.diff_runs(run(), run(), reg, reg)
        assert report["totals"][REAL] == 0
        assert report["totals"][EVIDENCE] == 0
        assert report["totals"][COVERAGE] == 0
        assert report["totals"][SCHEMA] == 0
        assert report["totals"][UNCHANGED] == 2  # the competitor cell + ziya

    def test_tier_improved_with_no_score_move_is_evidence_not_real(self):
        reg = registry()
        report = diff_mod.diff_runs(run(score=3, tier="D"), run(score=3, tier="B"), reg, reg)
        assert report["totals"][REAL] == 0
        assert only(report, EVIDENCE)["reason"].startswith("score 3 held")


class TestRealChange:
    def test_score_move_at_unchanged_tier_is_real(self):
        reg = registry()
        report = diff_mod.diff_runs(run(score=3, tier="C"), run(score=4, tier="C"), reg, reg)
        assert report["totals"][REAL] == 1
        assert "3 -> 4" in only(report, REAL)["reason"]

    def test_our_own_score_is_diffed_by_the_same_machinery(self):
        """Ziya must not get a separate, kinder path."""
        reg = registry()
        report = diff_mod.diff_runs(run(ziya_score=3), run(ziya_score=4), reg, reg)
        assert only(report, REAL, tool="ziya")["new"]["score"] == 4


class TestEvidenceChange:
    def test_score_move_with_stronger_tier_is_attributed_to_knowledge(self):
        reg = registry()
        report = diff_mod.diff_runs(run(score=2, tier="D"), run(score=4, tier="B"), reg, reg)
        assert report["totals"][REAL] == 0, (
            "a two-point jump alongside D->B is us looking harder, not them shipping"
        )
        assert "stronger" in only(report, EVIDENCE)["reason"]

    def test_score_move_with_weaker_tier_is_also_evidence(self):
        """A prior citation that did not hold up is not competitive movement."""
        reg = registry()
        report = diff_mod.diff_runs(run(score=4, tier="B"), run(score=2, tier="D"), reg, reg)
        assert report["totals"][REAL] == 0
        assert "weaker" in only(report, EVIDENCE)["reason"]


class TestCoverageChange:
    def test_new_cell_is_coverage(self):
        reg = registry()
        old = run()
        old["cap-a"]["dimensions"][0]["competitors"] = []
        report = diff_mod.diff_runs(old, run(), reg, reg)
        assert report["totals"][REAL] == 0
        assert "new cell" in only(report, COVERAGE)["reason"]

    def test_dropped_cell_is_coverage(self):
        reg = registry()
        new = run()
        new["cap-a"]["dimensions"][0]["competitors"] = []
        report = diff_mod.diff_runs(run(), new, reg, reg)
        assert "dropped" in only(report, COVERAGE)["reason"]

    def test_unknown_to_below_threshold_is_flagged_as_newly_real_signal(self):
        """'we never checked' becoming 'they genuinely lack it' IS informative,
        and is a different event from a score moving."""
        reg = registry()
        report = diff_mod.diff_runs(
            run(status="unknown"), run(status="below_threshold"), reg, reg)
        assert report["totals"][REAL] == 0
        assert "now real signal" in only(report, COVERAGE)["reason"]

    def test_regression_to_no_signal_is_called_out(self):
        reg = registry()
        report = diff_mod.diff_runs(
            run(status="below_threshold"), run(status="not_assessed"), reg, reg)
        assert "REGRESSED" in only(report, COVERAGE)["reason"]

    def test_same_absence_reason_is_unchanged(self):
        reg = registry()
        report = diff_mod.diff_runs(
            run(status="not_in_matrix"), run(status="not_in_matrix"), reg, reg)
        assert report["totals"][COVERAGE] == 0


class TestSchemaGate:
    def test_reworded_axis_under_a_stable_id_is_refused_not_diffed(self):
        """The defect that made run 1 undiffable, caught at the diff boundary.

        Same id, different name: the score change below is real-looking and must
        NOT be reported, because the two runs measured different things.
        """
        old_reg = registry(axis_name="tier count")
        new_reg = registry(axis_name="number of autonomy tiers")
        report = diff_mod.diff_runs(run(score=3), run(score=5), old_reg, new_reg)
        assert report["totals"][REAL] == 0, "a reworded axis must not yield a delta"
        assert report["totals"][SCHEMA] == 2
        assert "name changed" in report[SCHEMA][0]["reason"]

    def test_dimension_absent_from_one_registry_is_incomparable(self):
        old_reg = registry()
        new_reg = registry(dim_id="cap-a::a-different-axis", axis_name="other")
        report = diff_mod.diff_runs(run(), run(), old_reg, new_reg)
        assert report["totals"][SCHEMA] > 0
        assert report["totals"][REAL] == 0

    def test_comparable_fraction_is_reported(self):
        old_reg = registry(axis_name="tier count")
        new_reg = registry(axis_name="renamed axis")
        report = diff_mod.diff_runs(run(), run(), old_reg, new_reg)
        assert report["totals"]["comparable_fraction"] == 0.0

    def test_matching_registries_are_fully_comparable(self):
        reg = registry()
        report = diff_mod.diff_runs(run(), run(), reg, reg)
        assert report["totals"]["comparable_fraction"] == 1.0


class TestVerdictFlips:
    def test_flip_backed_by_a_real_cell_is_marked_real(self):
        reg = registry()
        report = diff_mod.diff_runs(
            run(score=3, tier="C", verdict="PARITY"),
            run(score=5, tier="C", verdict="ZIYA_BEHIND"),
            reg, reg)
        flip = report["verdict_flips"][0]
        assert flip["old_verdict"] == "PARITY"
        assert flip["driven_by_real_change"] is True

    def test_flip_backed_only_by_evidence_is_not_marked_real(self):
        """The headline protection: a verdict that moved because we read more
        must not be reported as the competitor having moved."""
        reg = registry()
        report = diff_mod.diff_runs(
            run(score=2, tier="D", verdict="PARITY"),
            run(score=4, tier="B", verdict="ZIYA_BEHIND"),
            reg, reg)
        flip = report["verdict_flips"][0]
        assert flip["driven_by_real_change"] is False
        assert flip["supporting_cells"][EVIDENCE] >= 1

    def test_no_flip_recorded_when_the_verdict_holds(self):
        reg = registry()
        report = diff_mod.diff_runs(run(score=3), run(score=4), reg, reg)
        assert report["verdict_flips"] == []


class TestRendering:
    def test_low_comparability_emits_a_warning(self):
        old_reg = registry(axis_name="tier count")
        new_reg = registry(axis_name="renamed")
        text = diff_mod.render(diff_mod.diff_runs(run(), run(), old_reg, new_reg))
        assert "WARNING" in text and "comparable" in text

    def test_full_comparability_emits_no_warning(self):
        reg = registry()
        text = diff_mod.render(diff_mod.diff_runs(run(), run(score=4), reg, reg))
        assert "WARNING" not in text

    def test_real_changes_are_rendered_before_evidence(self):
        """Detail sections, in reading order. Anchored on the section headings
        rather than the class names, which also appear in the count summary
        above them and would make this assert on the wrong line."""
        reg = registry()
        report = diff_mod.diff_runs(
            run(score=3, tier="C"), run(score=4, tier="C"), reg, reg)
        report[EVIDENCE].append({
            "capability_id": "cap-a", "dimension_id": DIM, "tool": "cline",
            "reason": "synthetic", "old": None, "new": None,
        })
        text = diff_mod.render(report)
        assert text.index("REAL CHANGES --") < text.index("EVIDENCE CHANGES --")


REAL_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".ziya", "complandscape",
)


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(REAL_ROOT, "25-dimension-registry.json")),
    reason="the frozen registry is not present in this checkout",
)
class TestEndToEndAgainstTheRealRegistry:
    """Two synthetic runs over the REAL frozen schema.

    The unit tests above use a one-dimension toy registry, which cannot catch a
    defect that only appears at 601 dimensions across 108 capabilities -- an id
    collision, or a capability whose contender list is empty.
    """

    def _registry(self):
        with open(os.path.join(REAL_ROOT, "25-dimension-registry.json"),
                  encoding="utf-8") as handle:
            return json.load(handle)

    def _synth_run(self, reg, *, score, tier, limit=12):
        out = {}
        for cap_id, cap in list(reg["capabilities"].items())[:limit]:
            tools = [c["tool"] for c in cap["contenders"]]
            if not tools:
                continue
            out[cap_id] = {
                "capability_id": cap_id,
                "registry_version": reg["registry_version"],
                "verdict": "PARITY", "confidence": "medium",
                "dimensions": [{
                    "dimension_id": dim["dimension_id"],
                    "ziya": {"score": 4, "evidence_tier": "A",
                             "as_of": "2026-08-25"},
                    "competitors": [{
                        "tool": tools[0], "status": "scored", "score": score,
                        "evidence_tier": tier, "as_of": "2026-08-25",
                    }],
                } for dim in cap["dimensions"]],
            }
        return out

    def test_identical_synthetic_runs_yield_no_deltas(self):
        reg = self._registry()
        run_a = self._synth_run(reg, score=3, tier="C")
        report = diff_mod.diff_runs(run_a, copy.deepcopy(run_a), reg, reg)
        assert report["totals"][REAL] == 0
        assert report["totals"][SCHEMA] == 0, (
            "every dimension came from the registry, so nothing is incomparable"
        )
        assert report["totals"]["comparable_fraction"] == 1.0
        assert report["totals"][UNCHANGED] > 0, "the fixture produced no cells"

    def test_uniform_score_bump_at_same_tier_is_all_real(self):
        reg = self._registry()
        report = diff_mod.diff_runs(
            self._synth_run(reg, score=3, tier="C"),
            self._synth_run(reg, score=4, tier="C"),
            reg, reg)
        # ziya cells are identical in both; only competitor cells moved.
        assert report["totals"][REAL] > 0
        assert report["totals"][EVIDENCE] == 0
        assert report["totals"][COVERAGE] == 0

    def test_uniform_score_bump_with_tier_shift_is_all_evidence(self):
        reg = self._registry()
        report = diff_mod.diff_runs(
            self._synth_run(reg, score=2, tier="D"),
            self._synth_run(reg, score=4, tier="B"),
            reg, reg)
        assert report["totals"][REAL] == 0, (
            "a fleet-wide D->B re-read is our knowledge improving, and must not "
            "be reported as the field having moved"
        )
        assert report["totals"][EVIDENCE] > 0

    def test_registry_dimension_ids_are_globally_unique(self):
        """A collision would make two capabilities' cells share a key."""
        reg = self._registry()
        ids = [d["dimension_id"]
               for cap in reg["capabilities"].values()
               for d in cap["dimensions"]]
        assert len(ids) == len(set(ids))


class TestRunLoading:
    def test_load_run_indexes_by_capability_id(self, tmp_path):
        d = tmp_path / "r"
        d.mkdir()
        (d / "whatever-filename.json").write_text(
            json.dumps(run()["cap-a"]), encoding="utf-8")
        loaded = diff_mod.load_run(str(d))
        assert list(loaded) == ["cap-a"], (
            "keying on filename rather than capability_id would break the "
            "moment a run is written to a different path"
        )

    def test_unreadable_file_is_skipped_not_fatal(self, tmp_path):
        d = tmp_path / "r"
        d.mkdir()
        (d / "broken.json").write_text("{not json", encoding="utf-8")
        (d / "ok.json").write_text(json.dumps(run()["cap-a"]), encoding="utf-8")
        assert list(diff_mod.load_run(str(d))) == ["cap-a"]

    def test_missing_directory_yields_empty_rather_than_raising(self):
        assert diff_mod.load_run("/nonexistent/path/xyz") == {}
