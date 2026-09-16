"""``allowed_write_patterns`` (and task-scope ``pattern`` grants) apply only
inside the project root.

Both matchers computed a project-relative path for the target and then, when
the target did *not* resolve under the root, fell back to matching the raw
string -- and its basename -- against the glob.  So a ``*.md`` pattern meant
for the project's docs also approved ``cp x /etc/foo.md``, ``~/foo.md``, and
(via the cwd-aware shell check) ``cd /etc && cp x foo.md``.  A second defect
in the same lines: the containment test was ``resolved.startswith(root)``
with no separator, so a sibling directory ``<root>2/`` counted as inside.

Patterns are presented to the model as *project* policy.  A glob now matches
only when the target resolves to ``root`` or below it; anything else falls
through to a refusal.
"""
from __future__ import annotations

import os

import pytest

from app.config.write_policy import WritePolicyManager
from app.mcp_servers.write_policy import ShellWriteChecker


@pytest.fixture
def roots(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (tmp_path / "proj2").mkdir()          # sibling sharing the name prefix
    (root / "docs").mkdir()
    return str(root), str(tmp_path / "proj2")


@pytest.fixture
def pm(roots):
    root, _ = roots
    pm = WritePolicyManager()
    pm.load_for_project("t", root)
    pm._policy["allowed_write_patterns"] = ["*.md", "*/__tests__/**"]
    return pm


@pytest.fixture
def checker(pm, roots):
    c = ShellWriteChecker(pm)
    c.set_project_root(roots[0])
    yield c
    c.clear_project_root()
    c.clear_task_scope()


def _split():
    from app.mcp_servers.shell_server import ShellServer
    return ShellServer.__new__(ShellServer)._split_by_shell_operators


class TestBasePolicy:
    # Positive controls: the pattern still does its job inside the project.
    @pytest.mark.parametrize("target", ["README.md", "docs/a.md", "src/__tests__/x.py"])
    def test_relative_inside_root_matches(self, pm, roots, target):
        assert pm.is_write_allowed(target, roots[0])

    def test_absolute_inside_root_matches(self, pm, roots):
        assert pm.is_write_allowed(os.path.join(roots[0], "docs", "a.md"), roots[0])

    def test_root_with_trailing_slash(self, pm, roots):
        assert pm.is_write_allowed("README.md", roots[0] + os.sep)
        assert not pm.is_write_allowed("/etc/foo.md", roots[0] + os.sep)

    # The holes.
    @pytest.mark.parametrize("target", ["/etc/foo.md", "~/foo.md", "/etc/__tests__/x.py"])
    def test_absolute_outside_root_does_not_match(self, pm, roots, target):
        assert not pm.is_write_allowed(target, roots[0])

    def test_sibling_directory_is_outside(self, pm, roots):
        root, sibling = roots
        assert not pm.is_write_allowed(os.path.join(sibling, "foo.md"), root)

    def test_dotdot_escape_is_outside(self, pm, roots):
        root, _ = roots
        assert not pm.is_write_allowed("../proj2/x.md", root)
        assert not pm.is_write_allowed(os.path.join(root, "..", "proj2", "x.md"), root)

    def test_no_root_absolute_target_does_not_match(self, pm):
        assert not pm.is_write_allowed("/etc/foo.md", "")


class TestShellChecker:
    def test_cp_to_outside_md_refused(self, checker):
        ok, reason = checker.check("cp x /etc/foo.md", _split())
        assert not ok
        assert "/etc/foo.md" in reason

    def test_cd_outside_then_relative_md_refused(self, checker):
        ok, _ = checker.check("cd /etc && cp x foo.md", _split())
        assert not ok

    def test_cd_into_project_docs_then_relative_md_allowed(self, checker, roots):
        ok, reason = checker.check(f"cd {roots[0]}/docs && cp /tmp/x a.md", _split())
        assert ok, reason

    def test_redirect_to_outside_md_refused(self, checker):
        ok, _ = checker.check("echo hi > /etc/foo.md", _split())
        assert not ok

    def test_redirect_to_project_md_allowed(self, checker):
        ok, reason = checker.check("echo hi > notes.md", _split())
        assert ok, reason


class TestTaskScopePatternGrant:
    def test_grant_matches_inside_root(self, checker, roots):
        checker.set_task_scope({"writable": [{"pattern": "*.toml"}], "project_root": roots[0]})
        assert checker._task_scope_grants_write("a.toml")
        assert checker._task_scope_grants_write(os.path.join(roots[0], "cfg", "a.toml"))

    def test_grant_does_not_match_outside_root(self, checker, roots):
        root, sibling = roots
        checker.set_task_scope({"writable": [{"pattern": "*.toml"}], "project_root": root})
        assert not checker._task_scope_grants_write("/etc/a.toml")
        assert not checker._task_scope_grants_write(os.path.join(sibling, "a.toml"))
        assert not checker._task_scope_grants_write("../proj2/a.toml")

    def test_explicit_path_grant_unaffected(self, checker, roots):
        # Path grants may legitimately point outside the project.
        checker.set_task_scope({"writable": [{"path": "/tmp/scratch", "is_dir": True}],
                                "project_root": roots[0]})
        assert checker._task_scope_grants_write("/tmp/scratch/out.bin")
