"""Corpus resolution, fact computation and report provenance for CL6.

THE DEFECT THIS EXISTS FOR
--------------------------
Every phase of the competitive-landscape study now writes into a
run-versioned directory with the current run id in ``<base>/CURRENT_RUN``.
CL6 -- synthesis -- predates versioning and reads the *unversioned*
directories, which hold the FIRST run's output.  Run CL3-CL5 fresh today and
CL6 would read run-1 data, emit a well-formed report full of superseded
numbers, and nothing would error.  Measured on the real corpus: ``resolve``
reports LEGACY for both ``40-reintegration`` (224 files) and ``50-depth``
(108 files), which is exactly what CL6 was pointed at.

Three properties are asserted here, each with its own failure mode:

  * RESOLUTION -- a versioned run wins; a legacy directory resolves but is
    flagged; nothing at all is reported rather than silently empty.  The
    negative control matters most: a test that only checked "resolves to
    something" would pass against the bug.

  * FACTS -- the counts a report must match are derived from the corpus,
    once, so the verifier compares numbers instead of deriving them.  A
    verifier that recomputes by hand agrees with a report that miscounts.

  * PROVENANCE -- a report declares which run it describes and the headline
    counts it used; the declaration is checked against the facts.  Prose is
    deliberately NOT parsed: inferring a corpus state from narrative text
    produces confident wrong answers.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

corpus_mod = pytest.importorskip(
    "complandscape_corpus",
    reason="complandscape_corpus.py not present",
)

REAL_ROOT = os.path.join(os.path.dirname(__file__), "..",
                         ".ziya", "complandscape")


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if isinstance(obj, str):
            fh.write(obj)
        else:
            json.dump(obj, fh)


def _build_corpus(root, *, n_caps=3, tools=("alpha", "beta"),
                  schema="2.0", complete_grid=True,
                  runs=None, depth_reg="2.0.0", registry_v="2.0.0"):
    """A minimal but structurally real corpus."""
    runs = runs or {"cells": "m1-x", "reintegration": "r2-x", "depth": "r2-x"}
    caps = [{"id": f"cap-{i}"} for i in range(n_caps)]
    cells = []
    for c in caps:
        cells.append({"capability_id": c["id"], "tool": "ziya",
                      "status": "present", "score": 3})
        for t in tools:
            if not complete_grid and c["id"] == "cap-0":
                continue
            cells.append({"capability_id": c["id"], "tool": t,
                          "status": "present", "score": 3})
    _write(os.path.join(root, "00-method.md"), "# method")
    _write(os.path.join(root, "30-matrix.json"),
           {"schema_version": schema, "capabilities": caps,
            "tools": ["ziya", *tools], "cells": cells})
    _write(os.path.join(root, "31-gap-queue.json"), {"gaps": [{"id": "cap-0"}]})
    _write(os.path.join(root, "32-contested-queue.json"),
           {"contested": [{"id": "cap-1"}]})
    _write(os.path.join(root, "33-unique-queue.json"),
           {"unique": [{"id": "cap-2"}]})
    _write(os.path.join(root, "19-ziya-ledger.json"), {"capabilities": []})
    _write(os.path.join(root, "25-dimension-registry.json"),
           {"registry_version": registry_v, "frozen_at": "2026-08-26",
            "capabilities": {"cap-1": {
                "dimensions": [{"dimension_id": "d1"}],
                "contenders": [{"tool": "alpha"}]}}})

    for base, run in (("30-cells", runs.get("cells")),
                      ("50-reintegration", runs.get("reintegration")),
                      ("50-depth", runs.get("depth"))):
        if not run:
            continue
        d = os.path.join(root, base, run)
        os.makedirs(d, exist_ok=True)
        _write(os.path.join(root, base, "CURRENT_RUN"), run)
        if base == "50-reintegration":
            _write(os.path.join(d, "cap-0-stageA.json"),
                   {"capability_id": "cap-0", "verdict": "ABSENT"})
            _write(os.path.join(d, "cap-0-disposition.json"),
                   {"capability_id": "cap-0", "disposition": "BUILD_CANDIDATE",
                    "stage_a_verdict": "ABSENT"})
        elif base == "50-depth":
            _write(os.path.join(d, "cap-1.json"),
                   {"capability_id": "cap-1", "registry_version": depth_reg,
                    "verdict": "PARITY", "confidence": "medium",
                    "dimensions": [{"dimension_id": "d1", "competitors": [
                        {"tool": "alpha", "status": "scored"}]}]})
        else:
            _write(os.path.join(d, "alpha__x.json"), {"cells": []})
    return root


def _provenance(**over):
    base = {
        "runs": {"cells": "m1-x", "reintegration": "r2-x", "depth": "r2-x"},
        "matrix_schema_version": "2.0",
        "grid_never_assessed": 0,
        "registry_version": "2.0.0",
        "gap_queue_entries": 1,
        "contested_queue_entries": 1,
        "unique_queue_entries": 1,
    }
    base.update(over)
    return base


def _report(root, prov=None, body="body"):
    blob = json.dumps(prov if prov is not None else _provenance())
    text = f"# Report\n<!-- corpus-provenance {blob} -->\n{body}\n"
    path = os.path.join(root, "REPORT.md")
    _write(path, text)
    return path


@pytest.fixture
def good(tmp_path):
    return _build_corpus(str(tmp_path / "c"))


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

class TestResolution:
    def test_versioned_run_is_resolved(self, good):
        r = corpus_mod.resolve_phase(good, "depth")
        assert r["run_id"] == "r2-x"
        assert r["legacy"] is False
        assert r["files"] == 1
        assert r["problems"] == []

    def test_legacy_directory_resolves_but_is_flagged(self, tmp_path):
        """The reported defect: loose records are an EARLIER run's output.

        They must still resolve -- refusing outright would make the tool
        useless on the current corpus -- but ``legacy`` has to be true, since
        that flag is the only thing standing between a synthesis phase and a
        report about superseded data.
        """
        root = str(tmp_path / "c")
        _build_corpus(root, runs={"cells": "m1-x", "reintegration": "r2-x"})
        legacy = os.path.join(root, "50-depth")
        _write(os.path.join(legacy, "cap-1.json"), {"capability_id": "cap-1"})
        r = corpus_mod.resolve_phase(root, "depth")
        assert r["legacy"] is True
        assert r["dir"] == legacy
        assert r["run_id"] is None
        assert any("EARLIER run" in p for p in r["problems"])

    def test_versioned_run_wins_over_legacy_siblings(self, good):
        """A CURRENT_RUN pointer must beat loose files in the same base."""
        _write(os.path.join(good, "50-depth", "stale.json"), {"x": 1})
        r = corpus_mod.resolve_phase(good, "depth")
        assert r["run_id"] == "r2-x" and r["legacy"] is False

    def test_reintegration_falls_back_to_its_renamed_predecessor(self, tmp_path):
        """CL4's output moved from 40-reintegration to 50-reintegration."""
        root = str(tmp_path / "c")
        _build_corpus(root, runs={"cells": "m1-x", "depth": "r2-x"})
        old = os.path.join(root, "40-reintegration")
        _write(os.path.join(old, "cap-0-stageA.json"), {"capability_id": "cap-0"})
        r = corpus_mod.resolve_phase(root, "reintegration")
        assert r["legacy"] is True and r["dir"] == old

    def test_absent_phase_is_reported_not_silently_empty(self, tmp_path):
        root = str(tmp_path / "c")
        _build_corpus(root, runs={"reintegration": "r2-x", "depth": "r2-x"})
        r = corpus_mod.resolve_phase(root, "cells")
        assert r["dir"] is None
        assert any("no output found" in p for p in r["problems"])

    def test_an_absent_phase_never_resolves_to_the_corpus_root(self, tmp_path):
        """Regression: joining an empty legacy_base onto the root yields the
        root itself, which holds the queue and matrix files -- so a phase with
        no output "resolved" to the root and claimed files it does not own."""
        root = str(tmp_path / "c")
        _build_corpus(root, runs={"reintegration": "r2-x", "depth": "r2-x"})
        r = corpus_mod.resolve_phase(root, "cells")
        assert r["dir"] != root
        assert r["files"] == 0

    def test_current_run_naming_a_missing_directory_is_reported(self, good):
        _write(os.path.join(good, "50-depth", "CURRENT_RUN"), "r9-nope")
        r = corpus_mod.resolve_phase(good, "depth")
        assert any("not a directory" in p for p in r["problems"])

    def test_empty_run_directory_is_reported(self, good):
        empty = os.path.join(good, "50-depth", "r3-empty")
        os.makedirs(empty)
        _write(os.path.join(good, "50-depth", "CURRENT_RUN"), "r3-empty")
        r = corpus_mod.resolve_phase(good, "depth")
        assert r["run_id"] == "r3-empty"
        assert any("wrote nowhere" in p for p in r["problems"])

    def test_missing_core_artifact_is_a_problem(self, good):
        os.remove(os.path.join(good, "25-dimension-registry.json"))
        c = corpus_mod.resolve_corpus(good)
        assert any("25-dimension-registry.json" in p for p in c["problems"])

    def test_unparseable_core_artifact_is_a_problem_not_a_crash(self, good):
        _write(os.path.join(good, "31-gap-queue.json"), "{ not json")
        c = corpus_mod.resolve_corpus(good)
        assert any("31-gap-queue.json" in p and "unparseable" in p
                   for p in c["problems"])


# --------------------------------------------------------------------------
# facts
# --------------------------------------------------------------------------

class TestFacts:
    def test_clean_corpus_has_no_problems(self, good):
        f = corpus_mod.corpus_facts(good)
        assert f["problems"] == [], f["problems"]
        assert f["blocking"] == []

    def test_grid_and_queue_counts_are_derived(self, good):
        f = corpus_mod.corpus_facts(good)
        assert f["matrix"]["capabilities"] == 3
        assert f["matrix"]["competitor_tools"] == 2
        assert f["matrix"]["grid"] == {"full": 6, "present": 6,
                                      "never_assessed": 0}
        assert f["queues"] == {"gap": 1, "contested": 1, "unique": 1}

    def test_v1_schema_is_blocking(self, tmp_path):
        f = corpus_mod.corpus_facts(_build_corpus(str(tmp_path / "c"),
                                                  schema=None))
        assert any("schema_version" in p for p in f["blocking"])

    def test_incomplete_grid_is_blocking(self, tmp_path):
        f = corpus_mod.corpus_facts(_build_corpus(str(tmp_path / "c"),
                                                  complete_grid=False))
        assert f["matrix"]["grid"]["never_assessed"] == 2
        assert any("no cell" in p for p in f["blocking"])

    def test_depth_scored_against_a_stale_registry_is_blocking(self, tmp_path):
        """The re-auditability failure: a depth corpus is only comparable to
        the registry version it was scored against."""
        f = corpus_mod.corpus_facts(
            _build_corpus(str(tmp_path / "c"), depth_reg="1.0.0",
                          registry_v="3.0.0"))
        assert any("registry" in p and "1.0.0" in p for p in f["blocking"])

    def test_depth_without_a_registry_version_is_reported(self, tmp_path):
        f = corpus_mod.corpus_facts(_build_corpus(str(tmp_path / "c"),
                                                  depth_reg=None))
        assert any("snapshot, not a" in p for p in f["problems"])

    def test_contested_entry_with_no_depth_record_is_reported(self, good):
        cq = os.path.join(good, "32-contested-queue.json")
        _write(cq, {"contested": [{"id": "cap-1"}, {"id": "cap-2"}]})
        f = corpus_mod.corpus_facts(good)
        assert any("no depth record" in p for p in f["problems"])

    def test_orphan_disposition_is_blocking(self, good):
        d = os.path.join(good, "50-reintegration", "r2-x")
        _write(os.path.join(d, "cap-9-disposition.json"),
               {"capability_id": "cap-9", "disposition": "BUILD_CANDIDATE"})
        f = corpus_mod.corpus_facts(good)
        assert f["reintegration"]["orphan_dispositions"] == ["cap-9"]
        assert any("no Stage A record" in p for p in f["blocking"])

    def test_unparseable_record_is_blocking_not_silently_skipped(self, good):
        d = os.path.join(good, "50-depth", "r2-x")
        _write(os.path.join(d, "cap-7.json"), "{ broken")
        f = corpus_mod.corpus_facts(good)
        assert "cap-7.json" in f["depth"]["unparseable"]
        assert any("unparseable" in p for p in f["blocking"])

    def test_legacy_resolution_is_blocking_for_synthesis(self, tmp_path):
        root = str(tmp_path / "c")
        _build_corpus(root, runs={"cells": "m1-x", "reintegration": "r2-x"})
        _write(os.path.join(root, "50-depth", "cap-1.json"),
               {"capability_id": "cap-1"})
        f = corpus_mod.corpus_facts(root)
        assert any("legacy" in p and "earlier run" in p for p in f["blocking"])

    def test_depth_cells_are_counted(self, good):
        f = corpus_mod.corpus_facts(good)
        assert f["depth"]["competitor_cells"] == 1
        assert f["depth"]["cell_statuses"] == {"scored": 1}

    def test_queue_entries_found_regardless_of_key_name(self, good):
        _write(os.path.join(good, "31-gap-queue.json"),
               [{"id": "a"}, {"id": "b"}])
        f = corpus_mod.corpus_facts(good)
        assert f["queues"]["gap"] == 2


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------

class TestProvenance:
    def test_honest_report_passes(self, good):
        res = corpus_mod.check_report(good, _report(good))
        assert res["ok"] is True, res["errors"]

    def test_missing_provenance_block_is_refused(self, good):
        path = os.path.join(good, "REPORT.md")
        _write(path, "# Report\nprose only, no declaration\n")
        res = corpus_mod.check_report(good, path)
        assert res["ok"] is False
        assert any("corpus-provenance" in e for e in res["errors"])

    def test_malformed_provenance_block_is_refused(self, good):
        path = os.path.join(good, "REPORT.md")
        _write(path, "# R\n<!-- corpus-provenance {not json} -->\n")
        res = corpus_mod.check_report(good, path)
        assert res["ok"] is False
        assert any("not valid JSON" in e for e in res["errors"])

    def test_stale_run_id_is_caught(self, good):
        prov = _provenance()
        prov["runs"]["depth"] = "r1-old"
        res = corpus_mod.check_report(good, _report(good, prov))
        assert res["ok"] is False
        assert any("runs.depth" in e and "different run" in e
                   for e in res["errors"])

    def test_wrong_count_is_caught_with_both_numbers(self, good):
        res = corpus_mod.check_report(
            good, _report(good, _provenance(gap_queue_entries=47)))
        assert res["ok"] is False
        err = next(e for e in res["errors"] if "gap_queue_entries" in e)
        assert "47" in err and "1" in err

    def test_undeclared_field_is_caught(self, good):
        prov = _provenance()
        del prov["registry_version"]
        res = corpus_mod.check_report(good, _report(good, prov))
        assert res["ok"] is False
        assert any("registry_version" in e and "not declared" in e
                   for e in res["errors"])

    def test_every_declared_field_is_actually_verified(self, good):
        """A field nobody checks is documentation, not a check.  Each
        PROVENANCE_FIELDS entry must be falsifiable, so corrupt each one in
        turn and require an error naming it."""
        for field in corpus_mod.PROVENANCE_FIELDS:
            prov = _provenance(**{field: "DELIBERATELY-WRONG"})
            res = corpus_mod.check_report(good, _report(good, prov))
            assert any(field in e for e in res["errors"]), (
                f"{field} is declared but never verified")

    def test_corpus_blocking_problems_reach_the_report_verdict(self, tmp_path):
        """An honest declaration about a broken corpus must still fail."""
        root = _build_corpus(str(tmp_path / "c"), complete_grid=False)
        res = corpus_mod.check_report(
            root, _report(root, _provenance(grid_never_assessed=2)))
        assert res["ok"] is False
        assert any("no cell" in e for e in res["errors"])

    def test_provenance_survives_multiline_and_surrounding_prose(self, good):
        blob = json.dumps(_provenance(), indent=2)
        path = os.path.join(good, "REPORT.md")
        _write(path, f"# Title\n\nintro\n\n<!-- corpus-provenance\n{blob}\n-->\n"
                     f"\n## Section\ntext\n")
        res = corpus_mod.check_report(good, path)
        assert res["ok"] is True, res["errors"]


# --------------------------------------------------------------------------
# the real corpus
# --------------------------------------------------------------------------

@pytest.mark.skipif(not os.path.isdir(REAL_ROOT), reason="no corpus checked in")
class TestAgainstTheRealCorpus:
    def test_the_real_corpus_reports_legacy_fallbacks(self):
        """The load-bearing test: CL6 as written reads the directories this
        must flag.  If it ever passes clean, either the phases were re-run
        (and the fallback is genuinely gone) or the detection broke."""
        c = corpus_mod.resolve_corpus(REAL_ROOT)
        legacy = [p for p, i in c["phases"].items() if i["legacy"]]
        assert "depth" in legacy or "reintegration" in legacy, (
            "the un-versioned run-1 directories are what CL6 was pointed at; "
            "resolution must flag them rather than accept them silently"
        )

    def test_the_real_corpus_is_not_synthesis_ready(self):
        f = corpus_mod.corpus_facts(REAL_ROOT)
        assert f["blocking"], (
            "the v1 matrix is 16% assessed and the run dirs are unversioned; "
            "a corpus check that finds nothing wrong here is not checking"
        )

    def test_real_queue_counts_are_read_correctly(self):
        f = corpus_mod.corpus_facts(REAL_ROOT)
        # Positive control: guards the three different key names.
        assert f["queues"]["gap"] and f["queues"]["contested"]
        assert f["queues"]["unique"]


PROTOCOL = os.path.join(REAL_ROOT, "61-synthesis-protocol.md")


@pytest.mark.skipif(not os.path.exists(PROTOCOL), reason="protocol absent")
class TestProtocolDocMatchesTheChecker:
    """The doc and the checker must not drift.

    108-plus agents follow the protocol literally. If the block it documents
    omits a field the checker requires, every report conforms to the doc and
    is refused by the tool -- and that only surfaces after the whole synthesis
    has been paid for. A doc/validator disagreement passes both their own
    tests, which is exactly why this seam needs its own.
    """

    @staticmethod
    def _documented_block():
        import re
        text = open(PROTOCOL, "r", encoding="utf-8").read()
        blocks = re.findall(r"```\n(<!--.*?-->)\n```", text, re.S)
        assert blocks, "protocol documents no provenance block"
        return blocks[0]

    def test_documented_block_is_extractable(self):
        prov, err = corpus_mod.extract_provenance(self._documented_block())
        assert err is None, err
        assert isinstance(prov, dict)

    def test_documented_block_declares_every_checked_field(self):
        prov, _ = corpus_mod.extract_provenance(self._documented_block())
        missing = set(corpus_mod.PROVENANCE_FIELDS) - set(prov)
        assert not missing, (
            f"the protocol's example omits {sorted(missing)}, which the "
            f"checker requires -- a conforming report would be refused"
        )

    def test_documented_runs_keys_match_the_phase_table(self):
        prov, _ = corpus_mod.extract_provenance(self._documented_block())
        assert set(prov.get("runs") or {}) == set(corpus_mod.PHASE_DIRS)

    def test_documented_block_declares_nothing_unchecked(self):
        """A declared-but-unverified field reads as a guarantee and is not one."""
        prov, _ = corpus_mod.extract_provenance(self._documented_block())
        extra = set(prov) - set(corpus_mod.PROVENANCE_FIELDS) - {"runs"}
        assert not extra, f"documented but never verified: {sorted(extra)}"

    def test_documented_phase_bases_match_the_phase_table(self):
        """The protocol's phase table is what agents read to know where each
        phase writes.  If it names a base the resolver does not know, an agent
        following the doc looks in a directory nothing resolves."""
        text = open(PROTOCOL, "r", encoding="utf-8").read()
        for phase, spec in corpus_mod.PHASE_DIRS.items():
            assert f"`{spec['base']}/`" in text, (
                f"protocol does not document the {phase} base "
                f"{spec['base']!r} that the resolver uses"
            )


SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")


class TestReferencedCommandsExist:
    """Every subcommand the protocols tell an agent to run must exist.

    This is a cheap seam guarding an expensive failure: a verifier stage that
    invokes a subcommand its script does not accept fails at the very END of a
    multi-thousand-agent study, after everything has been paid for. Checking
    the CLI's own advertised choices catches a rename in either direction --
    the script losing a command, or a doc naming one that never existed.
    """

    #: (module, subcommand) pairs the synthesis and matrix protocols instruct
    #: agents to run.  Kept explicit rather than scraped, so removing a
    #: command from a doc cannot silently shrink this test's coverage.
    REQUIRED = [
        ("complandscape_corpus", "resolve"),
        ("complandscape_corpus", "facts"),
        ("complandscape_corpus", "check-report"),
        ("complandscape_matrix", "plan"),
        ("complandscape_matrix", "check-space"),
        ("complandscape_matrix", "validate-cells"),
        ("complandscape_matrix", "merge"),
        ("complandscape_matrix", "check"),
        ("complandscape_reintegration", "plan"),
        ("complandscape_reintegration", "validate"),
        ("complandscape_reintegration", "apply-dispositions"),
        ("complandscape_registry", "build"),
        ("complandscape_registry", "check"),
        ("complandscape_registry", "validate-run"),
    ]

    @pytest.mark.parametrize("module,sub", REQUIRED)
    def test_subcommand_is_accepted(self, module, sub):
        path = os.path.join(SCRIPT_DIR, f"{module}.py")
        if not os.path.exists(path):
            pytest.skip(f"{module}.py not present")
        import subprocess
        out = subprocess.run([sys.executable, path, "--help"],
                             capture_output=True, text=True).stdout
        import re
        m = re.search(r"\{([a-z,\-]+)\}", out)
        assert m, f"{module} --help advertises no subcommand choices"
        assert sub in m.group(1).split(","), (
            f"{module}.py does not accept {sub!r}; the protocol instructs "
            f"agents to run it. Advertised: {m.group(1)}"
        )
