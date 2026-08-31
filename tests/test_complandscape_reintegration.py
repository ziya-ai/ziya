"""CL4 reintegration tooling: carry-forward, validation, disposition apply.

The first run of CL4 produced 225 records and hand-quarantined 27 of them
as "corrupted".  They parse fine.  What was wrong is legible in one of
their own fields:

    "stage_a_verdict": "PARTIAL (reconstructed) -- the Stage A file
     autonomy-level-controls-stageA.json was ABSENT from 40-reintegration/
     (the 'previous step' content handed to Stage B was a carry-over of the
     human-takeover-mode record). Stage B therefore performed the
     second-look code audit itself..."

Stage B's Stage A record was missing, so Stage B audited the capability
itself and wrote a disposition indistinguishable from a paired finding.
Nothing checked for it; a human spotted it and moved the files aside.

The load-bearing tests here are the ones that catch that shape three
independent ways -- orphan pair, prose in a vocabulary field, and
cross-file verdict disagreement -- plus
``test_the_real_first_run_fails_validation``, which requires the validator
to REJECT the shipped corpus.  Without that last one the suite could pass
against a validator that approves anything.

Every "refuses X" test is paired with a positive control that the
well-formed equivalent is ACCEPTED, so a validator that rejected
everything could not satisfy them.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

reint = pytest.importorskip(
    "complandscape_reintegration",
    reason="CL4 reintegration tooling not importable",
)
matrix_mod = pytest.importorskip("complandscape_matrix")

REAL_ROOT = ".ziya/complandscape"
REAL_PRIOR = os.path.join(REAL_ROOT, "40-reintegration")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _stage_a(cid="cap-a", verdict="PARTIAL", **over):
    rec = {
        "capability_id": cid,
        "verdict": verdict,
        "searched": ["grep foo", "ast_search bar"],
        "evidence": [{"path": "app/agents/block_executor.py", "lines": "1-10",
                      "note": "real file so the staleness check resolves"}],
        "nearest_subsystem": "app/agents",
        "as_of": "2026-08-26",
    }
    if verdict == "FOUND":
        rec["ziya_internal_name"] = "the internal name"
        rec["maturity_if_found"] = 3
    if verdict == "PARTIAL":
        rec["missing_behaviors"] = ["the missing half"]
    rec.update(over)
    return rec


def _disp(cid="cap-a", verdict="PARTIAL", disposition="BUILD_CANDIDATE", **over):
    rec = {
        "capability_id": cid,
        "disposition": disposition,
        "stage_a_verdict": verdict,
        "ledger_correction": ({"corrected_score": 3, "evidence_tier": "A",
                               "citation": "x.py:1", "note": "n"}
                              if disposition == "LEDGER_CORRECTION" else None),
        "residual_quality_gap": "narrower than cursor",
        "documentation_defect": "Docs/X.md should say Y",
        "stretch": ({"effort_class": "MEDIUM", "effort_reasoning": "r",
                     "integration_points": [], "existing_substrate": "s"}
                    if disposition == "BUILD_CANDIDATE" else {}),
        "recommendation": "do the reduced version",
    }
    rec.update(over)
    return rec


def _run(tmp_path, pairs):
    """Write a run dir from [(stage_a, disposition_or_None), ...]."""
    d = tmp_path / "run"
    d.mkdir(exist_ok=True)
    for a, b in pairs:
        if a is not None:
            (d / f"{a['capability_id']}-stageA.json").write_text(json.dumps(a))
        if b is not None:
            (d / f"{b['capability_id']}-disposition.json").write_text(json.dumps(b))
    return str(d)


@pytest.fixture
def queue_root(tmp_path):
    """A minimal study root whose gap queue names cap-a and cap-b."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "31-gap-queue.json").write_text(json.dumps({
        "gaps": [{"id": "cap-a"}, {"id": "cap-b"}]}))
    return str(root)


# --------------------------------------------------------------------------
# citation parsing -- the field specified as a path and used as prose
# --------------------------------------------------------------------------

class TestCitationParsing:
    def test_comma_joined_paths_both_extracted(self):
        assert reint._split_citation("app/routes/,app/api/") == [
            "app/routes/", "app/api/"]

    def test_plus_joined_paths_extracted(self):
        got = reint._split_citation(
            "app/services/pdf_exporter.py + html_exporter.py")
        assert "app/services/pdf_exporter.py" in got

    def test_slash_space_joined_with_line_refs(self):
        got = reint._split_citation("app/providers/base.py:283 / bedrock.py:429,642")
        assert "app/providers/base.py" in got, (
            "a trailing :line reference must not defeat path resolution")

    def test_and_joined_paths(self):
        got = reint._split_citation(
            "app/mcp/tools/context_management.py-and-memory_tools.py")
        assert "app/mcp/tools/context_management.py" in got

    def test_parenthetical_is_dropped(self):
        assert reint._split_citation("19-ziya-ledger.json (a note)") == [
            "19-ziya-ledger.json"]

    def test_prose_placeholder_yields_no_candidate(self):
        """'repo-root' is not a path and must not read as a missing file."""
        assert reint._split_citation("repo-root") == []

    def test_a_plain_path_is_unchanged(self):
        # Positive control: the parser must not mangle the ordinary case.
        assert reint._split_citation("app/agents/block_executor.py") == [
            "app/agents/block_executor.py"]


# --------------------------------------------------------------------------
# carry-forward: a Stage A verdict is about Ziya's code, not the grid
# --------------------------------------------------------------------------

class TestCarryForward:
    def test_a_sound_record_carries(self):
        # Positive control for every refusal below.
        carry, why = reint.stage_a_carry_state(_stage_a(), "cap-a", ".")
        assert carry, why

    def test_capability_id_mismatch_refuses(self):
        """The binding-leak shape: a record about a different capability."""
        carry, why = reint.stage_a_carry_state(
            _stage_a(cid="other-cap"), "cap-a", ".")
        assert not carry and "mismatch" in why

    def test_qualified_verdict_refuses(self):
        """'PARTIAL (reconstructed)' marked an audit that never ran."""
        carry, why = reint.stage_a_carry_state(
            _stage_a(verdict="PARTIAL (reconstructed)"), "cap-a", ".")
        assert not carry and "verdict" in why

    def test_missing_required_field_refuses(self):
        rec = _stage_a()
        rec.pop("evidence")
        carry, why = reint.stage_a_carry_state(rec, "cap-a", ".")
        assert not carry and "evidence" in why

    def test_all_citations_gone_refuses(self):
        rec = _stage_a(evidence=[{"path": "app/does/not/exist_xyz.py"}])
        carry, why = reint.stage_a_carry_state(rec, "cap-a", ".")
        assert not carry and "stale" in why

    def test_one_live_citation_among_several_carries(self):
        """A refactor that moved one of five files does not void a finding."""
        rec = _stage_a(evidence=[
            {"path": "app/does/not/exist_xyz.py"},
            {"path": "app/agents/block_executor.py"},
        ])
        carry, why = reint.stage_a_carry_state(rec, "cap-a", ".")
        assert carry, why

    def test_unresolvable_citation_carries_but_says_so(self):
        """Absence of evidence is not evidence of staleness -- but it shows."""
        rec = _stage_a(evidence=[{"path": "repo-root"}])
        carry, why = reint.stage_a_carry_state(rec, "cap-a", ".")
        assert carry and "no resolvable citation" in why

    def test_stage_b_is_never_carried(self):
        """A disposition weighs the gap against the grid, and the grid changed."""
        plan = reint.plan_reintegration(REAL_ROOT, REAL_PRIOR)
        assert plan["items"], "plan produced no items"
        assert all(i["stage_b"] == "fresh" for i in plan["items"])
        assert plan["totals"]["stage_b_fresh"] == plan["totals"]["queue_entries"]

    def test_plan_records_the_commit_it_was_built_at(self):
        """The first run recorded no provenance at all across 43 keys."""
        plan = reint.plan_reintegration(REAL_ROOT, REAL_PRIOR)
        assert "audited_at_commit" in plan

    def test_plan_carries_most_of_the_real_prior_run(self):
        """The point of the split: do not re-pay for the expensive half."""
        plan = reint.plan_reintegration(REAL_ROOT, REAL_PRIOR)
        t = plan["totals"]
        assert t["stage_a_carry_forward"] > t["stage_a_fresh"], (
            f"carry-forward should dominate on an unchanged tree; got {t}")


# --------------------------------------------------------------------------
# validation: the gate the first run did not have
# --------------------------------------------------------------------------

class TestValidationAcceptsGoodRuns:
    def test_a_well_formed_pair_validates(self, tmp_path, queue_root):
        run = _run(tmp_path, [(_stage_a("cap-a"), _disp("cap-a")),
                              (_stage_a("cap-b"), _disp("cap-b"))])
        res = reint.validate_run(run, queue_root)
        assert res["ok"], res["errors"]
        assert res["stats"]["pairs"] == 2

    def test_every_disposition_kind_is_accepted(self, tmp_path, queue_root):
        run = _run(tmp_path, [
            (_stage_a("cap-a", "FOUND"),
             _disp("cap-a", "FOUND", "LEDGER_CORRECTION")),
            (_stage_a("cap-b", "ABSENT", searched=["x"]),
             _disp("cap-b", "ABSENT", "DELIBERATE_NON_GOAL")),
        ])
        res = reint.validate_run(run, queue_root)
        assert res["ok"], res["errors"]


class TestValidationCatchesTheQuarantinedShape:
    def test_orphan_disposition_is_an_error(self, tmp_path, queue_root):
        """Stage B disposed of an audit that never ran."""
        run = _run(tmp_path, [(None, _disp("cap-a")), (_stage_a("cap-b"), _disp("cap-b"))])
        res = reint.validate_run(run, queue_root)
        assert not res["ok"]
        assert any("NO Stage A" in e for e in res["errors"])

    def test_prose_in_the_verdict_field_is_an_error(self, tmp_path, queue_root):
        """The exact field the first run used to hide a missing audit."""
        bad = _disp("cap-a", disposition="BUILD_CANDIDATE")
        bad["stage_a_verdict"] = (
            "PARTIAL (reconstructed) -- the Stage A file was ABSENT so Stage B "
            "performed the second-look code audit itself, grounded in "
            "write_policy.py and shell_config.py")
        run = _run(tmp_path, [(_stage_a("cap-a"), bad)])
        res = reint.validate_run(run, queue_root)
        assert not res["ok"]
        assert any("stage_a_verdict" in e and "prose" in e for e in res["errors"])

    def test_short_but_qualified_verdict_is_still_an_error(self, tmp_path, queue_root):
        """'PARTIAL (~40% present)' is 22 chars -- under any length bound."""
        run = _run(tmp_path, [
            (_stage_a("cap-a"),
             _disp("cap-a", verdict="PARTIAL (~40% present)"))])
        res = reint.validate_run(run, queue_root)
        assert not res["ok"]
        assert any("stage_a_verdict" in e for e in res["errors"])

    def test_cross_file_verdict_disagreement_is_an_error(self, tmp_path, queue_root):
        """Both files well-formed, built on different audits."""
        run = _run(tmp_path, [
            (_stage_a("cap-a", "ABSENT", searched=["x"]),
             _disp("cap-a", verdict="PARTIAL"))])
        res = reint.validate_run(run, queue_root)
        assert not res["ok"]
        assert any("different audit" in e for e in res["errors"])

    def test_found_with_build_candidate_is_an_error(self, tmp_path, queue_root):
        run = _run(tmp_path, [
            (_stage_a("cap-a", "FOUND"),
             _disp("cap-a", "FOUND", "BUILD_CANDIDATE"))])
        res = reint.validate_run(run, queue_root)
        assert any("nothing to build" in e for e in res["errors"])

    def test_absent_with_ledger_correction_is_an_error(self, tmp_path, queue_root):
        run = _run(tmp_path, [
            (_stage_a("cap-a", "ABSENT", searched=["x"]),
             _disp("cap-a", "ABSENT", "LEDGER_CORRECTION"))])
        res = reint.validate_run(run, queue_root)
        assert any("no score to correct" in e for e in res["errors"])

    def test_incomplete_queue_coverage_is_an_error(self, tmp_path, queue_root):
        run = _run(tmp_path, [(_stage_a("cap-a"), _disp("cap-a"))])
        res = reint.validate_run(run, queue_root)
        assert any("COVERAGE" in e for e in res["errors"])

    def test_thin_partial_is_an_error(self, tmp_path, queue_root):
        """'partially implemented' with no decomposition is a non-answer."""
        rec = _stage_a("cap-a", "PARTIAL")
        rec.pop("missing_behaviors")
        run = _run(tmp_path, [(rec, _disp("cap-a")), (_stage_a("cap-b"), _disp("cap-b"))])
        res = reint.validate_run(run, queue_root)
        assert any("missing_behaviors" in e for e in res["errors"])

    def test_effort_class_prose_is_an_error(self, tmp_path, queue_root):
        bad = _disp("cap-a")
        bad["stretch"]["effort_class"] = (
            "SMALL for the reduced version; LARGE for full parity.")
        run = _run(tmp_path, [(_stage_a("cap-a"), bad), (_stage_a("cap-b"), _disp("cap-b"))])
        res = reint.validate_run(run, queue_root)
        assert any("effort_class" in e for e in res["errors"])


class TestAgainstTheRealCorpus:
    def test_the_real_first_run_fails_validation(self):
        """Load-bearing: the validator must REJECT the shipped corpus.

        If this ever passes, either the corpus was repaired or the
        validator stopped validating.  Either way the rest of this file's
        assertions are no longer trustworthy.
        """
        if not os.path.isdir(REAL_PRIOR):
            pytest.skip("first-run corpus not present")
        res = reint.validate_run(REAL_PRIOR, REAL_ROOT)
        assert not res["ok"], (
            "the first-run reintegration corpus is known to contain prose in "
            "vocabulary fields and dispositions without corrected_score; a "
            "validator that accepts it is not checking")
        assert res["stats"]["stage_a_files"] == 112

    def test_the_real_corpus_verdict_counts_are_read_correctly(self):
        """Positive control: the reader works, so the errors above are real."""
        if not os.path.isdir(REAL_PRIOR):
            pytest.skip("first-run corpus not present")
        res = reint.validate_run(REAL_PRIOR, REAL_ROOT)
        assert res["stats"]["verdicts"].get("FOUND") == 17
        assert res["stats"]["pairs"] == 112


# --------------------------------------------------------------------------
# apply-dispositions: status and score written together, or not at all
# --------------------------------------------------------------------------

class TestProtocolDocMatchesTheValidator:
    """The doc 100+ agents follow must satisfy the validator that judges them.

    A doc/validator disagreement is invisible until the whole run has been
    paid for: every agent conforms to the protocol, the validator rejects
    every file, and nothing indicates which of the two is wrong. So the
    examples in the protocol are parsed and validated here rather than
    being trusted to stay in step by review.
    """

    DOC = os.path.join(REAL_ROOT, "43-reintegration-protocol.md")

    def _examples(self):
        import re
        if not os.path.exists(self.DOC):
            pytest.skip("reintegration protocol doc not present")
        blocks = re.findall(r"```json\n(.*?)```", open(self.DOC).read(), re.S)
        assert len(blocks) >= 2, (
            "expected a Stage A and a Stage B example in the protocol")
        return json.loads(blocks[0]), json.loads(blocks[1])

    def test_the_documented_shape_validates(self, tmp_path):
        a, b = self._examples()
        assert a["capability_id"] == b["capability_id"], (
            "the two examples must be a pair, or they demonstrate nothing "
            "about cross-file agreement")
        root = tmp_path / "root"
        root.mkdir()
        (root / "31-gap-queue.json").write_text(json.dumps(
            {"gaps": [{"id": a["capability_id"]}]}))
        run = _run(tmp_path, [(a, b)])
        res = reint.validate_run(run, str(root))
        assert res["ok"], (
            f"the protocol documents a shape the validator rejects: "
            f"{res['errors']}")

    def test_the_documented_verdicts_are_in_vocabulary(self):
        a, b = self._examples()
        assert a["verdict"] in reint.VERDICTS
        assert b["disposition"] in reint.DISPOSITIONS
        assert b["stretch"]["effort_class"] in reint.EFFORT_CLASSES

    def test_the_documented_evidence_path_resolves_as_one_path(self):
        """The doc tells agents one path per entry; prove the example obeys."""
        a, _ = self._examples()
        for ev in a["evidence"]:
            assert len(reint._split_citation(ev["path"])) == 1, (
                f"the protocol's own example puts multiple paths in one "
                f"entry: {ev['path']!r}")


class TestApplyDispositions:
    @pytest.fixture
    def applied_root(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        (root / "31-gap-queue.json").write_text(json.dumps({
            "gaps": [{"id": "cap-a"}, {"id": "cap-b"}]}))
        (root / "30-matrix.json").write_text(json.dumps({
            "schema_version": "2.0",
            "tools": ["ziya", "rival"],
            "capabilities": [{"id": "cap-a", "domain": "d"},
                             {"id": "cap-b", "domain": "d"}],
            "cells": [
                {"capability_id": "cap-a", "tool": "ziya",
                 "status": "unresolved", "score": None},
                {"capability_id": "cap-b", "tool": "ziya",
                 "status": "unresolved", "score": None},
            ],
        }))
        return str(root)

    def test_ledger_correction_writes_status_and_score_together(
            self, tmp_path, applied_root):
        run = _run(tmp_path, [
            (_stage_a("cap-a", "FOUND"),
             _disp("cap-a", "FOUND", "LEDGER_CORRECTION")),
            (_stage_a("cap-b", "ABSENT", searched=["x"]),
             _disp("cap-b", "ABSENT", "DELIBERATE_NON_GOAL")),
        ])
        out = reint.apply_dispositions(run, applied_root)
        cells = {c["capability_id"]: c for c in out["matrix"]["cells"]}
        a = cells["cap-a"]
        assert a["status"] == "present" and a["score"] == 3, (
            "status and score must be written in one operation")
        assert a["corrected"] is True
        assert matrix_mod.check_matrix(out["matrix"]) == [] or all(
            "grid incomplete" in p or "coverage" in p
            for p in matrix_mod.check_matrix(out["matrix"])), (
            "the applied matrix must not carry a status/score disagreement")

    def test_settled_absence_carries_a_tier(self, tmp_path, applied_root):
        """An absence with no tier is indistinguishable from a placeholder."""
        run = _run(tmp_path, [
            (_stage_a("cap-a", "ABSENT", searched=["x"]),
             _disp("cap-a", "ABSENT", "DELIBERATE_NON_GOAL")),
            (_stage_a("cap-b", "PARTIAL"), _disp("cap-b")),
        ])
        out = reint.apply_dispositions(run, applied_root)
        cells = {c["capability_id"]: c for c in out["matrix"]["cells"]}
        assert cells["cap-a"]["status"] == "absent"
        assert cells["cap-a"]["score"] == 0
        assert cells["cap-a"]["evidence_tier"] in matrix_mod.EVIDENCE_TIERS

    def test_partial_leaves_the_cell_alone(self, tmp_path, applied_root):
        run = _run(tmp_path, [(_stage_a("cap-a", "PARTIAL"), _disp("cap-a"))])
        out = reint.apply_dispositions(run, applied_root)
        cells = {c["capability_id"]: c for c in out["matrix"]["cells"]}
        assert cells["cap-a"]["status"] == "unresolved"
        assert any("unchanged" in s for s in out["skipped"])

    def test_a_non_integer_corrected_score_is_skipped_not_written(
            self, tmp_path, applied_root):
        d = _disp("cap-a", "FOUND", "LEDGER_CORRECTION")
        d["ledger_correction"]["corrected_score"] = "3 (maybe 4)"
        run = _run(tmp_path, [(_stage_a("cap-a", "FOUND"), d)])
        out = reint.apply_dispositions(run, applied_root)
        cells = {c["capability_id"]: c for c in out["matrix"]["cells"]}
        assert cells["cap-a"]["status"] == "unresolved"
        assert any("not an int" in s for s in out["skipped"])


# --------------------------------------------------------------------------
# the matrix-side invariant CL4's writes depend on
# --------------------------------------------------------------------------

class TestMatrixStatusScoreAgreement:
    def _mtx(self, cell):
        return {"schema_version": "2.0", "tools": ["ziya"],
                "capabilities": [{"id": "cap-a", "domain": "d"}],
                "cells": [cell]}

    def test_unscored_status_carrying_a_score_is_a_problem(self):
        """The shape a score-only hand edit produces on a Ziya cell."""
        p = matrix_mod.check_matrix(self._mtx(
            {"capability_id": "cap-a", "tool": "ziya",
             "status": "unresolved", "score": 3}))
        assert any("must not carry a score" in x for x in p)

    def test_scored_status_without_a_score_is_a_problem(self):
        p = matrix_mod.check_matrix(self._mtx(
            {"capability_id": "cap-a", "tool": "ziya",
             "status": "present", "score": None}))
        assert any("requires an integer score" in x for x in p)

    def test_a_consistent_cell_is_accepted(self):
        # Positive control: without it, a checker that flagged everything
        # would satisfy both assertions above.
        p = matrix_mod.check_matrix(self._mtx(
            {"capability_id": "cap-a", "tool": "ziya",
             "status": "present", "score": 3}))
        assert not [x for x in p if "score" in x]


# --------------------------------------------------------------------------
# v2 queue shape -- the Stage 4 rerun (2026-08-30) writes {"items": [...]}
# with entries keyed "capability_id"; v1 wrote {"gaps": [...]} keyed "id".
# The planner silently planned ZERO entries against a v2 queue (every id
# resolved to None and was skipped), and validate_run's coverage check threw
# TypeError joining a {None} set.  These pin the dual-schema support so a
# revert cannot pass the suite: the fixtures here use ONLY the v2 spelling.
# --------------------------------------------------------------------------

class TestV2QueueShape:
    def _v2_root(self, tmp_path):
        root = tmp_path / "v2root"
        root.mkdir()
        (root / "31-gap-queue.json").write_text(json.dumps({
            "schema_version": "2.0",
            "items": [{"capability_id": "cap-a"},
                      {"capability_id": "cap-b"}],
        }))
        return str(root)

    def test_load_gap_queue_accepts_items_key(self, tmp_path):
        entries = reint.load_gap_queue(self._v2_root(tmp_path))
        assert len(entries) == 2

    def test_plan_names_v2_entries_rather_than_planning_zero(self, tmp_path):
        """The observed defect: 54 queue entries planned as 0."""
        root = self._v2_root(tmp_path)
        plan = reint.plan_reintegration(root, prior_dir=str(tmp_path / "no-prior"))
        cids = {i["capability_id"] for i in plan["items"]}
        assert cids == {"cap-a", "cap-b"}, (
            "v2 entries keyed capability_id must be planned, not skipped"
        )

    def test_validate_coverage_names_v2_missing_entries(self, tmp_path):
        """The observed defect: TypeError joining a {None} coverage set."""
        run = tmp_path / "run"
        run.mkdir()
        a = {"capability_id": "cap-a", "verdict": "ABSENT",
             "searched": ["x"], "evidence": [],
             "nearest_subsystem": "app/x", "as_of": "2026-08-30",
             "audited_at_commit": "abc123"}
        (run / "cap-a-stageA.json").write_text(json.dumps(a))
        res = reint.validate_run(str(run), self._v2_root(tmp_path))
        cov = [e for e in res["errors"] if "COVERAGE" in e]
        assert cov and "cap-b" in cov[0], (
            "the uncovered v2 entry must be NAMED in the coverage error"
        )

    def test_v1_gaps_key_still_accepted(self, tmp_path):
        """Regression guard: the first run's corpus must keep loading."""
        root = tmp_path / "v1root"
        root.mkdir()
        (root / "31-gap-queue.json").write_text(json.dumps({
            "gaps": [{"id": "cap-z"}]}))
        entries = reint.load_gap_queue(str(root))
        assert [e.get("id") for e in entries] == ["cap-z"]
