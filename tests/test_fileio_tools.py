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
    return asyncio.get_event_loop().run_until_complete(coro)


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

    def test_patch_mode_replaces_first_occurrence(self, workspace):
        (Path(workspace) / ".ziya" / "config.txt").write_text("AAA BBB AAA")
        with patch("app.mcp.tools.fileio._check_write_allowed", return_value=""):
            result = run(self.tool.execute(
                path=".ziya/config.txt",
                patch="AAA",
                content="CCC",
                _workspace_path=workspace,
            ))
        assert result.get("success") is True
        content = (Path(workspace) / ".ziya" / "config.txt").read_text()
        assert content == "CCC BBB AAA"  # Only first occurrence replaced

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
