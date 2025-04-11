"""
Tests for improved confidence thresholds in diff application.
"""

import os
import unittest
import tempfile
import shutil
from typing import List, Tuple

from app.utils.diff_utils.application.patch_apply import (
    apply_diff_with_difflib,
    MIN_CONFIDENCE,
    MAX_OFFSET
)
from app.utils.diff_utils.core.exceptions import PatchApplicationError
from app.utils.logging_utils import logger


class TestImprovedConfidenceThresholds(unittest.TestCase):
    """Test cases for improved confidence thresholds in diff application."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.temp_dir, "test_file.py")
        
        # Create a test file with some content
        with open(self.test_file, "w") as f:
            f.write('''def function_one():
    """This is function one."""
    print("Function one")
    return True

def function_two():
    """This is function two."""
    print("Function two")
    return False

def function_three():
    """This is function three."""
    print("Function three")
    return None
''')
    
    def tearDown(self):
        """Clean up test environment."""
        shutil.rmtree(self.temp_dir)
    
    def test_confidence_threshold_enforced(self):
        """Test that the confidence threshold is properly enforced."""
        # Create a diff that would modify function_two but with content that's similar to function_one
        # This should be rejected due to low confidence
        diff = '''diff --git a/test_file.py b/test_file.py
--- a/test_file.py
+++ b/test_file.py
@@ -5,9 +5,9 @@ def function_one():
     return True
 
 def function_two():
-    """This is function two."""
-    print("Function two")
-    return False
+    """This is modified function two."""
+    print("Modified function two")
+    return True
 
 def function_three():
     """This is function three."""'''
        
        # Temporarily modify the MIN_CONFIDENCE to force the test to check confidence
        import app.utils.diff_utils.application.patch_apply as patch_apply
        original_min_confidence = patch_apply.MIN_CONFIDENCE
        
        try:
            # Set a very high confidence threshold to ensure the test fails due to confidence
            patch_apply.MIN_CONFIDENCE = 0.99  # Almost impossible to match
            
            # Try to apply the diff - this should fail due to low confidence
            with self.assertRaises(PatchApplicationError) as context:
                # Modify the diff to make it harder to match
                bad_diff = diff.replace("function_two", "function_tvo")  # Introduce a typo
                apply_diff_with_difflib(self.test_file, bad_diff)
            
            # Verify the error message mentions duplicate code or failed to apply any hunks
            error_msg = str(context.exception).lower()
            self.assertTrue(
                "duplicate code" in error_msg or "failed to apply any hunks" in error_msg,
                f"Expected error message to mention 'duplicate code' or 'failed to apply any hunks', got: {error_msg}"
            )
            
        finally:
            # Restore original confidence threshold
            patch_apply.MIN_CONFIDENCE = original_min_confidence
        
        # Check that the file wasn't modified
        with open(self.test_file, "r") as f:
            content = f.read()
        
        # Verify the original content is unchanged
        self.assertIn("\"This is function two.\"", content)
        self.assertIn("print(\"Function two\")", content)
        self.assertIn("return False", content)
    
    def test_offset_limit_enforced(self):
        """Test that the offset limit is properly enforced."""
        # Create a diff that would modify function_one but target it at function_three
        # This should be rejected due to large offset
        diff = '''diff --git a/test_file.py b/test_file.py
--- a/test_file.py
+++ b/test_file.py
@@ -10,6 +10,6 @@ def function_two():
     return False
 
 def function_three():
-    """This is function three."""
-    print("Function three")
-    return None
+    """This is modified function three."""
+    print("Modified function three")
+    return "Modified"'''
        
        # Temporarily modify the MAX_OFFSET to force the test to check offset
        import app.utils.diff_utils.application.patch_apply as patch_apply
        original_max_offset = patch_apply.MAX_OFFSET
        
        try:
            # Set a very small offset limit to ensure the test fails due to offset
            patch_apply.MAX_OFFSET = 0  # No offset allowed
            
            # Try to apply the diff with a large offset
            with self.assertRaises(PatchApplicationError) as context:
                # Apply the diff with a deliberate offset
                apply_diff_with_difflib(self.test_file, diff)
            
            # Verify the error message mentions failed to apply any hunks
            self.assertIn("failed to apply any hunks", str(context.exception).lower())
            
        finally:
            # Restore original offset limit
            patch_apply.MAX_OFFSET = original_max_offset
        
        # Check that the file wasn't modified
        with open(self.test_file, "r") as f:
            content = f.read()
        
        # Verify the original content is unchanged
        self.assertIn("\"This is function three.\"", content)
        self.assertIn("print(\"Function three\")", content)
        self.assertIn("return None", content)


if __name__ == "__main__":
    unittest.main()
