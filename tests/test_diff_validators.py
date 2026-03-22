"""
Tests for validator functions.

Moved from app/utils/diff_utils/tests/test_validators.py to be visible
to pytest (testpaths = tests).
"""

import unittest

from app.utils.diff_utils.validation.validators import (
    normalize_line_for_comparison,
    is_hunk_already_applied
)


class TestValidators(unittest.TestCase):
    """Test cases for validator functions."""
    
    def test_normalize_line_for_comparison(self):
        self.assertEqual(normalize_line_for_comparison("  Hello  "), "Hello")
        self.assertEqual(normalize_line_for_comparison("Hello\t"), "Hello")
        self.assertEqual(normalize_line_for_comparison("\tHello"), "Hello")
        self.assertEqual(normalize_line_for_comparison(""), "")
        self.assertEqual(normalize_line_for_comparison("  "), "")
        self.assertEqual(normalize_line_for_comparison("Hello, world!"), "Hello, world!")
        self.assertEqual(normalize_line_for_comparison("  Hello, world!  "), "Hello, world!")
        self.assertEqual(normalize_line_for_comparison("Hello\\nworld"), "Hello\\nworld")
    
    def test_is_hunk_already_applied(self):
        """Test detection of already applied hunks."""
        file_lines = [
            "def test_function():",
            "    # This is a test function",
            "    return True",
            "",
            "def another_function():",
            "    # This is another function",
            "    return False"
        ]
        
        hunk = {
            'old_start': 2,
            'old_lines': 2,
            'new_start': 2,
            'new_lines': 3,
            'old_block': ['def test_function():', '    # This is a test function', '    return True'],
            'new_lines': ['def test_function():', '    # This is a test function', '    # Added comment', '    return True'],
            'removed_lines': [],
            'added_lines': ['    # Added comment'],
        }
        
        # Not yet applied
        self.assertFalse(is_hunk_already_applied(file_lines, hunk, 0))
        
        # Apply manually
        file_lines_applied = [
            "def test_function():",
            "    # This is a test function",
            "    # Added comment",
            "    return True",
            "",
            "def another_function():",
            "    # This is another function",
            "    return False"
        ]
        
        # Now should be detected as applied
        self.assertTrue(is_hunk_already_applied(file_lines_applied, hunk, 0))


if __name__ == "__main__":
    unittest.main()
