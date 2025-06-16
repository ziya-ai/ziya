"""
Tests for the comment handling functionality.

This module tests the comment handling functionality to ensure that
it correctly identifies and handles comment-only changes in diffs.
"""

import unittest
from app.utils.diff_utils.application.comment_handler import (
    is_comment_line,
    detect_file_language,
    is_comment_only_change,
    remove_trailing_comment,
    calculate_match_quality_with_comment_awareness,
    handle_comment_only_changes
)

class TestCommentHandler(unittest.TestCase):
    """Test case for comment handling functionality."""
    
    def test_is_comment_line(self):
        """Test comment line detection."""
        # Python comments
        self.assertTrue(is_comment_line("# This is a comment"))
        self.assertTrue(is_comment_line("    # Indented comment"))
        
        # C-style comments
        self.assertTrue(is_comment_line("// C++ comment"))
        self.assertTrue(is_comment_line("/* C block comment */"))
        
        # SQL comments
        self.assertTrue(is_comment_line("-- SQL comment"))
        
        # HTML comments
        self.assertTrue(is_comment_line("<!-- HTML comment -->"))
        
        # Non-comments
        self.assertFalse(is_comment_line("def function():"))
        self.assertFalse(is_comment_line("    x = 1"))
        self.assertFalse(is_comment_line(""))
    
    def test_detect_file_language(self):
        """Test file language detection."""
        self.assertEqual(detect_file_language("file.py"), "python")
        self.assertEqual(detect_file_language("file.js"), "javascript")
        self.assertEqual(detect_file_language("file.java"), "java")
        self.assertEqual(detect_file_language("file.cpp"), "cpp")
        self.assertEqual(detect_file_language("file.html"), "html")
        self.assertEqual(detect_file_language("file.sql"), "sql")
        self.assertEqual(detect_file_language("file.md"), "markdown")
        self.assertEqual(detect_file_language("file.sh"), "shell")
        self.assertEqual(detect_file_language("file.css"), "css")
        self.assertIsNone(detect_file_language("file.unknown"))
    
    def test_is_comment_only_change(self):
        """Test detection of comment-only changes."""
        # Python example
        file_slice = [
            "def function():",
            "    # Old comment",
            "    x = 1",
            "    return x"
        ]
        
        chunk_lines = [
            "def function():",
            "    # New comment",
            "    x = 1",
            "    return x"
        ]
        
        self.assertTrue(is_comment_only_change(file_slice, chunk_lines, "python"))
        
        # C-style example
        file_slice_c = [
            "int function() {",
            "    // Old comment",
            "    int x = 1;",
            "    return x;",
            "}"
        ]
        
        chunk_lines_c = [
            "int function() {",
            "    // New comment",
            "    int x = 1;",
            "    return x;",
            "}"
        ]
        
        self.assertTrue(is_comment_only_change(file_slice_c, chunk_lines_c, "cpp"))
        
        # Not comment-only change
        chunk_lines_code = [
            "def function():",
            "    # New comment",
            "    x = 2",  # Changed code
            "    return x"
        ]
        
        self.assertFalse(is_comment_only_change(file_slice, chunk_lines_code, "python"))
    
    def test_remove_trailing_comment(self):
        """Test removal of trailing comments."""
        # Python
        self.assertEqual(remove_trailing_comment("x = 1  # Comment", "python"), "x = 1  ")
        
        # C-style
        self.assertEqual(remove_trailing_comment("int x = 1;  // Comment", "c_family"), "int x = 1;  ")
        
        # SQL
        self.assertEqual(remove_trailing_comment("SELECT * FROM table  -- Comment", "sql"), "SELECT * FROM table  ")
        
        # No comment
        self.assertEqual(remove_trailing_comment("x = 1", "python"), "x = 1")
    
    def test_calculate_match_quality_with_comment_awareness(self):
        """Test calculation of match quality with comment awareness."""
        # Exact match
        file_slice = [
            "def function():",
            "    x = 1",
            "    return x"
        ]
        
        chunk_lines = [
            "def function():",
            "    x = 1",
            "    return x"
        ]
        
        quality = calculate_match_quality_with_comment_awareness(file_slice, chunk_lines, "python")
        self.assertEqual(quality, 1.0)
        
        # Comment-only differences
        file_slice_comments = [
            "def function():",
            "    # Old comment",
            "    x = 1",
            "    return x"
        ]
        
        chunk_lines_comments = [
            "def function():",
            "    # New comment",
            "    x = 1",
            "    return x"
        ]
        
        quality_comments = calculate_match_quality_with_comment_awareness(file_slice_comments, chunk_lines_comments, "python")
        self.assertGreaterEqual(quality_comments, 0.8)
        
        # Code differences
        file_slice_code = [
            "def function():",
            "    x = 1",
            "    return x"
        ]
        
        chunk_lines_code = [
            "def function():",
            "    x = 2",  # Changed code
            "    return x"
        ]
        
        quality_code = calculate_match_quality_with_comment_awareness(file_slice_code, chunk_lines_code, "python")
        self.assertLess(quality_code, 0.8)
    
    def test_handle_comment_only_changes(self):
        """Test handling of comment-only changes."""
        file_lines = [
            "def function1():",
            "    # Old comment 1",
            "    x = 1",
            "    return x",
            "",
            "def function2():",
            "    # Old comment 2",
            "    y = 2",
            "    return y"
        ]
        
        chunk_lines = [
            "def function1():",
            "    # New comment 1",
            "    x = 1",
            "    return x"
        ]
        
        # Expected position is 0
        pos, ratio = handle_comment_only_changes("test.py", file_lines, chunk_lines, 0)
        self.assertEqual(pos, 0)
        self.assertGreaterEqual(ratio, 0.9)
        
        # Try with a different expected position
        chunk_lines2 = [
            "def function2():",
            "    # New comment 2",
            "    y = 2",
            "    return y"
        ]
        
        # Expected position is 5
        pos2, ratio2 = handle_comment_only_changes("test.py", file_lines, chunk_lines2, 5)
        self.assertEqual(pos2, 5)
        self.assertGreaterEqual(ratio2, 0.9)

if __name__ == "__main__":
    unittest.main()
