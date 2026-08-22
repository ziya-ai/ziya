"""
Regression tests for write-policy / prompt consistency.

Two independent defects made models believe a project-configured writable
path (e.g. ``tests/``) was read-only:

  1. ``get_effective_policy()`` was the only policy accessor that did NOT
     call ``_ensure_loaded_for_root``, so it returned DEFAULT_WRITE_POLICY
     unless ``load_for_project`` had already run.  Prompt builders read the
     policy through it, while enforcement read it through
     ``is_direct_write_allowed`` — which DID lazy-load.  The prompt and the
     enforcement therefore disagreed, intermittently, based on call order.

  2. The shell-policy prompt block hardcoded ".ziya/, /tmp/" and the
     absolute claim "For ALL code changes to project files, provide git
     diffs", appended after (and thus overriding) the accurate computed
     listing.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@pytest.fixture
def project_with_policy(tmp_path, monkeypatch):
    """A project root registered under a fake ~/.ziya with tests/ writable."""
    home = tmp_path / "home"
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    proj_dir = home / ".ziya" / "projects" / "p-abc"
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.json").write_text(json.dumps({
        "id": "p-abc",
        "path": str(root),
        "settings": {"writePolicy": {
            "safe_write_paths": ["tests/", "design/"],
            "allowed_write_patterns": ["*.md"],
        }},
    }))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(root))
    return root


@pytest.fixture
def fresh_manager(monkeypatch):
    """A brand-new WritePolicyManager with no load_for_project() call."""
    import app.config.write_policy as wp
    monkeypatch.setattr(wp, "_manager", None)
    return wp.get_write_policy_manager()


class TestEffectivePolicyLazyLoads:
    """get_effective_policy() must agree with the enforcement accessors."""

    def test_reports_project_paths_without_explicit_load(
        self, project_with_policy, fresh_manager
    ):
        # No load_for_project() — this is the state a fresh server process
        # is in until someone hits GET /write-policy/{id}.
        policy = fresh_manager.get_effective_policy()
        assert "tests/" in policy["safe_write_paths"], (
            "get_effective_policy() returned defaults; the prompt built from "
            "it will understate what file_write actually permits"
        )
        assert "*.md" in policy["allowed_write_patterns"]

    def test_agrees_with_enforcement_on_same_fresh_manager(
        self, project_with_policy, fresh_manager
    ):
        """The precise inconsistency: prompt says no, enforcement says yes."""
        target = "tests/test_new.py"
        allowed, _ = fresh_manager.is_direct_write_allowed(
            target, file_exists=False
        )
        policy = fresh_manager.get_effective_policy()
        prompt_claims_writable = any(
            target.startswith(p) for p in policy["safe_write_paths"]
        )
        assert allowed is True
        assert prompt_claims_writable == allowed, (
            "prompt-facing policy and enforcement disagree about "
            f"{target!r}: enforcement={allowed}, prompt={prompt_claims_writable}"
        )

    def test_order_independent(self, project_with_policy, fresh_manager):
        """Reading the policy first must match reading it after enforcement."""
        before = fresh_manager.get_effective_policy()["safe_write_paths"]
        fresh_manager.is_direct_write_allowed("tests/x.py", file_exists=False)
        after = fresh_manager.get_effective_policy()["safe_write_paths"]
        assert before == after, (
            "policy visible to the prompt changed after an enforcement call — "
            "this is the source of the intermittency"
        )

    def test_explicit_project_root_argument(self, project_with_policy, fresh_manager):
        policy = fresh_manager.get_effective_policy(
            project_root=str(project_with_policy)
        )
        assert "design/" in policy["safe_write_paths"]

    def test_defaults_when_no_project_registered(self, tmp_path, monkeypatch):
        import app.config.write_policy as wp
        monkeypatch.setattr(wp, "_manager", None)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "empty")
        monkeypatch.setenv("ZIYA_USER_CODEBASE_DIR", str(tmp_path / "nowhere"))
        policy = wp.get_write_policy_manager().get_effective_policy()
        assert ".ziya/" in policy["safe_write_paths"]
        assert "tests/" not in policy["safe_write_paths"]


class TestSessionContextReflectsProjectPolicy:
    """The rendered prompt block must name the configured paths."""

    def test_block_lists_project_configured_paths(
        self, project_with_policy, fresh_manager
    ):
        from app.utils.session_context_prompt import build_session_context_section
        out = build_session_context_section(
            project_root=str(project_with_policy)
        )
        assert "tests/" in out, (
            "Session Context omitted a writable path the project configured"
        )
        assert "*.md" in out


class TestShellBlockWritableSummary:
    """The shell block must not contradict the computed policy."""

    def test_summary_names_configured_paths(
        self, project_with_policy, fresh_manager
    ):
        from app.extensions.prompt_extensions.mcp_prompt_extensions import (
            _get_shell_writable_summary,
        )
        summary = _get_shell_writable_summary()
        assert "tests/" in summary
        assert "*.md" in summary

    def test_summary_excludes_dev_null(self, project_with_policy, fresh_manager):
        from app.extensions.prompt_extensions.mcp_prompt_extensions import (
            _get_shell_writable_summary,
        )
        assert "/dev/null" not in _get_shell_writable_summary()

    def test_summary_notes_direct_write_mode(
        self, project_with_policy, fresh_manager, monkeypatch
    ):
        from app.extensions.prompt_extensions.mcp_prompt_extensions import (
            _get_shell_writable_summary,
        )
        fresh_manager.get_effective_policy()  # force the lazy load
        monkeypatch.setitem(
            fresh_manager.policy, "direct_write_mode", "all_files"
        )
        assert "all_files" in _get_shell_writable_summary()

    def test_summary_falls_back_on_policy_error(self, monkeypatch):
        import app.config.write_policy as wp
        from app.extensions.prompt_extensions import mcp_prompt_extensions as mpe

        def boom():
            raise RuntimeError("policy unavailable")

        monkeypatch.setattr(wp, "get_write_policy_manager", boom)
        summary = mpe._get_shell_writable_summary()
        assert ".ziya/" in summary  # generic fallback, no crash


class TestBasePromptDefersToEffectivePolicy:
    """Static instructions must not override effective writable paths."""

    def test_base_prompt_has_no_unconditional_diff_mandate(self):
        from app.agents.prompts import original_template

        assert (
            "ALWAYS format code changes using the specified git diff format"
            not in original_template
        )
        assert 'listed under "Writable paths (effective)"' in original_template

    def test_mcp_self_check_has_no_unconditional_diff_mandate(self):
        import inspect
        from app.extensions.prompt_extensions import mcp_prompt_extensions as mpe

        src = inspect.getsource(mpe)
        assert (
            "If modifying files, I must provide a Git diff patch instead!"
            not in src
        )

    def test_gemini_extensions_have_no_all_changes_diff_mandate(self):
        import inspect
        from app.extensions.prompt_extensions import gemini_extensions

        src = inspect.getsource(gemini_extensions)
        assert "Use standard git diffs for all code changes" not in src
        assert (
            "strictly adhere to the git diff format specified in the instructions"
            not in src
        )
        assert '"Writable paths (effective)"' in src

    def test_sonnet_post_instructions_defer_to_effective_policy(self):
        import inspect
        from app.extensions.post_instructions import sonnet_post_instructions

        src = inspect.getsource(sonnet_post_instructions)
        assert (
            "All responses that involve specific (not general) changes"
            not in src
        )
        assert '"Writable paths (effective)"' in src


class TestNoAbsoluteDiffMandateInShellBlock:
    """The shell block must not assert that ALL project files need diffs."""

    def test_no_all_code_changes_claim(self):
        import inspect
        from app.extensions.prompt_extensions import mcp_prompt_extensions as mpe
        src = inspect.getsource(mpe)
        assert "For ALL code changes to project files, provide git diffs" not in src, (
            "absolute diff mandate reintroduced; it contradicts the computed "
            "writable-path listing and wins by recency"
        )

    def test_no_truncated_prohibition_header(self):
        import inspect
        from app.extensions.prompt_extensions import mcp_prompt_extensions as mpe
        src = inspect.getsource(mpe)
        assert "NEVER use tools to:\n## MCP Tool Usage" not in src, (
            "dangling 'NEVER use tools to:' with no object reintroduced"
        )
