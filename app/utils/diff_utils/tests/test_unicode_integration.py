"""
Integration tests for Unicode handling in diff application.
"""

import unittest
import tempfile
import os
from app.utils.diff_utils.core.unicode_handling import (
    contains_invisible_chars,
    normalize_unicode,
    handle_invisible_unicode
)
from app.utils.diff_utils.application.difflib_apply import apply_diff_with_difflib

class TestUnicodeIntegration(unittest.TestCase):
    """Integration tests for Unicode handling in diff application."""
    
    def setUp(self):
        """Set up test environment."""
        # Create a temporary directory for test files
        self.test_dir = tempfile.mkdtemp()
        
        # Create a test file with invisible Unicode characters
        self.test_file_path = os.path.join(self.test_dir, "test_unicode.py")
        with open(self.test_file_path, "w", encoding="utf-8") as f:
            f.write("""def greet(name):
    # This function greets the user
    message = "Hello\u200B, " + name + "!"
    print(message)
    return message

def farewell(name):
    # This function says goodbye
    message = "Goodbye\u200C, " + name + "!"
    print(message)
    return message
""")
    
    def tearDown(self):
        """Clean up test environment."""
        # Remove the test file and directory
        if os.path.exists(self.test_file_path):
            os.unlink(self.test_file_path)
        if os.path.exists(self.test_dir):
            os.rmdir(self.test_dir)
    
    def test_diff_with_invisible_chars(self):
        """Test applying a diff with invisible Unicode characters."""
        # Create a diff that modifies a line with invisible characters
        diff = """diff --git a/test_unicode.py b/test_unicode.py
index 1234567..abcdef0 100644
--- a/test_unicode.py
+++ b/test_unicode.py
@@ -1,6 +1,6 @@
 def greet(name):
     # This function greets the user
-    message = "Hello\u200B, " + name + "!"
+    message = "Hi, " + name + "!"
     print(message)
     return message
 
"""
        
        # Apply the diff
        with open(self.test_file_path, "r", encoding="utf-8") as f:
            original_content = f.read()
        
        # Verify the file contains invisible characters
        self.assertTrue(contains_invisible_chars(original_content))
        
        # Apply the diff using the handle_invisible_unicode function
        modified_content = handle_invisible_unicode(original_content, diff)
        
        # Write the modified content back to the file
        with open(self.test_file_path, "w", encoding="utf-8") as f:
            f.write(modified_content)
        
        # Read the file again
        with open(self.test_file_path, "r", encoding="utf-8") as f:
            result_content = f.read()
        
        # Verify the invisible character is removed (since the new line doesn't have it)
        self.assertNotIn("Hello\u200B,", result_content)
        self.assertIn("Hi,", result_content)
    
    def test_diff_adding_invisible_chars(self):
        """Test applying a diff that adds invisible Unicode characters."""
        # Create a diff that adds invisible characters
        diff = """diff --git a/test_unicode.py b/test_unicode.py
index 1234567..abcdef0 100644
--- a/test_unicode.py
+++ b/test_unicode.py
@@ -7,5 +7,5 @@
 def farewell(name):
     # This function says goodbye
-    message = "Goodbye\u200C, " + name + "!"
+    message = "Goodbye, " + name + "\u200D!"
     print(message)
     return message
"""
        
        # Apply the diff
        with open(self.test_file_path, "r", encoding="utf-8") as f:
            original_content = f.read()
        
        # Apply the diff using the handle_invisible_unicode function
        modified_content = handle_invisible_unicode(original_content, diff)
        
        # Write the modified content back to the file
        with open(self.test_file_path, "w", encoding="utf-8") as f:
            f.write(modified_content)
        
        # Read the file again
        with open(self.test_file_path, "r", encoding="utf-8") as f:
            result_content = f.read()
        
        # Verify the invisible character is present
        self.assertIn("\u200D", result_content)
        
        # Verify the content was changed correctly
        self.assertIn("Goodbye, " + "name" + "\u200D!", result_content)
    
    def test_difflib_with_invisible_chars(self):
        """Test applying a diff with invisible Unicode characters using difflib."""
        # Create a diff that modifies a line with invisible characters
        diff = """diff --git a/test_unicode.py b/test_unicode.py
index 1234567..abcdef0 100644
--- a/test_unicode.py
+++ b/test_unicode.py
@@ -1,6 +1,6 @@
 def greet(name):
     # This function greets the user
-    message = "Hello\u200B, " + name + "!"
+    message = "Hi\u200B, " + name + "!"
     print(message)
     return message
 
"""
        
        # Apply the diff using difflib
        modified_content = apply_diff_with_difflib(self.test_file_path, diff)
        
        # Write the modified content back to the file
        with open(self.test_file_path, "w", encoding="utf-8") as f:
            f.write(modified_content)
        
        # Read the file again
        with open(self.test_file_path, "r", encoding="utf-8") as f:
            result_content = f.read()
        
        # Verify the content was changed correctly
        # Note: difflib might not preserve invisible characters, so we check the visible content
        normalized_result = normalize_unicode(result_content)
        self.assertIn("Hi", normalized_result)
        self.assertNotIn("Hello", normalized_result)

if __name__ == "__main__":
    unittest.main()
