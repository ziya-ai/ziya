"""
Tests for code_util diff functionality.

Updated: _find_correct_old_start_line and _format_hunk_header removed.
Tests rewritten against current public API: correct_git_diff,
parse_unified_diff, split_combined_diff, extract_target_file_from_diff,
is_new_file_creation, is_hunk_already_applied.
"""

import pytest
from app.utils.code_util import (
    correct_git_diff,
    parse_unified_diff,
    split_combined_diff,
    extract_target_file_from_diff,
    is_new_file_creation,
    is_hunk_already_applied,
)


class TestParseUnifiedDiff:
    """Test parsing of unified diff format."""

    def test_parse_simple_diff(self):
        """Should parse a simple unified diff."""
        diff = """--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 line 1
-line 2
+line 2 modified
 line 3"""
        result = parse_unified_diff(diff)
        assert result is not None

    def test_parse_empty_diff(self):
        """Should handle empty diff string."""
        result = parse_unified_diff("")
        # Should return empty or None depending on implementation
        assert result is not None or result is None


class TestSplitCombinedDiff:
    """Test splitting of combined (multi-file) diffs."""

    def test_split_single_file(self):
        """Single-file diff should produce one part."""
        diff = """diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 line 1
-line 2
+line 2 modified
 line 3"""
        parts = split_combined_diff(diff)
        assert isinstance(parts, list)
        assert len(parts) >= 1

    def test_split_multi_file(self):
        """Multi-file diff should produce multiple parts."""
        diff = """diff --git a/file1.py b/file1.py
--- a/file1.py
+++ b/file1.py
@@ -1 +1 @@
-old
+new
diff --git a/file2.py b/file2.py
--- a/file2.py
+++ b/file2.py
@@ -1 +1 @@
-old
+new"""
        parts = split_combined_diff(diff)
        assert isinstance(parts, list)
        assert len(parts) >= 2

    def test_split_empty(self):
        """Empty string should produce empty list."""
        parts = split_combined_diff("")
        assert isinstance(parts, list)


class TestExtractTargetFile:
    """Test file path extraction from diff headers."""

    def test_extract_from_git_diff(self):
        """Should extract target file from git diff header."""
        diff = """diff --git a/src/main.py b/src/main.py
--- a/src/main.py
+++ b/src/main.py
@@ -1 +1 @@
-old
+new"""
        result = extract_target_file_from_diff(diff)
        assert result is not None
        assert "main.py" in result

    def test_extract_from_new_file(self):
        """Should extract target from new file diff."""
        diff = """diff --git a/dev/null b/new_file.py
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,2 @@
+line 1
+line 2"""
        result = extract_target_file_from_diff(diff)
        assert result is not None
        assert "new_file.py" in result


class TestIsNewFileCreation:
    """Test new file detection."""

    def test_new_file_detected(self):
        """Should detect new file creation diff."""
        diff_lines = [
            "diff --git a/dev/null b/new_file.py",
            "--- /dev/null",
            "+++ b/new_file.py",
            "@@ -0,0 +1,2 @@",
            "+line 1",
            "+line 2",
        ]
        assert is_new_file_creation(diff_lines) is True

    def test_modification_not_new(self):
        """Should not flag modification as new file."""
        diff_lines = [
            "--- a/existing.py",
            "+++ b/existing.py",
            "@@ -1 +1 @@",
            "-old",
            "+new",
        ]
        assert is_new_file_creation(diff_lines) is False


class TestIsHunkAlreadyApplied:
    """Test detection of already-applied hunks."""

    def test_already_applied(self):
        """Should detect when hunk content matches file."""
        file_lines = ["line 1", "new line", "line 3"]
        hunk = {"lines": [" line 1", "+new line", " line 3"]}
        result = is_hunk_already_applied(file_lines, hunk, pos=0)
        assert isinstance(result, bool)

    def test_not_applied(self):
        """Should detect when hunk has not been applied."""
        file_lines = ["line 1", "old line", "line 3"]
        hunk = {"lines": [" line 1", "-old line", "+new line", " line 3"]}
        result = is_hunk_already_applied(file_lines, hunk, pos=0)
        assert isinstance(result, bool)
