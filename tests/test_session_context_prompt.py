"""Tests for app.utils.session_context_prompt.

The helper has to behave correctly under three conditions:

  1. Chat path (no task scope) — emits Session Context plus the
     base write-policy section, nothing scope-specific.
  2. Task path (task scope present) — adds writable/readable/tools/
     skills/shell sections layered on top of the base policy.
  3. Failure isolation — if the write-policy manager raises, the
     helper still emits the Session Context lines and skips the
     writable section silently (never bubbles).
"""

import datetime
import os
import sys
from typing import List, Optional
from unittest.mock import patch

import pytest


# ── Fake task-scope objects (duck-typed; no Pydantic) ───────────────

class _Entry:
    def __init__(self, path: str, *, is_dir=False, read=True, write=False, context=False):
        self.path = path
        self.is_dir = is_dir
        self.read = read
        self.write = write
        self.context = context


class _Scope:
    def __init__(self, *, paths=None, tools=None, skills=None, shell_commands=None):
        self.paths = paths or []
        self.tools = tools or []
        self.skills = skills or []
        self.shell_commands = shell_commands or []


# ── Imports under test ──────────────────────────────────────────────

from app.utils.session_context_prompt import build_session_context_section


# ── Fixtures ────────────────────────────────────────────────────────

NOW = datetime.datetime(2026, 1, 2, 3, 4, 5)


@pytest.fixture(autouse=True)
def _stub_write_policy(monkeypatch):
    """Default: empty base policy.  Individual tests override."""
    class _Stub:
        def get_effective_policy(self):
            return {
                "safe_write_paths": [".ziya/", "/tmp/"],
                "allowed_write_patterns": [],
                "direct_write_mode": "none",
            }

    monkeypatch.setattr(
        "app.config.write_policy.get_write_policy_manager",
        lambda: _Stub(),
    )


# ── Tests ───────────────────────────────────────────────────────────

class TestSessionContextHeader:
    def test_includes_project_root_when_set(self):
        out = build_session_context_section(
            project_root="/foo/bar", now=NOW,
        )
        assert '<CurrentProjectRoot value="/foo/bar" />' in out
        assert '<CurrentWorkingDirectory value="/foo/bar" />' in out
        assert '<CurrentDateTime value="2026-01-02 03:04:05" />' in out

    def test_omits_project_root_when_none(self):
        out = build_session_context_section(project_root=None, now=NOW)
        assert "CurrentProjectRoot" not in out
        # cwd falls through to os.getcwd()
        assert '<CurrentWorkingDirectory ' in out

    def test_explicit_cwd_overrides_project_root(self):
        out = build_session_context_section(
            project_root="/foo/bar", cwd="/foo/bar/sub", now=NOW,
        )
        assert '<CurrentWorkingDirectory value="/foo/bar/sub" />' in out

    def test_conversation_start_when_provided(self):
        out = build_session_context_section(
            project_root="/p", now=NOW,
            conv_start_iso="2025-12-31 23:59:00",
        )
        assert '<ConversationStartTime value="2025-12-31 23:59:00" />' in out


class TestWritableSection:
    def test_base_policy_only(self):
        out = build_session_context_section(project_root="/p", now=NOW)
        assert "### Writable paths (effective)" in out
        assert "`.ziya/`" in out
        assert "`/tmp/`" in out
        # Base policy with no patterns should NOT mention patterns line
        assert "patterns allowed for direct write" not in out

    def test_task_scope_writes_listed_as_additive(self):
        scope = _Scope(paths=[
            _Entry("frontend/src/foo.tsx", write=True),
            _Entry("docs/", is_dir=True, write=True),
            _Entry("readonly.txt"),  # read-only — appears in readable, NOT writable
        ])
        out = build_session_context_section(
            project_root="/p", task_scope=scope, now=NOW,
        )
        # Slice the writable section out of the full block so we can
        # assert against its contents independently of the readable
        # section (where read-only entries DO appear).
        wstart = out.index("### Writable paths")
        wend = out.index("### Readable paths")
        writable_block = out[wstart:wend]
        assert "Task scope grants (additive):" in writable_block
        assert "`frontend/src/foo.tsx`" in writable_block
        assert "`docs/`" in writable_block
        assert "readonly.txt" not in writable_block

    def test_direct_write_mode_all_files(self, monkeypatch):
        class _Stub:
            def get_effective_policy(self):
                return {
                    "safe_write_paths": [".ziya/"],
                    "allowed_write_patterns": [],
                    "direct_write_mode": "all_files",
                }
        monkeypatch.setattr(
            "app.config.write_policy.get_write_policy_manager",
            lambda: _Stub(),
        )
        out = build_session_context_section(project_root="/p", now=NOW)
        assert "direct_write_mode=`all_files`" in out

    def test_diff_fallback_note_present(self):
        out = build_session_context_section(project_root="/p", now=NOW)
        assert "must be modified via a git diff" in out


class TestReadableSection:
    def test_separates_in_project_from_out_of_project(self):
        scope = _Scope(paths=[
            _Entry("frontend/src/", is_dir=True, read=True),
            _Entry("/etc/somefile", read=True),
            _Entry("/var/log/x.log", write=True),  # write implies read
        ])
        out = build_session_context_section(
            project_root="/p", task_scope=scope, now=NOW,
        )
        assert "### Readable paths (task scope)" in out
        assert "In-project:" in out
        assert "`frontend/src/`" in out
        assert "Out-of-project (additive):" in out
        assert "`/etc/somefile`" in out
        assert "`/var/log/x.log`" in out

    def test_no_readable_section_without_paths(self):
        scope = _Scope()
        out = build_session_context_section(
            project_root="/p", task_scope=scope, now=NOW,
        )
        assert "### Readable paths" not in out

    def test_skips_entries_with_no_read_or_write(self):
        # An entry with read=False, write=False, context=True (preload-only)
        # should NOT appear in the readable section because the agent
        # doesn't need to know about it for tool-mediated reads.
        scope = _Scope(paths=[
            _Entry("file.md", read=False, write=False, context=True),
        ])
        out = build_session_context_section(
            project_root="/p", task_scope=scope, now=NOW,
        )
        assert "### Readable paths" not in out


class TestToolsSection:
    def test_listed_when_scope_narrows(self):
        scope = _Scope(tools=["file_read", "file_write", "render_diagram"])
        out = build_session_context_section(
            project_root="/p", task_scope=scope, now=NOW,
        )
        assert "### Allowed tools (task scope)" in out
        assert "`file_read`" in out
        assert "`file_write`" in out
        assert "`render_diagram`" in out
        assert "All other tools are filtered out of this run." in out

    def test_omitted_when_scope_empty(self):
        scope = _Scope()
        out = build_session_context_section(
            project_root="/p", task_scope=scope, now=NOW,
        )
        assert "### Allowed tools" not in out


class TestSkillsSection:
    def test_listed_when_scope_narrows(self):
        scope = _Scope(skills=["debug_mode", "code_review"])
        out = build_session_context_section(
            project_root="/p", task_scope=scope, now=NOW,
        )
        assert "### Allowed skills (task scope)" in out
        assert "`debug_mode`" in out

    def test_omitted_when_scope_empty(self):
        out = build_session_context_section(
            project_root="/p", task_scope=_Scope(), now=NOW,
        )
        assert "### Allowed skills" not in out


class TestShellSection:
    def test_separates_literal_from_regex(self):
        scope = _Scope(shell_commands=[
            "pytest",
            "make",
            r"re:^npm\s+(test|run\s+lint)$",
        ])
        out = build_session_context_section(
            project_root="/p", task_scope=scope, now=NOW,
        )
        assert "### Shell command grants (task scope, additive)" in out
        assert "Literal first-token grants:" in out
        assert "`pytest`" in out
        assert "`make`" in out
        assert "Regex grants" in out
        # The "re:" prefix should be stripped in display
        assert r"`^npm\s+(test|run\s+lint)$`" in out
        # Should mention always_blocked carve-out
        assert "always_blocked" in out

    def test_only_literals(self):
        scope = _Scope(shell_commands=["pytest"])
        out = build_session_context_section(
            project_root="/p", task_scope=scope, now=NOW,
        )
        assert "Literal first-token grants:" in out
        assert "Regex grants" not in out

    def test_omitted_when_empty(self):
        out = build_session_context_section(
            project_root="/p", task_scope=_Scope(), now=NOW,
        )
        assert "### Shell command grants" not in out


class TestFailureIsolation:
    def test_write_policy_raise_does_not_propagate(self, monkeypatch):
        def _boom():
            raise RuntimeError("policy unavailable")
        monkeypatch.setattr(
            "app.config.write_policy.get_write_policy_manager",
            _boom,
        )
        out = build_session_context_section(project_root="/p", now=NOW)
        assert "## Session Context" in out
        assert '<CurrentProjectRoot value="/p" />' in out
        # Writable section silently absent when base policy can't be read
        # AND there's no task scope — the chat path with a broken policy
        # manager should still get a usable header.
        assert "### Writable paths" not in out

    def test_write_policy_raise_with_task_scope_still_emits_scope(self, monkeypatch):
        def _boom():
            raise RuntimeError("policy unavailable")
        monkeypatch.setattr(
            "app.config.write_policy.get_write_policy_manager",
            _boom,
        )
        scope = _Scope(paths=[_Entry("foo.tsx", write=True)])
        out = build_session_context_section(
            project_root="/p", task_scope=scope, now=NOW,
        )
        # Even with no base policy, task-scope grants still appear so
        # the agent knows about its additive permissions.
        assert "### Writable paths (effective)" in out
        assert "`foo.tsx`" in out


class TestStructure:
    def test_has_leading_blank_lines_for_appending(self):
        out = build_session_context_section(project_root="/p", now=NOW)
        assert out.startswith("\n\n## Session Context")

    def test_full_block_ordering(self):
        scope = _Scope(
            paths=[_Entry("a.tsx", write=True)],
            tools=["file_write"],
            skills=["code_review"],
            shell_commands=["pytest"],
        )
        out = build_session_context_section(
            project_root="/p", task_scope=scope, now=NOW,
        )
        # All sections present
        assert "## Session Context" in out
        assert "### Writable paths" in out
        assert "### Readable paths" in out
        assert "### Allowed tools" in out
        assert "### Allowed skills" in out
        assert "### Shell command grants" in out
        # Order: writable → readable → tools → skills → shell
        idx_writable = out.index("### Writable paths")
        idx_readable = out.index("### Readable paths")
        idx_tools = out.index("### Allowed tools")
        idx_skills = out.index("### Allowed skills")
        idx_shell = out.index("### Shell command grants")
        assert idx_writable < idx_readable < idx_tools < idx_skills < idx_shell
