"""
``release_plan.py batches``: every unclaimed path without a triage verdict
lands in exactly one batch, and a batch's id comes from its contents.

Field case (run cli-81921cb9b5eb, 2026-10-10): the model-built batching
dropped 25 unclaimed paths and put 8 in two batches, so the commit-plan gate
refused; and batch files were named by position, so a rerun with fewer
batches would overwrite the previous run's verdicts.
"""

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "release_plan", Path(__file__).resolve().parents[1] / "scripts" / "release_plan.py")
rp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rp)


def _setup(tmp_path, unclaimed, verdicts=None):
    (tmp_path / "ledger.json").write_text(json.dumps({"entry_index": {"unclaimed": unclaimed}}))
    tri = tmp_path / "triage"
    tri.mkdir()
    for name, vs in (verdicts or {}).items():
        (tri / name).write_text(json.dumps({"verdicts": vs}))
    return tri


def _run(tmp_path, capsys, size=15):
    assert rp.main(["batches", "--root", str(tmp_path), "--ledger", "ledger.json",
                    "--triage-dir", "triage", "--size", str(size)]) == 0
    return json.loads(capsys.readouterr().out)


def test_every_path_is_batched_exactly_once(tmp_path, capsys):
    paths = [f"d{i % 4}/f{i}.py" for i in range(40)]
    _setup(tmp_path, paths)
    out = _run(tmp_path, capsys)
    flat = [p for b in out["batches"] for p in b["paths"]]
    assert sorted(flat) == sorted(paths) and len(flat) == len(set(flat))
    assert all(len(b["paths"]) <= 15 for b in out["batches"])
    assert out["pending"] == 40 and out["triaged"] == 0


def test_already_triaged_paths_are_skipped(tmp_path, capsys):
    paths = ["a/1.py", "a/2.py", "b/3.py"]
    _setup(tmp_path, paths, {"batch-x.json": [
        {"path": "a/1.py", "disposition": "exclude", "reason": "wip"},
        {"path": "b/3.py", "disposition": "maybe"}]})        # not a verdict
    out = _run(tmp_path, capsys)
    assert [p for b in out["batches"] for p in b["paths"]] == ["a/2.py", "b/3.py"]
    assert out["triaged"] == 1


def test_batch_ids_follow_contents_not_position(tmp_path, capsys):
    _setup(tmp_path, ["a/1.py", "b/2.py"])
    one = _run(tmp_path, capsys, size=1)["batches"]
    assert len({b["id"] for b in one}) == 2
    (tmp_path / "ledger.json").write_text(json.dumps({"entry_index": {"unclaimed": ["b/2.py"]}}))
    again = _run(tmp_path, capsys, size=1)["batches"]
    assert again[0]["id"] == [b for b in one if b["paths"] == ["b/2.py"]][0]["id"]


def test_exclude_on_a_tracked_modification_is_rebatched(tmp_path, capsys):
    """Run cli-d1103758c5d8 (2026-10-13): 54 tracked modifications carried
    'exclude' verdicts.  The plan builder defers those as invalid, but
    batches counted them triaged, so no rerun ever re-triaged them."""
    (tmp_path / "ledger.json").write_text(json.dumps({"entry_index": {
        "unclaimed": ["t/mod.py", "u/new.py", "t/ok.py"],
        "untracked": ["u/new.py"]}}))
    tri = tmp_path / "triage"
    tri.mkdir()
    (tri / "batch-a.json").write_text(json.dumps({"verdicts": [
        {"path": "t/mod.py", "disposition": "exclude", "reason": "wip"},
        {"path": "u/new.py", "disposition": "exclude", "reason": "scratch"},
        {"path": "t/ok.py", "disposition": "assign", "entry_id": "e1"}]}))
    out = _run(tmp_path, capsys)
    assert [p for b in out["batches"] for p in b["paths"]] == ["t/mod.py"]
    assert out["rejected_excludes"] == ["t/mod.py"]
    assert out["triaged"] == 2      # the untracked exclude and the assign stand


def test_assign_to_an_entry_not_in_the_index_is_rebatched(tmp_path, capsys):
    """Run cli-188224db41e6 (2026-10-31): prior-release batches, and batches
    written before CHANGELOG.md was rewritten, assigned paths to entry ids
    the current Unreleased section no longer has.  The plan builder deferred
    all 69, but batches counted them triaged, so no stage re-triaged them."""
    (tmp_path / "ledger.json").write_text(json.dumps({"entry_index": {
        "unclaimed": ["a/old.py", "a/cur.py", "a/new.py"],
        "untracked": [],
        "entries": [{"entry_id": "live-entry"}]}}))
    tri = tmp_path / "triage"
    tri.mkdir()
    (tri / "batch-old.json").write_text(json.dumps({"verdicts": [
        {"path": "a/old.py", "disposition": "assign", "entry_id": "gone-entry"},
        {"path": "a/cur.py", "disposition": "assign", "entry_id": "live-entry"},
        {"path": "a/new.py", "disposition": "new_entry", "draft_entry": "x"}]}))
    out = _run(tmp_path, capsys)
    assert [p for b in out["batches"] for p in b["paths"]] == ["a/old.py"]
    assert out["stale_assigns"] == ["a/old.py"]
    assert out["triaged"] == 2      # the live assign and the new_entry stand


def test_a_later_valid_assign_wins_over_a_stale_one(tmp_path, capsys):
    (tmp_path / "ledger.json").write_text(json.dumps({"entry_index": {
        "unclaimed": ["a/x.py"], "entries": [{"entry_id": "live"}]}}))
    tri = tmp_path / "triage"
    tri.mkdir()
    (tri / "batch-1.json").write_text(json.dumps({"verdicts": [
        {"path": "a/x.py", "disposition": "assign", "entry_id": "gone"}]}))
    (tri / "batch-2.json").write_text(json.dumps({"verdicts": [
        {"path": "a/x.py", "disposition": "assign", "entry_id": "live"}]}))
    out = _run(tmp_path, capsys)
    assert out["batches"] == [] and out["stale_assigns"] == [] and out["triaged"] == 1


def test_nothing_pending_gives_an_empty_list(tmp_path, capsys):
    _setup(tmp_path, ["a/1.py"], {"batch-q.json": [{"path": "a/1.py", "disposition": "assign"}]})
    assert _run(tmp_path, capsys)["batches"] == []


# -- consumed verdicts (runs cli-600cd44c2821 / cli-97476739fd29, 2026-11-18) --
# batches counted these triaged while the commit plan deferred them, so the
# sweep stopped at the release gate with the same 11 deferrals every rerun.

import os
import subprocess


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _commit(root, path, text, when):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    _git(root, "add", path)
    env = {**os.environ, "GIT_AUTHOR_DATE": f"@{when} +0000", "GIT_COMMITTER_DATE": f"@{when} +0000"}
    subprocess.run(["git", "commit", "-q", "-m", path], cwd=root, check=True, capture_output=True, env=env)


def _verdicts(root, name, vs, mtime):
    tri = root / "triage"
    tri.mkdir(exist_ok=True)
    f = tri / name
    f.write_text(json.dumps({"verdicts": vs}))
    os.utime(f, (mtime, mtime))


def test_assign_to_a_committed_entry_is_rebatched(tmp_path, capsys):
    (tmp_path / "ledger.json").write_text(json.dumps({
        "groups_done": ["done-entry"],
        "entry_index": {"unclaimed": ["a/x.py", "a/y.py"], "untracked": [],
                        "entries": [{"entry_id": "done-entry"}, {"entry_id": "open-entry"}]}}))
    _verdicts(tmp_path, "batch-1.json", [
        {"path": "a/x.py", "disposition": "assign", "entry_id": "done-entry"},
        {"path": "a/y.py", "disposition": "assign", "entry_id": "open-entry"}], 1_000)
    out = _run(tmp_path, capsys)
    assert [p for b in out["batches"] for p in b["paths"]] == ["a/x.py"]
    assert out["stale_assigns"] == ["a/x.py"] and out["triaged"] == 1   # open-entry's stands


def test_a_verdict_older_than_the_paths_last_commit_is_rebatched(tmp_path, capsys):
    root = _repo(tmp_path)
    _commit(root, "docs/later.md", "v1", 2_000)       # committed after the verdict
    _commit(root, "docs/earlier.md", "v1", 500)       # committed before it (control)
    (root / "ledger.json").write_text(json.dumps({"entry_index": {
        "unclaimed": ["docs/later.md", "docs/earlier.md"], "untracked": [], "entries": []}}))
    _verdicts(root, "batch-1.json", [
        {"path": "docs/later.md", "disposition": "new_entry", "draft_entry": "x"},
        {"path": "docs/earlier.md", "disposition": "new_entry", "draft_entry": "y"}], 1_000)
    out = _run(root, capsys)
    assert [p for b in out["batches"] for p in b["paths"]] == ["docs/later.md"]
    assert out["stale_assigns"] == ["docs/later.md"] and out["triaged"] == 1


def test_a_fresh_verdict_after_the_commit_counts_again(tmp_path, capsys):
    root = _repo(tmp_path)
    _commit(root, "docs/a.md", "v1", 2_000)
    (root / "ledger.json").write_text(json.dumps({"entry_index": {
        "unclaimed": ["docs/a.md"], "untracked": [], "entries": []}}))
    _verdicts(root, "batch-old.json", [{"path": "docs/a.md", "disposition": "new_entry"}], 1_000)
    _verdicts(root, "batch-new.json", [{"path": "docs/a.md", "disposition": "new_entry"}], 3_000)
    out = _run(root, capsys)
    assert out["batches"] == [] and out["stale_assigns"] == [] and out["triaged"] == 1


def test_exclude_on_an_untracked_file_tracked_code_imports_is_rebatched(tmp_path, capsys):
    root = _repo(tmp_path)
    _commit(root, "src/components/View.tsx", "import { r } from '../utils/references';\n", 500)
    for p in ("src/utils/references.ts", "scratch/probe.mjs"):
        (root / p).parent.mkdir(parents=True, exist_ok=True)
        (root / p).write_text("x")
    untracked = ["scratch/probe.mjs", "src/utils/references.ts"]
    (root / "ledger.json").write_text(json.dumps({"entry_index": {
        "unclaimed": untracked, "untracked": untracked, "entries": []}}))
    _verdicts(root, "batch-1.json", [
        {"path": p, "disposition": "exclude", "reason": "wip"} for p in untracked], 1_000)
    out = _run(root, capsys)
    assert [p for b in out["batches"] for p in b["paths"]] == ["src/utils/references.ts"]
    assert out["rejected_excludes"] == ["src/utils/references.ts"]
    assert out["triaged"] == 1          # the unreferenced scratch exclude stands


def test_reserved_release_metadata_is_never_batched(tmp_path, capsys):
    _setup(tmp_path, ["CHANGELOG.md", "pyproject.toml", "frontend/package.json", "a/x.py"])
    assert [p for b in _run(tmp_path, capsys)["batches"] for p in b["paths"]] == ["a/x.py"]


def test_a_committed_entrys_citation_no_longer_claims_a_dirty_path(tmp_path):
    root = _repo(tmp_path)
    _commit(root, "src/plot.ts", "v1", 500)
    _commit(root, "src/open.ts", "v1", 500)
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n"
        "- **Shipped thing.** Touches `src/plot.ts`.\n"
        "- **Open thing.** Touches `src/open.ts`.\n\n## [0.1.0]\n")
    (root / "src/plot.ts").write_text("v2")
    (root / "src/open.ts").write_text("v2")
    ix = rp.build_index(str(root), committed=["shipped-thing"])
    assert "src/plot.ts" in ix["unclaimed"] and "src/plot.ts" not in ix["claimed"]
    assert ix["claimed"].get("src/open.ts") == "open-thing"      # an open entry still claims
    assert rp.build_index(str(root))["claimed"].get("src/plot.ts") == "shipped-thing"
