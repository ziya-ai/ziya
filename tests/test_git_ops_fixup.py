"""
``fixup`` op (design/git-tool.md, 2026-10-10): fix a commit below HEAD
without rewriting history.

The op records a NEW commit ``fixup! <target subject>``; the human folds it
in later with the ``fold_in`` command the result carries.  The seam that
matters is that this message is one ``git rebase --autosquash`` really folds
into the target: ``test_fold_in_command_squashes_the_fix_into_the_target``
runs that command and inspects the rewritten target.

Real git against a throwaway repository, as in tests/test_git_ops.py.
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
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=check, env=env)


def _allow_all(rel, root, exists):
    return ""


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for k, v in (("user.name", "Test"), ("user.email", "t@example.invalid"),
                 ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        _git(root, "config", k, v)
    for name, text, msg in (("a.py", "a = 1\n", "add a"),
                            ("b.py", "b = 1\n", "add b"),
                            ("c.py", "c = 1\n", "add c")):
        (root / name).write_text(text)
        _git(root, "add", name)
        _git(root, "commit", "-qm", msg)
    return root


def _sha(root, rev):
    return _git(root, "rev-parse", rev).stdout.strip()


def run(op, root, **args):
    return g.run_op(op, args, str(root), write_check=_allow_all, model="m")


def test_fixup_records_a_new_commit_and_rewrites_nothing(repo):
    target = _sha(repo, "HEAD~1")              # "add b", in the middle
    before = _git(repo, "rev-list", "HEAD").stdout.split()
    (repo / "b.py").write_text("b = 2\n")
    run("add", repo, paths=["b.py"])
    out = run("fixup", repo, target="HEAD~1")
    assert not out.get("error"), out
    assert out["target"] == target and out["target_subject"] == "add b"
    assert out["files"] == ["b.py"]
    assert [ln.split(" ", 1)[1] for ln in out["commits_after_target"]] == ["add c"]
    # History below the new commit is untouched: the old tip is HEAD's parent.
    after = _git(repo, "rev-list", "HEAD").stdout.split()
    assert after[1:] == before
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "fixup! add b"
    assert g.provenance_line("m") in _git(repo, "log", "-1", "--format=%B").stdout
    base = out["fold_in"].split()[-1]
    assert base.endswith("~1") and target.startswith(base[:-2])


def test_fold_in_command_squashes_the_fix_into_the_target(repo):
    (repo / "b.py").write_text("b = 2\n")
    run("add", repo, paths=["b.py"])
    out = run("fixup", repo, target="HEAD~1", message="why it changed")
    assert not out.get("error"), out
    cmd = out["fold_in"]
    assert cmd.startswith("GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash ")
    env = {**os.environ, "GIT_SEQUENCE_EDITOR": ":", "GIT_EDITOR": ":"}
    _git(repo, *cmd.split()[2:], env=env)
    subjects = _git(repo, "log", "--format=%s").stdout.splitlines()
    assert subjects == ["add c", "add b", "add a"], "the fixup did not fold in"
    assert _git(repo, "show", "HEAD~1:b.py").stdout == "b = 2\n"
    assert _git(repo, "show", "HEAD:c.py").stdout == "c = 1\n"


def test_fixup_of_the_root_commit_folds_with_root(repo):
    (repo / "a.py").write_text("a = 2\n")
    run("add", repo, paths=["a.py"])
    out = run("fixup", repo, target="HEAD~2")
    assert out["fold_in"] == "GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash --root"
    env = {**os.environ, "GIT_SEQUENCE_EDITOR": ":", "GIT_EDITOR": ":"}
    _git(repo, *out["fold_in"].split()[2:], env=env)
    assert _git(repo, "log", "--format=%s").stdout.splitlines() == ["add c", "add b", "add a"]
    assert _git(repo, "show", "HEAD~2:a.py").stdout == "a = 2\n"


def test_fixup_with_paths_commits_exactly_those_paths(repo):
    (repo / "b.py").write_text("b = 3\n")
    (repo / "c.py").write_text("c = 3\n")
    out = run("fixup", repo, target="HEAD~1", paths=["b.py"])
    assert not out.get("error"), out
    assert _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split() == ["b.py"]
    assert (repo / "c.py").read_text() == "c = 3\n"


def test_preflight_shows_the_plan_and_changes_nothing(repo):
    (repo / "b.py").write_text("b = 2\n")
    run("add", repo, paths=["b.py"])
    head = _sha(repo, "HEAD")
    pf = g.preflight("fixup", {"target": "HEAD~1"}, str(repo), write_check=_allow_all, model="m")
    assert not pf.get("error"), pf
    assert pf["op"] == "fixup" and pf["message"].startswith("fixup! add b\n")
    assert "_paths" not in pf
    assert _sha(repo, "HEAD") == head, "preflight must not commit"


@pytest.mark.parametrize("target, fragment", [
    (None, "target is required"),
    ("", "target is required"),
    ("-n", "not an acceptable revision"),
    ("HEAD --amend", "not an acceptable revision"),
    ("nope", "does not name a commit"),
])
def test_bad_targets_are_refused(repo, target, fragment):
    (repo / "b.py").write_text("b = 2\n")
    run("add", repo, paths=["b.py"])
    out = run("fixup", repo, target=target)
    assert out["error"] and fragment in out["message"], out


def test_a_target_not_on_this_branch_is_refused(repo):
    _git(repo, "switch", "-q", "-c", "side")
    (repo / "s.py").write_text("s\n")
    _git(repo, "add", "s.py")
    _git(repo, "commit", "-qm", "side only")
    side = _sha(repo, "HEAD")
    _git(repo, "switch", "-q", "main")
    (repo / "b.py").write_text("b = 2\n")
    run("add", repo, paths=["b.py"])
    out = run("fixup", repo, target=side)
    assert out["error"] and "not on the current branch" in out["message"]


def test_nothing_staged_is_refused(repo):
    out = run("fixup", repo, target="HEAD~1")
    assert out["error"] and "nothing staged" in out["message"]


def test_a_published_target_is_allowed_with_a_warning(repo, tmp_path):
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "main")
    (repo / "b.py").write_text("b = 2\n")
    run("add", repo, paths=["b.py"])
    out = run("fixup", repo, target="HEAD~1")
    assert not out.get("error"), out
    assert out["target_published_on"] == ["origin/main"]
    assert "rewrites published history" in out["warning"]


def test_an_unpublished_target_has_no_warning(repo):
    (repo / "b.py").write_text("b = 2\n")
    run("add", repo, paths=["b.py"])
    out = run("fixup", repo, target="HEAD~1")
    assert "warning" not in out and "target_published_on" not in out


def test_no_rewriting_op_was_added():
    """fixup never rewrites history: no rebase or amend anywhere in the op."""
    import inspect
    for fn in (g.plan_fixup, g.op_fixup, g._record_commit):
        src = inspect.getsource(fn)
        assert '"rebase"' not in src and '"--amend"' not in src and '"--fixup' not in src
    for name in ("rebase", "amend"):
        assert name not in g.OPS
