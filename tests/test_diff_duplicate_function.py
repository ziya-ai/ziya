"""
Tests for detecting and preventing duplicate function issues in diff application.

Moved from app/utils/diff_utils/tests/test_duplicate_function.py to be visible
to pytest (testpaths = tests).
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


class TestDuplicateFunctionDetection(unittest.TestCase):
    """Test cases for detecting and preventing duplicate function issues."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.temp_dir, "test_file.py")
        
        with open(self.test_file, "w") as f:
            f.write('''import os
from typing import List, Tuple, Optional, Set, Dict, Any

class FileStateManager:
    def __init__(self):
        self.conversation_states = {}
        
    def some_other_function(self):
        """This is another function to add some context"""
        return True
        
    def get_file_content(self, file_path: str) -> str:
        """Get raw file content"""
        with open(file_path, 'r') as f:
            return f.read()
    
    def get_annotated_content(self, conversation_id: str, file_path: str) -> Tuple[List[str], bool]:
        """Get content with line state annotations"""
        state = self.conversation_states.get(conversation_id, {}).get(file_path)
        
        if os.path.isdir(os.path.join(os.environ["ZIYA_USER_CODEBASE_DIR"], file_path)):
            return [], False
            
        if not state:
            return [], False
            
        annotated_lines = []
        for i, line in enumerate(state.current_content, 1):
            line_state = state.line_states.get(i, ' ')
            annotated_lines.append(f"[{i:03d}{line_state}] {line}")
            
        return annotated_lines, True
    
    def update_file_state(self, conversation_id: str, file_path: str, new_content: str):
        """Update file state and return set of changed line numbers"""
        if conversation_id not in self.conversation_states:
            return None
            
        state = self.conversation_states[conversation_id].get(file_path)
        
    def another_method(self):
        """Just another method to add more context"""
        pass
''')
    
    def tearDown(self):
        """Clean up test environment."""
        shutil.rmtree(self.temp_dir)
    
    def test_manual_duplicate_function(self):
        """Test that manually creates a duplicate function to demonstrate the issue."""
        duplicate_file = os.path.join(self.temp_dir, "duplicate_example.py")
        
        with open(duplicate_file, "w") as f:
            f.write('''import os
from typing import List, Tuple

class FileStateManager:
    def __init__(self):
        self.conversation_states = {}
    
    def get_annotated_content(self, conversation_id, file_path):
        """Get content with line state annotations"""
        return [], False
            
    def get_annotated_content(self, conversation_id, file_path):
        """Get content with line state annotations (duplicate)"""
        return [], False
''')
        
        with open(duplicate_file, "r") as f:
            content = f.read()
        
        function_signature = "def get_annotated_content"
        occurrences = content.count(function_signature)
        
        self.assertEqual(occurrences, 2,
            f"Expected 2 occurrences but found {occurrences}")
        
    def test_duplicate_function_prevention(self):
        """Test that the language handler prevents duplicate functions."""
        diff = '''diff --git a/test_file.py b/test_file.py
--- a/test_file.py
+++ b/test_file.py
@@ -35,6 +35,25 @@ class FileStateManager:
         
     def another_method(self):
-        """Just another method to add more context"""
+        """Just another method to add more context."""
         pass
+        
+    def get_annotated_content(self, conversation_id: str, file_path: str):
+        """Get content with line state annotations (duplicate)"""
+        return [], False'''
        
        import app.utils.diff_utils.application.patch_apply as patch_apply
        original_min_confidence = patch_apply.MIN_CONFIDENCE
        
        try:
            patch_apply.MIN_CONFIDENCE = 0.5
            
            with self.assertRaises(PatchApplicationError):
                apply_diff_with_difflib(self.test_file, diff)
            
            with open(self.test_file, "r") as f:
                content = f.read()
            
            function_signature = "def get_annotated_content"
            occurrences = content.count(function_signature)
            
            self.assertEqual(occurrences, 1,
                f"Expected 1 occurrence but found {occurrences}")
            
        finally:
            patch_apply.MIN_CONFIDENCE = original_min_confidence


if __name__ == "__main__":
    unittest.main()
