"""Tests for scripts/gfx_ledger.py — the single writer for GFX sweep evidence.

The invariants under test are the ones the file_write-based cards violated:
  * recording a verdict never drops another spec (append, not replace);
  * a re-sweep appends history rather than overwriting the prior verdict;
  * merge-triage preserves existing defect status/attempts/history and
    refuses to lose an id;
  * a verified defect that reappears becomes 'regression', not 'open';
  * retirement removes an item from the queue and survives re-merge.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gfx_ledger as L  # noqa: E402


OK = {"status": "ok", "signature": "", "detail": "fine"}
FAIL = {"status": "fail", "signature": "axis-grid-invisible:dark", "detail": "grid gone"}


@pytest.fixture
def root(tmp_path):
    return tmp_path / "gfx-sweep"


def _write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# ── record ────────────────────────────────────────────────────────────────

class TestRecord:
    def test_record_creates_blackboard_and_spec(self, root):
        spec = L.record(root, "mermaid", "mermaid-w1-01", run="r1", light=OK, dark=FAIL,
                        wave=1, intent="flowchart")
        assert spec["history"][0]["run"] == "r1"
        assert spec["current"]["dark"]["status"] == "fail"
        bb = json.loads((root / "mermaid.json").read_text())
        assert bb["schema"] == 2 and len(bb["specs"]) == 1

    def test_record_never_drops_other_specs(self, root):
        for i in range(1, 6):
            L.record(root, "mermaid", f"mermaid-w1-0{i}", run="r1", light=OK, dark=OK)
        L.record(root, "mermaid", "mermaid-w2-01", run="r1", light=OK, dark=OK)
        bb = json.loads((root / "mermaid.json").read_text())
        assert {s["id"] for s in bb["specs"]} == {f"mermaid-w1-0{i}" for i in range(1, 6)} | {"mermaid-w2-01"}

    def test_resweep_appends_history_and_updates_current(self, root):
        L.record(root, "mermaid", "s", run="r1", light=OK, dark=FAIL)
        L.record(root, "mermaid", "s", run="r2", light=OK, dark=OK)
        bb = json.loads((root / "mermaid.json").read_text())
        s = bb["specs"][0]
        assert [h["run"] for h in s["history"]] == ["r1", "r2"]
        assert s["history"][0]["dark"]["status"] == "fail"   # before preserved
        assert s["current"]["dark"]["status"] == "ok"        # after visible

    def test_resweep_does_not_rewrite_wave_or_intent(self, root):
        L.record(root, "e", "s", run="r1", light=OK, dark=OK, wave=1, intent="original")
        L.record(root, "e", "s", run="r2", light=OK, dark=OK, wave=3, intent="changed")
        s = json.loads((root / "e.json").read_text())["specs"][0]
        assert s["wave"] == 1 and s["intent"] == "original"

    def test_rejects_bad_verdict(self, root):
        with pytest.raises(L.LedgerError):
            L.record(root, "e", "s", run="r", light={"status": "maybe"}, dark=OK)

    def test_snapshot_taken_before_write(self, root):
        L.record(root, "e", "s", run="r1", light=OK, dark=OK)
        L.record(root, "e", "s", run="r2", light=OK, dark=OK)
        snaps = list((root / "history").glob("*/e.json"))
        assert snaps, "pre-write snapshot missing"


# ── migrate ───────────────────────────────────────────────────────────────

class TestMigrate:
    def _legacy(self, engine, ids, extra=None):
        bb = {"engine": engine, "specs": [
            {"id": i, "wave": 1, "intent": "x", "light": OK, "dark": FAIL} for i in ids],
              "lessons": ["l1"], "probes": [{"p": 1}]}
        if extra:
            bb.update(extra)
        return bb

    def test_promotes_and_preserves_unmodelled_keys(self, root):
        _write(root / "mermaid.json", self._legacy("mermaid", ["a", "b"]))
        counts = L.migrate(root, run_label="legacy")
        assert counts == {"mermaid": 2}
        bb = json.loads((root / "mermaid.json").read_text())
        assert bb["schema"] == 2
        assert bb["extra"]["probes"] == [{"p": 1}]
        assert bb["specs"][0]["history"][0]["run"] == "legacy"
        assert bb["specs"][0]["current"]["dark"]["signature"] == FAIL["signature"]

    def test_folds_orphan_sidecars_without_duplicating(self, root):
        _write(root / "mermaid.json", self._legacy("mermaid", ["a", "b"]))
        _write(root / "mermaid-wave2.json", self._legacy("mermaid", ["b", "c"]))
        counts = L.migrate(root, run_label="legacy")
        assert counts == {"mermaid": 3}
        bb = json.loads((root / "mermaid.json").read_text())
        assert [s["id"] for s in bb["specs"]] == ["a", "b", "c"]
        assert "mermaid-wave2.json" in bb["extra"]["folded_sidecars"]

    def test_idempotent(self, root):
        _write(root / "e.json", self._legacy("e", ["a"]))
        L.migrate(root, run_label="legacy")
        first = (root / "e.json").read_text()
        L.migrate(root, run_label="legacy")
        assert (root / "e.json").read_text() == first

    def test_skips_non_blackboard_files(self, root):
        _write(root / "backlog.json", {"groups": []})
        _write(root / "inventory.json", {"roster": []})
        _write(root / "e.json", self._legacy("e", ["a"]))
        assert L.migrate(root, run_label="x") == {"e": 1}
        assert json.loads((root / "backlog.json").read_text()) == {"groups": []}


# ── merge-triage ──────────────────────────────────────────────────────────

def _triage(root, engine, clusters, regression=None):
    _write(root / "triage" / f"{engine}.json",
           {"engine": engine, "clusters": clusters,
            "regression_set": regression or {"spec_ids": [f"{engine}-w1-02"]}})


CL = {"kind": "theme", "signature": "axis-grid-invisible:dark", "themes_affected": ["dark"],
      "spec_ids": ["mermaid-w2-01"], "hypothesis": "h", "suspect_files": ["frontend/src/plugins/d3/theme.ts"],
      "severity": "high", "confidence": "high"}


class TestMergeTriage:
    def test_creates_defects_from_empty_backlog(self, root):
        _triage(root, "mermaid", [CL])
        stats = L.merge_triage(root, run="r1")
        assert stats["new"] == 1
        b = json.loads((root / "backlog.json").read_text())
        d = b["defects"][0]
        assert d["id"] == "D-001" and d["status"] == "open" and d["engines"] == ["mermaid"]
        assert b["regression_sets"]["mermaid"] == ["mermaid-w1-02"]

    def test_low_severity_is_deferred(self, root):
        _triage(root, "e", [{**CL, "severity": "low"}])
        L.merge_triage(root, run="r1")
        assert L.show(root, defect_id="D-001")["status"] == "deferred"

    def test_remerge_preserves_status_attempts_history(self, root):
        _triage(root, "mermaid", [CL])
        L.merge_triage(root, run="r1")
        L.set_status(root, "D-001", "fix-applied", run="r1", note="patched", bump_attempts=True)
        # Second sweep re-triages the same cluster (plus a new spec id)
        _triage(root, "mermaid", [{**CL, "spec_ids": ["mermaid-w2-01", "mermaid-w2-05"]}])
        stats = L.merge_triage(root, run="r2")
        assert stats["matched"] == 1 and stats["new"] == 0
        d = L.show(root, defect_id="D-001")
        assert d["status"] == "fix-applied"
        assert d["attempts"] == 1
        assert d["spec_ids"]["mermaid"] == ["mermaid-w2-01", "mermaid-w2-05"]
        assert any(h["note"] == "patched" for h in d["history"] if "note" in h)

    def test_same_signature_other_engine_merges_only_with_file_overlap(self, root):
        _triage(root, "mermaid", [CL])
        L.merge_triage(root, run="r1")
        # same signature, DIFFERENT engine, shared suspect file -> merges
        _triage(root, "graphviz", [{**CL, "spec_ids": ["graphviz-w2-03"]}])
        L.merge_triage(root, run="r1")
        d = L.show(root, defect_id="D-001")
        assert d["engines"] == ["mermaid", "graphviz"]
        # same signature, different engine, NO file overlap -> new defect
        _triage(root, "plotly", [{**CL, "spec_ids": ["plotly-w2-01"], "suspect_files": ["other.ts"]}])
        L.merge_triage(root, run="r1")
        assert L.show(root)["defects"] == 2

    def test_verified_reappearance_becomes_regression(self, root):
        _triage(root, "mermaid", [CL])
        L.merge_triage(root, run="r1")
        L.set_status(root, "D-001", "fix-applied", run="r1")
        L.set_status(root, "D-001", "verified", run="r1")
        _triage(root, "mermaid", [CL])
        stats = L.merge_triage(root, run="r2")
        assert stats["regressions"] == 1
        assert L.show(root, defect_id="D-001")["status"] == "regression"

    def test_retired_signature_is_not_requeued(self, root):
        _triage(root, "mermaid", [CL])
        L.merge_triage(root, run="r1")
        L.retire(root, defect_id="D-001", reason="accepted limitation")
        _triage(root, "mermaid", [CL])
        stats = L.merge_triage(root, run="r2")
        assert stats["retired_reappearances"] == 1 and stats["new"] == 0
        assert L.queue(root)["group_ids"] == []

    def test_refuses_to_lose_ids(self, root, monkeypatch):
        _triage(root, "mermaid", [CL])
        L.merge_triage(root, run="r1")
        before = (root / "backlog.json").read_text()
        # A buggy matcher that DROPS an existing defect while matching —
        # the class of defect the guard exists to catch.
        real = L._cluster_matches

        def dropping(cl, d, engine):
            L._DEFECTS_REF.clear()
            return real(cl, d, engine)
        # Give the matcher a handle to the live list via the loader.
        orig_load = L._load_backlog

        def load(r):
            b = orig_load(r)
            L._DEFECTS_REF = b["defects"]
            return b
        monkeypatch.setattr(L, "_load_backlog", load)
        monkeypatch.setattr(L, "_cluster_matches", dropping)
        _triage(root, "mermaid", [CL])
        with pytest.raises(L.LedgerError, match="would drop defect ids"):
            L.merge_triage(root, run="r2")
        assert (root / "backlog.json").read_text() == before, "guard must not write"

    def test_regression_sets_union_across_sweeps(self, root):
        _triage(root, "e", [], regression={"spec_ids": ["e-1"]})
        L.merge_triage(root, run="r1")
        _triage(root, "e", [], regression={"spec_ids": ["e-2"]})
        L.merge_triage(root, run="r2")
        b = json.loads((root / "backlog.json").read_text())
        assert b["regression_sets"]["e"] == ["e-1", "e-2"]


# ── set-status / retire / queue ───────────────────────────────────────────

class TestStatusAndQueue:
    def _seed(self, root, n=3, sev="high"):
        cls = [{**CL, "signature": f"sig-{i}", "spec_ids": [f"e-{i}"],
                "suspect_files": [f"f{i}.ts"], "severity": sev} for i in range(n)]
        _triage(root, "e", cls)
        L.merge_triage(root, run="r1")

    def test_illegal_transition_refused(self, root):
        self._seed(root, 1)
        with pytest.raises(L.LedgerError):
            L.set_status(root, "D-001", "verified", run="r1")  # open -> verified skips fix-applied

    def test_verification_recorded_per_engine(self, root):
        self._seed(root, 1)
        L.set_status(root, "D-001", "fix-applied", run="r1")
        L.set_status(root, "D-001", "verified", run="r1",
                     verification={"e": {"light": "ok", "dark": "ok", "detail": "clean"}})
        d = L.show(root, defect_id="D-001")
        assert d["verification"]["e"]["dark"] == "ok"

    def test_queue_excludes_terminal_and_retired(self, root):
        self._seed(root, 3)
        L.set_status(root, "D-001", "wont-fix", run="r1", note="disproportionate")
        L.retire(root, defect_id="D-002", reason="done by hand")
        q = L.queue(root)
        assert q["live_defects"] == 1
        assert q["groups"][0]["defect_ids"] == ["D-003"]

    def test_queue_groups_by_most_specific_suspect_file(self, root):
        cls = [{**CL, "signature": "a", "spec_ids": ["e-1"], "suspect_files": ["x.ts", "hub.py"]},
               {**CL, "signature": "b", "spec_ids": ["e-2"], "suspect_files": ["x.ts", "hub.py"]},
               # names x.ts too, but y.ts is its unique witness -> its own group
               {**CL, "signature": "b2", "spec_ids": ["e-4"], "suspect_files": ["x.ts", "y.ts", "hub.py"]},
               {**CL, "signature": "c", "spec_ids": ["e-3"], "suspect_files": ["z.ts", "hub.py"]}]
        _triage(root, "e", cls)
        L.merge_triage(root, run="r1")
        q = L.queue(root)
        by_key = {g["key"]: sorted(g["defect_ids"]) for g in q["groups"]}
        assert by_key == {"file:x.ts": ["D-001", "D-002"],
                          "file:y.ts": ["D-003"],
                          "file:z.ts": ["D-004"]}

    def test_prose_suspect_files_still_group(self, root):
        cls = [{**CL, "signature": "a", "spec_ids": ["e-1"],
                "suspect_files": ["frontend/src/plugins/d3/vegaPlugin.ts (postRenderSizing() ~L1133)"]},
               {**CL, "signature": "b", "spec_ids": ["e-2"],
                "suspect_files": ["frontend/src/plugins/d3/vegaPlugin.ts :: resolveViewBox, floodFactor"]}]
        _triage(root, "e", cls)
        L.merge_triage(root, run="r1")
        q = L.queue(root)
        assert len(q["groups"]) == 1
        assert q["groups"][0]["suspect_files"] == ["frontend/src/plugins/d3/vegaPlugin.ts"]

    def test_hub_files_do_not_weld_groups(self, root):
        # 10 defects in 5 unrelated pairs, every one also naming the hub.
        # Grouping is by the RAREST suspect file, so the hub (named by
        # all ten) never wins and the pairs stay separate.
        cls = []
        for i in range(10):
            cls.append({**CL, "signature": f"s{i}", "spec_ids": [f"e-{i}"],
                        "suspect_files": [f"own{i // 2}.ts", "app/services/diagram_renderer.py"]})
        _triage(root, "e", cls)
        L.merge_triage(root, run="r1")
        q = L.queue(root)
        assert len(q["groups"]) == 5
        assert all(len(g["defect_ids"]) == 2 for g in q["groups"])
        assert all(g["key"].startswith("file:own") for g in q["groups"])

    def test_no_path_suspect_falls_back_to_signature(self, root):
        cls = [{**CL, "signature": "tex-fonts", "spec_ids": ["e-1"],
                "suspect_files": ["(TeX install / environment: TS1 companion fonts)"]},
               {**CL, "signature": "tex-fonts", "spec_ids": ["e-2"],
                "suspect_files": ["(TeX install / environment: TS1 companion fonts)"]}]
        # same signature+engine merge into ONE defect; a second distinct one
        cls.append({**CL, "signature": "other", "spec_ids": ["e-3"], "suspect_files": ["nothing here"]})
        _triage(root, "e", cls)
        L.merge_triage(root, run="r1")
        keys = {g["key"] for g in L.queue(root)["groups"]}
        assert any(k.startswith("file:") for k in keys)  # prose kept verbatim as a key
        assert len(keys) == 2

    def test_oversize_component_is_chunked_worst_first(self, root):
        cls = [{**CL, "signature": f"s{i}", "spec_ids": [f"e-{i}"], "suspect_files": ["same.ts"],
                "severity": "high" if i == 7 else "medium"} for i in range(8)]
        _triage(root, "e", cls)
        L.merge_triage(root, run="r1")
        q = L.queue(root, max_group=3)
        sizes = [len(g["defect_ids"]) for g in q["groups"]]
        assert sorted(sizes, reverse=True) == [3, 3, 2]
        # the lone high-severity defect (D-008) leads the first chunk
        first = q["groups"][0]
        assert first["severity_rank"] == 3 and "D-008" in first["defect_ids"]

    def test_group_ids_stable_across_calls(self, root):
        self._seed(root, 2)
        assert L.queue(root)["group_ids"] == L.queue(root)["group_ids"]

    def test_regressions_sort_first(self, root):
        self._seed(root, 2)
        L.set_status(root, "D-002", "fix-applied", run="r1")
        L.set_status(root, "D-002", "verified", run="r1")
        L.set_status(root, "D-002", "regression", run="r2")
        q = L.queue(root)
        assert q["groups"][0]["defect_ids"] == ["D-002"]

    def test_unretire_restores(self, root):
        self._seed(root, 1)
        L.retire(root, defect_id="D-001", reason="x")
        assert L.queue(root)["live_defects"] == 0
        L.unretire(root, defect_id="D-001", signature="sig-0")
        assert L.queue(root)["live_defects"] == 1


class TestSystemRetirement:
    """Fixed work leaves the queue because the EVIDENCE says so, not because
    someone remembered to say so."""

    def _defect_from_failing_sweep(self, root):
        # Sweep r1: the spec fails dark; triage clusters it; backlog opens D-001.
        L.record(root, "mermaid", "mermaid-w2-01", run="r1", light=OK, dark=FAIL)
        _triage(root, "mermaid", [CL])
        L.merge_triage(root, run="r1")
        assert L.queue(root)["live_defects"] == 1

    def test_reconcile_verifies_from_all_ok_evidence(self, root):
        self._defect_from_failing_sweep(root)
        L.set_status(root, "D-001", "fix-applied", run="r2")
        # Verification pass re-renders and records; nobody calls set-status.
        L.record(root, "mermaid", "mermaid-w2-01", run="r3", light=OK, dark=OK)
        stats = L.reconcile(root, run="r3")
        assert stats["verified"] == 1
        d = L.show(root, defect_id="D-001")
        assert d["status"] == "verified"
        assert d["verification"]["mermaid"]["dark"] == "ok"
        assert d["verification"]["mermaid"]["evidence_run"] == "r3"
        assert L.queue(root)["live_defects"] == 0

    def test_reconcile_is_idempotent(self, root):
        self._defect_from_failing_sweep(root)
        L.record(root, "mermaid", "mermaid-w2-01", run="r3", light=OK, dark=OK)
        L.reconcile(root, run="r3")
        n_hist = len(L.show(root, defect_id="D-001")["history"])
        stats = L.reconcile(root, run="r3")
        assert stats["verified"] == 0 and stats["unchanged"] == 1
        assert len(L.show(root, defect_id="D-001")["history"]) == n_hist

    def test_reconcile_verifies_directly_from_open(self, root):
        # An open defect fixed as a side effect of another group still closes.
        self._defect_from_failing_sweep(root)
        L.record(root, "mermaid", "mermaid-w2-01", run="r3", light=OK, dark=OK)
        assert L.reconcile(root, run="r3")["verified"] == 1

    def test_reconcile_needs_both_themes(self, root):
        self._defect_from_failing_sweep(root)
        L.record(root, "mermaid", "mermaid-w2-01", run="r3", light=OK, dark=FAIL)
        stats = L.reconcile(root, run="r3")
        assert stats["verified"] == 0
        assert L.show(root, defect_id="D-001")["status"] == "open"

    def test_reconcile_leaves_incomplete_evidence_alone(self, root):
        # Defect names two specs; only one has been re-rendered.
        L.record(root, "mermaid", "mermaid-w2-01", run="r1", light=OK, dark=FAIL)
        cl = {**CL, "spec_ids": ["mermaid-w2-01", "mermaid-w2-02"]}
        _triage(root, "mermaid", [cl])
        L.merge_triage(root, run="r1")
        L.record(root, "mermaid", "mermaid-w2-01", run="r3", light=OK, dark=OK)
        stats = L.reconcile(root, run="r3")
        assert stats["incomplete"] == 1 and stats["verified"] == 0

    def test_reconcile_regresses_verified_on_new_failure(self, root):
        self._defect_from_failing_sweep(root)
        L.record(root, "mermaid", "mermaid-w2-01", run="r3", light=OK, dark=OK)
        L.reconcile(root, run="r3")
        # A later sweep finds it broken again.
        L.record(root, "mermaid", "mermaid-w2-01", run="r4", light=OK, dark=FAIL)
        stats = L.reconcile(root, run="r4")
        assert stats["regressed"] == 1
        d = L.show(root, defect_id="D-001")
        assert d["status"] == "regression" and d["severity"] == "high"
        q = L.queue(root)
        assert q["live_defects"] == 1
        assert q["groups"][0]["regressions"] == ["D-001"]

    def test_reconcile_marks_still_broken_from_newer_failing_evidence(self, root):
        self._defect_from_failing_sweep(root)
        L.set_status(root, "D-001", "fix-applied", run="r2")
        # The sweep evidence that FOUND the defect predates the fix: not proof
        # the fix failed.
        assert L.reconcile(root, run="r2")["still_broken"] == 0
        assert L.show(root, defect_id="D-001")["status"] == "fix-applied"
        # A re-render after the fix that still fails IS proof.
        L.record(root, "mermaid", "mermaid-w2-01", run="r3", light=OK, dark=FAIL)
        stats = L.reconcile(root, run="r3")
        assert stats["still_broken"] == 1
        d = L.show(root, defect_id="D-001")
        assert d["status"] == "still-broken"
        assert d["verification"]["mermaid"]["dark"] == "fail"
        assert L.queue(root)["live_defects"] == 1

    def test_wont_fix_auto_retires_signature(self, root):
        self._defect_from_failing_sweep(root)
        L.set_status(root, "D-001", "wont-fix", run="r2", note="disproportionate")
        retired = json.loads((root / "retired.json").read_text())
        assert retired["signatures"][CL["signature"]]["by"] == "system"
        assert retired["signatures"][CL["signature"]]["reason"] == "disproportionate"
        assert retired["defect_ids"]["D-001"]["signature"] == CL["signature"]
        assert L.queue(root)["live_defects"] == 0

    def test_wont_fix_reappearance_is_logged_not_reminted(self, root):
        self._defect_from_failing_sweep(root)
        L.set_status(root, "D-001", "wont-fix", run="r2", note="n")
        # Next sweep's triage produces the same cluster again.
        _triage(root, "mermaid", [CL])
        stats = L.merge_triage(root, run="r3")
        assert stats["new"] == 0 and stats["retired_reappearances"] == 1
        b = json.loads((root / "backlog.json").read_text())
        assert len(b["defects"]) == 1 and b["defects"][0]["status"] == "wont-fix"
        assert L.queue(root)["live_defects"] == 0

    def test_deferred_is_not_reconciled(self, root):
        L.record(root, "mermaid", "mermaid-w2-01", run="r1", light=OK, dark=FAIL)
        _triage(root, "mermaid", [{**CL, "severity": "low"}])
        L.merge_triage(root, run="r1")
        assert L.show(root, defect_id="D-001")["status"] == "deferred"
        L.record(root, "mermaid", "mermaid-w2-01", run="r3", light=OK, dark=OK)
        stats = L.reconcile(root, run="r3")
        assert stats["checked"] == 0
        assert L.show(root, defect_id="D-001")["status"] == "deferred"


class TestCLI:
    def test_reconcile_cli(self, root, capsys):
        rc = L.main(["--root", str(root), "reconcile"])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["checked"] == 0

    def test_record_and_queue_roundtrip(self, root, capsys):
        rc = L.main(["--root", str(root), "--run", "r1", "record", "e", "e-1",
                     "--light", json.dumps(OK), "--dark", json.dumps(FAIL), "--wave", "1"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["current"]["dark"]["status"] == "fail"

    def test_error_exit_code(self, root, capsys):
        rc = L.main(["--root", str(root), "set-status", "D-999", "open"])
        assert rc == 2
        assert "no defect" in capsys.readouterr().err
