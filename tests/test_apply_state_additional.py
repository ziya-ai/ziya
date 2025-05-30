import sys
import os

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import unittest
import json
import tempfile
import shutil
import logging
from app.utils.code_util import use_git_to_apply_code_diff

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ApplyStateAdditionalTest(unittest.TestCase):
    """Additional tests for validating the diff apply state reporting mechanism"""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # Set required environment variable
        os.environ['ZIYA_USER_CODEBASE_DIR'] = self.temp_dir
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def test_empty_diff(self):
        """Test that empty diffs are handled correctly"""
        # Create a simple file
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n")
        
        # Create an empty diff
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
"""
        
        # Store original content
        with open(test_file, "r") as f:
            original_content = f.read()
        
        # Apply the diff
        result = use_git_to_apply_code_diff(diff, test_file)
        
        # Read the modified content
        with open(test_file, "r") as f:
            modified_content = f.read()
        
        # Content shouldn't change
        self.assertEqual(original_content, modified_content,
                        "Content changed when applying an empty diff")
        
        # changes_written should be False
        self.assertFalse(result["details"]["changes_written"],
                        "changes_written should be False for empty diff")
        
        # Status should not be error
        self.assertNotEqual(result["status"], "error",
                          "Status should not be error for empty diff")
    
    def test_whitespace_only_changes(self):
        """Test that whitespace-only changes are correctly detected and reported"""
        # Create a simple file
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n")
        
        # Create a diff with only whitespace changes
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
 def hello():
-    print('Hello')
+    print('Hello')  
"""
        
        # Store original content
        with open(test_file, "r") as f:
            original_content = f.read()
        
        # Apply the diff
        result = use_git_to_apply_code_diff(diff, test_file)
        
        # Read the modified content
        with open(test_file, "r") as f:
            modified_content = f.read()
        
        # Check if content actually changed
        content_changed = original_content != modified_content
        
        # If content changed, changes_written should be True
        # If content didn't change, changes_written should be False
        self.assertEqual(content_changed, result["details"]["changes_written"],
                        f"Content changed: {content_changed}, but changes_written: {result['details']['changes_written']}")
        
        # Status should not be error
        self.assertNotEqual(result["status"], "error",
                          "Status should not be error for whitespace-only changes")
    
    def test_invisible_unicode_characters(self):
        """Test proper handling of invisible Unicode characters"""
        # Create a file with invisible Unicode characters
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            # Add zero-width space between 'Hello' and closing quote
            f.write("def hello():\n    print('Hello\u200B')\n")
        
        # Create a diff that would change the invisible character
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
 def hello():
-    print('Hello\u200B')
+    print('Hello')
"""
        
        # Store original content
        with open(test_file, "r") as f:
            original_content = f.read()
        
        # Apply the diff
        result = use_git_to_apply_code_diff(diff, test_file)
        
        # Read the modified content
        with open(test_file, "r") as f:
            modified_content = f.read()
        
        # Check if content actually changed
        content_changed = original_content != modified_content
        
        # If content changed, changes_written should be True
        # If content didn't change, changes_written should be False
        self.assertEqual(content_changed, result["details"]["changes_written"],
                        f"Content changed: {content_changed}, but changes_written: {result['details']['changes_written']}")
        
        # Status should not be error if content changed
        if content_changed:
            self.assertNotEqual(result["status"], "error",
                              "Status should not be error when content changed")
    
    def test_multiple_hunks_mixed_results(self):
        """Test a case where some hunks succeed and some fail"""
        # Create a file with multiple functions
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n\ndef goodbye():\n    print('Goodbye')\n")
        
        # Create a diff with two hunks, one that should apply and one that should fail
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,3 @@
 def hello():
     print('Hello')
+    print('World')
@@ -4,2 +5,2 @@
 def goodbye():
-    print('Goodbye')
+    print('Universe')
"""
        
        # Store original content
        with open(test_file, "r") as f:
            original_content = f.read()
        
        # Apply the diff
        result = use_git_to_apply_code_diff(diff, test_file)
        
        # Read the modified content
        with open(test_file, "r") as f:
            modified_content = f.read()
        
        # Check if content actually changed
        content_changed = original_content != modified_content
        
        # If content changed but not all hunks applied, status should be partial
        if content_changed and (len(result["details"]["succeeded"]) < 2 or len(result["details"]["failed"]) > 0):
            self.assertEqual(result["status"], "partial",
                           f"Status should be partial for partial apply, got {result['status']}")
            self.assertTrue(result["details"]["changes_written"],
                          "changes_written should be True for partial apply")
    
    def test_new_file_creation(self):
        """Test that creating a new file is correctly reported"""
        # Create a path for a new file that doesn't exist yet
        test_file = os.path.join(self.temp_dir, "new_file.py")
        
        # Create a diff that creates a new file
        diff = """diff --git a/new_file.py b/new_file.py
new file mode 100644
index 0000000..abcdefg
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,2 @@
+def hello():
+    print('Hello')
"""
        
        # Apply the diff
        result = use_git_to_apply_code_diff(diff, test_file)
        
        # Check if the file was created
        file_created = os.path.exists(test_file)
        
        # File should be created
        self.assertTrue(file_created, "New file was not created")
        
        # changes_written should be True
        self.assertTrue(result["details"]["changes_written"],
                       "changes_written should be True for new file creation")
        
        # Status should be success
        self.assertEqual(result["status"], "success",
                        f"Status should be success for new file creation, got {result['status']}")
    
    def test_line_ending_differences(self):
        """Test that differences in line endings are handled correctly"""
        # Create a file with LF line endings
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w", newline='\n') as f:
            f.write("def hello():\n    print('Hello')\n")
        
        # Create a diff that would change line endings to CRLF
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
 def hello():
-    print('Hello')
+    print('Hello')
"""
        
        # Store original content
        with open(test_file, "rb") as f:  # Use binary mode to preserve line endings
            original_content = f.read()
        
        # Apply the diff
        result = use_git_to_apply_code_diff(diff, test_file)
        
        # Read the modified content
        with open(test_file, "rb") as f:  # Use binary mode to preserve line endings
            modified_content = f.read()
        
        # Check if content actually changed
        content_changed = original_content != modified_content
        
        # If content changed, changes_written should be True
        # If content didn't change, changes_written should be False
        self.assertEqual(content_changed, result["details"]["changes_written"],
                        f"Content changed: {content_changed}, but changes_written: {result['details']['changes_written']}")
    
    def test_escape_sequence_handling(self):
        """Test proper handling of escape sequences"""
        # Create a file with escape sequences
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello\\n')\n")
        
        # Create a diff that would change the escape sequence
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,2 @@
 def hello():
-    print('Hello\\n')
+    print('Hello\\t')
"""
        
        # Store original content
        with open(test_file, "r") as f:
            original_content = f.read()
        
        # Apply the diff
        result = use_git_to_apply_code_diff(diff, test_file)
        
        # Read the modified content
        with open(test_file, "r") as f:
            modified_content = f.read()
        
        # Check if content actually changed
        content_changed = original_content != modified_content
        
        # If content changed, changes_written should be True
        # If content didn't change, changes_written should be False
        self.assertEqual(content_changed, result["details"]["changes_written"],
                        f"Content changed: {content_changed}, but changes_written: {result['details']['changes_written']}")
        
        # Status should not be error if content changed
        if content_changed:
            self.assertNotEqual(result["status"], "error",
                              "Status should not be error when content changed")

if __name__ == '__main__':
    unittest.main()
