"""
A task-scope write grant naming an ABSOLUTE directory outside the project
root must admit destructive shell writes (cp/mv/mkdir) into it.

Regression for the GFX Stage 2 card's deploy hop: the build block copies
the rebuilt ``templates/`` into the installed package
(``<site-packages>/app/templates``, outside the project).  Three runs in a
row failed at that ``cp`` with "WRITE BLOCKED ... use git diffs".  The
checker itself already accepted absolute grants; the card's block scope
simply never carried the entry its description promised.  This pins the
checker half of the seam so a future refusal can be attributed to the
card, not the policy.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config.write_policy import WritePolicyManager  # noqa: E402
from app.mcp_servers.shell_server import ShellServer  # noqa: E402
from app.mcp_servers.write_policy import ShellWriteChecker  # noqa: E402


@pytest.fixture
def project_root(tmp_path):
    proj = tmp_path / "project"
    (proj / "templates").mkdir(parents=True)
    (proj / "templates" / "index.html").write_text("<html/>")
    return str(proj)


@pytest.fixture
def site_templates(tmp_path):
    """Stand-in for <site-packages>/app/templates: a sibling of the project."""
    d = tmp_path / "site-packages" / "app" / "templates"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def split():
    return ShellServer()._split_by_shell_operators


@pytest.fixture
def checker(project_root):
    pm = WritePolicyManager()
    pm.load_for_project("test-project", project_root)
    c = ShellWriteChecker(pm)
    c.set_project_root(project_root)
    try:
        yield c
    finally:
        c.clear_task_scope()
        c.clear_project_root()


def _cp(site_templates):
    # Verbatim shape of the command the card's build block issues.
    return f"cp -R templates/. {site_templates}/"


def test_refused_without_grant(checker, split, site_templates):
    ok, reason = checker.check(_cp(site_templates), split)
    assert not ok
    assert "blocked" in reason.lower()


def test_admitted_with_absolute_dir_grant(checker, split, project_root, site_templates):
    checker.set_task_scope({
        "project_root": project_root,
        "writable": [{"path": site_templates, "is_dir": True}],
    })
    ok, reason = checker.check(_cp(site_templates), split)
    assert ok, reason


def test_grant_is_bounded_to_the_directory(checker, split, project_root, site_templates):
    """The grant admits the named directory and nothing beside it."""
    checker.set_task_scope({
        "project_root": project_root,
        "writable": [{"path": site_templates, "is_dir": True}],
    })
    sibling = os.path.join(os.path.dirname(site_templates), "server.py")
    ok, _ = checker.check(f"cp templates/index.html {sibling}", split)
    assert not ok


def test_admitted_after_cd_into_project(checker, split, project_root, site_templates):
    """The cwd simulation must not undo an absolute grant."""
    checker.set_task_scope({
        "project_root": project_root,
        "writable": [{"path": site_templates, "is_dir": True}],
    })
    ok, reason = checker.check(
        f"cd {project_root} && npm run build && {_cp(site_templates)}", split,
    )
    assert ok, reason
