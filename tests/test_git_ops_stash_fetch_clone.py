"""
Tests for the git tool ops added 2026-10-02: ``stash`` (alias), ``stash_pop``,
``fetch`` and ``clone`` (app/utils/git_ops.py, design/git-tool.md).

Real git against throwaway repositories; the write policy is injected (only
``docs/`` writable).  Every refusal test also asserts that the repository or
filesystem is unchanged, and each is paired with a case where the op runs.
"""

import os
import subprocess

import pytest

from app.utils import git_ops as g

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not available",
)


def _git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


def docs_only(rel, root, exists):
    return "" if rel == "docs" or rel.startswith("docs" + os.sep) else f"{rel} is not writable"


def _init(root):
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for k, v in (("user.name", "Test"), ("user.email", "t@example.invalid"),
                 ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        _git(root, "config", k, v)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    _init(root)
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("a\n")
    (root / "src.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def run(op, root, **args):
    return g.run_op(op, args, str(root), write_check=docs_only, model="test-model")


def pre(op, root, **args):
    return g.preflight(op, args, str(root), write_check=docs_only, model="test-model")


def _stash_count(root):
    return len([ln for ln in _git(root, "stash", "list").stdout.splitlines() if ln])


# ── stash (alias of stash_push) ───────────────────────────────────────────

def test_stash_alias_stashes_like_stash_push(repo):
    (repo / "docs" / "a.md").write_text("c\n")
    out = run("stash", repo, message="via alias")
    assert not out.get("error"), out
    assert out["op"] == "stash_push" and out["stashed"] == ["docs/a.md"]
    assert "via alias" in _git(repo, "stash", "list").stdout
    assert (repo / "docs" / "a.md").read_text() == "a\n"


def test_stash_alias_has_a_preflight(repo):
    (repo / "docs" / "a.md").write_text("c\n")
    plan = pre("stash", repo)
    assert plan["op"] == "stash_push" and plan["to_stash"] == ["docs/a.md"]
    assert _stash_count(repo) == 0   # preflight never executes


def test_status_lists_stashes(repo):
    assert run("status", repo)["stashes"] == []
    (repo / "docs" / "a.md").write_text("c\n")
    run("stash_push", repo, message="saved")
    st = run("status", repo)["stashes"]
    assert len(st) == 1 and st[0]["ref"] == "stash@{0}" and "saved" in st[0]["message"]


# ── stash_pop ─────────────────────────────────────────────────────────────

def test_stash_pop_round_trip(repo):
    (repo / "docs" / "a.md").write_text("edited\n")
    run("stash_push", repo, message="wip")
    plan = pre("stash_pop", repo)
    assert plan["to_write"] == ["docs/a.md"] and plan["stash"] == "stash@{0}"
    assert "warning" not in plan
    assert _stash_count(repo) == 1   # preflight did not pop
    out = run("stash_pop", repo)
    assert not out.get("error"), out
    assert out["written"] == ["docs/a.md"] and out["remaining_stashes"] == 0
    assert (repo / "docs" / "a.md").read_text() == "edited\n"
    assert _stash_count(repo) == 0


def test_stash_pop_with_no_stashes(repo):
    out = run("stash_pop", repo)
    assert out["error"] and "no stashes" in out["message"]


def test_stash_pop_refuses_a_dirty_tree(repo):
    (repo / "docs" / "a.md").write_text("one\n")
    run("stash_push", repo)
    (repo / "docs" / "a.md").write_text("two\n")
    out = run("stash_pop", repo)
    assert out["error"] and out["uncommitted"] == ["docs/a.md"]
    assert _stash_count(repo) == 1 and (repo / "docs" / "a.md").read_text() == "two\n"


def test_stash_pop_asks_for_paths_outside_the_write_policy(repo):
    (repo / "src.py").write_text("x = 2\n")
    _git(repo, "stash", "push", "-q")
    plan = pre("stash_pop", repo)
    assert not plan.get("error") and [o["path"] for o in plan["outside_policy"]] == ["src.py"]
    out = run("stash_pop", repo)        # no grant: refused at execution
    assert out["error"] and [o["path"] for o in out["outside_policy"]] == ["src.py"]
    assert _stash_count(repo) == 1 and (repo / "src.py").read_text() == "x = 1\n"


def test_stash_pop_never_overwrites_an_untracked_file(repo):
    (repo / "docs" / "n.md").write_text("stashed\n")
    _git(repo, "add", "docs/n.md")
    _git(repo, "stash", "push", "-q")
    (repo / "docs" / "n.md").write_text("MINE\n")       # untracked now
    out = run("stash_pop", repo)
    assert out["error"] and out["untracked"] == ["docs/n.md"]
    assert (repo / "docs" / "n.md").read_text() == "MINE\n" and _stash_count(repo) == 1


def test_stash_pop_restores_an_untracked_files_stash(repo):
    (repo / "docs" / "u.md").write_text("u\n")
    _git(repo, "stash", "push", "-q", "-u")
    assert not (repo / "docs" / "u.md").exists()
    plan = pre("stash_pop", repo)
    assert plan["restores_untracked"] == ["docs/u.md"] and "docs/u.md" in plan["to_write"]
    out = run("stash_pop", repo)
    assert not out.get("error"), out
    assert (repo / "docs" / "u.md").read_text() == "u\n"


@pytest.mark.parametrize("index", [1, "1", "stash@{1}"])
def test_stash_pop_index_selects_an_older_stash(repo, index):
    (repo / "docs" / "a.md").write_text("older\n")
    run("stash_push", repo, message="older")
    (repo / "docs" / "a.md").write_text("newer\n")
    run("stash_push", repo, message="newer")
    out = run("stash_pop", repo, index=index)
    assert not out.get("error"), out
    assert out["stash"] == "stash@{1}" and "older" in out["message"]
    assert (repo / "docs" / "a.md").read_text() == "older\n"
    remaining = _git(repo, "stash", "list").stdout
    assert "newer" in remaining and "older" not in remaining


@pytest.mark.parametrize("index, fragment", [
    (-1, "0 or greater"), ("x", "not a stash number"), (True, "stash number"),
    ("stash@{-1}", "not a stash number"), ("HEAD", "not a stash number"),
])
def test_stash_pop_rejects_bad_index(repo, index, fragment):
    (repo / "docs" / "a.md").write_text("c\n")
    run("stash_push", repo)
    out = run("stash_pop", repo, index=index)
    assert out["error"] and fragment in out["message"]
    assert _stash_count(repo) == 1


def test_stash_pop_index_out_of_range_lists_stashes(repo):
    (repo / "docs" / "a.md").write_text("c\n")
    run("stash_push", repo)
    out = run("stash_pop", repo, index=3)
    assert out["error"] and "does not exist" in out["message"] and len(out["stashes"]) == 1


def test_stash_pop_conflict_keeps_the_stash_and_reports(repo):
    (repo / "docs" / "a.md").write_text("from stash\n")
    run("stash_push", repo)
    (repo / "docs" / "a.md").write_text("committed meanwhile\n")
    _git(repo, "commit", "-qam", "diverge")
    assert "warning" in pre("stash_pop", repo)    # base differs from HEAD
    out = run("stash_pop", repo)
    assert out["error"] and out["stash_kept"] is True
    assert out["conflicted"] == ["docs/a.md"] and "op=checkout" in out["message"]
    assert _stash_count(repo) == 1


def test_stash_pop_refuses_files_outside_the_project_root(tmp_path):
    top = tmp_path / "top"
    _init(top)
    (top / "proj").mkdir()
    (top / "proj" / "docs").mkdir()
    (top / "proj" / "docs" / "a.md").write_text("a\n")
    (top / "other.txt").write_text("o\n")
    _git(top, "add", "-A")
    _git(top, "commit", "-qm", "init")
    (top / "other.txt").write_text("changed\n")
    _git(top, "stash", "push", "-q")
    out = g.run_op("stash_pop", {}, str(top / "proj"), write_check=lambda *a: "")
    assert out["error"] and out["outside"] == ["other.txt"]
    assert (top / "other.txt").read_text() == "o\n"


# ── fetch ─────────────────────────────────────────────────────────────────

@pytest.fixture
def remote_pair(repo, tmp_path):
    """``repo`` with an ``origin`` bare remote and a second clone that pushes to it."""
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(bare), str(other))
    for k, v in (("user.name", "O"), ("user.email", "o@example.invalid"),
                 ("commit.gpgsign", "false")):
        _git(other, "config", k, v)
    return repo, other


def test_fetch_reports_new_and_updated_refs_without_touching_the_worktree(remote_pair):
    repo, other = remote_pair
    (other / "docs" / "a.md").write_text("upstream\n")
    _git(other, "commit", "-qam", "upstream change")
    _git(other, "push", "-q", "origin", "main")
    _git(other, "push", "-q", "origin", "main:feature")
    _git(repo, "branch", "--set-upstream-to=origin/main", "main")
    plan = pre("fetch", repo, remote="origin")
    assert plan["remote"] == "origin" and plan["prune"] is False
    out = run("fetch", repo, remote="origin")
    assert not out.get("error"), out
    assert out["new_refs"] == ["origin/feature"] and out["updated_refs"] == ["origin/main"]
    assert out["behind"] == 1 and out["ahead"] == 0
    assert (repo / "docs" / "a.md").read_text() == "a\n"


def test_fetch_defaults_to_origin(remote_pair):
    repo, _ = remote_pair
    out = run("fetch", repo)
    assert not out.get("error"), out
    assert out["remote"] == "origin" and out["remote_defaulted"] is True


def test_fetch_never_prunes_even_when_config_asks(remote_pair):
    repo, other = remote_pair
    _git(other, "push", "-q", "origin", "main:gone")
    run("fetch", repo, remote="origin")
    assert _git(repo, "show-ref", "--verify", "-q", "refs/remotes/origin/gone",
                check=False).returncode == 0
    _git(other, "push", "-q", "origin", "--delete", "gone")
    _git(repo, "config", "fetch.prune", "true")
    out = run("fetch", repo, remote="origin")
    assert not out.get("error"), out
    assert _git(repo, "show-ref", "--verify", "-q", "refs/remotes/origin/gone",
                check=False).returncode == 0


@pytest.mark.parametrize("remote", ["nope", "ext::sh -c touch% /tmp/pwned",
                                    "https://example.invalid/x.git", "--upload-pack=touch"])
def test_fetch_accepts_only_configured_remotes(remote_pair, remote):
    repo, _ = remote_pair
    out = run("fetch", repo, remote=remote)
    assert out["error"] and "unknown remote" in out["message"] and out["remotes"] == ["origin"]


def test_fetch_with_no_remotes(repo):
    out = run("fetch", repo)
    assert out["error"] and "no remotes" in out["message"]


def test_fetch_redacts_credentials_in_the_url(repo):
    _git(repo, "remote", "add", "origin", "https://user:s3cret@example.invalid/r.git")
    plan = pre("fetch", repo)
    assert plan["url"] == "https://example.invalid/r.git"


# ── clone ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "/tmp/some/repo", "file:///tmp/r.git", "ext::sh -c touch% /tmp/x",
    "fd::17", "--upload-pack=touch /tmp/x", "../sibling", "host:path",
    "https://example.invalid/a b.git",
])
def test_clone_refuses_unsafe_urls(tmp_path, url):
    out = g.run_op("clone", {"url": url, "dest": "docs/x"}, str(tmp_path), write_check=docs_only)
    assert out["error"] and "url" in out["message"]
    assert not (tmp_path / "docs" / "x").exists()


@pytest.mark.parametrize("url, dest", [
    ("https://example.invalid/org/thing.git", "thing"),
    ("git@example.invalid:org/thing.git", "thing"),
    ("ssh://git@example.invalid/org/thing", "thing"),
])
def test_clone_plan_accepts_remote_urls_and_derives_dest(tmp_path, url, dest):
    plan = g.preflight("clone", {"url": url}, str(tmp_path), write_check=lambda *a: "")
    assert not plan.get("error"), plan
    assert plan["dest"] == dest and plan["submodules"] is False
    assert "_url" not in plan and "_root" not in plan


@pytest.mark.parametrize("dest, fragment", [
    ("../out", "inside the project root"), (".", "dest is required"),
    ("-x", "not acceptable"),
])
def test_clone_refuses_bad_destinations(tmp_path, dest, fragment):
    out = g.preflight("clone", {"url": "https://example.invalid/r.git", "dest": dest},
                      str(tmp_path), write_check=docs_only)
    assert out["error"] and fragment in out["message"]


def test_clone_outside_the_write_policy_asks_rather_than_refuses(tmp_path):
    plan = g.preflight("clone", {"url": "https://example.invalid/r.git", "dest": "src"},
                       str(tmp_path), write_check=docs_only)
    assert not plan.get("error"), plan
    assert plan["outside_policy"] == [{"path": "src", "reason": "src is not writable"}]
    out = g.run_op("clone", {"url": "https://example.invalid/r.git", "dest": "src"},
                   str(tmp_path), write_check=docs_only)
    assert out["error"] and "not approved" in out["message"]
    assert not (tmp_path / "src").exists()


def test_clone_refuses_an_existing_destination(tmp_path):
    (tmp_path / "docs" / "r").mkdir(parents=True)
    out = g.preflight("clone", {"url": "https://example.invalid/r.git", "dest": "docs/r"},
                      str(tmp_path), write_check=docs_only)
    assert out["error"] and "already exists" in out["message"]


def test_clone_refuses_a_symlinked_parent_outside_the_project(tmp_path):
    proj, outside = tmp_path / "proj", tmp_path / "outside"
    proj.mkdir()
    outside.mkdir()
    (proj / "docs").symlink_to(outside)
    out = g.preflight("clone", {"url": "https://example.invalid/r.git", "dest": "docs/r"},
                      str(proj), write_check=docs_only)
    assert out["error"] and "outside the project root" in out["message"]


def test_clone_rejects_a_bad_branch(tmp_path):
    out = g.preflight("clone", {"url": "https://example.invalid/r.git", "dest": "docs/r",
                                "branch": "--upload-pack=x"}, str(tmp_path), write_check=docs_only)
    assert out["error"] and "branch" in out["message"]


def test_clone_does_not_need_a_repository(tmp_path):
    out = g.preflight("clone", {"url": "https://example.invalid/r.git", "dest": "docs/r"},
                      str(tmp_path), write_check=docs_only)
    assert not out.get("error"), out


def _bare_with_commit(tmp_path, repo):
    bare = tmp_path / "src.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    _git(repo, "push", "-q", str(bare), "main", "main:side")
    return bare


def test_clone_executes_into_a_new_directory(tmp_path, repo, monkeypatch):
    """Success path, using a local bare repo: the URL check and the protocol
    allowlist are widened for this test only (both are covered above/below)."""
    bare = _bare_with_commit(tmp_path, repo)
    monkeypatch.setattr(g, "_valid_clone_url", lambda u: str(u))
    monkeypatch.setattr(g, "_ALLOWED_PROTOCOLS", "file:https:ssh:git")
    proj = tmp_path / "work"
    proj.mkdir()
    out = g.run_op("clone", {"url": f"file://{bare}", "dest": "docs/copy", "branch": "side"},
                   str(proj), write_check=docs_only)
    assert not out.get("error"), out
    assert (proj / "docs" / "copy" / "docs" / "a.md").read_text() == "a\n"
    assert _git(proj / "docs" / "copy", "symbolic-ref", "--short", "HEAD").stdout.strip() == "side"
    assert out["commit"] and len(out["commit"]) == 40


def test_clone_protocol_allowlist_is_enforced_by_git(tmp_path, repo, monkeypatch):
    """Second layer: with the URL check bypassed, git itself refuses file://."""
    bare = _bare_with_commit(tmp_path, repo)
    monkeypatch.setattr(g, "_valid_clone_url", lambda u: str(u))
    proj = tmp_path / "work"
    proj.mkdir()
    out = g.run_op("clone", {"url": f"file://{bare}", "dest": "docs/copy"},
                   str(proj), write_check=docs_only)
    assert out["error"] and "not allowed" in out["message"]
    assert not (proj / "docs" / "copy").exists()


def test_protocol_allowlist_only_applies_to_clone():
    assert g.git_env(restrict_protocols=True)["GIT_ALLOW_PROTOCOL"] == "https:ssh:git"
    assert "GIT_ALLOW_PROTOCOL" not in g.git_env() or \
        os.environ.get("GIT_ALLOW_PROTOCOL") == g.git_env()["GIT_ALLOW_PROTOCOL"]


# ── dispatch completeness ─────────────────────────────────────────────────

@pytest.mark.parametrize("op", [o for o in g.OPS if o not in g.READ_ONLY_OPS])
def test_every_mutating_op_has_a_preflight_and_a_dispatch(repo, op):
    """The old dispatch fell through to switch; a missing branch must now fail loudly."""
    p = pre(op, repo)
    assert p is not None
    assert "has no preflight" not in p.get("message", "")
    assert "has no dispatch" not in run(op, repo).get("message", "")
