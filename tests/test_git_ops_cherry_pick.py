"""
Tests for ``cherry_pick`` / ``cherry_pick_continue`` / ``cherry_pick_abort``
and ``fetch`` with ``url`` + ``branch`` (app/utils/git_ops.py, added
2026-11-03, design/git-tool.md).

Real git against throwaway repositories.  Every refusal asserts the
repository is unchanged; each refusal is paired with a case where the op runs.
The last test is the workflow these ops were added for: fetch a contributor's
branch by URL, then pick a range off it through a conflict.
"""

import os
import subprocess

import pytest

from app.utils import git_ops as g

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not available",
)


def _git(cwd, *args, check=True, env=None):
    e = None
    if env:
        e = dict(os.environ)
        e.update(env)
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=check, env=e)


def allow_all(rel, root, exists):
    return ""


def docs_only(rel, root, exists):
    return "" if rel == "docs" or rel.startswith("docs" + os.sep) else f"{rel} is not writable"


def run(op, root, check=allow_all, **args):
    return g.run_op(op, args, str(root), write_check=check, model="test-model")


def pre(op, root, check=allow_all, **args):
    return g.preflight(op, args, str(root), write_check=check, model="test-model")


def _init(root):
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for k, v in (("user.name", "Test"), ("user.email", "t@example.invalid"),
                 ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        _git(root, "config", k, v)


BILL = {"GIT_AUTHOR_NAME": "Bill", "GIT_AUTHOR_EMAIL": "bill@example.invalid"}


def _commit(root, path, text, msg, env=None):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    _git(root, "add", path)
    _git(root, "commit", "-q", "-m", msg, env=env)
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _head(root):
    return _git(root, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """main: init.  side (from init): three of Bill's commits touching a.py, b.py, c.py."""
    root = tmp_path / "proj"
    _init(root)
    base = _commit(root, "a.py", "a = 1\n", "init")
    _git(root, "switch", "-q", "-c", "side")
    _commit(root, "a.py", "a = 2\n", "side: a", env=BILL)
    _commit(root, "b.py", "b = 1\n", "side: b", env=BILL)
    _commit(root, "c.py", "c = 1\n", "side: c", env=BILL)
    _git(root, "switch", "-q", "main")
    return root, base


# ── cherry_pick: the happy paths ──────────────────────────────────────────

def test_range_pick_applies_every_commit_in_order_keeping_the_author(repo):
    root, base = repo
    plan = pre("cherry_pick", root, rev=f"{base}..side")
    assert not plan.get("error"), plan
    assert [c.split(" ", 1)[1] for c in plan["commits"]] == [
        "side: a (Bill)", "side: b (Bill)", "side: c (Bill)"]
    assert plan["to_write"] == ["a.py", "b.py", "c.py"]
    assert plan["counts"] == {"commits": 3, "to_write": 3}

    out = run("cherry_pick", root, rev=f"{base}..side")
    assert out["status"] == "done", out
    assert out["counts"]["applied"] == 3
    log = _git(root, "log", "--format=%an %s", "-3").stdout.splitlines()
    assert log == ["Bill side: c", "Bill side: b", "Bill side: a"]
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"


def test_single_commit_pick(repo):
    root, _ = repo
    target = _git(root, "rev-parse", "side~1").stdout.strip()   # "side: b"
    out = run("cherry_pick", root, rev="side~1")
    assert out["status"] == "done", out
    assert out["tip"] == target
    assert (root / "b.py").read_text() == "b = 1\n"
    assert not (root / "c.py").exists()


# ── cherry_pick: refusals leave the repository alone ──────────────────────

def test_dirty_tracked_tree_is_refused(repo):
    root, base = repo
    (root / "a.py").write_text("dirty\n")
    before = _head(root)
    out = run("cherry_pick", root, rev=f"{base}..side")
    assert out["error"] and out["uncommitted"] == ["a.py"]
    assert _head(root) == before and (root / "a.py").read_text() == "dirty\n"


def test_untracked_target_is_never_overwritten(repo):
    root, base = repo
    (root / "b.py").write_text("mine, untracked\n")
    out = run("cherry_pick", root, rev=f"{base}..side")
    assert out["error"] and out["untracked"] == ["b.py"]
    assert (root / "b.py").read_text() == "mine, untracked\n"


def test_commits_already_in_head_are_refused(repo):
    root, base = repo
    out = run("cherry_pick", root, rev="HEAD")
    assert out["error"] and out["already_in_head"]


def test_merge_commits_are_refused(repo):
    root, base = repo
    _git(root, "switch", "-q", "-c", "m", base)
    _commit(root, "m.py", "m\n", "m")
    _git(root, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    _git(root, "switch", "-q", "main")
    before = _head(root)
    out = run("cherry_pick", root, rev=f"{base}..m")
    assert out["error"] and any("merge side" in m for m in out["merges"])
    assert _head(root) == before


@pytest.mark.parametrize("rev, fragment", [
    ("", "rev is required"), ("a...b", "symmetric"), ("nope..side", "does not name a commit"),
    ("--help", "not an acceptable"), ("side..side", "no commits"),
])
def test_bad_revs_are_refused(repo, rev, fragment):
    root, _ = repo
    out = run("cherry_pick", root, rev=rev)
    assert out["error"] and fragment in out["message"], out


def test_outside_policy_files_need_the_call_approved(repo):
    root, base = repo
    plan = pre("cherry_pick", root, check=docs_only, rev=f"{base}..side")
    assert {r["path"] for r in plan["outside_policy"]} == {"a.py", "b.py", "c.py"}
    before = _head(root)
    out = run("cherry_pick", root, check=docs_only, rev=f"{base}..side")
    assert out["error"] and "not approved" in out["message"]
    assert _head(root) == before
    # With the grant (here: a permissive check) the same call runs.
    assert run("cherry_pick", root, rev=f"{base}..side")["status"] == "done"


def test_patch_equivalent_commits_are_warned_about(repo):
    root, base = repo
    _commit(root, "b.py", "b = 1\n", "same change as side: b")
    plan = pre("cherry_pick", root, rev=f"{base}..side")
    assert not plan.get("error"), plan
    assert len(plan["already_applied"]) == 1 and "side: b" in plan["already_applied"][0]
    assert "empty" in plan["warning"]


# ── conflict: stop, continue, abort ───────────────────────────────────────

@pytest.fixture
def conflicting(repo):
    """main also changes a.py, so the first of the three picks conflicts."""
    root, base = repo
    _commit(root, "a.py", "a = 99\n", "main: a")
    return root, base, _head(root)


def test_conflict_stops_and_reports_what_remains(conflicting):
    root, base, orig = conflicting
    out = run("cherry_pick", root, rev=f"{base}..side")
    assert not out.get("error"), out
    assert out["status"] == "stopped"
    assert out["conflicted"] == ["a.py"]
    assert "side: a" in out["stopped_at"]
    assert [r.split(" ", 1)[1] for r in out["remaining"]] == ["side: b (Bill)", "side: c (Bill)"]
    assert out["counts"]["applied"] == 0 and "cherry_pick_continue" in out["next"]
    # A second pick is refused while this one is in progress.
    again = run("cherry_pick", root, rev="side")
    assert again["error"] and "already in progress" in again["message"]


def test_continue_refuses_until_resolved_and_staged(conflicting):
    root, base, _ = conflicting
    run("cherry_pick", root, rev=f"{base}..side")
    out = run("cherry_pick_continue", root)
    assert out["error"] and out["conflicted"] == ["a.py"]
    (root / "a.py").write_text("a = 100\n")        # resolved, not staged
    _git(root, "add", "a.py")
    (root / "a.py").write_text("a = 101\n")        # then edited again
    out = run("cherry_pick_continue", root)
    assert out["error"] and out["unstaged"] == ["a.py"]


def test_continue_refuses_staged_conflict_markers(conflicting):
    root, base, _ = conflicting
    run("cherry_pick", root, rev=f"{base}..side")
    _git(root, "add", "a.py")  # staged with the markers still in it
    out = run("cherry_pick_continue", root)
    assert out["error"] and out["conflict_markers"] == ["a.py"]


def test_continue_finishes_the_sequence(conflicting):
    root, base, orig = conflicting
    run("cherry_pick", root, rev=f"{base}..side")
    (root / "a.py").write_text("a = 100\n")
    _git(root, "add", "a.py")
    plan = pre("cherry_pick_continue", root)
    assert not plan.get("error"), plan
    assert "side: a" in plan["committing"] and plan["staged"] == ["a.py"]
    assert plan["to_write"] == ["b.py", "c.py"]
    out = run("cherry_pick_continue", root)
    assert out["status"] == "done", out
    assert [ln.split(" ", 1)[1] for ln in out["applied"]] == [
        "side: a (Bill)", "side: b (Bill)", "side: c (Bill)"]
    assert (root / "a.py").read_text() == "a = 100\n"
    assert g._pick_state(g.open_repo(str(root))) is None


def test_abort_returns_to_the_original_head_and_lists_what_it_discards(tmp_path):
    root = tmp_path / "proj"
    _init(root)
    base = _commit(root, "a.py", "a = 1\n", "init")
    _git(root, "switch", "-q", "-c", "side")
    _commit(root, "b.py", "b = 1\n", "side: b", env=BILL)
    _commit(root, "a.py", "a = 2\n", "side: a", env=BILL)   # second pick conflicts
    _git(root, "switch", "-q", "main")
    orig = _commit(root, "a.py", "a = 99\n", "main: a")
    out = run("cherry_pick", root, rev=f"{base}..side")
    assert out["status"] == "stopped" and out["counts"]["applied"] == 1
    (root / "a.py").write_text("half-resolved\n")

    plan = pre("cherry_pick_abort", root)
    assert not plan.get("error"), plan
    assert plan["returns_to"] == orig
    assert [d.split(" ", 1)[1] for d in plan["drops_commits"]] == ["side: b (Bill)"]
    assert plan["discards_uncommitted"] == ["a.py"]
    assert "discarded" in plan["warning"]
    done = run("cherry_pick_abort", root)
    assert done["status"] == "aborted" and _head(root) == orig
    assert (root / "a.py").read_text() == "a = 99\n" and not (root / "b.py").exists()


def test_continue_and_abort_need_a_pick_in_progress(repo):
    root, _ = repo
    for op in ("cherry_pick_continue", "cherry_pick_abort"):
        out = run(op, root)
        assert out["error"] and "no cherry-pick is in progress" in out["message"]


# ── fetch with url ────────────────────────────────────────────────────────

@pytest.mark.parametrize("url, slug", [
    ("https://github.com/barnstorm/ziya.git", "barnstorm/ziya"),
    ("https://github.com/barnstorm/ziya", "barnstorm/ziya"),
    ("git@github.com:barnstorm/ziya.git", "barnstorm/ziya"),
    ("ssh://git@host:22/team/repo.git/", "team/repo"),
])
def test_url_fetch_plan_names_a_private_ref(repo, url, slug):
    root, _ = repo
    plan = pre("fetch", root, url=url, branch="win-port-main")
    assert not plan.get("error"), plan
    assert plan["into"] == f"refs/ziya/fetched/{slug}/win-port-main"
    assert plan["tags"] is False and plan["prune"] is False
    assert "_url" not in plan


@pytest.mark.parametrize("args, fragment", [
    ({"url": "/tmp/somewhere/repo.git", "branch": "x"}, "must be https"),
    ({"url": "file:///tmp/r.git", "branch": "x"}, "must be https"),
    ({"url": "https://h/o/r.git"}, "needs branch"),
    ({"url": "https://h/o/r.git", "branch": "a..b"}, "valid ref"),
    ({"url": "https://h/o/r.git", "branch": "x", "remote": "origin"}, "not both"),
])
def test_url_fetch_refusals(repo, args, fragment):
    root, _ = repo
    out = run("fetch", root, **args)
    assert out["error"] and fragment in out["message"], out


def _upstream_fork(tmp_path, base_repo):
    """A second repository: a clone of base_repo with Bill's work on 'feature'."""
    fork = tmp_path / "fork"
    _git(tmp_path, "clone", "-q", str(base_repo), str(fork))
    for k, v in (("user.name", "Bill"), ("user.email", "bill@example.invalid"),
                 ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        _git(fork, "config", k, v)
    _git(fork, "switch", "-q", "-c", "feature", "origin/main")
    return fork


@pytest.fixture
def local_urls(monkeypatch):
    # Real transports are network; let the test use a local path, exactly as
    # the clone execution tests do.  The URL rule itself is tested above.
    monkeypatch.setattr(g, "_valid_clone_url", lambda u: str(u))
    monkeypatch.setattr(g, "_ALLOWED_PROTOCOLS", "file:https:ssh:git")


def test_url_fetch_moves_no_local_branch_and_refetches(repo, tmp_path, local_urls):
    root, base = repo
    fork = _upstream_fork(tmp_path, root)
    tip1 = _commit(fork, "w.py", "w = 1\n", "feature: w")
    branches = _git(root, "for-each-ref", "refs/heads", "refs/remotes").stdout
    out = run("fetch", root, url=str(fork), branch="feature")
    assert not out.get("error"), out
    assert out["commit"] == tip1 and out["commits_not_in_head"] == 1
    assert _git(root, "rev-parse", out["into"]).stdout.strip() == tip1
    assert _git(root, "for-each-ref", "refs/heads", "refs/remotes").stdout == branches
    assert _git(root, "tag").stdout == ""
    # The other side rewrites its branch: a re-fetch replaces the private ref.
    _git(fork, "commit", "-q", "--amend", "-m", "feature: w (amended)")
    tip2 = _head(fork)
    again = run("fetch", root, url=str(fork), branch="feature")
    assert again["replaces"] == tip1 and again["commit"] == tip2


def test_fetch_then_pick_a_range_through_a_conflict(repo, tmp_path, local_urls):
    """The PR workflow: fetch a fork's branch by URL, pick the range after its
    base, resolve the one conflict, continue to the end."""
    root, base = repo
    fork = _upstream_fork(tmp_path, root)
    fork_base = _head(fork)
    _commit(fork, "a.py", "a = 'windows'\n", "fix(windows): a")
    _commit(fork, "w.py", "w = 1\n", "fix(windows): w")
    _commit(root, "a.py", "a = 'mac'\n", "main: a")     # conflicts with the first pick

    fetched = run("fetch", root, url=str(fork), branch="feature")
    rng = f"{fork_base}..{fetched['into']}"
    plan = pre("cherry_pick", root, rev=rng)
    assert plan["counts"]["commits"] == 2 and plan["to_write"] == ["a.py", "w.py"]

    stop = run("cherry_pick", root, rev=rng)
    assert stop["status"] == "stopped" and stop["conflicted"] == ["a.py"]
    (root / "a.py").write_text("a = 'both'\n")
    assert run("add", root, paths=["a.py"]).get("error") is None
    done = run("cherry_pick_continue", root)
    assert done["status"] == "done", done
    log = _git(root, "log", "--format=%an %s", "-2").stdout.splitlines()
    assert log == ["Bill fix(windows): w", "Bill fix(windows): a"]
    assert (root / "a.py").read_text() == "a = 'both'\n"
