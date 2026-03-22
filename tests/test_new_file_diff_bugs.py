"""
Tests for new-file diff handling bugs:

Bug 1: diff_parser.py - old_start=0 is valid for new file diffs (@@ -0,0 +1,N @@)
        but the parser warns and "corrects" it to 1.

Bug 2a: git_diff.py apply_diff_atomically - `not hunk.get('old_block')` is True
         for empty lists, falsely flagging new-file hunks as malformed.

Bug 2b: pipeline_manager.py - already fixed (uses `'old_block' not in hunk`),
         included here as a regression guard.
"""

import os
import sys
import tempfile
import logging

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import pytest
from app.utils.diff_utils.parsing.diff_parser import parse_unified_diff_exact_plus
from app.utils.diff_utils.application.git_diff import apply_diff_atomically
from app.utils.diff_utils.core.exceptions import PatchApplicationError
from app.utils.code_util import use_git_to_apply_code_diff

logger = logging.getLogger(__name__)

# Shared test fixtures
NEW_FILE_DIFF = """\
diff --git a/Docs/UserConfigurationFiles.md b/Docs/UserConfigurationFiles.md
--- a/Docs/UserConfigurationFiles.md
+++ b/Docs/UserConfigurationFiles.md
@@ -0,0 +1,5 @@
+# User Configuration Files
+
+All config lives under `~/.ziya/`.
+
+## `~/.ziya/mcp_config.json`
"""

EXPECTED_CONTENT = """\
# User Configuration Files

All config lives under `~/.ziya/`.

## `~/.ziya/mcp_config.json`
"""


class TestDiffParserOldStartZero:
    """Bug 1: Parser should not warn/correct old_start=0 for new-file diffs."""

    def test_parser_accepts_old_start_zero(self):
        """@@ -0,0 +1,N @@ should parse without warnings or corrections."""
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "test.md")
            # File must exist for the parser (it reads it for context)
            with open(file_path, 'w') as f:
                f.write("")

            hunks = list(parse_unified_diff_exact_plus(NEW_FILE_DIFF, file_path))

            assert len(hunks) >= 1, "Parser should produce at least one hunk"

            hunk = hunks[0]
            # old_start should be 0, not corrected to 1
            assert hunk['old_start'] == 0, (
                f"old_start should be 0 for new-file diffs, got {hunk['old_start']}"
            )
            assert hunk['old_count'] == 0, (
                f"old_count should be 0 for new-file diffs, got {hunk['old_count']}"
            )
            # old_block should be an empty list (no old content)
            assert hunk.get('old_block') == [] or hunk.get('old_block') is not None, (
                "old_block should be an empty list, not missing"
            )
            # new_lines should have content
            assert len(hunk.get('new_lines', [])) > 0, (
                "new_lines should contain the added lines"
            )


class TestApplyDiffAtomicallyNewFile:
    """Bug 2a: apply_diff_atomically falsely flags new-file hunks as malformed."""

    def test_apply_to_empty_existing_file(self):
        """Applying a @@ -0,0 diff to an existing empty file should succeed."""
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "Docs", "UserConfigurationFiles.md")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'w') as f:
                f.write("")

            os.environ['ZIYA_USER_CODEBASE_DIR'] = temp_dir
            try:
                result = apply_diff_atomically(file_path, NEW_FILE_DIFF)
            finally:
                if 'ZIYA_USER_CODEBASE_DIR' in os.environ:
                    del os.environ['ZIYA_USER_CODEBASE_DIR']

            assert result['status'] == 'success', (
                f"apply_diff_atomically should succeed, got: {result}"
            )

            with open(file_path, 'r') as f:
                content = f.read()

            assert "# User Configuration Files" in content, (
                f"File should contain the new content, got: {content!r}"
            )

    def test_apply_to_nonexistent_file(self):
        """Applying a new-file diff when the file doesn't exist should succeed."""
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "Docs", "UserConfigurationFiles.md")
            # Don't create the file — it shouldn't exist

            os.environ['ZIYA_USER_CODEBASE_DIR'] = temp_dir
            try:
                result = apply_diff_atomically(file_path, NEW_FILE_DIFF)
            finally:
                if 'ZIYA_USER_CODEBASE_DIR' in os.environ:
                    del os.environ['ZIYA_USER_CODEBASE_DIR']

            assert result['status'] == 'success', (
                f"apply_diff_atomically should succeed for new file, got: {result}"
            )


class TestPipelineManagerNewFile:
    """Regression guard: pipeline_manager already handles empty old_block correctly."""

    def test_pipeline_applies_new_file_diff_to_empty_file(self):
        """The main pipeline should handle @@ -0,0 diffs on existing empty files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "Docs", "UserConfigurationFiles.md")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'w') as f:
                f.write("")

            os.environ['ZIYA_USER_CODEBASE_DIR'] = temp_dir
            try:
                result = use_git_to_apply_code_diff(NEW_FILE_DIFF, file_path)
            finally:
                if 'ZIYA_USER_CODEBASE_DIR' in os.environ:
                    del os.environ['ZIYA_USER_CODEBASE_DIR']

            assert result['status'] == 'success', (
                f"Pipeline should succeed, got: {result}"
            )

            with open(file_path, 'r') as f:
                content = f.read()

            assert "# User Configuration Files" in content, (
                f"File should contain new content, got: {content!r}"
            )


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
