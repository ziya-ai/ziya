"""
Regression tests: prompt-facing write-policy reads must be ROOT-ADDRESSED.

Enforcement (``fileio.file_write`` -> ``is_direct_write_allowed``) passes the
request-scoped project root, so it always evaluates the right project's
policy.  Every prompt reader called ``get_effective_policy()`` with no root,
which resolves to ``self._project_root`` -- whatever project last touched the
shared singleton -- and ``_ensure_loaded_for_root`` then early-returns because
the roots "match".

With two projects open the result was a prompt describing the WRONG project:
the model was told a path required a git diff, tried ``file_write`` anyway,
and found it accepted.  That is not model confusion; the prompt was false.

test_write_policy_prompt_consistency.py covers the single-project case (its
fixtures set one ZIYA_USER_CODEBASE_DIR), which is why this survived it.

The ``/tmp`` defaults are removed from the fixture below, because the natural
place to build a fake project tree is under ``/tmp/`` -- itself a default
safe_write_path, so every write would be allowed for reasons unrelated to the
policy under test.

Which tests here actually REPRODUCE the bug, verified by restoring the
root-blind reads and re-running: the three in
``TestPromptDescribesTheCurrentProject`` that render a prompt, and the one in
``TestShellSummaryIsRootAddressed``.  The remaining four cannot fail against
pre-fix code by construction -- ``TestCacheBehaviour`` exercises the new
helper, which did not exist, and
``test_render_does_not_repin_the_shared_singleton`` guards against a plausible
WRONG fix (passing the root to the shared singleton, which would repair the
read while making concurrent windows thrash ``_project_root``) rather than
against the original defect.  Both kinds are worth keeping; neither should be
mistaken for a reproducer.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    """Two registered projects with DIFFERENT write policies.

    A allows ``docs/*`` only; B allows ``tests/`` and ``app/``.
    Returns ``(root_a, root_b)``.
    """
    import app.config.write_policy as wp

    home = tmp_path / "home"
    root_a, root_b = tmp_path / "A", tmp_path / "B"
    for r in (root_a, root_b):
        (r / "docs").mkdir(parents=True)
        (r / "tests").mkdir(parents=True)
        (r / "app" / "utils").mkdir(parents=True)
    specs = [
        ("p-a", root_a, {"allowed_write_patterns": ["docs/*"]}),
        ("p-b", root_b, {"safe_write_paths": ["tests/", "app/"]}),
    ]
    for pid, root, policy in specs:
        d = home / ".ziya" / "projects" / pid
        d.mkdir(parents=True)
        (d / "project.json").write_text(json.dumps({
            "id": pid, "path": str(root),
            "settings": {"writePolicy": policy},
        }))

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    # The server was launched in A, so this is the root-blind fallback.
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(root_a))
    # See module docstring: keep /tmp out of the picture.
    monkeypatch.setitem(
        wp.DEFAULT_WRITE_POLICY, "safe_write_paths", [".ziya/", "/dev/null"],
    )
    monkeypatch.setattr(wp, "_manager", None)
    wp.invalidate_policy_cache()
    yield str(root_a), str(root_b)
    wp.invalidate_policy_cache()


def _writable_lines(block: str) -> str:
    return "\n".join(
        line for line in block.splitlines() if "Project policy" in line
    )


class TestPromptDescribesTheCurrentProject:
    def test_does_not_leak_another_projects_policy(self, two_projects):
        """The reported bug, end to end."""
        import app.config.write_policy as wp
        from app.utils.session_context_prompt import (
            build_session_context_section,
        )
        root_a, root_b = two_projects
        pm = wp.get_write_policy_manager()
        # Any enforcement call in project A pins the singleton to A.
        pm.is_direct_write_allowed("docs/x.md", root_a, file_exists=False)

        shown = _writable_lines(
            build_session_context_section(project_root=root_b)
        )
        assert "tests/" in shown, (
            "project B's prompt omitted B's own writable paths; it rendered "
            f"whatever the singleton held:\n{shown}"
        )
        assert "docs/*" not in shown, (
            f"project B's prompt leaked project A's pattern:\n{shown}"
        )

    @pytest.mark.parametrize("target", ["app/utils/x.py", "tests/test_x.py"])
    def test_prompt_agrees_with_enforcement(self, two_projects, target):
        """Prompt and enforcement must agree for the SAME root.

        ORDER IS LOAD-BEARING: the prompt is rendered while the singleton is
        still pinned to project A, which is the real sequence (the block is
        built at request start, and some other project touched the manager
        last).  Calling enforcement on B first re-pins the singleton, so even
        the root-blind read returns B's policy and this test passes against
        unpatched code -- i.e. it would certify the bug.  Verified: with the
        pre-fix read restored, asserting in this order fails and the reversed
        order passes.
        """
        import app.config.write_policy as wp
        from app.utils.session_context_prompt import (
            build_session_context_section,
        )
        root_a, root_b = two_projects
        pm = wp.get_write_policy_manager()
        pm.is_direct_write_allowed("docs/x.md", root_a, file_exists=False)
        assert pm._project_root == root_a, "fixture precondition"

        shown = _writable_lines(
            build_session_context_section(project_root=root_b)
        )
        # Enforcement AFTER the render, exactly as file_write would run it.
        allowed, _ = pm.is_direct_write_allowed(target, root_b)
        prefix = target.split("/", 1)[0] + "/"
        assert allowed is True
        assert prefix in shown, (
            f"enforcement allows {target!r} but the prompt does not list "
            f"{prefix!r}, so the model will emit a diff instead:\n{shown}"
        )

    def test_render_does_not_repin_the_shared_singleton(self, two_projects):
        """A prompt render must not move the singleton's loaded project.

        Otherwise two windows on different projects thrash it, and the fix
        would merely relocate the race instead of removing it.
        """
        import app.config.write_policy as wp
        from app.utils.session_context_prompt import (
            build_session_context_section,
        )
        root_a, root_b = two_projects
        pm = wp.get_write_policy_manager()
        pm.is_direct_write_allowed("docs/x.md", root_a, file_exists=False)
        assert pm._project_root == root_a

        build_session_context_section(project_root=root_b)
        assert pm._project_root == root_a


class TestShellSummaryIsRootAddressed:
    def test_summary_names_the_current_projects_paths(
        self, two_projects, monkeypatch,
    ):
        import app.config.write_policy as wp
        import app.context as ctx
        from app.extensions.prompt_extensions import (
            mcp_prompt_extensions as mpe,
        )
        root_a, root_b = two_projects
        wp.get_write_policy_manager().is_direct_write_allowed(
            "docs/x.md", root_a, file_exists=False,
        )
        monkeypatch.setattr(ctx, "get_project_root_or_none", lambda: root_b)
        summary = mpe._get_shell_writable_summary()
        assert "tests/" in summary
        assert "docs/*" not in summary, (
            f"shell block leaked another project's pattern: {summary}"
        )


class TestCacheBehaviour:
    def test_policy_update_is_visible_to_the_prompt(self, two_projects):
        """A cached snapshot must not outlive the policy it describes."""
        import app.config.write_policy as wp
        _root_a, root_b = two_projects
        assert "design/" not in wp.effective_policy_for_root(
            root_b)["safe_write_paths"]
        wp.get_write_policy_manager().update_project_policy(
            "p-b", {"safe_write_paths": ["tests/", "app/", "design/"]},
        )
        assert "design/" in wp.effective_policy_for_root(
            root_b)["safe_write_paths"], (
            "stale cache: the prompt would keep describing the old policy"
        )

    def test_no_root_falls_back_to_the_singleton(self, two_projects):
        """Unchanged behaviour for the single-project / CLI case."""
        import app.config.write_policy as wp
        root_a, _root_b = two_projects
        pm = wp.get_write_policy_manager()
        pm.is_direct_write_allowed("docs/x.md", root_a, file_exists=False)
        assert (
            wp.effective_policy_for_root("")["allowed_write_patterns"]
            == pm.get_effective_policy()["allowed_write_patterns"]
        )

    def test_unregistered_root_does_not_report_another_project(
        self, two_projects, tmp_path,
    ):
        import app.config.write_policy as wp
        root_a, _root_b = two_projects
        pm = wp.get_write_policy_manager()
        pm.is_direct_write_allowed("docs/x.md", root_a, file_exists=False)
        policy = wp.effective_policy_for_root(str(tmp_path / "unknown"))
        assert "docs/*" not in policy["allowed_write_patterns"]
