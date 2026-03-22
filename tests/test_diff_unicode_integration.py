"""
Integration tests for Unicode handling in diff application.

Moved from app/utils/diff_utils/tests/test_unicode_integration.py to be visible
to pytest (testpaths = tests).  Stale ``difflib_apply`` import replaced with
the current ``patch_apply`` module.
"""

import unittest
import tempfile
import os
from app.utils.diff_utils.core.unicode_handling import (
    contains_invisible_chars,
    normalize_unicode,
    handle_invisible_unicode
)
from app.utils.diff_utils.application.patch_apply import apply_diff_with_difflib


class TestUnicodeIntegration(unittest.TestCase):
    """Integration tests for Unicode handling in diff application."""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_file_path = os.path.join(self.test_dir, "test_unicode.py")
        with open(self.test_file_path, "w", encoding="utf-8") as f:
            f.write("def greet(name):\n"
                    "    # This function greets the user\n"
                    '    message = "Hello\u200B, " + name + "!"\n'
                    "    print(message)\n"
                    "    return message\n"
                    "\n"
                    "def farewell(name):\n"
                    "    # This function says goodbye\n"
                    '    message = "Goodbye\u200C, " + name + "!"\n'
                    "    print(message)\n"
                    "    return message\n")
    
    def tearDown(self):
        if os.path.exists(self.test_file_path):
            os.unlink(self.test_file_path)
        if os.path.exists(self.test_dir):
            os.rmdir(self.test_dir)
    
    def test_diff_with_invisible_chars(self):
        """Test applying a diff with invisible Unicode characters."""
        diff = ('diff --git a/test_unicode.py b/test_unicode.py\n'
                'index 1234567..abcdef0 100644\n'
                '--- a/test_unicode.py\n'
                '+++ b/test_unicode.py\n'
                '@@ -1,6 +1,6 @@\n'
                ' def greet(name):\n'
                '     # This function greets the user\n'
                '-    message = "Hello\u200B, " + name + "!"\n'
                '+    message = "Hi, " + name + "!"\n'
                '     print(message)\n'
                '     return message\n'
                ' \n')
        
        with open(self.test_file_path, "r", encoding="utf-8") as f:
            original_content = f.read()
        
        self.assertTrue(contains_invisible_chars(original_content))
        
        modified_content = handle_invisible_unicode(original_content, diff)
        
        with open(self.test_file_path, "w", encoding="utf-8") as f:
            f.write(modified_content)
        
        with open(self.test_file_path, "r", encoding="utf-8") as f:
            result_content = f.read()
        
        self.assertNotIn("Hello\u200B,", result_content)
        self.assertIn("Hi,", result_content)
    
    def test_diff_adding_invisible_chars(self):
        """Test applying a diff that adds invisible Unicode characters."""
        diff = ('diff --git a/test_unicode.py b/test_unicode.py\n'
                'index 1234567..abcdef0 100644\n'
                '--- a/test_unicode.py\n'
                '+++ b/test_unicode.py\n'
                '@@ -7,5 +7,5 @@\n'
                ' def farewell(name):\n'
                '     # This function says goodbye\n'
                '-    message = "Goodbye\u200C, " + name + "!"\n'
                '+    message = "Goodbye, " + name + "\u200D!"\n'
                '     print(message)\n'
                '     return message\n')
        
        with open(self.test_file_path, "r", encoding="utf-8") as f:
            original_content = f.read()
        
        modified_content = handle_invisible_unicode(original_content, diff)
        
        with open(self.test_file_path, "w", encoding="utf-8") as f:
            f.write(modified_content)
        
        with open(self.test_file_path, "r", encoding="utf-8") as f:
            result_content = f.read()
        
        self.assertIn("\u200D", result_content)
    
    def test_difflib_with_invisible_chars(self):
        """Test applying a diff with invisible Unicode characters using difflib."""
        diff = ('diff --git a/test_unicode.py b/test_unicode.py\n'
                'index 1234567..abcdef0 100644\n'
                '--- a/test_unicode.py\n'
                '+++ b/test_unicode.py\n'
                '@@ -1,6 +1,6 @@\n'
                ' def greet(name):\n'
                '     # This function greets the user\n'
                '-    message = "Hello\u200B, " + name + "!"\n'
                '+    message = "Hi\u200B, " + name + "!"\n'
                '     print(message)\n'
                '     return message\n'
                ' \n')
        
        modified_content = apply_diff_with_difflib(self.test_file_path, diff)
        
        with open(self.test_file_path, "w", encoding="utf-8") as f:
            f.write(modified_content)
        
        with open(self.test_file_path, "r", encoding="utf-8") as f:
            result_content = f.read()
        
        normalized_result = normalize_unicode(result_content)
        self.assertIn("Hi", normalized_result)
        self.assertNotIn("Hello", normalized_result)


if __name__ == "__main__":
    unittest.main()
