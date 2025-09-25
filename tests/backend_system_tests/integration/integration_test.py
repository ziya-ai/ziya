#!/usr/bin/env python3
"""
Integration test for the refactored diff utilities.
This script tests the integration of the refactored diff utilities with the main Ziya application.
"""

import os
import sys
import tempfile
import shutil
import unittest
from typing import Dict, Any

# Add the project root to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import both original and refactored implementations
from app.utils.code_util import use_git_to_apply_code_diff as original_apply
from app.utils.code_util_refactored import use_git_to_apply_code_diff as refactored_apply

class IntegrationTest(unittest.TestCase):
    """Integration tests for the refactored diff utilities."""

    def setUp(self):
        """Set up the test environment."""
        # Create a temporary directory for test files
        self.temp_dir = tempfile.mkdtemp()
        os.environ["ZIYA_USER_CODEBASE_DIR"] = self.temp_dir
        
        # Create test files
        self.test_files = {}
        self.create_test_files()
        
    def tearDown(self):
        """Clean up the test environment."""
        # Remove the temporary directory
        shutil.rmtree(self.temp_dir)
        
    def create_test_files(self):
        """Create test files for the integration tests."""
        # Test file 1: Simple Python file
        file1_path = os.path.join(self.temp_dir, "test1.py")
        with open(file1_path, "w") as f:
            f.write("""def hello_world():
    print("Hello, World!")
    
def add(a, b):
    return a + b
""")
        self.test_files["test1.py"] = file1_path
        
        # Test file 2: File with escape sequences
        file2_path = os.path.join(self.temp_dir, "test2.py")
        with open(file2_path, "w") as f:
            f.write("""def test_escapes():
    text = ""
    # Add some escape sequences
    text += "Line 1\\n"
    text += "Line 2\\t"
    return text
""")
        self.test_files["test2.py"] = file2_path
        
        # Test file 3: File with constants
        file3_path = os.path.join(self.temp_dir, "test3.py")
        with open(file3_path, "w") as f:
            f.write("""# Configuration constants
DEFAULT_PORT = 6969
DEFAULT_HOST = "localhost"

def start_server(port=DEFAULT_PORT, host=DEFAULT_HOST):
    print(f"Starting server on {host}:{port}")
""")
        self.test_files["test3.py"] = file3_path
        
    def read_file(self, file_path):
        """Read a file and return its contents."""
        with open(file_path, "r") as f:
            return f.read()
            
    def apply_diff(self, diff, file_path, implementation):
        """Apply a diff to a file using the specified implementation."""
        full_path = self.test_files.get(file_path, os.path.join(self.temp_dir, file_path))
        return implementation(diff, full_path)
        
    def test_simple_addition(self):
        """Test adding a new function to a file."""
        diff = """diff --git a/test1.py b/test1.py
--- a/test1.py
+++ b/test1.py
@@ -3,3 +3,6 @@ def hello_world():
 
 def add(a, b):
     return a + b
+
+def subtract(a, b):
+    return a - b
"""
        # Apply with original implementation
        original_result = self.apply_diff(diff, "test1.py", original_apply)
        original_content = self.read_file(self.test_files["test1.py"])
        
        # Reset the file
        self.create_test_files()
        
        # Apply with refactored implementation
        refactored_result = self.apply_diff(diff, "test1.py", refactored_apply)
        refactored_content = self.read_file(self.test_files["test1.py"])
        
        # Compare results
        self.assertEqual(original_content, refactored_content)
        self.assertEqual(original_result["status"], refactored_result["status"])
        
    def test_escape_sequence_handling(self):
        """Test handling of escape sequences."""
        diff = """diff --git a/test2.py b/test2.py
--- a/test2.py
+++ b/test2.py
@@ -3,4 +3,5 @@ def test_escapes():
     # Add some escape sequences
     text += "Line 1\\n"
     text += "Line 2\\t"
+    text += "Line 3\\r\\n"
     return text
"""
        # Apply with original implementation
        original_result = self.apply_diff(diff, "test2.py", original_apply)
        original_content = self.read_file(self.test_files["test2.py"])
        
        # Reset the file
        self.create_test_files()
        
        # Apply with refactored implementation
        refactored_result = self.apply_diff(diff, "test2.py", refactored_apply)
        refactored_content = self.read_file(self.test_files["test2.py"])
        
        # Compare results
        self.assertEqual(original_content, refactored_content)
        self.assertEqual(original_result["status"], refactored_result["status"])
        
    def test_constant_duplicate_handling(self):
        """Test handling of duplicate constants."""
        diff = """diff --git a/test3.py b/test3.py
--- a/test3.py
+++ b/test3.py
@@ -1,5 +1,6 @@
 # Configuration constants
 DEFAULT_PORT = 6969
+DEFAULT_PORT = 6969  # Duplicate constant
 DEFAULT_HOST = "localhost"
 
 def start_server(port=DEFAULT_PORT, host=DEFAULT_HOST):
"""
        # Apply with original implementation
        original_result = self.apply_diff(diff, "test3.py", original_apply)
        original_content = self.read_file(self.test_files["test3.py"])
        
        # Reset the file
        self.create_test_files()
        
        # Apply with refactored implementation
        refactored_result = self.apply_diff(diff, "test3.py", refactored_apply)
        refactored_content = self.read_file(self.test_files["test3.py"])
        
        # Compare results
        self.assertEqual(original_content, refactored_content)
        self.assertEqual(original_result["status"], refactored_result["status"])
        
    def test_new_file_creation(self):
        """Test creating a new file."""
        diff = """diff --git a/new_file.py b/new_file.py
new file mode 100644
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,4 @@
+def new_function():
+    print("This is a new file")
+    return True
+
"""
        # Apply with original implementation
        original_result = self.apply_diff(diff, "new_file.py", original_apply)
        original_file_path = os.path.join(self.temp_dir, "new_file.py")
        self.assertTrue(os.path.exists(original_file_path))
        original_content = self.read_file(original_file_path)
        
        # Remove the file
        os.remove(original_file_path)
        
        # Apply with refactored implementation
        refactored_result = self.apply_diff(diff, "new_file.py", refactored_apply)
        refactored_file_path = os.path.join(self.temp_dir, "new_file.py")
        self.assertTrue(os.path.exists(refactored_file_path))
        refactored_content = self.read_file(refactored_file_path)
        
        # Compare results
        self.assertEqual(original_content, refactored_content)
        
    def test_multiple_hunks(self):
        """Test applying multiple hunks to a file."""
        diff = """diff --git a/test1.py b/test1.py
--- a/test1.py
+++ b/test1.py
@@ -1,5 +1,6 @@
 def hello_world():
     print("Hello, World!")
+    print("Welcome!")
     
 def add(a, b):
     return a + b
@@ -3,3 +4,6 @@ def hello_world():
 
 def add(a, b):
     return a + b
+
+def multiply(a, b):
+    return a * b
"""
        # Apply with original implementation
        original_result = self.apply_diff(diff, "test1.py", original_apply)
        original_content = self.read_file(self.test_files["test1.py"])
        
        # Reset the file
        self.create_test_files()
        
        # Apply with refactored implementation
        refactored_result = self.apply_diff(diff, "test1.py", refactored_apply)
        refactored_content = self.read_file(self.test_files["test1.py"])
        
        # Compare results
        self.assertEqual(original_content, refactored_content)
        self.assertEqual(original_result["status"], refactored_result["status"])
        
    def test_already_applied_changes(self):
        """Test handling of changes that are already applied."""
        # First apply a change
        diff1 = """diff --git a/test1.py b/test1.py
--- a/test1.py
+++ b/test1.py
@@ -3,3 +3,6 @@ def hello_world():
 
 def add(a, b):
     return a + b
+
+def subtract(a, b):
+    return a - b
"""
        self.apply_diff(diff1, "test1.py", original_apply)
        
        # Then try to apply the same change again
        # Apply with original implementation
        original_result = self.apply_diff(diff1, "test1.py", original_apply)
        
        # Reset and repeat with refactored implementation
        self.create_test_files()
        self.apply_diff(diff1, "test1.py", refactored_apply)
        refactored_result = self.apply_diff(diff1, "test1.py", refactored_apply)
        
        # Compare results
        self.assertEqual(original_result["status"], refactored_result["status"])
        
    def test_line_calculation_fix(self):
        """Test the line calculation fix case."""
        # Create a test file with the line calculation issue
        file_path = os.path.join(self.temp_dir, "line_calc.py")
        with open(file_path, "w") as f:
            f.write("""def calculate_positions():
    old_start = h['old_start'] - 1
    old_count = h['old_count']
    initial_remove_pos = clamp(old_start + offset, 0, len(final_lines))

    # Adjust counts based on available lines
    available_lines = len(final_lines) - initial_remove_pos
    actual_old_count = min(old_count, available_lines)
    end_remove = remove_pos + actual_old_count

    # Final position adjustment
    remove_pos = clamp(initial_remove_pos, 0, len(final_lines) - 1 if final_lines else 0)

    return {
        'remove_pos': remove_pos,
        'old_count': old_count,
        'actual_old_count': actual_old_count,
        'end_remove': end_remove
    }
""")
        self.test_files["line_calc.py"] = file_path
        
        # Create a diff to fix the line calculation
        diff = """diff --git a/line_calc.py b/line_calc.py
--- a/line_calc.py
+++ b/line_calc.py
@@ -5,7 +5,7 @@ def calculate_positions():
 
     # Adjust counts based on available lines
     available_lines = len(final_lines) - initial_remove_pos
     actual_old_count = min(old_count, available_lines)
-    end_remove = remove_pos + actual_old_count
+    end_remove = min(remove_pos + actual_old_count, len(final_lines))
 
     # Final position adjustment
     remove_pos = clamp(initial_remove_pos, 0, len(final_lines) - 1 if final_lines else 0)
"""
        # Apply with original implementation
        original_result = self.apply_diff(diff, "line_calc.py", original_apply)
        original_content = self.read_file(file_path)
        
        # Reset the file
        with open(file_path, "w") as f:
            f.write("""def calculate_positions():
    old_start = h['old_start'] - 1
    old_count = h['old_count']
    initial_remove_pos = clamp(old_start + offset, 0, len(final_lines))

    # Adjust counts based on available lines
    available_lines = len(final_lines) - initial_remove_pos
    actual_old_count = min(old_count, available_lines)
    end_remove = remove_pos + actual_old_count

    # Final position adjustment
    remove_pos = clamp(initial_remove_pos, 0, len(final_lines) - 1 if final_lines else 0)

    return {
        'remove_pos': remove_pos,
        'old_count': old_count,
        'actual_old_count': actual_old_count,
        'end_remove': end_remove
    }
""")
        
        # Apply with refactored implementation
        refactored_result = self.apply_diff(diff, "line_calc.py", refactored_apply)
        refactored_content = self.read_file(file_path)
        
        # Compare results
        self.assertEqual(original_content, refactored_content)
        self.assertEqual(original_result["status"], refactored_result["status"])

if __name__ == "__main__":
    unittest.main()
