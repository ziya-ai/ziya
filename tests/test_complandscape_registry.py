"""The frozen comparison schema for the competitive-landscape study.

The defect being fixed, measured on the real corpus: the first CL5 run scored
2,135 cells, of which only ~101 could be aligned to a re-run, because the
dimension -- the middle third of a cell's ``(capability, dimension, tool)``
key -- was authored fresh per run (52% novel, 43% reworded, 5% verbatim) and
carried no id.  Alongside it, 37% of the queue's contender tokens were
placeholders ("many", "all", "various") and 16 capabilities named no tool at
all, so depth agents invented rosters and reached for the best-documented
tools.

These tests pin the three things that make a run re-auditable:

  * contenders are derived MECHANICALLY from the matrix, and the three reasons
    a tool can be absent stay separate -- especially the distinction between
    "the matrix scored them 0-1" (real signal) and "the matrix has no cell at
    all" (no signal, 56% of the contested grid);
  * every dimension carries a stable id AND a name fingerprint, so rewording
    an axis under a stable id is DETECTED rather than silently re-aligning two
    different measurements;
  * a run is validated against the registry before its numbers are trusted.

The load-bearing test is ``test_run_one_corpus_fails_validation``: the tool
must reject the actual first-run output.  Without it the whole suite could pass
against a validator that approves anything.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

registry_mod = pytest.importorskip(
    "complandscape_registry",
    reason="scripts/complandscape_registry.py not present",
)

ROSTER = ["claude-code", "codex-cli", "cursor", "claude-ai", "cline"]


def _matrix(cells):
    """``{capability: {tool: cell}}`` from ``(cap, tool, score, tier)`` tuples."""
    out = {}
    for cap, tool, score, tier in cells:
        out.setdefault(cap, {})[tool] = {
            "capability_id": cap, "tool": tool, "score": score,
            "evidence_tier": tier,
        }
    return out


class TestContenderDerivation:
    """The mechanical replacement for prose contender lists."""

    def test_score_at_or_above_two_is_a_contender(self):
        matrix = _matrix([
            ("cap-a", "claude-code", 4, "C"),
            ("cap-a", "codex-cli", 2, "D"),
            ("cap-a", "cursor", 1, "D"),
            ("cap-a", "cline", 0, "C"),
        ])
        got = registry_mod.derive_contenders(matrix, "cap-a", ROSTER)
        assert [r["tool"] for r in got["contenders"]] == ["claude-code", "codex-cli"]

    def test_below_threshold_is_kept_as_real_signal(self):
        """Scored 0-1 means they genuinely lack it -- that is a finding."""
        matrix = _matrix([
            ("cap-a", "cursor", 1, "D"),
            ("cap-a", "cline", 0, "C"),
        ])
        got = registry_mod.derive_contenders(matrix, "cap-a", ROSTER)
        assert {r["tool"] for r in got["below_threshold"]} == {"cursor", "cline"}

    def test_missing_matrix_cell_is_not_confused_with_a_zero(self):
        """The distinction the whole taxonomy exists for.

        56% of the contested grid has no matrix cell.  Treating that as a zero
        would convert 1,583 unassessed pairs into evidence of absence.
        """
        matrix = _matrix([("cap-a", "claude-code", 3, "C"), ("cap-a", "cline", 0, "C")])
        got = registry_mod.derive_contenders(matrix, "cap-a", ROSTER)
        assert got["blind_spot"] == ["claude-ai", "codex-cli", "cursor"]
        assert "cline" not in got["blind_spot"], (
            "an explicitly-scored 0 must be below_threshold, not a blind spot"
        )
        assert [r["tool"] for r in got["below_threshold"]] == ["cline"]

    def test_coverage_reports_the_assessed_fraction(self):
        matrix = _matrix([("cap-a", "claude-code", 3, "C"), ("cap-a", "cline", 0, "C")])
        got = registry_mod.derive_contenders(matrix, "cap-a", ROSTER)
        assert got["coverage"]["not_in_matrix"] == 3
        assert got["coverage"]["assessed_fraction"] == pytest.approx(2 / 5)

    def test_contenders_are_ranked_for_a_budgeted_run(self):
        matrix = _matrix([
            ("cap-a", "cline", 2, "D"),
            ("cap-a", "cursor", 5, "C"),
            ("cap-a", "claude-code", 3, "C"),
        ])
        got = registry_mod.derive_contenders(matrix, "cap-a", ROSTER)
        assert [r["rank"] for r in got["contenders"]] == [1, 2, 3]
        assert got["contenders"][0]["tool"] == "cursor"

    def test_empty_matrix_yields_no_contenders_rather_than_guessing(self):
        got = registry_mod.derive_contenders({}, "cap-missing", ROSTER)
        assert got["contenders"] == []
        assert len(got["blind_spot"]) == len(ROSTER)


class TestToolLabelCanonicalisation:
    """96 distinct labels were used for 26 tools in the first run."""

    @pytest.mark.parametrize("label,expected", [
        ("cursor", "cursor"),
        ("cursor (background agents)", "cursor"),
        ("cline (list_code_definition_names)", "cline"),
        ("claude-ai (Claude apps/API)", "claude-ai"),
        ("LSP call hierarchy / find-references (cursor ide, zed)", "cursor"),
    ])
    def test_variants_resolve_to_the_roster_id(self, label, expected):
        assert registry_mod.canonical_tool(label, ROSTER) == expected

    def test_longer_roster_name_wins_over_a_prefix_sibling(self):
        """``claude-code`` must not be captured by ``claude-ai``-style matching."""
        assert registry_mod.canonical_tool("claude-code", ROSTER) == "claude-code"

    def test_unknown_label_returns_none_rather_than_a_guess(self):
        assert registry_mod.canonical_tool("some-tool-we-never-audited", ROSTER) is None
        assert registry_mod.canonical_tool("", ROSTER) is None


class TestDimensionMerging:
    def test_restatement_folds_in_as_an_alias(self):
        harvested = [{"name": "# languages indexed by the background build",
                      "why_it_matters": "breadth"}]
        declared = ["# languages indexed (Ziya ~25 via tree-sitter+py+ts)"]
        frozen, candidates = registry_mod.merge_dimension_sets(declared, harvested)
        assert len(frozen) == 1, "a restatement must not become a second axis"
        assert declared[0] in frozen[0]["aliases"]
        assert frozen[0]["provenance"] == "both"
        assert candidates == []

    def test_distinct_axis_is_not_merged_away(self):
        """Negative control: without this, a lenient threshold passes silently."""
        harvested = [{"name": "# languages indexed", "why_it_matters": ""}]
        declared = ["enforced at BOTH CLI and server entrypoints"]
        frozen, candidates = registry_mod.merge_dimension_sets(declared, harvested)
        assert len(frozen) == 1
        assert len(candidates) == 1, "a genuinely novel declared axis must be parked"

    def test_unmatched_declared_is_parked_not_frozen(self):
        harvested = [{"name": "throughput under concurrency", "why_it_matters": ""}]
        frozen, candidates = registry_mod.merge_dimension_sets(
            ["cryptographic escalation ceiling present"], harvested)
        assert [d["name"] for d in frozen] == ["throughput under concurrency"]
        assert candidates[0]["status"] == "awaiting_review"

    def test_declared_is_frozen_when_there_is_nothing_harvested(self):
        """A capability run 1 never reached still needs scoreable axes."""
        frozen, candidates = registry_mod.merge_dimension_sets(["some axis"], [])
        assert [d["name"] for d in frozen] == ["some axis"]
        assert candidates == []


class TestDimensionIdentity:
    def test_ids_are_unique_within_a_capability(self):
        dims = [{"name": "recovery behaviour"}, {"name": "recovery behaviour"}]
        out = registry_mod.assign_dimension_ids("cap-a", dims)
        assert len({d["dimension_id"] for d in out}) == 2, (
            "two axes that slugify alike must not collapse onto one id"
        )

    def test_id_is_namespaced_by_capability(self):
        out = registry_mod.assign_dimension_ids("cap-a", [{"name": "tier count"}])
        assert out[0]["dimension_id"].startswith("cap-a::")

    def test_name_hash_travels_with_the_name(self):
        out = registry_mod.assign_dimension_ids("cap-a", [{"name": "tier count"}])
        assert out[0]["name_hash"] == registry_mod.name_hash("tier count")

    def test_kind_hint_distinguishes_countable_from_behavioural(self):
        out = registry_mod.assign_dimension_ids("c", [
            {"name": "# provider families normalized"},
            {"name": "recovers from a malformed hunk instead of failing"},
        ])
        assert out[0]["kind"] == "count"
        assert out[1]["kind"] == "behavioral"


def _tiny_registry():
    dims = registry_mod.assign_dimension_ids("cap-a", [{"name": "tier count"}])
    return {
        "schema_version": registry_mod.SCHEMA_VERSION,
        "registry_version": "1.0.0",
        "roster": ROSTER,
        "capabilities": {
            "cap-a": {
                "capability_id": "cap-a",
                "dimensions": dims,
                "contenders": [{"tool": "claude-code", "matrix_score": 3, "rank": 1}],
                "below_threshold": [],
                "blind_spot": ["cursor"],
            }
        },
    }


class TestRegistryCheck:
    def test_a_well_formed_registry_has_no_problems(self):
        assert registry_mod.check_registry(_tiny_registry()) == []

    def test_reworded_dimension_without_refreeze_is_detected(self):
        """The mechanism that stops two different axes sharing one id.

        Editing a name in place is the easy mistake; every diff across it would
        silently compare unlike measurements.
        """
        reg = _tiny_registry()
        reg["capabilities"]["cap-a"]["dimensions"][0]["name"] = "number of tiers"
        problems = registry_mod.check_registry(reg)
        assert any("name_hash" in p for p in problems), problems

    def test_contender_below_threshold_is_rejected(self):
        reg = _tiny_registry()
        reg["capabilities"]["cap-a"]["contenders"][0]["matrix_score"] = 1
        assert any("contender" in p for p in registry_mod.check_registry(reg))

    def test_capability_with_no_dimensions_is_rejected(self):
        reg = _tiny_registry()
        reg["capabilities"]["cap-a"]["dimensions"] = []
        assert any("no dimensions" in p for p in registry_mod.check_registry(reg))


def _write_run(tmp_path, doc, name="cap-a.json"):
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    (run_dir / name).write_text(json.dumps(doc), encoding="utf-8")
    return str(run_dir)


def _good_doc(dim_id):
    return {
        "schema_version": 2,
        "capability_id": "cap-a",
        "registry_version": "1.0.0",
        "verdict": "PARITY",
        "confidence": "medium",
        "dimensions": [{
            "dimension_id": dim_id,
            "ziya": {"score": 4, "evidence_tier": "A", "as_of": "2026-08-25"},
            "competitors": [
                {"tool": "claude-code", "status": "scored", "score": 3,
                 "evidence_tier": "C", "as_of": "2026-08-25"},
                {"tool": "cursor", "status": "not_in_matrix"},
            ],
        }],
    }


class TestRunValidation:
    def test_a_conforming_run_validates(self, tmp_path):
        reg = _tiny_registry()
        did = reg["capabilities"]["cap-a"]["dimensions"][0]["dimension_id"]
        result = registry_mod.validate_run(reg, _write_run(tmp_path, _good_doc(did)))
        assert result["ok"], result["errors"]
        assert result["stats"]["cells"] == 2

    def test_invented_dimension_is_rejected(self, tmp_path):
        reg = _tiny_registry()
        doc = _good_doc("cap-a::an-axis-i-made-up")
        result = registry_mod.validate_run(reg, _write_run(tmp_path, doc))
        assert not result["ok"]
        assert any("not in the registry" in e for e in result["errors"])

    def test_scored_cell_without_as_of_is_rejected(self, tmp_path):
        """0 of the first run's 2,135 cells carried a date."""
        reg = _tiny_registry()
        did = reg["capabilities"]["cap-a"]["dimensions"][0]["dimension_id"]
        doc = _good_doc(did)
        del doc["dimensions"][0]["competitors"][0]["as_of"]
        result = registry_mod.validate_run(reg, _write_run(tmp_path, doc))
        assert any("as_of" in e for e in result["errors"])

    def test_off_vocabulary_status_is_rejected(self, tmp_path):
        reg = _tiny_registry()
        did = reg["capabilities"]["cap-a"]["dimensions"][0]["dimension_id"]
        doc = _good_doc(did)
        doc["dimensions"][0]["competitors"][1]["status"] = "probably-missing"
        result = registry_mod.validate_run(reg, _write_run(tmp_path, doc))
        assert any("off-vocabulary" in e for e in result["errors"])

    def test_essay_verdict_is_rejected(self, tmp_path):
        """One first-run verdict field held a 200-word essay."""
        reg = _tiny_registry()
        did = reg["capabilities"]["cap-a"]["dimensions"][0]["dimension_id"]
        doc = _good_doc(did)
        doc["verdict"] = "ZIYA_BEHIND (narrow) vs the document-centric leaders " * 8
        result = registry_mod.validate_run(reg, _write_run(tmp_path, doc))
        assert any("verdict" in e for e in result["errors"])

    def test_registry_version_mismatch_is_rejected(self, tmp_path):
        reg = _tiny_registry()
        did = reg["capabilities"]["cap-a"]["dimensions"][0]["dimension_id"]
        doc = _good_doc(did)
        doc["registry_version"] = "0.9.0"
        result = registry_mod.validate_run(reg, _write_run(tmp_path, doc))
        assert any("registry_version" in e for e in result["errors"])

    def test_unreported_dimension_warns_rather_than_passing_silently(self, tmp_path):
        reg = _tiny_registry()
        reg["capabilities"]["cap-a"]["dimensions"] += registry_mod.assign_dimension_ids(
            "cap-a", [{"name": "a second axis"}])
        did = reg["capabilities"]["cap-a"]["dimensions"][0]["dimension_id"]
        result = registry_mod.validate_run(reg, _write_run(tmp_path, _good_doc(did)))
        assert any("not reported" in w for w in result["warnings"])

    def test_dimension_proposals_are_collected_not_scored(self, tmp_path):
        reg = _tiny_registry()
        did = reg["capabilities"]["cap-a"]["dimensions"][0]["dimension_id"]
        doc = _good_doc(did)
        doc["dimension_proposals"] = [{"name": "a new axis", "why_it_matters": "x"}]
        result = registry_mod.validate_run(reg, _write_run(tmp_path, doc))
        assert result["ok"], result["errors"]
        assert len(result["dimension_proposals"]) == 1


REAL_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".ziya", "complandscape",
)


@pytest.mark.skipif(
    not os.path.isdir(os.path.join(REAL_ROOT, "50-depth")),
    reason="the real study corpus is not present in this checkout",
)
class TestAgainstTheRealCorpus:
    def test_registry_builds_and_checks_clean(self):
        reg = registry_mod.build_registry(REAL_ROOT)
        assert registry_mod.check_registry(reg) == []
        assert reg["totals"]["capabilities"] == 108

    def test_run_one_corpus_fails_validation(self):
        """The load-bearing assertion: the validator must reject run 1.

        If this passes, the validator approves output that provably cannot be
        diffed, and every other test here is decoration.  Run 1's files carry
        no ``dimension_id``, no ``registry_version`` and no ``as_of``.
        """
        reg = registry_mod.build_registry(REAL_ROOT)
        result = registry_mod.validate_run(reg, os.path.join(REAL_ROOT, "50-depth"))
        assert not result["ok"], (
            "run 1 validated clean, which cannot be true -- it has no "
            "dimension ids at all"
        )
        assert any("registry_version" in e for e in result["errors"])

    def test_the_blind_spot_is_reported_rather_than_hidden(self):
        reg = registry_mod.build_registry(REAL_ROOT)
        blind = sum(c["coverage"]["not_in_matrix"]
                    for c in reg["capabilities"].values())
        assessed = sum(c["coverage"]["contenders"] + c["coverage"]["below_threshold"]
                       for c in reg["capabilities"].values())
        # Measured: 1583 unassessed vs 1225 assessed pairs.  Pinning the
        # majority-unassessed shape, not the exact number, so a matrix
        # improvement does not fail the suite.
        assert blind > assessed, (
            f"expected the known coverage gap; got blind={blind} assessed={assessed}"
        )

    def test_no_placeholder_contenders_survive(self):
        """'many', 'all', 'various' cannot appear -- contenders are roster ids."""
        reg = registry_mod.build_registry(REAL_ROOT)
        roster = set(reg["roster"])
        for cap in reg["capabilities"].values():
            for record in cap["contenders"]:
                assert record["tool"] in roster, record


PROTOCOL_PATH = os.path.join(REAL_ROOT, "26-depth-protocol.md")


@pytest.mark.skipif(
    not os.path.isfile(PROTOCOL_PATH),
    reason="26-depth-protocol.md not present in this checkout",
)
class TestProtocolDocMatchesTheValidator:
    """The seam between what agents are TOLD and what is ENFORCED.

    The protocol doc carries the output schema as a fenced JSON example. If the
    validator rejects that example, every agent will follow the doc faithfully
    and have its output discarded -- and both halves would pass their own tests
    while the run produced nothing usable. This is the one assertion that spans
    the two.
    """

    def _example(self):
        import re
        text = open(PROTOCOL_PATH, encoding="utf-8").read()
        blocks = re.findall(r"```json\n(.*?)```", text, re.S)
        assert blocks, "the protocol doc no longer contains a JSON example"
        return json.loads(blocks[0])

    def test_the_documented_example_is_valid_json(self):
        assert isinstance(self._example(), dict)

    def test_the_documented_example_declares_the_required_keys(self):
        example = self._example()
        for key in ("schema_version", "run_id", "registry_version",
                    "capability_id", "dimensions", "verdict", "confidence"):
            assert key in example, f"protocol example omits {key!r}"
        cell = example["dimensions"][0]["competitors"][0]
        for key in ("tool", "status", "score", "evidence_tier", "as_of"):
            assert key in cell, f"protocol example's scored cell omits {key!r}"

    def test_the_documented_shape_passes_validation(self, tmp_path):
        """Bind the doc's example to a real capability and validate it."""
        reg = registry_mod.build_registry(REAL_ROOT)
        cap_id, cap = next(iter(reg["capabilities"].items()))
        tools = [c["tool"] for c in cap["contenders"]]
        if not tools:
            pytest.skip("first capability has no contenders to bind to")

        example = self._example()
        example["capability_id"] = cap_id
        example["registry_version"] = reg["registry_version"]
        example["dimensions"] = [{
            "dimension_id": dim["dimension_id"],
            "ziya": {"score": 4, "evidence_tier": "A",
                     "citation": "app/x.py:1", "as_of": "2026-08-25"},
            "competitors": [{
                "tool": tools[0], "status": "scored", "score": 3,
                "evidence_tier": "C", "citation": "docs", "as_of": "2026-08-25",
            }] + [{"tool": t, "status": "not_assessed"} for t in tools[1:]],
        } for dim in cap["dimensions"]]

        result = registry_mod.validate_run(
            reg, _write_run(tmp_path, example, name=f"{cap_id}.json"))
        assert result["ok"], (
            "the schema the protocol documents does not pass the validator that "
            f"enforces it: {result['errors'][:5]}"
        )

    def test_every_status_the_doc_names_is_in_the_enum(self):
        """The doc's status table and ABSENCE_REASONS must not drift apart."""
        text = open(PROTOCOL_PATH, encoding="utf-8").read()
        for status in registry_mod.ABSENCE_REASONS:
            assert f"`{status}`" in text, (
                f"status {status!r} is enforced but never documented"
            )
