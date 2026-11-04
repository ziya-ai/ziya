"""
Tests for ``stash_drop`` and ``delete_fetched_ref`` (app/utils/git_ops.py,
added 2026-11-04).  Real git against throwaway repositories.  Every refusal
asserts the repository is unchanged and is paired with a case where the op runs.
"""

import subprocess
import typing

import pytest

from app.utils import git_ops as g

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not available",
)


def _git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for k, v in (("user.name", "Test"), ("user.email", "t@example.invalid"),
                 ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        _git(root, "config", k, v)
    (root / "a.py").write_text("a = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def run(op, root, **args):
    return g.run_op(op, args, str(root), write_check=lambda *a: "")


def pre(op, root, **args):
    return g.preflight(op, args, str(root), write_check=lambda *a: "")


def _stashes(root):
    return [ln for ln in _git(root, "stash", "list", "--format=%H").stdout.splitlines() if ln]


def _head(root):
    return _git(root, "rev-parse", "HEAD").stdout.strip()


# ── stash_drop ────────────────────────────────────────────────────────────

def test_stash_drop_plans_then_drops_the_named_stash(repo):
    (repo / "a.py").write_text("a = 2\n")
    _git(repo, "stash", "push", "-m", "older")
    (repo / "a.py").write_text("a = 3\n")
    _git(repo, "stash", "push", "-m", "newer")
    older, newer = _stashes(repo)[1], _stashes(repo)[0]

    plan = pre("stash_drop", repo, index=1)
    assert plan["stash"] == "stash@{1}" and plan["commit"] == older
    assert plan["files"] == ["a.py"] and plan["recover"] == f"git stash apply {older}"
    assert plan["already_in_head"] == [] and "note" not in plan
    assert len(_stashes(repo)) == 2     # preflight never drops

    out = run("stash_drop", repo, index="stash@{1}")
    assert not out.get("error"), out
    assert out["dropped"] == "stash@{1}" and out["remaining_stashes"] == 1
    assert _stashes(repo) == [newer]
    assert (repo / "a.py").read_text() == "a = 1\n"   # worktree untouched


def test_stash_drop_flags_a_stash_already_in_head(repo):
    (repo / "a.py").write_text("a = 2\n")
    _git(repo, "stash", "push")
    (repo / "a.py").write_text("a = 2\n")
    _git(repo, "commit", "-qam", "same change landed")
    plan = pre("stash_drop", repo)
    assert plan["already_in_head"] == ["a.py"]
    assert "redundant" in plan["note"]


def test_stash_drop_refusals_leave_stashes_alone(repo):
    out = run("stash_drop", repo)
    assert out["error"] and "no stashes" in out["message"]
    (repo / "a.py").write_text("a = 2\n")
    _git(repo, "stash", "push")
    before = _stashes(repo)
    out = run("stash_drop", repo, index=5)
    assert out["error"] and "does not exist" in out["message"]
    assert _stashes(repo) == before


# ── delete_fetched_ref ────────────────────────────────────────────────────

FETCHED = "refs/ziya/fetched/barnstorm/ziya/win-port-main"


def _make_fetched(root):
    _git(root, "update-ref", FETCHED, _head(root))


def _exists(root, ref):
    return _git(root, "rev-parse", "--verify", "-q", ref, check=False).returncode == 0


@pytest.mark.parametrize("name", [FETCHED, "barnstorm/ziya/win-port-main"])
def test_delete_fetched_ref_accepts_full_or_short_names(repo, name):
    _make_fetched(repo)
    plan = pre("delete_fetched_ref", repo, ref=name)
    assert plan["ref"] == FETCHED and plan["commit"] == _head(repo)
    assert plan["subject"] == "init" and plan["commits_only_here"] == 0
    assert _exists(repo, FETCHED)       # preflight never deletes
    out = run("delete_fetched_ref", repo, ref=name)
    assert not out.get("error"), out
    assert out["deleted"] == FETCHED and not _exists(repo, FETCHED)
    assert _exists(repo, "refs/heads/main")


def test_delete_fetched_ref_counts_commits_reachable_only_from_it(repo):
    (repo / "b.py").write_text("b\n")
    _git(repo, "add", "b.py")
    tree = _git(repo, "write-tree").stdout.strip()
    lone = _git(repo, "commit-tree", tree, "-p", _head(repo), "-m", "fork only").stdout.strip()
    _git(repo, "reset", "-q")
    _git(repo, "update-ref", FETCHED, lone)
    plan = pre("delete_fetched_ref", repo, ref=FETCHED)
    assert plan["commits_only_here"] == 1 and "fetched again" in plan["note"]


@pytest.mark.parametrize("ref", [
    "refs/heads/main", "refs/tags/v1", "refs/remotes/origin/main",
    "refs/ziya/fetched/../../heads/main", "-d", "refs/ziya/fetchedX/a",
])
def test_delete_fetched_ref_refuses_everything_outside_the_namespace(repo, ref):
    _make_fetched(repo)
    _git(repo, "tag", "v1")
    out = run("delete_fetched_ref", repo, ref=ref)
    assert out["error"], out
    assert _exists(repo, "refs/heads/main") and _exists(repo, "refs/tags/v1")
    assert _exists(repo, FETCHED)


def test_delete_fetched_ref_missing_lists_what_exists(repo):
    _make_fetched(repo)
    for args in ({}, {"ref": "nope/x/y"}):
        out = run("delete_fetched_ref", repo, **args)
        assert out["error"] and out["fetched_refs"] == [FETCHED]


def test_delete_fetched_ref_keeps_a_ref_that_moved_after_the_plan(repo, monkeypatch):
    """update-ref -d is given the planned sha: a ref re-fetched in between survives."""
    _make_fetched(repo)
    real = g.plan_delete_fetched_ref

    def stale(r, ref):
        plan = real(r, ref)
        (repo / "c.py").write_text("c\n")
        _git(repo, "add", "c.py"); _git(repo, "commit", "-qm", "moved")
        _git(repo, "update-ref", FETCHED, _head(repo))
        return plan
    monkeypatch.setattr(g, "plan_delete_fetched_ref", stale)
    out = run("delete_fetched_ref", repo, ref=FETCHED)
    assert out["error"] and _exists(repo, FETCHED)


# ── seam: the tool surface ────────────────────────────────────────────────

def test_tool_schema_forwards_ref_and_index_and_drop_asks_every_time(repo):
    import asyncio
    from app.mcp.tools import git_tool as gt
    ops = set(typing.get_args(gt.GitInput.model_fields["op"].annotation))
    assert {"stash_drop", "delete_fetched_ref"} <= ops
    assert "ref" in gt.GitInput.model_fields and "ref" in gt._ARG_KEYS
    assert "stash_drop" in gt.GitTool.consent_human_only_ops
    for op in ("stash_drop", "delete_fetched_ref"):
        assert op not in gt.GitTool.consent_op_defaults   # both ask
    _make_fetched(repo)
    plan = asyncio.run(gt.GitTool().preflight(op="delete_fetched_ref",
                                              ref="barnstorm/ziya/win-port-main",
                                              _workspace_path=str(repo)))
    assert not plan.get("error"), plan
    assert plan["ref"] == FETCHED


def test_grant_hazards_redirect_to_the_new_ops():
    from app.utils import git_grant_hazards as gh
    assert "op=stash_drop" in (gh.assess("git stash").alternative or "")
    assert "op=delete_fetched_ref" in (gh.assess("git update-ref").alternative or "")
