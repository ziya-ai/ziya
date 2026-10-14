"""
``commit amend=true`` and ``push force=true`` for the git builtin tool
(added 2026-10-13).

Field case: a hand-off ended with "my git tool can't --amend or force-push",
leaving ``git commit --amend -F <msg>`` and ``git push -f origin <branch>``
for the human to type.

Layers:
  * git_ops against real repositories and a bare remote: what lands, what
    is refused, what the plan shows.
  * consent_gate: an op in ``consent_human_only_ops`` asks even when config,
    a standing grant or a session grant would cover it -- paired with a
    control op that IS covered, so the test proves the path ran.
  * the tool seam (fails until the git_tool.py diff is applied): amend /
    force reach dispatch, and the consent op the human sees is the one that
    runs.
"""

import asyncio
import os
import subprocess

import pytest

from app.utils import consent_gate as cg
from app.utils import git_ops as g


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
    (root / "a.txt").write_text("a\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def run(op, root, **args):
    return g.run_op(op, args, str(root), write_check=lambda *a: "", model="m")


def head(root):
    return _git(root, "rev-parse", "HEAD").stdout.strip()


# ── truthy ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("v,want", [(True, True), ("true", True), ("1", True), (1, True),
                                    (False, False), ("false", False), ("", False),
                                    (None, False), (0, False), ("no", False)])
def test_truthy_reads_model_booleans(v, want):
    assert g.truthy(v) is want


def test_string_false_does_not_amend(repo):
    """bool("false") is True; dispatch must not read it that way."""
    (repo / "a.txt").write_text("b\n")
    run("add", repo, paths=["a.txt"])
    before = head(repo)
    out = run("commit", repo, message="second", amend="false")
    assert not out.get("error"), out
    assert _git(repo, "rev-parse", "HEAD~1").stdout.strip() == before


# ── amend ─────────────────────────────────────────────────────────────────

@pytest.fixture
def two_commits(repo):
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "second\n\nbody line")
    return repo


def test_amend_folds_staged_changes_into_head_and_keeps_parent(two_commits):
    r = two_commits
    parent = _git(r, "rev-parse", "HEAD~1").stdout.strip()
    old = head(r)
    (r / "c.txt").write_text("c\n")
    run("add", r, paths=["c.txt"])
    out = run("commit", r, message="second, amended", amend=True)
    assert not out.get("error"), out
    assert out["replaces"]["commit"] == old and out["files"] == ["c.txt"]
    assert head(r) != old
    assert _git(r, "rev-parse", "HEAD~1").stdout.strip() == parent   # replaced, not stacked
    tree = _git(r, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert sorted(tree) == ["b.txt", "c.txt"]
    assert _git(r, "log", "-1", "--format=%s").stdout.strip() == "second, amended"


def test_amend_without_message_reuses_heads(two_commits):
    r = two_commits
    (r / "c.txt").write_text("c\n")
    run("add", r, paths=["c.txt"])
    out = run("commit", r, amend=True)
    assert not out.get("error"), out
    assert out["message_reused"] is True
    body = _git(r, "log", "-1", "--format=%B").stdout
    assert body.startswith("second\n\nbody line") and "Co-authored-by: Ziya" in body


def test_message_only_amend_is_allowed_but_a_no_op_is_refused(two_commits):
    r = two_commits
    out = run("commit", r, message="reworded", amend=True)
    assert not out.get("error"), out
    assert out["files"] == []
    assert _git(r, "log", "-1", "--format=%s").stdout.strip() == "reworded"
    again = run("commit", r, amend=True)          # nothing staged, same message
    assert again["error"] and "nothing to amend" in again["message"]


def test_plain_commit_still_requires_a_message(two_commits):
    out = run("commit", two_commits, message="")
    assert out["error"] and "message is required" in out["message"]


def test_amend_with_no_commits_is_refused(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    out = run("commit", root, message="x", amend=True)
    assert out["error"] and "no commits" in out["message"]


# ── force push ────────────────────────────────────────────────────────────

@pytest.fixture
def remote(repo, tmp_path):
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "switch", "-q", "-c", "feature")
    (repo / "f.txt").write_text("1\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "feature work")
    pushed = run("push", repo, remote="origin", branch="feature")
    assert not pushed.get("error"), pushed
    return bare


def _remote_sha(bare, branch):
    return _git(bare, "rev-parse", branch).stdout.strip()


def test_amend_then_force_push_replaces_the_remote_commit(repo, remote):
    """The hand-off this was built for: amend, then force-push."""
    published = head(repo)
    assert _remote_sha(remote, "feature") == published
    (repo / "f.txt").write_text("2\n")
    run("add", repo, paths=["f.txt"])
    amended = run("commit", repo, message="feature work v2", amend=True)
    assert not amended.get("error"), amended
    assert "force=true" in amended["warning"] and amended["replaces_published_on"] == ["origin/feature"]

    plain = run("push", repo, remote="origin", branch="feature")
    assert plain["error"] and "rejected" in plain["message"]
    assert _remote_sha(remote, "feature") == published        # unforced: untouched

    plan = g.preflight("push", {"remote": "origin", "branch": "feature", "force": True},
                       str(repo))
    assert plan["lease"] == {"ref": "origin/feature", "expect": published}
    assert plan["force_needed"] is True and len(plan["discards_remote_commits"]) == 1
    out = run("push", repo, remote="origin", branch="feature", force=True)
    assert not out.get("error"), out
    assert _remote_sha(remote, "feature") == head(repo)


def test_force_push_refuses_when_the_remote_moved_since_fetch(repo, remote, tmp_path):
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", "-b", "feature", str(remote), str(other))
    _git(other, "config", "user.email", "o@example.invalid")
    _git(other, "config", "user.name", "O")
    (other / "g.txt").write_text("theirs\n")
    _git(other, "add", "g.txt")
    _git(other, "commit", "-q", "-m", "theirs")
    _git(other, "push", "-q", "origin", "feature")
    theirs = _remote_sha(remote, "feature")
    _git(repo, "commit", "-q", "--amend", "-m", "rewritten")
    out = run("push", repo, remote="origin", branch="feature", force=True)
    assert out["error"] and "op=fetch" in out["message"], out
    assert _remote_sha(remote, "feature") == theirs, "a lease must not overwrite unseen work"


def test_force_push_to_the_default_branch_is_refused(repo, remote):
    _git(repo, "switch", "-q", "main")
    run("push", repo, remote="origin", branch="main")
    _git(repo, "commit", "-q", "--amend", "-m", "rewritten init")
    out = run("push", repo, remote="origin", branch="main", force=True)
    assert out["error"] and "default branch" in out["message"]


def test_force_on_a_new_remote_branch_is_a_plain_push(repo, remote):
    _git(repo, "switch", "-q", "-c", "fresh")
    plan = g.preflight("push", {"remote": "origin", "branch": "fresh", "force": True},
                       str(repo))
    assert plan["force_needed"] is False and "lease" not in plan
    out = run("push", repo, remote="origin", branch="fresh", force=True)
    assert not out.get("error"), out
    assert _remote_sha(remote, "fresh") == head(repo)


# ── consent gate: human-only ops ──────────────────────────────────────────

class _Fake:
    consent_default = "ask"
    consent_op_key = "op"
    consent_human_only_ops = ("rewrite",)


def _decide(op, conversation_id=None):
    async def go():
        async for ev in cg.consent_gate(tool_name="fake", tool_id="t", args={"op": op},
                                        conversation_id=conversation_id, tool=_Fake()):
            if ev["type"] == cg.DECISION_EVENT:
                return ev
    return asyncio.run(go())


@pytest.fixture
def gate(tmp_path, monkeypatch):
    cg.reset_for_tests(tmp_path / "consents")
    monkeypatch.setattr(cg, "_consent_config",
                        lambda: {"tools": {"fake": {"default": "always"}}})
    yield
    cg.reset_for_tests()


def test_configured_always_does_not_skip_a_human_only_op(gate):
    assert _decide("plain")["approved"] is True            # control: always applies
    ev = _decide("rewrite")
    assert ev["approved"] is False and "no interactive conversation" in ev["answer"]["answer"]


def test_a_session_grant_does_not_cover_a_human_only_op(gate, monkeypatch):
    monkeypatch.setattr(cg, "_consent_config", lambda: {})  # declared ask
    cg._grants.remember(cg.SCOPE_SESSION, "fake", "plain", None)
    cg._grants.remember(cg.SCOPE_SESSION, "fake", "rewrite", None)
    assert _decide("plain")["approved"] is True             # control: grant covers
    assert _decide("rewrite")["approved"] is False


def test_autonomous_project_does_not_skip_a_human_only_op(gate, monkeypatch):
    from app.utils import project_tool_modes as ptm
    monkeypatch.setattr(ptm, "mode_for", lambda tool, root: "autonomous")
    assert _decide("plain")["approved"] is True
    assert _decide("rewrite")["approved"] is False


# ── hazard text ───────────────────────────────────────────────────────────

def test_hazard_redirect_no_longer_claims_amend_and_force_are_absent():
    from app.utils.git_grant_hazards import _TOOL_ALTERNATIVE
    assert "amend=true" in _TOOL_ALTERNATIVE["commit"]
    assert "force=true" in _TOOL_ALTERNATIVE["push"]
    assert "never forced" not in _TOOL_ALTERNATIVE["push"]


# ── tool seam (needs the app/mcp/tools/git_tool.py diff) ──────────────────

def test_tool_consent_op_separates_history_rewrites():
    from app.mcp.tools.git_tool import GitTool
    t = GitTool()
    assert cg.op_for_call(t, {"op": "commit"}) == "commit"
    assert cg.op_for_call(t, {"op": "commit", "amend": True}) == "commit_amend"
    assert cg.op_for_call(t, {"op": "commit", "amend": "false"}) == "commit"
    assert cg.op_for_call(t, {"op": "push", "force": "true"}) == "push_force"
    assert {"commit_amend", "push_force"} <= cg.human_only_ops(t)
    assert cg.declared_mode(t, "push_force") == "ask"
    assert cg.editable_args(t, "commit_amend") == ["message"]


def test_tool_forwards_amend_and_force(repo):
    from app.mcp.tools import git_tool as gt
    assert {"amend", "force"} <= set(gt.GitInput.model_fields)
    args = gt._op_args({"op": "push", "force": True, "amend": True})
    assert args["force"] is True and args["amend"] is True
    (repo / "a.txt").write_text("z\n")
    _git(repo, "add", "a.txt")
    plan = asyncio.run(gt.GitTool().preflight(op="commit", amend=True,
                                              _workspace_path=str(repo)))
    assert not plan.get("error"), plan
    assert plan["amend"] is True            # dropped args would plan a plain commit
