"""
Tests for detecting and preventing duplicate function issues in diff application.
"""

import os
import unittest
import tempfile
import shutil
from typing import List, Tuple

from ..application.patch_apply import (
    apply_diff_with_difflib,
    MIN_CONFIDENCE,
    MAX_OFFSET
)
from ..core.exceptions import PatchApplicationError
from app.utils.logging_utils import logger


class TestDuplicateFunctionDetection(unittest.TestCase):
    """Test cases for detecting and preventing duplicate function issues."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.temp_dir, "test_file.py")
        
        # Create a test file with a function similar to file_state_manager.py
        # Add some extra content to make the fuzzy matching more likely to fail
        with open(self.test_file, "w") as f:
            f.write('''import os
from typing import List, Tuple, Optional, Set, Dict, Any

class FileStateManager:
    def __init__(self):
        self.conversation_states = {}
        
    # Several other methods...
    
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
        
        # Skip directories
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
        # Rest of the method...
        
    def another_method(self):
        """Just another method to add more context"""
        pass
''')
    
    def tearDown(self):
        """Clean up test environment."""
        shutil.rmtree(self.temp_dir)
    
    def test_manual_duplicate_function(self):
        """Test that manually creates a duplicate function to demonstrate the issue."""
        # First, let's manually create a file with a duplicated function to show what we're testing for
        duplicate_file = os.path.join(self.temp_dir, "duplicate_example.py")
        
        with open(duplicate_file, "w") as f:
            f.write('''import os
from typing import List, Tuple

class FileStateManager:
    def __init__(self):
        self.conversation_states = {}
    
    def get_annotated_content(self, conversation_id: str, file_path: str) -> Tuple[List[str], bool]:
        """Get content with line state annotations"""
        # Skip directories
        full_path = os.path.join(os.environ["ZIYA_USER_CODEBASE_DIR"], file_path)
        if os.path.isdir(full_path):
            logger.debug(f"Skipping directory in get_annotated_content: {file_path}")
            return [], False

        state = self.conversation_states.get(conversation_id, {}).get(file_path)
        if not state or not os.path.isfile(full_path):
            return [], False
            
        annotated_lines = []
        try:
            for i, line in enumerate(state.current_content, 1):
                line_state = state.line_states.get(i, ' ')
                annotated_lines.append(f"[{i:03d}{line_state}] {line}")
            return annotated_lines, True
        except Exception as e:
            logger.debug(f"Error annotating content for {file_path}: {str(e)}")
            return [], False
            
    def get_annotated_content(self, conversation_id: str, file_path: str) -> Tuple[List[str], bool]:
        """Get content with line state annotations"""
        state = self.conversation_states.get(conversation_id, {}).get(file_path)
        
        # Skip directories
        if os.path.isdir(os.path.join(os.environ["ZIYA_USER_CODEBASE_DIR"], file_path)):
            return [], False
            
        if not state:
            return [], False
            
        annotated_lines = []
        for i, line in enumerate(state.current_content, 1):
            line_state = state.line_states.get(i, ' ')
            annotated_lines.append(f"[{i:03d}{line_state}] {line}")
            
        return annotated_lines, True
''')
        
        # Verify the duplicate function exists
        with open(duplicate_file, "r") as f:
            content = f.read()
        
        # Count occurrences of the function signature
        function_signature = "def get_annotated_content"
        occurrences = content.count(function_signature)
        
        # This should pass because we manually created the duplicate
        self.assertEqual(
            occurrences, 
            2,
            f"Expected 2 occurrences but found {occurrences} occurrences of '{function_signature}'"
        )
        
        print(f"Manually created duplicate function example with {occurrences} occurrences of '{function_signature}'")
        
    def test_duplicate_function_prevention(self):
        """Test that the language handler prevents duplicate functions."""
        # Create a diff that would add a duplicate function
        diff = '''diff --git a/test_file.py b/test_file.py
--- a/test_file.py
+++ b/test_file.py
@@ -35,6 +35,25 @@ class FileStateManager:
         # Rest of the method...
         
     def another_method(self):
-        """Just another method to add more context"""
+        """Just another method to add more context."""
         pass
+        
+    def get_annotated_content(self, conversation_id: str, file_path: str) -> Tuple[List[str], bool]:
+        """Get content with line state annotations (duplicate)"""
+        # Skip directories
+        full_path = os.path.join(os.environ["ZIYA_USER_CODEBASE_DIR"], file_path)
+        if os.path.isdir(full_path):
+            logger.debug(f"Skipping directory in get_annotated_content: {file_path}")
+            return [], False
+
+        state = self.conversation_states.get(conversation_id, {}).get(file_path)
+        if not state or not os.path.isfile(full_path):
+            return [], False
+            
+        annotated_lines = []
+        try:
+            for i, line in enumerate(state.current_content, 1):
+                line_state = state.line_states.get(i, ' ')
+                annotated_lines.append(f"[{i:03d}{line_state}] {line}")
+            return annotated_lines, True
+        except Exception as e:
+            logger.debug(f"Error annotating content for {file_path}: {str(e)}")
+            return [], False'''
        
        # Temporarily lower the confidence threshold to force the issue
        original_min_confidence = MIN_CONFIDENCE
        original_max_offset = MAX_OFFSET
        
        try:
            # Set values that would have caused the issue in the old implementation
            import app.utils.diff_utils.application.patch_apply as patch_apply
            patch_apply.MIN_CONFIDENCE = 0.5  # Lower threshold to accept poor matches
            patch_apply.MAX_OFFSET = 20      # Higher offset to allow misplaced changes
            
            # Apply the diff - this should now be prevented by the language handler
            with self.assertRaises(PatchApplicationError) as context:
                apply_diff_with_difflib(self.test_file, diff)
            
            # Verify the error message mentions duplicate code
            self.assertIn("duplicate", str(context.exception).lower())
            
            # Check that the file wasn't modified to have duplicates
            with open(self.test_file, "r") as f:
                content = f.read()
            
            # Count occurrences of the function signature
            function_signature = "def get_annotated_content"
            occurrences = content.count(function_signature)
            
            # Verify there's still only one occurrence
            self.assertEqual(
                occurrences, 
                1,
                f"Expected 1 occurrence but found {occurrences} occurrences of '{function_signature}'"
            )
            
            print(f"Successfully prevented duplicate function creation")
            
        finally:
            # Restore original values
            patch_apply.MIN_CONFIDENCE = original_min_confidence
            patch_apply.MAX_OFFSET = original_max_offset


if __name__ == "__main__":
    unittest.main()
