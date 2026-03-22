"""
Tests for improved confidence thresholds in diff application.

Moved from app/utils/diff_utils/tests/test_improved_confidence.py to be visible
to pytest (testpaths = tests).  Stale MAX_OFFSET import replaced with
get_max_offset() from the config module.
"""

import os
import unittest
import tempfile
import shutil
from typing import List, Tuple

from app.utils.diff_utils.application.patch_apply import (
    apply_diff_with_difflib,
    MIN_CONFIDENCE,
)
from app.utils.diff_utils.core.config import get_max_offset
from app.utils.diff_utils.core.exceptions import PatchApplicationError
from app.utils.logging_utils import logger


class TestImprovedConfidenceThresholds(unittest.TestCase):
    """Test cases for improved confidence thresholds in diff application."""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.temp_dir, "test_file.py")
        
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
        shutil.rmtree(self.temp_dir)
    
    def test_confidence_threshold_enforced(self):
        """Test that the confidence threshold is properly enforced."""
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
        
        import app.utils.diff_utils.application.patch_apply as patch_apply
        original_min_confidence = patch_apply.MIN_CONFIDENCE
        
        try:
            patch_apply.MIN_CONFIDENCE = 0.99
            
            with self.assertRaises(PatchApplicationError):
                bad_diff = diff.replace("function_two", "function_tvo")
                apply_diff_with_difflib(self.test_file, bad_diff)
            
        finally:
            patch_apply.MIN_CONFIDENCE = original_min_confidence
        
        with open(self.test_file, "r") as f:
            content = f.read()
        
        self.assertIn('"This is function two."', content)
        self.assertIn('print("Function two")', content)
        self.assertIn("return False", content)
    
    def test_offset_limit_enforced(self):
        """Test that the offset limit is properly enforced."""
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
        
        import app.utils.diff_utils.application.patch_apply as patch_apply
        # MAX_OFFSET was refactored into get_max_offset(); monkey-patch
        # the function for this test.
        from app.utils.diff_utils.core import config as diff_config
        original_get_max = diff_config.get_max_offset
        
        try:
            diff_config.get_max_offset = lambda: 0
            
            with self.assertRaises(PatchApplicationError):
                apply_diff_with_difflib(self.test_file, diff)
            
        finally:
            diff_config.get_max_offset = original_get_max
        
        with open(self.test_file, "r") as f:
            content = f.read()
        
        self.assertIn('"This is function three."', content)
        self.assertIn('print("Function three")', content)
        self.assertIn("return None", content)


if __name__ == "__main__":
    unittest.main()
