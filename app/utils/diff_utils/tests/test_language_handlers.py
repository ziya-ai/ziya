"""
Tests for language handlers.
"""

import os
import unittest
import tempfile
import shutil

from ..language_handlers import (
    LanguageHandler,
    LanguageHandlerRegistry,
    GenericTextHandler
)
from ..language_handlers.python import PythonHandler


class TestLanguageHandlers(unittest.TestCase):
    """Test cases for language handlers."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        
        # Create test files
        self.python_file = os.path.join(self.temp_dir, "test.py")
        self.js_file = os.path.join(self.temp_dir, "test.js")
        self.text_file = os.path.join(self.temp_dir, "test.txt")
        
        # Create a Python file with a function
        with open(self.python_file, "w") as f:
            f.write('''def test_function():
    """Test function."""
    return True

class TestClass:
    def method(self):
        return True
''')
        
        # Create a JavaScript file
        with open(self.js_file, "w") as f:
            f.write('''function testFunction() {
    return true;
}

class TestClass {
    method() {
        return true;
    }
}
''')
        
        # Create a text file
        with open(self.text_file, "w") as f:
            f.write('''This is a test file.
It has multiple lines.
Some lines might be repeated.
Some lines might be repeated.
''')
    
    def tearDown(self):
        """Clean up test environment."""
        shutil.rmtree(self.temp_dir)
    
    def test_handler_registry(self):
        """Test that the handler registry returns the correct handler."""
        # Python handler should handle Python files
        handler = LanguageHandlerRegistry.get_handler(self.python_file)
        self.assertEqual(handler, PythonHandler)
        
        # Generic handler should handle text files
        handler = LanguageHandlerRegistry.get_handler(self.text_file)
        self.assertEqual(handler, GenericTextHandler)
    
    def test_python_handler(self):
        """Test Python handler functionality."""
        # Python handler should detect Python files
        self.assertTrue(PythonHandler.can_handle(self.python_file))
        self.assertFalse(PythonHandler.can_handle(self.text_file))
        
        # Python handler should verify valid Python code
        with open(self.python_file, "r") as f:
            content = f.read()
        
        is_valid, error = PythonHandler.verify_changes(content, content, self.python_file)
        self.assertTrue(is_valid)
        self.assertIsNone(error)
        
        # Python handler should detect invalid Python code
        invalid_content = content + "\nthis is not valid python:"
        is_valid, error = PythonHandler.verify_changes(content, invalid_content, self.python_file)
        self.assertFalse(is_valid)
        self.assertIsNotNone(error)
    
    def test_duplicate_detection(self):
        """Test duplicate detection in Python code."""
        # Create content with a duplicated function
        with open(self.python_file, "r") as f:
            original_content = f.read()
        
        duplicated_content = original_content + '''
def test_function():
    """Duplicate function."""
    return False
'''
        
        # Python handler should detect the duplicate
        has_duplicates, duplicates = PythonHandler.detect_duplicates(original_content, duplicated_content)
        self.assertTrue(has_duplicates)
        self.assertIn("test_function", duplicates)
        
        # No duplicates in original content
        has_duplicates, duplicates = PythonHandler.detect_duplicates(original_content, original_content)
        self.assertFalse(has_duplicates)
        self.assertEqual(len(duplicates), 0)
    
    def test_generic_handler(self):
        """Test generic text handler functionality."""
        # Generic handler should handle any file
        self.assertTrue(GenericTextHandler.can_handle(self.text_file))
        self.assertTrue(GenericTextHandler.can_handle(self.python_file))
        
        # Generic handler should verify text content
        with open(self.text_file, "r") as f:
            content = f.read()
        
        is_valid, error = GenericTextHandler.verify_changes(content, content, self.text_file)
        self.assertTrue(is_valid)
        self.assertIsNone(error)
        
        # Generic handler should detect repeated lines
        with open(self.text_file, "r") as f:
            original_content = f.read()
        
        # Add another repeated line
        duplicated_content = original_content + "Some lines might be repeated.\n"
        
        has_duplicates, duplicates = GenericTextHandler.detect_duplicates(original_content, duplicated_content)
        self.assertTrue(has_duplicates)
        self.assertTrue(any("Some lines might be repeated" in dup for dup in duplicates))


if __name__ == "__main__":
    unittest.main()
