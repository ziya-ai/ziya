"""
Tests for validator functions.
"""

import unittest

from app.utils.diff_utils.validation.validators import (
    normalize_line_for_comparison,
    is_hunk_already_applied
)


class TestValidators(unittest.TestCase):
    """Test cases for validator functions."""
    
    def test_normalize_line_for_comparison(self):
        """Test normalization of lines for comparison."""
        # Test basic normalization
        self.assertEqual(normalize_line_for_comparison("  Hello  "), "Hello")
        self.assertEqual(normalize_line_for_comparison("Hello\t"), "Hello")
        self.assertEqual(normalize_line_for_comparison("\tHello"), "Hello")
        
        # Test empty lines
        self.assertEqual(normalize_line_for_comparison(""), "")
        self.assertEqual(normalize_line_for_comparison("  "), "")
        self.assertEqual(normalize_line_for_comparison("\t\t"), "")
        
        # Test special characters
        self.assertEqual(normalize_line_for_comparison("Hello, world!"), "Hello, world!")
        self.assertEqual(normalize_line_for_comparison("  Hello, world!  "), "Hello, world!")
        
        # Test escape sequences (should be preserved in normalized form)
        self.assertEqual(normalize_line_for_comparison("Hello\\nworld"), "Hello\\nworld")
        self.assertEqual(normalize_line_for_comparison("Hello\\tworld"), "Hello\\tworld")
    
    def test_is_hunk_already_applied(self):
        """Test detection of already applied hunks."""
        # Create a simple file
        file_lines = [
            "def test_function():",
            "    # This is a test function",
            "    return True",
            "",
            "def another_function():",
            "    # This is another function",
            "    return False"
        ]
        
        # Create a hunk that adds a comment
        hunk = {
            'old_start': 2,
            'old_lines': 2,
            'new_start': 2,
            'new_lines': 3,
            'old_block': [' def test_function():', '-     # This is a test function', ' return True'],
            'new_block': [' def test_function():', '+     # This is a test function', '+     # Added comment', ' return True'],
            'new_lines': ['def test_function():', '    # This is a test function', '    # Added comment', '    return True']
        }
        
        # The hunk should not be detected as already applied
        self.assertFalse(is_hunk_already_applied(file_lines, hunk, 0))
        
        # Apply the hunk manually
        file_lines = [
            "def test_function():",
            "    # This is a test function",
            "    # Added comment",
            "    return True",
            "",
            "def another_function():",
            "    # This is another function",
            "    return False"
        ]
        
        # Now the hunk should be detected as already applied
        self.assertTrue(is_hunk_already_applied(file_lines, hunk, 0))
        
        # Test with an offset
        hunk['old_start'] = 1  # Offset by 1
        self.assertTrue(is_hunk_already_applied(file_lines, hunk, 1))


if __name__ == "__main__":
    unittest.main()
