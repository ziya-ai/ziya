"""
Tests for escape sequence handling in diff application.
"""

import unittest
import sys
import os

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from app.utils.diff_utils.core.escape_handling_improved import (
    normalize_escape_sequences,
    is_escape_sequence_line,
    handle_escape_sequence_line,
    clean_escape_sequences_in_diff
)

class TestEscapeHandling(unittest.TestCase):
    """Test cases for escape sequence handling."""
    
    def test_normalize_escape_sequences(self):
        """Test normalizing escape sequences."""
        # Test with preserve_literals=True
        self.assertEqual(
            normalize_escape_sequences('\\n', preserve_literals=True),
            '\\n'
        )
        
        # Test with preserve_literals=False
        self.assertEqual(
            normalize_escape_sequences('\\n', preserve_literals=False),
            '\n'
        )
        
        # Test with trailing whitespace and preserve_trailing_space=True
        self.assertEqual(
            normalize_escape_sequences('\\n   ', preserve_literals=True, preserve_trailing_space=True),
            '\\n   '
        )
        
        # Test with trailing whitespace and preserve_trailing_space=False
        self.assertEqual(
            normalize_escape_sequences('\\n   ', preserve_literals=True, preserve_trailing_space=False),
            '\\n'
        )
    
    def test_is_escape_sequence_line(self):
        """Test detecting lines with escape sequences."""
        # Test with escape sequences
        self.assertTrue(is_escape_sequence_line('const str = "\\n";'))
        self.assertTrue(is_escape_sequence_line('const regex = /\\n/g;'))
        self.assertTrue(is_escape_sequence_line('const template = `\\n`;'))
        self.assertTrue(is_escape_sequence_line('.replace(/\\r\\n/g, "\\n")'))
        
        # Test without escape sequences
        self.assertFalse(is_escape_sequence_line('const str = "normal";'))
        self.assertFalse(is_escape_sequence_line('const num = 42;'))
    
    def test_handle_escape_sequence_line(self):
        """Test handling lines with escape sequences."""
        # Test with trailing whitespace after escape sequences
        self.assertEqual(
            handle_escape_sequence_line('.replace(/\\r\\n/g, "\\n") '),
            '.replace(/\\r\\n/g, "\\n")'
        )
        
        # Test with trailing whitespace after escape sequences in single quotes
        self.assertEqual(
            handle_escape_sequence_line(".replace(/\\r\\n/g, '\\n') "),
            ".replace(/\\r\\n/g, '\\n')"
        )
        
        # Test with multiple escape sequences and trailing whitespace
        # Our implementation only removes whitespace between escape sequences
        # but preserves trailing whitespace at the end of the string
        result = handle_escape_sequence_line('const str = "\\n  \\r  \\t  ";')
        # Just check that the whitespace between escape sequences is removed
        self.assertIn('\\n\\r\\t', result)
    
    def test_clean_escape_sequences_in_diff(self):
        """Test cleaning escape sequences in diff content."""
        # Test the specific json_escape_sequence test case
        json_escape_diff = """diff --git a/test.ts b/test.ts
--- a/test.ts
+++ b/test.ts
@@ -1,8 +1,10 @@
 function parseJson() {
    const jsonStr = `{
        "key": "value"
    }`;
-    // Some comment
-    parsed = typeof jsonStr === 'string' ? JSON.parse(jsonStr) : jsonStr;
+      if (typeof jsonStr === 'string') {
+          // Clean up the JSON string
+          const cleanJson = jsonStr
+              .replace(/\\r\\n/g, '\\n') 
+              .split('\\n')
+              .map(line => line.trim())
+              .join('\\n');
+          parsed = JSON.parse(cleanJson);
+      }"""
        
        expected_json_escape_cleaned = """diff --git a/test.ts b/test.ts
--- a/test.ts
+++ b/test.ts
@@ -1,8 +1,10 @@
 function parseJson() {
    const jsonStr = `{
        "key": "value"
    }`;
-    // Some comment
-    parsed = typeof jsonStr === 'string' ? JSON.parse(jsonStr) : jsonStr;
+      if (typeof jsonStr === 'string') {
+          // Clean up the JSON string
+          const cleanJson = jsonStr
+              .replace(/\\r\\n/g, '\\n')
+              .split('\\n')
+              .map(line => line.trim())
+              .join('\\n');
+          parsed = JSON.parse(cleanJson);
+      }"""
        
        self.assertEqual(clean_escape_sequences_in_diff(json_escape_diff), expected_json_escape_cleaned)

if __name__ == '__main__':
    unittest.main()
