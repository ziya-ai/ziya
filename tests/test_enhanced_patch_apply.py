"""
Tests for the enhanced patch application functionality.

This module tests the enhanced patch application functionality to ensure that
it correctly applies diffs to files with improved fuzzy matching.
"""

import unittest
import os
import tempfile
import shutil
from unittest.mock import patch, mock_open

from app.utils.diff_utils.application.enhanced_patch_apply import (
    apply_diff_with_enhanced_matching,
    calculate_match_quality
)

class TestEnhancedPatchApply(unittest.TestCase):
    """Test case for enhanced patch application functionality."""
    
    def setUp(self):
        """Set up test environment."""
        # Create a temporary directory for test files
        self.test_dir = tempfile.mkdtemp()
        
        # Create a test file
        self.test_file = os.path.join(self.test_dir, "test.py")
        with open(self.test_file, "w") as f:
            f.write("def main():\n    x = 1\n    return x\n")
    
    def tearDown(self):
        """Clean up test environment."""
        # Remove the temporary directory and its contents
        shutil.rmtree(self.test_dir)
    
    def test_basic_patch_application(self):
        """Test basic patch application."""
        # Create a simple diff
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdef0 100644
--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 def main():
     x = 1
-    return x
+    return x + 1
"""
        
        # Read the original content
        with open(self.test_file, "r") as f:
            original_lines = f.read().splitlines(True)
        
        # Apply the diff
        modified_lines = apply_diff_with_enhanced_matching(self.test_file, diff, original_lines)
        modified_content = "".join(modified_lines)
        
        # Check the result
        expected = "def main():\n    x = 1\n    return x + 1\n"
        self.assertEqual(modified_content, expected)
    
    def test_whitespace_only_changes(self):
        """Test application of whitespace-only changes."""
        # Create a diff with only whitespace changes
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdef0 100644
--- a/test.py
+++ b/test.py
@@ -1,3 +1,3 @@
 def main():
-    x = 1
+    x =  1
     return x
"""
        
        # Read the original content
        with open(self.test_file, "r") as f:
            original_lines = f.read().splitlines(True)
        
        # Apply the diff
        modified_lines = apply_diff_with_enhanced_matching(self.test_file, diff, original_lines)
        modified_content = "".join(modified_lines)
        
        # Check the result
        expected = "def main():\n    x =  1\n    return x\n"
        self.assertEqual(modified_content, expected)
    
    def test_indentation_changes(self):
        """Test application of indentation changes."""
        # Create a file with indentation
        indentation_file = os.path.join(self.test_dir, "indentation.py")
        with open(indentation_file, "w") as f:
            f.write("def test():\n    normal_indent = True\n    also_normal = True\n    still_normal = True\n    return True\n")
        
        # Create a diff that changes indentation
        diff = """diff --git a/indentation.py b/indentation.py
index 1234567..abcdef0 100644
--- a/indentation.py
+++ b/indentation.py
@@ -1,5 +1,5 @@
 def test():
     normal_indent = True
-    also_normal = True
-    still_normal = True
+      more_indent = True
+        even_more = True
+    back_to_normal = True
     return True
"""
        
        # Read the original content
        with open(indentation_file, "r") as f:
            original_lines = f.read().splitlines(True)
        
        # Apply the diff
        modified_lines = apply_diff_with_enhanced_matching(indentation_file, diff, original_lines)
        modified_content = "".join(modified_lines)
        
        # Check the result
        expected = "def test():\n    normal_indent = True\n      more_indent = True\n        even_more = True\n    back_to_normal = True\n    return True\n"
        self.assertEqual(modified_content, expected)
    
    def test_match_quality_calculation(self):
        """Test calculation of match quality."""
        file_slice = [
            "    x = 1",
            "    y = 2",
            "    z = 3"
        ]
        
        # Exact match
        chunk_lines = [
            "    x = 1",
            "    y = 2",
            "    z = 3"
        ]
        
        quality = calculate_match_quality(file_slice, chunk_lines)
        self.assertEqual(quality, 1.0)
        
        # Partial match (whitespace differences)
        chunk_lines_ws = [
            "    x = 1",
            "  y = 2",  # Different indentation
            "    z = 3"
        ]
        
        quality_ws = calculate_match_quality(file_slice, chunk_lines_ws)
        self.assertGreaterEqual(quality_ws, 0.9)
        
        # Partial match (content differences)
        chunk_lines_content = [
            "    x = 1",
            "    y = 2",
            "    w = 4"  # Different variable
        ]
        
        quality_content = calculate_match_quality(file_slice, chunk_lines_content)
        self.assertLess(quality_content, 0.9)
        self.assertGreaterEqual(quality_content, 0.6)

if __name__ == "__main__":
    unittest.main()
