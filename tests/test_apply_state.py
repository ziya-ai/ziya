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

class ApplyStateTest(unittest.TestCase):
    """Tests for validating the diff apply state reporting mechanism"""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # Set required environment variable
        os.environ['ZIYA_USER_CODEBASE_DIR'] = self.temp_dir
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def test_content_change_detection(self):
        """Test that content changes are correctly detected and reported"""
        # Create a simple file
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n")
        
        # Create a simple diff
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,3 @@
 def hello():
     print('Hello')
+    print('World')
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
    
    def test_status_reflects_changes(self):
        """Test that status correctly reflects whether changes were made"""
        # Create a simple file
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n")
        
        # Create a simple diff
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,3 @@
 def hello():
     print('Hello')
+    print('World')
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
        
        # If content changed, status should be success or partial
        if content_changed:
            self.assertIn(result["status"], ["success", "partial"],
                        f"Content changed but status is {result['status']}")
        
        # If changes_written is True, status should be success or partial
        if result["details"]["changes_written"]:
            self.assertIn(result["status"], ["success", "partial"],
                        f"changes_written is True but status is {result['status']}")
    
    def test_already_applied_detection(self):
        """Test that already applied changes are correctly detected"""
        # Create a file with changes already applied
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n    print('World')\n")
        
        # Create a diff that would add the same line
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,3 @@
 def hello():
     print('Hello')
+    print('World')
"""
        
        # Store original content
        with open(test_file, "r") as f:
            original_content = f.read()
        
        # Apply the diff
        result = use_git_to_apply_code_diff(diff, test_file)
        
        # Read the content after applying
        with open(test_file, "r") as f:
            after_content = f.read()
        
        # Content shouldn't change
        self.assertEqual(original_content, after_content,
                        "Content changed when applying an already applied diff")
        
        # changes_written should be False
        self.assertFalse(result["details"]["changes_written"],
                        "changes_written should be False for already applied changes")
        
        # Status should be success
        self.assertEqual(result["status"], "success",
                        f"Status should be success for already applied changes, got {result['status']}")
        
        # already_applied should have entries
        self.assertTrue(len(result["details"]["already_applied"]) > 0,
                       "already_applied should have entries for already applied changes")
    
    def test_double_application_consistency(self):
        """Test consistency when applying the same diff twice"""
        # Create a simple file
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n")
        
        # Create a simple diff
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,3 @@
 def hello():
     print('Hello')
+    print('World')
"""
        
        # First application
        first_result = use_git_to_apply_code_diff(diff, test_file)
        
        # Read the modified content
        with open(test_file, "r") as f:
            modified_content = f.read()
        
        # Second application
        second_result = use_git_to_apply_code_diff(diff, test_file)
        
        # Read the content after second application
        with open(test_file, "r") as f:
            second_content = f.read()
        
        # Content shouldn't change after second application
        self.assertEqual(modified_content, second_content, 
                       "Content changed after second application")
        
        # First application should report changes_written=True if content changed
        if modified_content != "def hello():\n    print('Hello')\n":
            self.assertTrue(first_result["details"]["changes_written"],
                          "First application should report changes_written=True")
            
            # Second application should report changes_written=False
            self.assertFalse(second_result["details"]["changes_written"],
                           "Second application should report changes_written=False")
            
            # Second application should report already_applied hunks
            self.assertTrue(len(second_result["details"]["already_applied"]) > 0,
                          "Second application should report hunks as already_applied")
    
    def test_failed_apply_with_success_report(self):
        """Test that failed applications don't report success"""
        # Create a simple file
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n")
        
        # Create a diff with incorrect context that should fail to apply
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,3 @@
 def hello():
-    print('Wrong context')
+    print('Hello')
+    print('World')
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
        
        # If content didn't change, status should not be success
        if not content_changed:
            self.assertNotEqual(result["status"], "success",
                              "Status should not be success when no changes were made")
            self.assertFalse(result["details"]["changes_written"],
                           "changes_written should be False when no changes were made")
    
    def test_partial_apply_reporting(self):
        """Test that partial applications are correctly reported"""
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
@@ -4,2 +5,3 @@
 def goodbye():
-    print('Wrong context')
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
        if content_changed and len(result["details"]["failed"]) > 0:
            self.assertEqual(result["status"], "partial",
                           f"Status should be partial for partial apply, got {result['status']}")
            self.assertTrue(result["details"]["changes_written"],
                          "changes_written should be True for partial apply")
    
    def test_incorrect_success_reporting(self):
        """Test that success is not reported when changes weren't actually applied"""
        # Create a simple file
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n")
        
        # Create a diff with a complex change that might be mishandled
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,5 @@
+# This is a comment
 def hello():
     print('Hello')
+    # Another comment
+    print('World')
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
        
        # If status is success, content must have changed
        if result["status"] == "success":
            self.assertTrue(content_changed,
                          "Status is success but content didn't change")
            self.assertTrue(result["details"]["changes_written"],
                          "Status is success but changes_written is False")
        
        # If content didn't change, status should not be success (unless already applied)
        if not content_changed:
            if result["status"] == "success":
                self.assertTrue(len(result["details"]["already_applied"]) > 0,
                              "Status is success with no changes but no hunks marked as already_applied")
                self.assertFalse(result["details"]["changes_written"],
                               "No changes made but changes_written is True")
    
    def test_changes_written_accuracy(self):
        """Test that changes_written flag accurately reflects whether changes were made"""
        # Create a simple file
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n")
        
        # Create a simple diff
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,3 @@
 def hello():
     print('Hello')
+    print('World')
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
        
        # changes_written must accurately reflect whether content changed
        self.assertEqual(content_changed, result["details"]["changes_written"],
                        f"Content changed: {content_changed}, but changes_written: {result['details']['changes_written']}")
        
        # If changes_written is True, content must have changed
        if result["details"]["changes_written"]:
            self.assertNotEqual(original_content, modified_content,
                              "changes_written is True but content didn't change")
        
        # If changes_written is False, content must not have changed
        if not result["details"]["changes_written"]:
            self.assertEqual(original_content, modified_content,
                           "changes_written is False but content changed")

if __name__ == '__main__':
    unittest.main()
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
    
    def test_malformed_diff(self):
        """Test that malformed diffs are correctly reported as errors"""
        # Create a simple file
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n")
        
        # Create a malformed diff
        diff = """This is not a valid diff format
It should be reported as an error
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
                        "Content changed when applying a malformed diff")
        
        # Status should be error
        self.assertEqual(result["status"], "error",
                        f"Status should be error for malformed diff, got {result['status']}")
        
        # changes_written should be False
        self.assertFalse(result["details"]["changes_written"],
                        "changes_written should be False for malformed diff")
    def test_already_applied_with_whitespace_differences(self):
        """Test that changes that differ only in whitespace are correctly detected as already applied"""
        # Create a file with the content already applied but with different whitespace
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')  \n    print('World')\n")  # Note the extra spaces
        
        # Create a diff that would add the same content but with different whitespace
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,3 @@
 def hello():
     print('Hello')
+    print('World')
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
                        "Content changed when applying an already applied diff with whitespace differences")
        
        # changes_written should be False
        self.assertFalse(result["details"]["changes_written"],
                        "changes_written should be False for already applied changes with whitespace differences")
        
        # Status should be success
        self.assertEqual(result["status"], "success",
                        f"Status should be success for already applied changes with whitespace differences, got {result['status']}")
        
        # already_applied should have entries
        self.assertTrue(len(result["details"]["already_applied"]) > 0,
                       "already_applied should have entries for already applied changes with whitespace differences")
    
    def test_reporting_consistency_across_stages(self):
        """Test that reporting is consistent across different stages of the pipeline"""
        # Create a simple file
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n")
        
        # Create a simple diff
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,3 @@
 def hello():
     print('Hello')
+    print('World')
"""
        
        # First, force difflib mode
        os.environ['ZIYA_FORCE_DIFFLIB'] = '1'
        
        # Apply the diff with difflib
        difflib_result = use_git_to_apply_code_diff(diff, test_file)
        
        # Read the modified content
        with open(test_file, "r") as f:
            modified_content = f.read()
        
        # Reset the file
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n")
        
        # Now, use normal mode
        if 'ZIYA_FORCE_DIFFLIB' in os.environ:
            del os.environ['ZIYA_FORCE_DIFFLIB']
        
        # Apply the diff with normal pipeline
        normal_result = use_git_to_apply_code_diff(diff, test_file)
        
        # The status and changes_written should be consistent across both modes
        self.assertEqual(difflib_result["status"], normal_result["status"],
                        f"Status is inconsistent: difflib={difflib_result['status']}, normal={normal_result['status']}")
        
        self.assertEqual(difflib_result["details"]["changes_written"], normal_result["details"]["changes_written"],
                        f"changes_written is inconsistent: difflib={difflib_result['details']['changes_written']}, normal={normal_result['details']['changes_written']}")
    def test_error_propagation(self):
        """Test that errors from earlier stages are correctly propagated to the final result"""
        # Create a simple file
        test_file = os.path.join(self.temp_dir, "test.py")
        with open(test_file, "w") as f:
            f.write("def hello():\n    print('Hello')\n")
        
        # Create a diff with incorrect context that should fail in all stages
        diff = """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,2 +1,3 @@
 def hello():
-    print('Wrong context')
+    print('Hello')
+    print('World')
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
        
        # If content didn't change, status should be error
        if not content_changed:
            self.assertEqual(result["status"], "error",
                           f"Status should be error when no changes were made, got {result['status']}")
            self.assertFalse(result["details"]["changes_written"],
                           "changes_written should be False when no changes were made")
            
            # Check that error details are propagated
            self.assertTrue(result["details"]["error"] is not None,
                          "Error details should be propagated to the final result")
    
    def test_file_deletion(self):
        """Test that file deletion is correctly reported"""
        # This test is a placeholder since the current implementation doesn't support file deletion
        # When file deletion is implemented, this test should be updated
        pass
