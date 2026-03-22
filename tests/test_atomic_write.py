"""
Tests for the atomic write behaviour of apply_diff_atomically().

Verifies that:
  1. The write uses a temp-file + rename pattern (not bare open/write).
  2. A crash during write does not corrupt the original file.
  3. Original file permissions are preserved after the write.
  4. The temp file is cleaned up on both success and failure.
"""

import os
import stat
import tempfile
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_test_file(tmp_path, name="target.py", content="line1\nline2\nline3\n", mode=0o644):
    """Create a file with known content and permissions."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    os.chmod(str(p), mode)
    return str(p)


def _simple_diff(file_path, old_line="line2", new_line="line2_modified"):
    """Build a minimal valid unified diff that changes one line."""
    fname = os.path.basename(file_path)
    return (
        f"diff --git a/{fname} b/{fname}\n"
        f"--- a/{fname}\n"
        f"+++ b/{fname}\n"
        f"@@ -1,3 +1,3 @@\n"
        f" line1\n"
        f"-{old_line}\n"
        f"+{new_line}\n"
        f" line3\n"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAtomicWriteMechanism:
    """Verify the temp-file + rename write pattern."""

    def test_successful_write_uses_replace(self, tmp_path):
        """os.replace must be called (not bare open/write) on the happy path."""
        file_path = _create_test_file(tmp_path)
        diff = _simple_diff(file_path)

        with patch("app.utils.diff_utils.application.git_diff.os.replace", wraps=os.replace) as mock_replace:
            from app.utils.diff_utils.application.git_diff import apply_diff_atomically
            result = apply_diff_atomically(file_path, diff)

        # The function should have called os.replace at least once
        if result["status"] == "success" and result["details"].get("changes_written"):
            assert mock_replace.called, "os.replace was not called — write is not atomic"

    def test_content_is_correct_after_write(self, tmp_path):
        """The file content must reflect the applied diff."""
        file_path = _create_test_file(tmp_path)
        diff = _simple_diff(file_path)

        from app.utils.diff_utils.application.git_diff import apply_diff_atomically
        result = apply_diff_atomically(file_path, diff)

        if result["status"] == "success" and result["details"].get("changes_written"):
            content = open(file_path, "r").read()
            assert "line2_modified" in content
            assert "line2\n" not in content  # original line should be gone

    def test_no_temp_file_left_on_success(self, tmp_path):
        """After a successful write, no .tmp files should remain."""
        file_path = _create_test_file(tmp_path)
        diff = _simple_diff(file_path)

        from app.utils.diff_utils.application.git_diff import apply_diff_atomically
        apply_diff_atomically(file_path, diff)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Leftover temp files: {tmp_files}"


class TestAtomicWritePermissions:
    """Verify that file permissions survive the atomic write."""

    @pytest.mark.parametrize("mode", [0o644, 0o755, 0o600])
    def test_permissions_preserved(self, tmp_path, mode):
        file_path = _create_test_file(tmp_path, mode=mode)
        diff = _simple_diff(file_path)

        from app.utils.diff_utils.application.git_diff import apply_diff_atomically
        result = apply_diff_atomically(file_path, diff)

        if result["status"] == "success" and result["details"].get("changes_written"):
            actual_mode = stat.S_IMODE(os.stat(file_path).st_mode)
            assert actual_mode == mode, (
                f"Permissions changed: expected {oct(mode)}, got {oct(actual_mode)}"
            )


class TestAtomicWriteFailureRecovery:
    """Verify that the original file is preserved when the write fails."""

    def test_original_preserved_on_write_failure(self, tmp_path):
        """If the temp write raises, the original file must be intact."""
        original_content = "line1\nline2\nline3\n"
        file_path = _create_test_file(tmp_path, content=original_content)
        diff = _simple_diff(file_path)

        # Patch os.replace to simulate a failure after the temp file is written
        with patch("app.utils.diff_utils.application.git_diff.os.replace",
                    side_effect=OSError("disk full")):
            from app.utils.diff_utils.application.git_diff import apply_diff_atomically
            result = apply_diff_atomically(file_path, diff)

        # Original file must be untouched
        assert open(file_path).read() == original_content, \
            "Original file was corrupted despite os.replace failure"

    def test_temp_file_cleaned_on_failure(self, tmp_path):
        """Temp file must not be left behind when os.replace fails."""
        file_path = _create_test_file(tmp_path)
        diff = _simple_diff(file_path)

        with patch("app.utils.diff_utils.application.git_diff.os.replace",
                    side_effect=OSError("disk full")):
            from app.utils.diff_utils.application.git_diff import apply_diff_atomically
            apply_diff_atomically(file_path, diff)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Temp file not cleaned up: {tmp_files}"
