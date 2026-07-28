"""
Tests for app.mcp.tools.fileio — FileIO builtin MCP tools.

Covers:
  - _resolve_and_validate: path traversal rejection, empty paths
  - FileReadTool: read full file, offset/limit, missing file
  - FileWriteTool: full write, create_only, patch mode, write policy gating
  - FileListTool: directory listing, glob patterns, hidden files
"""

import os
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.mcp.tools.fileio import (
    _resolve_and_validate,
    FileReadTool,
    FileWriteTool,
    FileListTool,
)


@pytest.fixture
def workspace(tmp_path):
    """Set up a workspace directory with sample files."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("line1\nline2\nline3\nline4\nline5\n")
    (tmp_path / "README.md").write_text("# Project\n\nHello world\n")
    (tmp_path / ".ziya").mkdir()
    (tmp_path / ".ziya" / "state.json").write_text('{"status": "ok"}')
    (tmp_path / ".hidden_file").write_text("secret")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n")
    (tmp_path / "docs" / "api.md").write_text("# API\n")
    return str(tmp_path)


def run(coro):
    """Run an async function synchronously."""
    # asyncio.run() always creates a fresh event loop and closes it when
    # done, unlike asyncio.get_event_loop(), which can return a stale/
    # already-closed loop left behind by a prior test file in the same
    # pytest process (e.g. one that calls asyncio.run() itself).
    return asyncio.run(coro)


# ── _resolve_and_validate ──────────────────────────────────────────

class TestResolveAndValidate:

    def test_normal_path(self, workspace):
        result = _resolve_and_validate("src/main.py", workspace)
        assert result == Path(workspace) / "src" / "main.py"

    def test_dotdot_rejected(self, workspace):
        with pytest.raises(ValueError, match="traversal"):
            _resolve_and_validate("../../../etc/passwd", workspace)

    def test_dotdot_in_middle_rejected(self, workspace):
        with pytest.raises(ValueError, match="traversal"):
            _resolve_and_validate("src/../../etc/shadow", workspace)

    def test_empty_path_rejected(self, workspace):
        with pytest.raises(ValueError, match="empty"):
            _resolve_and_validate("", workspace)

    def test_whitespace_path_rejected(self, workspace):
        with pytest.raises(ValueError, match="empty"):
            _resolve_and_validate("   ", workspace)

    def test_quoted_path_cleaned(self, workspace):
        result = _resolve_and_validate("'src/main.py'", workspace)
        assert result == Path(workspace) / "src" / "main.py"

    def test_double_quoted_path_cleaned(self, workspace):
        result = _resolve_and_validate('"src/main.py"', workspace)
        assert result == Path(workspace) / "src" / "main.py"

    def test_dot_path_resolves_to_root(self, workspace):
        result = _resolve_and_validate(".", workspace)
        assert result == Path(workspace).resolve()

    def test_absolute_tmp_rejected_without_prefixes(self, workspace):
        """Absolute /tmp path rejected when no allowed prefixes are given."""
        with pytest.raises(ValueError, match="escapes project root"):
            _resolve_and_validate("/tmp/test.py", workspace)

    def test_absolute_tmp_allowed_with_prefixes(self, workspace):
        """Absolute /tmp path allowed when /tmp/ is in allowed prefixes."""
        result = _resolve_and_validate("/tmp/test.py", workspace, allowed_absolute_prefixes=["/tmp/"])
        assert str(result).endswith("test.py")
        # On macOS /tmp -> /private/tmp; verify resolution is consistent
        assert result == Path("/tmp/test.py").resolve()

    def test_absolute_non_allowed_prefix_rejected(self, workspace):
        """Absolute path outside allowed prefixes is still rejected."""
        with pytest.raises(ValueError, match="escapes project root"):
            _resolve_and_validate("/etc/passwd", workspace, allowed_absolute_prefixes=["/tmp/"])

    def test_relative_path_unaffected_by_prefixes(self, workspace):
        """Relative paths still resolve against workspace regardless of prefixes."""
        result = _resolve_and_validate("src/main.py", workspace, allowed_absolute_prefixes=["/tmp/"])
        assert result == Path(workspace) / "src" / "main.py"


# ── FileReadTool ───────────────────────────────────────────────────

class TestFileReadTool:

    def setup_method(self):
        self.tool = FileReadTool()

    def test_read_full_file(self, workspace):
        result = run(self.tool.execute(path="src/main.py", _workspace_path=workspace))
        assert "error" not in result
        assert "line1\nline2" in result["content"]
        assert "5 total lines" in result["metadata"]

    def test_read_with_offset(self, workspace):
        result = run(self.tool.execute(path="src/main.py", offset=3, _workspace_path=workspace))
        assert "error" not in result
        assert result["content"].startswith("line3")

    def test_read_with_limit(self, workspace):
        result = run(self.tool.execute(path="src/main.py", max_lines=2, _workspace_path=workspace))
        assert "error" not in result
        lines = result["content"].strip().split("\n")
        assert len(lines) == 2

    def test_read_with_offset_and_limit(self, workspace):
        result = run(self.tool.execute(path="src/main.py", offset=2, max_lines=2, _workspace_path=workspace))
        assert "error" not in result
        assert result["content"].startswith("line2")
        assert "showing lines 2" in result["metadata"]

    def test_read_missing_file(self, workspace):
        result = run(self.tool.execute(path="nonexistent.py", _workspace_path=workspace))
        assert result["error"] is True
        assert "not found" in result["message"].lower()

    def test_read_directory_fails(self, workspace):
        result = run(self.tool.execute(path="src", _workspace_path=workspace))
        assert result["error"] is True
        assert "not a file" in result["message"].lower()

    def test_read_traversal_rejected(self, workspace):
        result = run(self.tool.execute(path="../../../etc/passwd", _workspace_path=workspace))
        assert result["error"] is True
        assert "traversal" in result["message"].lower()


# ── FileWriteTool ──────────────────────────────────────────────────

class TestFileWriteTool:

    def setup_method(self):
        self.tool = FileWriteTool()

    def _mock_write_allowed(self, path, root=""):
        """Mock that allows writes to .ziya/ and /tmp/."""
        clean = path.strip().strip("'\"")
        return clean.startswith(".ziya/") or clean.startswith("/tmp/")

    def test_write_to_ziya_allowed(self, workspace):
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
            result = run(self.tool.execute(
                path=".ziya/notes.md",
                content="# Notes\n\nSome content",
                _workspace_path=workspace,
            ))
        assert result.get("success") is True
        assert (Path(workspace) / ".ziya" / "notes.md").read_text() == "# Notes\n\nSome content"

    def test_write_to_src_blocked(self, workspace):
        rejection = "Write to 'src/evil.py' blocked. Approved: .ziya/, /tmp/"
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=rejection):
            result = run(self.tool.execute(
                path="src/evil.py",
                content="import evil",
                _workspace_path=workspace,
            ))
        assert result["error"] is True
        assert "blocked" in result["message"].lower()

    def test_create_only_fails_if_exists(self, workspace):
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
            result = run(self.tool.execute(
                path=".ziya/state.json",
                content="new content",
                create_only=True,
                _workspace_path=workspace,
            ))
        assert result["error"] is True
        assert "already exists" in result["message"].lower()

    def test_create_only_succeeds_for_new_file(self, workspace):
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
            result = run(self.tool.execute(
                path=".ziya/new_file.txt",
                content="brand new",
                create_only=True,
                _workspace_path=workspace,
            ))
        assert result.get("success") is True
        assert "Created" in result["message"]

    def test_patch_ambiguous_errors_by_default(self, workspace):
        """Multiple matches without occurrence= should error, not silently pick one."""
        (Path(workspace) / ".ziya" / "config.txt").write_text("AAA BBB AAA")
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
            result = run(self.tool.execute(
                path=".ziya/config.txt",
                patch="AAA",
                content="CCC",
                _workspace_path=workspace,
            ))
        assert result["error"] is True
        assert "ambiguous" in result["message"].lower()
        assert "2 occurrences" in result["message"]
        # File should be untouched
        content = (Path(workspace) / ".ziya" / "config.txt").read_text()
        assert content == "AAA BBB AAA"

    def test_patch_single_match_works_without_occurrence(self, workspace):
        """A unique match applies cleanly with no occurrence= needed."""
        (Path(workspace) / ".ziya" / "config.txt").write_text("AAA BBB CCC")
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
            result = run(self.tool.execute(
                path=".ziya/config.txt",
                patch="BBB",
                content="DDD",
                _workspace_path=workspace,
            ))
        assert result.get("success") is True
        content = (Path(workspace) / ".ziya" / "config.txt").read_text()
        assert content == "AAA DDD CCC"

    def test_patch_occurrence_zero_replaces_all(self, workspace):
        """occurrence=0 replaces every match."""
        (Path(workspace) / ".ziya" / "config.txt").write_text("AAA BBB AAA CCC AAA")
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
            result = run(self.tool.execute(
                path=".ziya/config.txt",
                patch="AAA",
                content="XXX",
                occurrence=0,
                _workspace_path=workspace,
            ))
        assert result.get("success") is True
        assert "replaced 3 of 3" in result["message"]
        content = (Path(workspace) / ".ziya" / "config.txt").read_text()
        assert content == "XXX BBB XXX CCC XXX"

    def test_patch_occurrence_specific_nth(self, workspace):
        """occurrence=2 replaces only the second match."""
        (Path(workspace) / ".ziya" / "config.txt").write_text("AAA BBB AAA CCC AAA")
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
            result = run(self.tool.execute(
                path=".ziya/config.txt",
                patch="AAA",
                content="XXX",
                occurrence=2,
                _workspace_path=workspace,
            ))
        assert result.get("success") is True
        assert "replaced 1 of 3" in result["message"]
        content = (Path(workspace) / ".ziya" / "config.txt").read_text()
        assert content == "AAA BBB XXX CCC AAA"

    def test_patch_occurrence_out_of_range(self, workspace):
        """occurrence=5 when only 2 matches exist should error."""
        (Path(workspace) / ".ziya" / "config.txt").write_text("AAA BBB AAA")
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
            result = run(self.tool.execute(
                path=".ziya/config.txt",
                patch="AAA",
                content="XXX",
                occurrence=5,
                _workspace_path=workspace,
            ))
        assert result["error"] is True
        assert "only 2" in result["message"].lower()

    def test_patch_occurrence_first_explicit(self, workspace):
        """occurrence=1 explicitly targets the first match."""
        (Path(workspace) / ".ziya" / "config.txt").write_text("AAA BBB AAA")
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
            result = run(self.tool.execute(
                path=".ziya/config.txt",
                patch="AAA",
                content="CCC",
                occurrence=1,
                _workspace_path=workspace,
            ))
        assert result.get("success") is True
        content = (Path(workspace) / ".ziya" / "config.txt").read_text()
        assert content == "CCC BBB AAA"

    def test_patch_mode_target_not_found(self, workspace):
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
            result = run(self.tool.execute(
                path=".ziya/state.json",
                patch="NONEXISTENT_TEXT",
                content="replacement",
                _workspace_path=workspace,
            ))
        assert result["error"] is True
        assert "not found" in result["message"].lower()

    def test_patch_mode_file_must_exist(self, workspace):
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
            result = run(self.tool.execute(
                path=".ziya/no_such_file.txt",
                patch="something",
                content="replacement",
                _workspace_path=workspace,
            ))
        assert result["error"] is True
        assert "does not exist" in result["message"].lower()

    def test_write_creates_parent_directories(self, workspace):
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
            result = run(self.tool.execute(
                path=".ziya/deep/nested/dir/file.txt",
                content="nested content",
                _workspace_path=workspace,
            ))
        assert result.get("success") is True
        assert (Path(workspace) / ".ziya" / "deep" / "nested" / "dir" / "file.txt").exists()

    def test_traversal_in_write_rejected(self, workspace):
        result = run(self.tool.execute(
            path="../../../etc/crontab",
            content="* * * * * evil",
            _workspace_path=workspace,
        ))
        assert result["error"] is True
        assert "traversal" in result["message"].lower()


# ── FileListTool ───────────────────────────────────────────────────

class TestFileListTool:

    def setup_method(self):
        self.tool = FileListTool()

    def test_list_root(self, workspace):
        result = run(self.tool.execute(path=".", _workspace_path=workspace))
        assert "error" not in result
        assert "src/" in result["content"]
        assert "docs/" in result["content"]
        assert "README.md" in result["content"]

    def test_list_subdirectory(self, workspace):
        result = run(self.tool.execute(path="docs", _workspace_path=workspace))
        assert "error" not in result
        assert "guide.md" in result["content"]
        assert "api.md" in result["content"]

    def test_hidden_files_excluded(self, workspace):
        result = run(self.tool.execute(path=".", _workspace_path=workspace))
        assert ".hidden_file" not in result["content"]
        assert ".ziya" not in result["content"]

    def test_glob_pattern(self, workspace):
        result = run(self.tool.execute(path="docs", pattern="*.md", _workspace_path=workspace))
        assert "error" not in result
        assert "guide.md" in result["content"]

    def test_max_entries(self, workspace):
        result = run(self.tool.execute(path=".", max_entries=1, _workspace_path=workspace))
        assert "error" not in result
        assert "truncated" in result["content"].lower()

    def test_missing_directory(self, workspace):
        result = run(self.tool.execute(path="nonexistent_dir", _workspace_path=workspace))
        assert result["error"] is True
        assert "not found" in result["message"].lower()

    def test_file_not_directory(self, workspace):
        result = run(self.tool.execute(path="README.md", _workspace_path=workspace))
        assert result["error"] is True
        assert "not a directory" in result["message"].lower()

    def test_traversal_rejected(self, workspace):
        result = run(self.tool.execute(path="../../../", _workspace_path=workspace))
        assert result["error"] is True


# ── _get_include_dir_prefixes / _get_all_readable_prefixes ─────────
#
# Regression coverage for the "External include" bug: a path added via
# Project Settings ("Add External Path" -> app.services.folder_service.
# _explicit_external_paths) or via --include/ZIYA_INCLUDE_DIRS was not
# recognized by _resolve_and_validate's allowed_absolute_prefixes, so
# file_read / file_list / pdf_* tools rejected it as "outside the
# allowed directories" even though the user explicitly approved it.

class TestIncludeDirPrefixes:

    def test_empty_by_default(self, monkeypatch):
        from app.mcp.tools.fileio import _get_include_dir_prefixes
        monkeypatch.delenv("ZIYA_INCLUDE_DIRS", raising=False)
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths", set(), raising=False
        )
        assert _get_include_dir_prefixes() == []

    def test_reads_ziya_include_dirs_env_var(self, monkeypatch):
        from app.mcp.tools.fileio import _get_include_dir_prefixes
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths", set(), raising=False
        )
        monkeypatch.setenv("ZIYA_INCLUDE_DIRS", "/tmp/vendor-docs,/tmp/other-docs")
        prefixes = _get_include_dir_prefixes()
        assert "/tmp/vendor-docs" in prefixes
        assert "/tmp/other-docs" in prefixes

    def test_env_var_relative_entries_skipped(self, monkeypatch):
        from app.mcp.tools.fileio import _get_include_dir_prefixes
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths", set(), raising=False
        )
        monkeypatch.setenv("ZIYA_INCLUDE_DIRS", "relative/dir,/tmp/absolute-dir")
        prefixes = _get_include_dir_prefixes()
        assert "/tmp/absolute-dir" in prefixes
        assert "relative/dir" not in prefixes

    def test_reads_explicit_external_paths_from_folder_service(self, monkeypatch):
        """This is the actual mechanism behind Project Settings' 'Add
        External Path' -- the bug reported against pdf_search."""
        from app.mcp.tools.fileio import _get_include_dir_prefixes
        monkeypatch.delenv("ZIYA_INCLUDE_DIRS", raising=False)
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths",
            {"/Users/dcohn/Documents/Vendor Docs/Marvell Switching"},
            raising=False,
        )
        prefixes = _get_include_dir_prefixes()
        assert "/Users/dcohn/Documents/Vendor Docs/Marvell Switching" in prefixes

    def test_combines_both_sources(self, monkeypatch):
        from app.mcp.tools.fileio import _get_include_dir_prefixes
        monkeypatch.setenv("ZIYA_INCLUDE_DIRS", "/tmp/from-env")
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths",
            {"/tmp/from-explicit"},
            raising=False,
        )
        prefixes = _get_include_dir_prefixes()
        assert "/tmp/from-env" in prefixes
        assert "/tmp/from-explicit" in prefixes

    def test_missing_folder_service_attr_does_not_raise(self, monkeypatch):
        """If folder_service is ever refactored and the set is renamed/
        removed, this helper must degrade gracefully rather than 500
        every read-oriented tool call."""
        from app.mcp.tools.fileio import _get_include_dir_prefixes
        import app.services.folder_service as folder_service
        monkeypatch.delenv("ZIYA_INCLUDE_DIRS", raising=False)
        monkeypatch.delattr(folder_service, "_explicit_external_paths", raising=False)
        # Should not raise; env var absent too => empty list.
        assert _get_include_dir_prefixes() == []


class TestGetAllReadablePrefixes:
    """``_get_all_readable_prefixes`` is the single consolidated
    allowlist that file_read, file_list, pdf_* tools, and
    context_add_file all now build from."""

    def test_combines_all_three_sources(self, monkeypatch):
        from app.mcp.tools.fileio import _get_all_readable_prefixes
        from app.context import set_task_readable_paths, reset_task_readable_paths

        monkeypatch.setattr(
            "app.mcp.tools.fileio._get_safe_write_paths", lambda: ["/tmp/", ".ziya/"]
        )
        monkeypatch.setenv("ZIYA_INCLUDE_DIRS", "/tmp/external-include")
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths",
            {"/tmp/external-approved"},
            raising=False,
        )
        token = set_task_readable_paths([{"path": "/tmp/task-granted", "is_dir": True}])
        try:
            prefixes = _get_all_readable_prefixes()
        finally:
            reset_task_readable_paths(token)

        assert "/tmp/" in prefixes
        assert "/tmp/external-include" in prefixes
        assert "/tmp/external-approved" in prefixes
        assert "/tmp/task-granted" in prefixes


# ── End-to-end: file_read / file_list against an externally-approved path ──

class TestExternalPathEndToEnd:
    """Mirrors the reported bug: a path added via Project Settings'
    'Add External Path' feature must be readable by file_read and
    file_list, not rejected as 'outside the allowed directories'."""

    @pytest.fixture
    def external_dir(self, tmp_path):
        ext = tmp_path.parent / f"external-{tmp_path.name}"
        ext.mkdir(exist_ok=True)
        (ext / "datasheet.txt").write_text("EXTERNAL CONTENT\n")
        return str(ext)

    def test_file_read_rejects_unapproved_external_path(self, workspace, external_dir, monkeypatch):
        monkeypatch.delenv("ZIYA_INCLUDE_DIRS", raising=False)
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths", set(), raising=False
        )
        tool = FileReadTool()
        result = run(tool.execute(
            path=f"{external_dir}/datasheet.txt", _workspace_path=workspace,
        ))
        assert result.get("error") is True
        assert "escapes project root" in result["message"].lower()

    def test_file_read_allows_approved_external_path(self, workspace, external_dir, monkeypatch):
        monkeypatch.delenv("ZIYA_INCLUDE_DIRS", raising=False)
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths",
            {external_dir},
            raising=False,
        )
        tool = FileReadTool()
        result = run(tool.execute(
            path=f"{external_dir}/datasheet.txt", _workspace_path=workspace,
        ))
        assert result.get("error") is not True, result
        assert "EXTERNAL CONTENT" in result["content"]

    def test_file_list_allows_approved_external_path(self, workspace, external_dir, monkeypatch):
        monkeypatch.delenv("ZIYA_INCLUDE_DIRS", raising=False)
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths",
            {external_dir},
            raising=False,
        )
        tool = FileListTool()
        result = run(tool.execute(path=external_dir, _workspace_path=workspace))
        assert result.get("error") is not True, result
        assert "datasheet.txt" in result["content"]

    def test_file_write_does_not_inherit_include_dir_grant(self, workspace, external_dir, monkeypatch):
        """External-include approval is a read grant only; file_write
        must remain restricted to the safe-write allowlist regardless
        of what's in _explicit_external_paths -- an approved-for-read
        external path must still fail at path resolution before
        _check_write_allowed is ever consulted."""
        monkeypatch.delenv("ZIYA_INCLUDE_DIRS", raising=False)
        monkeypatch.setattr(
            "app.services.folder_service._explicit_external_paths",
            {external_dir},
            raising=False,
        )
        tool = FileWriteTool()
        result = run(tool.execute(
            path=f"{external_dir}/evil.txt",
            content="pwned",
            _workspace_path=workspace,
        ))
        assert result.get("error") is True
        assert "escapes project root" in result["message"].lower()
        assert not Path(external_dir, "evil.txt").exists()
