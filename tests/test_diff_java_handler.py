"""
Tests for Java language handler.

Moved from app/utils/diff_utils/tests/test_java_handler.py to be visible
to pytest (testpaths = tests).
"""

import os
import unittest
import tempfile
import shutil

from app.utils.diff_utils.language_handlers import (
    LanguageHandlerRegistry,
    JavaHandler
)


class TestJavaHandler(unittest.TestCase):
    """Test cases for Java language handler."""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.java_file = os.path.join(self.temp_dir, "Test.java")
        
        with open(self.java_file, "w") as f:
            f.write('''package com.example;

public class Test {
    private String name;
    
    public Test(String name) {
        this.name = name;
    }
    
    public String getName() {
        return name;
    }
    
    public void setName(String name) {
        this.name = name;
    }
    
    public void printInfo() {
        System.out.println("Name: " + name);
    }
}
''')
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def test_handler_detection(self):
        handler = LanguageHandlerRegistry.get_handler(self.java_file)
        self.assertEqual(handler, JavaHandler)
    
    def test_duplicate_detection(self):
        with open(self.java_file, "r") as f:
            original_content = f.read()
        
        duplicated_content = original_content + '''
    public String getName() {
        return this.name;
    }
'''
        has_duplicates, duplicates = JavaHandler.detect_duplicates(original_content, duplicated_content)
        self.assertTrue(has_duplicates)
        self.assertIn("getName", duplicates)
        
        has_duplicates, duplicates = JavaHandler.detect_duplicates(original_content, original_content)
        self.assertFalse(has_duplicates)
    
    def test_syntax_verification(self):
        with open(self.java_file, "r") as f:
            original_content = f.read()
        
        # Invalid modification (unbalanced braces)
        invalid_modification = original_content.replace(
            "public void printInfo() {", "public void printInfo() {"
        ).replace("    }", "", 1)  # Remove one closing brace
        
        is_valid, error = JavaHandler.verify_changes(original_content, invalid_modification, self.java_file)
        self.assertFalse(is_valid)
        self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
