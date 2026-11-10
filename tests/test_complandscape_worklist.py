"""``complandscape_reintegration.py worklist``: the refresh worklist as a
set operation over git and the prior records, not a model task.

Two model-authored worklists in a row were wrong (2026-11-05): one selected
127 of 128 records as fresh because every changed file counted as evidence;
the continuation dropped two real items by asserting file state it had not
checked.  Pinned here against a throwaway repo: a record is fresh when a
cited evidence path (or a file under a cited directory) changed since the
base date or the capability is on the seed list; everything else is copied
forward with ``carried_forward`` set and every other field byte-identical.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "complandscape_reintegration.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("complandscape_reintegration", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["complandscape_reintegration"] = m
    spec.loader.exec_module(m)
    return m


def _git(repo, *args, env=None):
    e = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
         "GIT_COMMITTER_EMAIL": "t@x", **(env or {})}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=e)


def _record(cid, paths, verdict="PARTIAL"):
    return {"capability_id": cid, "verdict": verdict, "searched": ["x"],
            "evidence": [{"path": p, "lines": "1-2", "note": "n"} for p in paths],
            "nearest_subsystem": "app", "missing_behaviors": ["m"], "as_of": "2026-09-01"}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "app").mkdir(); (r / "app" / "sub").mkdir()
    for f in ("app/a.py", "app/b.py", "app/sub/c.py", "app/untouched.py"):
        (r / f).write_text("v1\n")
    _git(r, "init", "-q")
    _git(r, "add", "."); _git(r, "commit", "-q", "-m", "base",
                               env={"GIT_AUTHOR_DATE": "2026-08-20T00:00:00", "GIT_COMMITTER_DATE": "2026-08-20T00:00:00"})
    (r / "app" / "a.py").write_text("v2\n")           # exact-path change
    (r / "app" / "sub" / "c.py").write_text("v2\n")   # under a cited directory
    _git(r, "add", "."); _git(r, "commit", "-q", "-m", "later",
                               env={"GIT_AUTHOR_DATE": "2026-10-01T00:00:00", "GIT_COMMITTER_DATE": "2026-10-01T00:00:00"})
    (r / "app" / "new_untracked.py").write_text("x\n")
    prior = tmp_path / "r1-20260901"
    prior.mkdir()
    for cid, paths in (("exact", ["app/a.py:10-20"]), ("dir", ["app/sub/"]),
                       ("quiet", ["app/b.py"]), ("seeded", ["app/untouched.py"]),
                       ("untracked", ["app/new_untracked.py"])):
        (prior / f"{cid}-stageA.json").write_text(json.dumps(_record(cid, paths)))
        (prior / f"{cid}-disposition.json").write_text(json.dumps(
            {"capability_id": cid, "stage_a_verdict": "PARTIAL", "disposition": "BUILD_CANDIDATE"}))
    return r, prior


def test_fresh_is_changed_evidence_or_seed_everything_else_carries(mod, repo):
    r, prior = repo
    wl = mod.build_worklist(str(prior), "2026-09-01", seed=["seeded", "exact"], repo_root=str(r))
    by = {f["cid"]: f for f in wl["fresh"]}
    assert set(by) == {"exact", "dir", "seeded", "untracked"}
    assert by["exact"]["reason"] == "both" and by["exact"]["changed_paths"] == ["app/a.py"]
    assert by["dir"]["reason"] == "evidence_changed" and by["dir"]["changed_paths"] == ["app/sub/c.py"]
    assert by["seeded"]["reason"] == "seed" and by["seeded"]["changed_paths"] == []
    assert by["untracked"]["reason"] == "evidence_changed"
    assert wl["carried"] == ["quiet"]
    assert wl["since"] == "2026-09-01" and wl["changed_file_count"] == 3


def test_write_copies_carried_pairs_byte_identical_plus_flags(mod, repo, tmp_path):
    r, prior = repo
    wl = mod.build_worklist(str(prior), "2026-09-01", repo_root=str(r))
    run = tmp_path / "r2"
    n = mod.write_worklist(wl, str(prior), str(run), "r1-20260901")
    carried = set(wl["carried"])
    assert n == 2 * len(carried)
    for cid in carried:
        for suffix in ("-stageA.json", "-disposition.json"):
            src = json.loads((prior / f"{cid}{suffix}").read_text())
            dst = json.loads((run / f"{cid}{suffix}").read_text())
            assert dst.pop("carried_forward") is True and dst.pop("carried_from") == "r1-20260901"
            assert dst == src
    # Fresh cids get nothing: their lanes write new records.
    assert not any((run / f"{f['cid']}-stageA.json").exists() for f in wl["fresh"])
    assert json.loads((run / "00-worklist.json").read_text())["carried"] == wl["carried"]


def test_cli_infers_since_from_the_prior_dir_name(mod, repo, tmp_path, monkeypatch):
    r, prior = repo
    monkeypatch.chdir(r)
    rc = mod.main(["worklist", str(tmp_path / "r2"), "--prior", str(prior), "--seed", "seeded"])
    assert rc == 0
    rc = mod.main(["worklist", str(tmp_path / "r2"), "--prior", str(tmp_path / "undated")])
    assert rc == 2
