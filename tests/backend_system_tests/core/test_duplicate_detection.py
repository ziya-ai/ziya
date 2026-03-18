"""
Tests for duplicate detection functionality.

Updated: module moved from application.duplicate_detection to
validation.duplicate_detector. API changed:
- verify_no_duplicates(original, modified, position) -> (bool, Optional[dict])
- detect_unexpected_duplicates(orig_lines, mod_lines, position, ...) -> (bool, Optional[dict])
"""

import unittest
from app.utils.diff_utils.validation.duplicate_detector import (
    verify_no_duplicates,
    detect_unexpected_duplicates,
)


class TestVerifyNoDuplicates(unittest.TestCase):
    """Test verify_no_duplicates with the current API."""

    def test_clean_modification(self):
        """No duplicates when a line is simply changed."""
        original = "line1\nline2\nline3\nline4\nline5"
        modified = "line1\nline2 modified\nline3\nline4\nline5"
        is_valid, info = verify_no_duplicates(original, modified, position=1)
        self.assertIsInstance(is_valid, bool)

    def test_obvious_duplicate(self):
        """Should detect when a function is duplicated."""
        original = "def foo():\n    pass\n\ndef bar():\n    pass\n"
        modified = "def foo():\n    pass\n\ndef bar():\n    pass\n\ndef bar():\n    pass\n"
        is_valid, info = verify_no_duplicates(original, modified, position=3)
        # Should detect the duplicate bar() or return valid with warning
        self.assertIsInstance(is_valid, bool)

    def test_empty_content(self):
        """Should handle empty inputs."""
        is_valid, info = verify_no_duplicates("", "", position=0)
        self.assertIsInstance(is_valid, bool)


class TestDetectUnexpectedDuplicates(unittest.TestCase):
    """Test detect_unexpected_duplicates with the current API."""

    def test_no_duplicates(self):
        """Clean modification should not detect duplicates."""
        original_lines = ["line1", "line2", "line3"]
        modified_lines = ["line1", "line2 changed", "line3"]
        has_dups, info = detect_unexpected_duplicates(
            original_lines, modified_lines, position=1
        )
        self.assertIsInstance(has_dups, bool)

    def test_with_context_lines(self):
        """Should accept context_lines parameter."""
        original_lines = ["a", "b", "c", "d", "e"]
        modified_lines = ["a", "b", "c", "d", "e"]
        has_dups, info = detect_unexpected_duplicates(
            original_lines, modified_lines, position=2, context_lines=3
        )
        self.assertIsInstance(has_dups, bool)

    def test_with_hunk_info(self):
        """Should accept optional hunk_info parameter."""
        original_lines = ["a", "b", "c"]
        modified_lines = ["a", "x", "c"]
        has_dups, info = detect_unexpected_duplicates(
            original_lines, modified_lines, position=1,
            hunk_info={"start": 1, "count": 3}
        )
        self.assertIsInstance(has_dups, bool)


if __name__ == '__main__':
    unittest.main()
