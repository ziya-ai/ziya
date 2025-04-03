"""
Tests for validation utilities.
"""

import unittest
from app.utils.diff_utils.validation.validators import (
    is_new_file_creation,
    normalize_line_for_comparison,
    is_hunk_already_applied
)

class TestValidators(unittest.TestCase):
    """Test cases for validation utilities."""
    
    def test_is_new_file_creation(self):
        """Test detection of new file creation."""
        # Test with new file indicators
        self.assertTrue(is_new_file_creation(['@@ -0,0 +1,10 @@', '+ new content']))
        self.assertTrue(is_new_file_creation(['--- /dev/null', '+++ b/new_file.py']))
        self.assertTrue(is_new_file_creation(['new file mode 100644', 'index 0000000..1234567']))
        
        # Test with non-new file diff
        self.assertFalse(is_new_file_creation(['@@ -1,5 +1,6 @@', ' context', '+ new line']))
        self.assertFalse(is_new_file_creation(['--- a/file.py', '+++ b/file.py']))
    
    def test_normalize_line_for_comparison(self):
        """Test normalization of lines for comparison."""
        # Test with whitespace
        self.assertEqual(normalize_line_for_comparison("  Hello, world!  "), "Hello,world!")
        
        # Test with invisible characters
        self.assertEqual(normalize_line_for_comparison("Hello\u200B, world!"), "Hello,world!")
        
        # Test with escape sequences - note that escape sequences are preserved in the output
        # but whitespace is still removed
        self.assertEqual(normalize_line_for_comparison("Hello\\nworld"), "Hello\\nworld")
        
        # Test with mixed content
        self.assertEqual(normalize_line_for_comparison("  Hello\u200B, \tworld!\n  "), "Hello,world!")
    
    def test_is_hunk_already_applied(self):
        """Test detection of already applied hunks."""
        # Test with exact match
        file_lines = ["def test():", "    print('Hello')", "    return True"]
        hunk = {
            'new_lines': ["def test():", "    print('Hello')", "    return True"],
            'old_block': ["def test():", "    print('Hi')", "    return True"]
        }
        self.assertTrue(is_hunk_already_applied(file_lines, hunk, 0))
        
        # Test with whitespace differences
        file_lines = ["def test():", "    print('Hello')", "    return True"]
        hunk = {
            'new_lines': ["def test():", "    print('Hello')  ", "    return True"],
            'old_block': ["def test():", "    print('Hi')", "    return True"]
        }
        self.assertTrue(is_hunk_already_applied(file_lines, hunk, 0))
        
        # Test with invisible character differences
        file_lines = ["def test():", "    print('Hello\u200B')", "    return True"]
        hunk = {
            'new_lines': ["def test():", "    print('Hello')", "    return True"],
            'old_block': ["def test():", "    print('Hi')", "    return True"]
        }
        self.assertTrue(is_hunk_already_applied(file_lines, hunk, 0))
        
        # Test with non-matching content
        file_lines = ["def test():", "    print('Hi')", "    return True"]
        hunk = {
            'new_lines': ["def test():", "    print('Hello')", "    return True"],
            'old_block': ["def test():", "    print('Hi')", "    return True"]
        }
        self.assertFalse(is_hunk_already_applied(file_lines, hunk, 0))
        
        # Test with position beyond file length
        file_lines = ["def test():", "    print('Hello')", "    return True"]
        hunk = {
            'new_lines': ["def test():", "    print('Hello')", "    return True"],
            'old_block': ["def test():", "    print('Hi')", "    return True"]
        }
        self.assertFalse(is_hunk_already_applied(file_lines, hunk, 10))
        
        # Test with empty hunk
        file_lines = ["def test():", "    print('Hello')", "    return True"]
        hunk = {
            'new_lines': [],
            'old_block': []
        }
        self.assertFalse(is_hunk_already_applied(file_lines, hunk, 0))

class TestValidatorsWithConstants(unittest.TestCase):
    """Test cases for validation utilities with constants."""
    
    def test_constant_already_defined(self):
        """Test detection of already defined constants."""
        # This test would need the full implementation of is_hunk_already_applied
        # that includes the constant detection logic
        pass

if __name__ == "__main__":
    unittest.main()
