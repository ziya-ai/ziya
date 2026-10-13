"""
Tests for the release-sweep tooling: ``scripts/release_plan.py`` (entry /
path index) and ``scripts/release_hunks.py`` (per-entry patch splitting).

Why this file exists
--------------------
The v0.8.9.0 sweep stalled because the only hunk-splitting tooling in the
tree was per-release scratch: ``release_plan.py`` hardcoded the previous
release's target version, an exclusion list naming that release's files and
~80 affinity rules keyed on changelog ENTRY INDEX (which shifts whenever an
entry is added mid-sweep).  Run against a later tree it would have assigned
the wrong hunks to the wrong entries, so the agent correctly refused it and
then had no non-interactive way to split a file claimed by eleven entries.

What is pinned here
-------------------
Both scripts are now release-agnostic.  The index derives everything from
``CHANGELOG.md`` and ``git status``; the splitter turns a hunk->entry mapping
into patches and validates each one against the real index with
``git apply --cached --check``.  The tests build a scratch repository so
the seam that matters -- a produced patch actually staging exactly its
hunks and nothing else -- is asserted against git, not against the parser.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "scripts" / "release_plan.py"
HUNKS = REPO / "scripts" / "release_hunks.py"

CHANGELOG = """# Changelog

## [Unreleased]

### Added
- **Widget gains a frobnicate mode** (`src/widget.py`, `src/new_helper.py` (new)) — body text.
- **Config loader honours `{src/a,src/b}.cfg` overrides** (`src/{a,b}.cfg`) — body.

### Fixed
- **Widget no longer double-counts on reset** (`src/widget.py`, `tests/test_widget.py`) — body.
- **Stale entry whose work already shipped** (`src/shipped.py`) — body.
- Entry with no bold lead and no cited files.

## [0.1.0] - 2026-01-01

### Added
- **Published entry** (`src/widget.py`) — must never be read.
"""

WIDGET_BEFORE = "\n".join(f"line {i}" for i in range(1, 41)) + "\n"


def run(args, cwd, check=True):
    r = subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"{args}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}")
    return r


def git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    """A repository with: a contended file (two separate hunks, two entries),
    a claimed file, an untracked file, an unclaimed modification, a cited
    brace-glob pair, and a cited path that is not dirty."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    git(["init", "-q", "-b", "main"], root)
    git(["config", "user.email", "t@example.com"], root)
    git(["config", "user.name", "t"], root)
    (root / "CHANGELOG.md").write_text(CHANGELOG)
    (root / "src" / "widget.py").write_text(WIDGET_BEFORE)
    (root / "src" / "a.cfg").write_text("a=1\n")
    (root / "src" / "b.cfg").write_text("b=1\n")
    (root / "src" / "shipped.py").write_text("x = 1\n")
    (root / "src" / "orphan.py").write_text("y = 1\n")
    (root / "tests" / "test_widget.py").write_text("def test(): pass\n")
    git(["add", "-A"], root)
    git(["commit", "-q", "-m", "base"], root)

    # Contended: hunk 0 near the top (frobnicate), hunk 1 near the bottom (reset).
    lines = WIDGET_BEFORE.split("\n")
    lines[2] = "line 3  # frobnicate mode"
    lines[36] = "line 37  # reset fix"
    (root / "src" / "widget.py").write_text("\n".join(lines))
    # Claimed by exactly one entry.
    (root / "tests" / "test_widget.py").write_text("def test(): assert True\n")
    # Brace-glob citation.
    (root / "src" / "a.cfg").write_text("a=2\n")
    (root / "src" / "b.cfg").write_text("b=2\n")
    # Untracked, cited as (new).
    (root / "src" / "new_helper.py").write_text("z = 1\n")
    # Changed but cited by nobody.
    (root / "src" / "orphan.py").write_text("y = 2\n")
    return root


# ------------------------------------------------------------- release_plan

def index(root):
    r = run([str(PLAN), "index", "--json"], cwd=root)
    return json.loads(r.stdout)


def test_index_reads_only_the_unreleased_section(repo):
    ix = index(repo)
    titles = [e["title"] for e in ix["entries"]]
    assert "Published entry" not in titles
    assert ix["counts"]["entries"] == 5


def test_entry_ids_are_stable_slugs_of_the_lead(repo):
    ix = index(repo)
    ids = [e["entry_id"] for e in ix["entries"]]
    assert ids[0] == "widget-gains-a-frobnicate-mode"
    assert ids[2] == "widget-no-longer-double-counts-on-reset"
    assert len(set(ids)) == len(ids), "entry ids must be unique: the commit loop keys on them"
    # Running twice yields the same ids -- a resume depends on it.
    assert ids == [e["entry_id"] for e in index(repo)["entries"]]


def test_partition_is_exhaustive_and_exclusive(repo):
    ix = index(repo)
    assert ix["contended"] == {
        "src/widget.py": ["widget-gains-a-frobnicate-mode",
                          "widget-no-longer-double-counts-on-reset"],
    }
    assert ix["claimed"]["tests/test_widget.py"] == "widget-no-longer-double-counts-on-reset"
    assert ix["claimed"]["src/new_helper.py"] == "widget-gains-a-frobnicate-mode"
    assert ix["unclaimed"] == ["src/orphan.py"]
    buckets = set(ix["claimed"]) | set(ix["contended"]) | set(ix["unclaimed"])
    assert len(buckets) == ix["counts"]["dirty"], "every dirty path is in exactly one bucket"
    assert not (set(ix["claimed"]) & set(ix["contended"]))


def test_brace_globs_in_citations_are_expanded(repo):
    ix = index(repo)
    assert ix["claimed"]["src/a.cfg"] == "config-loader-honours-src-a-src-b-cfg-overrides"
    assert ix["claimed"]["src/b.cfg"] == "config-loader-honours-src-a-src-b-cfg-overrides"


def test_stale_citations_are_reported_not_silently_dropped(repo):
    ix = index(repo)
    dropped = {(d["entry_id"], d["path"]) for d in ix["dropped_citations"]}
    assert ("stale-entry-whose-work-already-shipped", "src/shipped.py") in dropped
    assert "stale-entry-whose-work-already-shipped" in ix["entries_without_live_paths"]
    assert ix["entries_without_cited_paths"] == ["entry-with-no-bold-lead-and-no-cited-files"]


def test_index_merges_into_the_ledger_preserving_other_keys(repo):
    ledger = repo / ".ziya" / "sweep" / "ledger.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"mode": "fresh", "release": {"target_version": "9.9.9"}}))
    run([str(PLAN), "index", "--ledger", ".ziya/sweep/ledger.json"], cwd=repo)
    data = json.loads(ledger.read_text())
    assert data["mode"] == "fresh" and data["release"]["target_version"] == "9.9.9"
    assert data["entry_index"]["counts"]["contended"] == 1


def test_index_carries_no_release_specific_literals():
    """The previous version hardcoded a target version, an exclusion list and
    an affinity table.  None of that may come back."""
    src = PLAN.read_text()
    for token in ("AFFINITY", "EXCLUSIONS", "HUNK_CENSUS", '"target":', "0.8.7.0"):
        assert token not in src, f"release-specific literal {token!r} is back in release_plan.py"


# ------------------------------------------------------------ release_hunks

def test_dump_numbers_the_hunks(repo):
    r = run([str(HUNKS), "dump", "src/widget.py", "src/new_helper.py"], cwd=repo)
    assert "src/widget.py  (2 hunks)" in r.stdout
    assert "[0] @@" in r.stdout and "[1] @@" in r.stdout
    assert "frobnicate" in r.stdout and "reset fix" in r.stdout
    assert "new_helper.py  (untracked" in r.stdout


def test_split_writes_one_patch_per_entry_that_stages_only_its_hunks(repo):
    attrib = repo / "attrib.json"
    attrib.write_text(json.dumps({"0": "widget-gains-a-frobnicate-mode",
                                  "1": "widget-no-longer-double-counts-on-reset"}))
    r = run([str(HUNKS), "split", "--file", "src/widget.py",
             "--attrib", str(attrib), "--out", ".ziya/sweep/patches"], cwd=repo)
    assert "PROBLEM" not in r.stdout
    p0 = repo / ".ziya/sweep/patches/widget-gains-a-frobnicate-mode/src__widget.py.patch"
    p1 = repo / ".ziya/sweep/patches/widget-no-longer-double-counts-on-reset/src__widget.py.patch"
    assert p0.exists() and p1.exists()

    # The seam: the patch stages exactly its hunk against the real index.
    git(["apply", "--cached", str(p0)], repo)
    staged = git(["diff", "--cached", "--", "src/widget.py"], repo)
    assert "frobnicate" in staged
    assert "reset fix" not in staged
    # The other entry's hunk is still unstaged in the working tree.
    unstaged = git(["diff", "--", "src/widget.py"], repo)
    assert "reset fix" in unstaged and "frobnicate" not in unstaged
    # And the second patch still applies on top (offsets tolerated).
    git(["apply", "--cached", str(p1)], repo)
    assert git(["diff", "--", "src/widget.py"], repo) == ""


def test_split_refuses_an_incomplete_attribution(repo):
    attrib = repo / "attrib.json"
    attrib.write_text(json.dumps({"0": "widget-gains-a-frobnicate-mode"}))
    r = run([str(HUNKS), "split", "--file", "src/widget.py",
             "--attrib", str(attrib), "--out", ".ziya/sweep/patches"], cwd=repo, check=False)
    assert r.returncode == 1
    assert "unattributed ['1']" in r.stdout
    assert not (repo / ".ziya/sweep/patches").exists(), "nothing is written on a refused split"


def test_split_refuses_a_nonexistent_hunk_index(repo):
    attrib = repo / "attrib.json"
    attrib.write_text(json.dumps({"0": "a", "1": "b", "7": "a"}))
    r = run([str(HUNKS), "split", "--file", "src/widget.py",
             "--attrib", str(attrib), "--out", "p"], cwd=repo, check=False)
    assert r.returncode == 1 and "do not exist ['7']" in r.stdout


def test_split_ships_an_untracked_file_whole_or_refuses_splitting_it(repo):
    # An untracked file has no hunks: mapped to one entry it ships whole with
    # no patch; mapped to two it is refused (it cannot be split).
    attrib = repo / "attrib.json"
    attrib.write_text(json.dumps({"0": "x"}))
    r = run([str(HUNKS), "split", "--file", "src/new_helper.py",
             "--attrib", str(attrib), "--out", "p"], cwd=repo, check=False)
    assert r.returncode == 0 and "whole file" in r.stdout
    assert not (repo / "p").exists() or not any((repo / "p").rglob("*.patch"))
    attrib.write_text(json.dumps({"0": "x", "1": "y"}))
    r = run([str(HUNKS), "split", "--file", "src/new_helper.py",
             "--attrib", str(attrib), "--out", "p"], cwd=repo, check=False)
    assert r.returncode == 1 and "untracked" in r.stdout


def test_split_check_catches_a_patch_that_no_longer_applies(repo):
    """--check is what keeps a bad patch from half-staging later: a file
    rewritten after attribution must fail here, not in the commit loop."""
    attrib = repo / "attrib.json"
    attrib.write_text(json.dumps({"0": "a", "1": "b"}))
    # Stage hunk 0 first so the working diff shrinks to one hunk, making the
    # saved two-hunk attribution stale.
    run([str(HUNKS), "split", "--file", "src/widget.py",
         "--attrib", str(attrib), "--out", "p1"], cwd=repo)
    git(["apply", "--cached", "p1/a/src__widget.py.patch"], repo)
    r = run([str(HUNKS), "split", "--file", "src/widget.py",
             "--attrib", str(attrib), "--out", "p2"], cwd=repo, check=False)
    assert r.returncode == 1
    assert "do not exist ['1']" in r.stdout or "--check failed" in r.stdout


def test_merge_records_the_escape_with_a_specific_reason(repo):
    r = run([str(HUNKS), "merge", "--file", "src/widget.py",
             "--entries", "a,b", "--out", ".ziya/sweep/patches",
             "--reason", "hunk 0 renames the function both entries describe; "
                         "no line-level seam exists"], cwd=repo)
    rec = json.loads((repo / ".ziya/sweep/patches/_merges/src__widget.py.json").read_text())
    assert rec == {"file": "src/widget.py", "entry_ids": ["a", "b"],
                   "reason": "hunk 0 renames the function both entries describe; "
                             "no line-level seam exists"}


@pytest.mark.parametrize("reason", ["", "hard to tell", "interleaved", "unclear."])
def test_merge_refuses_a_generic_reason(repo, reason):
    r = run([str(HUNKS), "merge", "--file", "src/widget.py", "--entries", "a,b",
             "--out", "p", "--reason", reason], cwd=repo, check=False)
    assert r.returncode == 1 and "specific obstacle" in r.stdout
    assert not (repo / "p" / "_merges").exists()


def test_merge_needs_two_entries(repo):
    r = run([str(HUNKS), "merge", "--file", "src/widget.py", "--entries", "a",
             "--out", "p", "--reason", "x" * 40], cwd=repo, check=False)
    assert r.returncode == 1 and "at least two" in r.stdout
