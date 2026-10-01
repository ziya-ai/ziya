"""
Tests for app/utils/git_ops.py — the structured git operations behind the
``git`` builtin tool (design/git-tool.md).

Every test runs real git against a throwaway repository.  The write policy is
injected (only ``docs/`` is writable) so the live project's policy cannot leak
in and make a refusal test pass or fail by accident.
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


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for k, v in (("user.name", "Test"), ("user.email", "t@example.invalid"),
                 ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        _git(root, "config", k, v)
    (root / "docs").mkdir()
    (root / "docs" / "a.md").write_text("a\n")
    (root / "src.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def run(op, root, **args):
    return g.run_op(op, args, str(root), write_check=docs_only, model="test-model")


# ── repository resolution ─────────────────────────────────────────────────

def test_not_a_repo_is_refused(tmp_path):
    out = g.run_op("status", {}, str(tmp_path), write_check=docs_only)
    assert out["error"] and "not inside a git work tree" in out["message"]


def test_unknown_op_is_refused(repo):
    out = run("reset", repo)
    assert out["error"] and "unknown op" in out["message"]


# ── status ────────────────────────────────────────────────────────────────

def test_status_reports_structure(repo):
    (repo / "docs" / "a.md").write_text("changed\n")
    (repo / "docs" / "new.md").write_text("n\n")
    _git(repo, "add", "docs/new.md")
    (repo / "scratch.txt").write_text("s\n")
    out = run("status", repo)
    assert out["branch"] == "main" and not out["clean"]
    assert {"path": "docs/new.md", "change": "added"} in out["staged"]
    assert {"path": "docs/a.md", "change": "modified"} in out["unstaged"]
    assert "scratch.txt" in out["untracked"]


def test_status_clean(repo):
    out = run("status", repo)
    assert out["clean"] is True and out["staged"] == [] and out["untracked"] == []


# ── path validation ───────────────────────────────────────────────────────

@pytest.mark.parametrize("bad, fragment", [
    ("--all", "begins with '-'"),
    ("-A", "begins with '-'"),
    (":/", "pathspec magic"),
    (":(exclude)docs", "pathspec magic"),
    ("../outside", "outside the project root"),
    (".git/config", "inside .git"),
    ("", "empty path"),
])
def test_hostile_paths_are_refused_before_git_runs(repo, bad, fragment):
    out = run("add", repo, paths=[bad])
    assert out["error"] and fragment in out["message"]


def test_paths_are_required(repo):
    out = run("add", repo)
    assert out["error"] and "paths is required" in out["message"]


# ── add: path scoping through the write policy ────────────────────────────

def test_add_dot_stages_only_writable_paths_and_reports_the_rest(repo):
    (repo / "docs" / "a.md").write_text("changed\n")
    (repo / "docs" / "b.md").write_text("new\n")
    (repo / "src.py").write_text("x = 2\n")
    out = run("add", repo, paths=["."])
    assert not out.get("error"), out
    staged = {s["path"] for s in out["staged"]}
    assert staged == {"docs/a.md", "docs/b.md"}
    assert [r["path"] for r in out["refused"]] == ["src.py"]
    # Positive check against git itself: src.py is NOT in the index.
    cached = _git(repo, "diff", "--cached", "--name-only").stdout.split()
    assert sorted(cached) == ["docs/a.md", "docs/b.md"]


def test_add_everything_refused_is_an_error_not_a_silent_noop(repo):
    (repo / "src.py").write_text("x = 3\n")
    out = run("add", repo, paths=["src.py"])
    assert out["error"] and "refused by the write policy" in out["message"]
    assert out["refused"][0]["path"] == "src.py"
    assert _git(repo, "diff", "--cached", "--name-only").stdout.strip() == ""


def test_add_with_no_matching_changes(repo):
    out = run("add", repo, paths=["docs"])
    assert out["error"] and "no changes match" in out["message"]


def test_add_stages_a_deletion(repo):
    (repo / "docs" / "a.md").unlink()
    out = run("add", repo, paths=["docs"])
    assert {"path": "docs/a.md", "change": "deleted"} in out["staged"]
    assert _git(repo, "diff", "--cached", "--name-status").stdout.strip() == "D\tdocs/a.md"


def test_add_treats_glob_characters_in_expanded_names_literally(repo):
    # A file literally named "docs/*.md" must stage only itself, not every
    # .md: expansion happened via status; the add runs with literal pathspecs.
    (repo / "docs" / "*.md").write_text("star\n")
    (repo / "docs" / "other.md").write_text("o\n")
    out = run("add", repo, paths=["docs/other.md"])
    assert [s["path"] for s in out["staged"]] == ["docs/other.md"]
    assert _git(repo, "diff", "--cached", "--name-only").stdout.split() == ["docs/other.md"]


def test_add_refuses_a_conflicted_file_that_still_has_markers(repo):
    _git(repo, "switch", "-q", "-c", "side")
    (repo / "docs" / "a.md").write_text("side\n")
    _git(repo, "commit", "-qam", "side")
    _git(repo, "switch", "-q", "main")
    (repo / "docs" / "a.md").write_text("main\n")
    _git(repo, "commit", "-qam", "main")
    _git(repo, "merge", "side", check=False)
    out = run("add", repo, paths=["docs/a.md"])
    assert out["error"] and "conflict markers" in out["refused"][0]["reason"]
    (repo / "docs" / "a.md").write_text("resolved\n")
    out = run("add", repo, paths=["docs/a.md"])
    assert not out.get("error"), out


# ── unstage ───────────────────────────────────────────────────────────────

def test_unstage_is_index_only(repo):
    (repo / "docs" / "a.md").write_text("changed\n")
    _git(repo, "add", "docs/a.md")
    out = run("unstage", repo, paths=["docs/a.md"])
    assert out["unstaged"] == ["docs/a.md"]
    assert _git(repo, "diff", "--cached", "--name-only").stdout.strip() == ""
    assert (repo / "docs" / "a.md").read_text() == "changed\n", "worktree must be untouched"


def test_unstage_with_nothing_staged(repo):
    out = run("unstage", repo, paths=["docs"])
    assert out["error"] and "nothing staged" in out["message"]


def test_unstage_before_first_commit(tmp_path):
    root = tmp_path / "fresh"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "docs").mkdir()
    (root / "docs" / "x.md").write_text("x\n")
    _git(root, "add", "docs/x.md")
    out = g.run_op("unstage", {"paths": ["docs/x.md"]}, str(root), write_check=docs_only)
    assert out.get("unstaged") == ["docs/x.md"], out
    assert (root / "docs" / "x.md").exists()


# ── commit ────────────────────────────────────────────────────────────────

def test_commit_requires_a_message(repo):
    (repo / "docs" / "a.md").write_text("c\n")
    _git(repo, "add", "-A")
    for msg in ("", "   "):
        out = run("commit", repo, message=msg)
        assert out["error"] and "message is required" in out["message"]


def test_commit_with_nothing_staged(repo):
    out = run("commit", repo, message="x")
    assert out["error"] and "nothing staged" in out["message"]


def test_commit_appends_provenance_trailers(repo):
    (repo / "docs" / "a.md").write_text("c\n")
    run("add", repo, paths=["docs"])
    out = run("commit", repo, message="Update docs")
    assert not out.get("error"), out
    body = _git(repo, "log", "-1", "--format=%B").stdout
    assert body.startswith("Update docs\n\n")
    assert f"Co-authored-by: {g.PROVENANCE_IDENTITY}" in body
    assert "Ziya-Model: test-model" in body
    assert out["commit"] == _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_commit_provenance_can_be_disabled(repo):
    (repo / "docs" / "a.md").write_text("c\n")
    run("add", repo, paths=["docs"])
    out = g.run_op("commit", {"message": "plain"}, str(repo), write_check=docs_only,
                   model="m", provenance=False)
    assert not out.get("error"), out
    assert _git(repo, "log", "-1", "--format=%B").stdout.strip() == "plain"


def test_commit_reports_partial_files(repo):
    (repo / "docs" / "a.md").write_text("staged\n")
    run("add", repo, paths=["docs/a.md"])
    (repo / "docs" / "a.md").write_text("staged\nand more\n")
    out = run("commit", repo, message="partial")
    assert out["partially_committed"] == ["docs/a.md"]
    assert _git(repo, "show", "HEAD:docs/a.md").stdout == "staged\n"


def test_commit_with_paths_is_all_or_nothing_under_the_write_policy(repo):
    (repo / "docs" / "a.md").write_text("c\n")
    (repo / "src.py").write_text("x = 9\n")
    head = _git(repo, "rev-parse", "HEAD").stdout
    out = run("commit", repo, message="m", paths=["docs/a.md", "src.py"])
    assert out["error"] and out["refused"][0]["path"] == "src.py"
    assert _git(repo, "rev-parse", "HEAD").stdout == head, "nothing may be committed"
    ok = run("commit", repo, message="m", paths=["docs/a.md"])
    assert not ok.get("error") and ok["files"] == ["docs/a.md"]
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split() == ["docs/a.md"]


def test_a_failing_hook_is_reported_not_bypassed(repo):
    hooks = repo / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'lint failed' >&2\nexit 1\n")
    hook.chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))
    (repo / "docs" / "a.md").write_text("c\n")
    run("add", repo, paths=["docs"])
    out = run("commit", repo, message="m")
    assert out["error"] and "lint failed" in out["message"]


# ── stash_push ────────────────────────────────────────────────────────────

def test_stash_push_keeps_untracked_in_place(repo):
    (repo / "docs" / "a.md").write_text("c\n")
    (repo / "notes.txt").write_text("mine\n")
    out = run("stash_push", repo, message="wip")
    assert out["stashed"] == ["docs/a.md"] and out["untracked_left_in_place"] == ["notes.txt"]
    assert (repo / "notes.txt").exists()
    assert "wip" in _git(repo, "stash", "list").stdout


def test_stash_push_with_nothing_tracked_changed(repo):
    (repo / "notes.txt").write_text("mine\n")
    out = run("stash_push", repo)
    assert out["error"] and "no tracked changes" in out["message"]


# ── switch ────────────────────────────────────────────────────────────────

def test_switch_refuses_a_dirty_tree(repo):
    (repo / "src.py").write_text("x = 5\n")
    out = run("switch", repo, branch="feature", create=True)
    assert out["error"] and out["uncommitted"] == ["src.py"]
    assert _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip() == "main"


def test_switch_create_and_back(repo):
    (repo / "notes.txt").write_text("untracked is fine\n")
    out = run("switch", repo, branch="feature", create=True)
    assert out == {**out, "from": "main", "to": "feature", "create": True}
    assert run("switch", repo, branch="main")["to"] == "main"


@pytest.mark.parametrize("name", ["-", "@{-1}", "a:b", "+main", "bad name", "a..b", "HEAD~1"])
def test_switch_rejects_unsafe_branch_names(repo, name):
    out = run("switch", repo, branch=name)
    assert out["error"]


# ── push ──────────────────────────────────────────────────────────────────

@pytest.fixture
def remote(repo, tmp_path):
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    return bare


def test_push_current_branch_and_report_commits(repo, remote):
    out = run("push", repo, remote="origin", branch="main", set_upstream=True)
    assert not out.get("error"), out
    assert out["new_remote_branch"] is True and len(out["pushed_commits"]) == 1
    assert _git(remote, "rev-parse", "main").stdout == _git(repo, "rev-parse", "HEAD").stdout


def test_push_only_the_current_branch(repo, remote):
    _git(repo, "branch", "other")
    out = run("push", repo, remote="origin", branch="other")
    assert out["error"] and "not the current branch" in out["message"]


def test_push_unknown_remote_lists_known_ones(repo, remote):
    out = run("push", repo, remote="upstream", branch="main")
    assert out["error"] and out["remotes"] == ["origin"]


def test_push_non_fast_forward_is_rejected_by_git(repo, remote, tmp_path):
    run("push", repo, remote="origin", branch="main")
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(remote), str(other))
    _git(other, "config", "user.email", "o@example.invalid")
    _git(other, "config", "user.name", "O")
    (other / "docs" / "a.md").write_text("theirs\n")
    _git(other, "commit", "-qam", "theirs")
    _git(other, "push", "-q", "origin", "main")
    (repo / "docs" / "a.md").write_text("ours\n")
    run("add", repo, paths=["docs"])
    run("commit", repo, message="ours")
    theirs = _git(remote, "rev-parse", "main").stdout
    out = run("push", repo, remote="origin", branch="main")
    assert out["error"] and "rejected" in out["message"]
    assert _git(remote, "rev-parse", "main").stdout == theirs, "remote must be unchanged"


def test_push_warns_about_uncommitted_work(repo, remote):
    (repo / "src.py").write_text("x = 7\n")
    out = run("push", repo, remote="origin", branch="main")
    assert "uncommitted" in out and out["uncommitted"] == ["src.py"]


# ── preflight ─────────────────────────────────────────────────────────────

def test_preflight_matches_what_run_would_do_and_changes_nothing(repo):
    (repo / "docs" / "a.md").write_text("c\n")
    (repo / "src.py").write_text("x = 2\n")
    pf = g.preflight("add", {"paths": ["."]}, str(repo), write_check=docs_only)
    assert [s["path"] for s in pf["to_stage"]] == ["docs/a.md"]
    assert [r["path"] for r in pf["refused"]] == ["src.py"]
    assert "_paths" not in pf
    assert _git(repo, "diff", "--cached", "--name-only").stdout.strip() == "", "preflight must not stage"


def test_preflight_shows_the_final_commit_message(repo):
    (repo / "docs" / "a.md").write_text("c\n")
    run("add", repo, paths=["docs"])
    pf = g.preflight("commit", {"message": "m"}, str(repo), write_check=docs_only, model="x")
    assert pf["files"] == ["docs/a.md"] and "Ziya-Model: x" in pf["message"]


def test_preflight_surfaces_a_refusal(repo):
    pf = g.preflight("commit", {"message": "m"}, str(repo), write_check=docs_only)
    assert pf["error"] and "nothing staged" in pf["message"]


def test_status_has_no_preflight(repo):
    assert g.preflight("status", {}, str(repo)) is None


# ── env hardening ─────────────────────────────────────────────────────────

def test_env_strips_redirects_and_config_injection(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.pager=sh'")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.pager")
    env = g.git_env()
    assert "GIT_DIR" not in env and "GIT_CONFIG_PARAMETERS" not in env
    assert "GIT_CONFIG_KEY_0" not in env
    assert env["GIT_EDITOR"] == ":" and env["GIT_TERMINAL_PROMPT"] == "0"


def test_provenance_joins_an_existing_trailer_block():
    msg = g.with_provenance("Subject\n\nBody\n\nSigned-off-by: A <a@b>", "m")
    assert msg.endswith("Signed-off-by: A <a@b>\nCo-authored-by: "
                        f"{g.PROVENANCE_IDENTITY}\nZiya-Model: m\n")
    assert g.with_provenance(msg, "m") == msg.rstrip() + "\n", "idempotent"


# ── untracked files are never overwritten (2026-09-25) ────────────────────
# ``switch`` is the only op that writes the worktree.  An untracked file the
# target branch tracks must survive: git refuses by default, and the tool
# never passes --force / --discard-changes / --merge.  Pinned because the
# guarantee is otherwise only git's default behaviour.

def test_switch_refuses_to_overwrite_an_untracked_file(repo):
    _git(repo, "switch", "-q", "-c", "side")
    (repo / "f.txt").write_text("theirs\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-qm", "side adds f.txt")
    _git(repo, "switch", "-q", "main")
    (repo / "f.txt").write_text("MINE\n")          # untracked on main
    out = run("switch", repo, branch="side")
    assert out["error"] and "untracked" in out["message"].lower()
    assert (repo / "f.txt").read_text() == "MINE\n"
    assert _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip() == "main"


def test_stash_push_never_takes_untracked_files(repo):
    (repo / "docs" / "a.md").write_text("c\n")
    (repo / "mine.txt").write_text("keep\n")
    run("stash_push", repo)
    listing = _git(repo, "stash", "show", "--include-untracked", "--name-only",
                   "stash@{0}", check=False).stdout.split()
    assert "mine.txt" not in listing
    assert (repo / "mine.txt").read_text() == "keep\n"


@pytest.mark.parametrize("op", ["pull", "fetch", "restore", "checkout", "rebase",
                                "reset", "clean", "merge", "worktree", "rm", "mv"])
def test_worktree_clobbering_ops_do_not_exist(repo, op):
    out = run(op, repo)
    assert out["error"] and "unknown op" in out["message"]


def test_no_op_invocation_can_carry_an_overwrite_flag():
    """Static check on the module: none of the forcing flags appear as args."""
    import inspect
    src = inspect.getsource(g)
    for flag in ('"--force"', '"-f"', '"--discard-changes"', '"--merge"',
                 '"--include-untracked"', '"-u"', '"--all"', '"--hard"'):
        assert flag not in src, f"git_ops passes {flag}"
