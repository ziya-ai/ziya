"""
``release_hunks.py reconcile``: which contended files an interrupted sweep
must re-attribute, and quarantine of artifacts nothing currently vouches for.

Field case (run cli-4da9fec1b70e, 2026-10-10): the P2b fan-out died with
EMFILE after 83 of 130 files; a rerun must redo only the other 47, and must
not let patches from earlier runs (renamed entries, already-committed hunks)
reach the commit plan.  Real git against a throwaway repository, driven
through the CLI entry point.
"""

import json
import os
import subprocess

import pytest

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "release_hunks", Path(__file__).resolve().parents[1] / "scripts" / "release_hunks.py")
rh = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rh)

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not available")


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.name", "T"); _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "commit.gpgsign", "false")
    for name in ("a.py", "b.py", "c.py"):
        (r / name).write_text("".join(f"line{i}\n" for i in range(40)))
    _git(r, "add", "-A"); _git(r, "commit", "-qm", "init")
    for name in ("a.py", "b.py", "c.py"):
        lines = [f"line{i}\n" for i in range(40)]
        lines[2], lines[35] = "TOP\n", "BOTTOM\n"   # two hunks per file
        (r / name).write_text("".join(lines))
    sweep = r / ".ziya" / "sweep"
    (sweep / "attrib").mkdir(parents=True)
    (sweep / "patches").mkdir()
    (sweep / "ledger.json").write_text(json.dumps({"entry_index": {
        "entries": [{"entry_id": "e1"}, {"entry_id": "e2"}],
        "contended": ["a.py", "b.py", "c.py"],
    }}))
    return r


def _attrib_and_split(repo, path, mapping):
    a = repo / ".ziya/sweep/attrib" / (rh.flattened(path) + ".json")
    a.write_text(json.dumps(mapping))
    assert rh.do_split(str(repo), path, str(a), str(repo / ".ziya/sweep/patches")) == 0


def _reconcile(repo, capsys, quarantine=None):
    args = ["--root", str(repo), "reconcile", "--ledger", ".ziya/sweep/ledger.json",
            "--attrib-dir", ".ziya/sweep/attrib", "--out", ".ziya/sweep/patches"]
    if quarantine:
        args += ["--quarantine", quarantine]
    assert rh.main(args) == 0
    return json.loads(capsys.readouterr().out)


def test_only_unfinished_files_are_pending(repo, capsys):
    _attrib_and_split(repo, "a.py", {"0": "e1", "1": "e2"})
    rh.do_merge(str(repo), "b.py", "e1,e2", "both entries edit the same function body",
                str(repo / ".ziya/sweep/patches"))
    capsys.readouterr()
    out = _reconcile(repo, capsys)
    assert out["pending"] == ["c.py"] and out["done"] == 2 and out["stale"] == []


def test_fresh_sweep_has_everything_pending(repo, capsys):
    out = _reconcile(repo, capsys)
    assert out["pending"] == ["a.py", "b.py", "c.py"] and out["done"] == 0


def test_attribution_to_a_renamed_entry_is_redone_and_quarantined(repo, capsys):
    _attrib_and_split(repo, "a.py", {"0": "e1", "1": "old-slug"})
    capsys.readouterr()
    out = _reconcile(repo, capsys, quarantine=".ziya/sweep/stale")
    assert "a.py" in out["pending"]
    assert not (repo / ".ziya/sweep/patches/old-slug/a.py.patch").exists()
    assert not (repo / ".ziya/sweep/patches/e1/a.py.patch").exists()
    assert (repo / ".ziya/sweep/stale/patches/old-slug/a.py.patch").exists()
    assert (repo / ".ziya/sweep/stale/attrib/a.py.json").exists()


def test_a_patch_that_no_longer_applies_makes_the_file_pending(repo, capsys):
    _attrib_and_split(repo, "a.py", {"0": "e1", "1": "e2"})
    _git(repo, "add", "a.py"); _git(repo, "commit", "-qm", "landed")
    (repo / "a.py").write_text((repo / "a.py").read_text().replace("TOP", "TOP2"))
    capsys.readouterr()
    out = _reconcile(repo, capsys)
    assert "a.py" in out["pending"]


def test_hunk_count_drift_makes_the_file_pending(repo, capsys):
    _attrib_and_split(repo, "a.py", {"0": "e1", "1": "e2"})
    lines = (repo / "a.py").read_text().splitlines(True)
    lines[18] = "MIDDLE\n"                                  # third hunk appears
    (repo / "a.py").write_text("".join(lines))
    capsys.readouterr()
    assert "a.py" in _reconcile(repo, capsys)["pending"]


def test_an_edit_inside_an_existing_hunk_makes_the_file_pending(repo, capsys):
    """Run cli-d1103758c5d8 (2026-10-13): four contended files were edited
    after their split, inside existing hunks.  Hunk count unchanged, and the
    old patches still passed ``git apply --cached --check`` (the index is
    HEAD, which the edit did not touch), so reconcile counted them done.
    Their patches covered a subset of the diff; the plan then dropped 31
    groups depending on them and ~300 paths stayed dirty."""
    _attrib_and_split(repo, "a.py", {"0": "e1", "1": "e2"})
    capsys.readouterr()
    assert _reconcile(repo, capsys)["pending"] == ["b.py", "c.py"]   # path runs
    lines = (repo / "a.py").read_text().splitlines(True)
    lines[3] = "ADDED-NEXT-TO-TOP\n"            # merges into hunk 0
    (repo / "a.py").write_text("".join(lines))
    _, hunks = rh.split_hunks(rh.raw_diff(str(repo), "a.py"))
    assert len(hunks) == 2                       # the count check cannot see it
    for e in ("e1", "e2"):                       # and neither can --check
        assert not rh.check_patches(str(repo), {
            e: str(repo / ".ziya/sweep/patches" / e / "a.py.patch")})
    assert "a.py" in _reconcile(repo, capsys)["pending"]


def test_leftovers_for_uncontended_files_are_quarantined_and_done_artifacts_kept(repo, capsys):
    _attrib_and_split(repo, "a.py", {"0": "e1", "1": "e2"})
    leftover = repo / ".ziya/sweep/patches/e1/z.py.patch"
    leftover.write_text("stale")
    (repo / ".ziya/sweep/attrib/z.py.json").write_text("{}")
    capsys.readouterr()
    out = _reconcile(repo, capsys, quarantine=".ziya/sweep/stale")
    assert out["done"] == 1
    assert (repo / ".ziya/sweep/patches/e1/a.py.patch").exists()
    assert (repo / ".ziya/sweep/patches/e2/a.py.patch").exists()
    assert (repo / ".ziya/sweep/attrib/a.py.json").exists()
    assert not leftover.exists()
    assert (repo / ".ziya/sweep/stale/patches/e1/z.py.patch").exists()
    assert (repo / ".ziya/sweep/stale/attrib/z.py.json").exists()


def _contend(repo, *paths):
    led = repo / ".ziya/sweep/ledger.json"
    d = json.loads(led.read_text())
    d["entry_index"]["contended"] += list(paths)
    led.write_text(json.dumps(d))


def test_untracked_file_mapped_to_one_entry_ships_whole(repo, capsys):
    # Run cli-81921cb9b5eb: split refused the new test file, the agent wrote
    # {"0": entry} anyway, and reconcile kept it pending on every rerun.
    (repo / "new.py").write_text("x = 1\n")
    _contend(repo, "new.py")
    a = repo / ".ziya/sweep/attrib/new.py.json"
    a.write_text(json.dumps({"0": "e1"}))
    assert rh.do_split(str(repo), "new.py", str(a), str(repo / ".ziya/sweep/patches")) == 0
    assert "ships with e1" in capsys.readouterr().out
    assert not (repo / ".ziya/sweep/patches/e1/new.py.patch").exists()
    out = _reconcile(repo, capsys, quarantine=".ziya/sweep/stale")
    assert "new.py" not in out["pending"]
    assert a.exists(), "the whole-file attribution was quarantined"


def test_untracked_file_split_between_two_entries_is_refused(repo, capsys):
    (repo / "new.py").write_text("x = 1\n")
    a = repo / ".ziya/sweep/attrib/new.py.json"
    a.write_text(json.dumps({"0": "e1", "1": "e2"}))
    assert rh.do_split(str(repo), "new.py", str(a), str(repo / ".ziya/sweep/patches")) == 1
    assert "exactly one" in capsys.readouterr().out


def test_a_new_entry_hunk_counts_only_with_its_draft(repo, capsys):
    # Run cli-81921cb9b5eb: hunks no entry described got invented slugs, which
    # reconcile could never accept, so those files were redone every rerun.
    _attrib_and_split(repo, "a.py", {"0": "e1", "1": "new:datasets-in-charts"})
    capsys.readouterr()
    assert "a.py" in _reconcile(repo, capsys)["pending"]
    drafts = repo / ".ziya/sweep/attrib/a.py.drafts.json"
    drafts.write_text(json.dumps({"new:datasets-in-charts": {
        "category": "Added", "draft_entry": "**Charts resolve dataset ids**"}}))
    out = _reconcile(repo, capsys, quarantine=".ziya/sweep/stale")
    assert "a.py" not in out["pending"]
    assert drafts.exists() and (repo / ".ziya/sweep/patches/new:datasets-in-charts/a.py.patch").exists()


def test_merge_naming_an_unknown_entry_is_pending(repo, capsys):
    rh.do_merge(str(repo), "b.py", "e1,gone", "both entries edit the same function body",
                str(repo / ".ziya/sweep/patches"))
    capsys.readouterr()
    assert "b.py" in _reconcile(repo, capsys)["pending"]
