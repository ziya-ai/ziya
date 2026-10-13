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


def test_nothing_pending_gives_an_empty_list(tmp_path, capsys):
    _setup(tmp_path, ["a/1.py"], {"batch-q.json": [{"path": "a/1.py", "disposition": "assign"}]})
    assert _run(tmp_path, capsys)["batches"] == []
