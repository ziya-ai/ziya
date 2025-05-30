"""
Tests for Java language handler.
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
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        
        # Create test files
        self.java_file = os.path.join(self.temp_dir, "Test.java")
        
        # Create a Java file with a class and methods
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
        """Clean up test environment."""
        shutil.rmtree(self.temp_dir)
    
    def test_handler_detection(self):
        """Test that the Java handler is detected for Java files."""
        handler = LanguageHandlerRegistry.get_handler(self.java_file)
        self.assertEqual(handler, JavaHandler)
    
    def test_duplicate_detection(self):
        """Test duplicate detection in Java code."""
        # Create content with a duplicated method
        with open(self.java_file, "r") as f:
            original_content = f.read()
        
        duplicated_content = original_content + '''
    public String getName() {
        // This is a duplicate method
        return this.name;
    }
'''
        
        # Java handler should detect the duplicate
        has_duplicates, duplicates = JavaHandler.detect_duplicates(original_content, duplicated_content)
        self.assertTrue(has_duplicates)
        self.assertIn("getName", duplicates)
        
        # No duplicates in original content
        has_duplicates, duplicates = JavaHandler.detect_duplicates(original_content, original_content)
        self.assertFalse(has_duplicates)
        self.assertEqual(len(duplicates), 0)
    
    def test_syntax_verification(self):
        """Test syntax verification for Java code."""
        with open(self.java_file, "r") as f:
            original_content = f.read()
        
        # Valid modification
        valid_modification = original_content.replace(
            "public void printInfo() {",
            "public void printInfo() {\n        // Added comment"
        )
        
        # Since javac might not be available, we'll just check that the basic validation works
        is_valid, error = JavaHandler.verify_changes(original_content, valid_modification, self.java_file)
        # We don't assert is_valid here since it depends on javac availability
        
        # Invalid modification (unbalanced braces)
        invalid_modification = original_content.replace(
            "public void printInfo() {",
            "public void printInfo() {"
        ).replace(
            "    }",
            ""
        )
        
        # This should fail with basic validation even if javac is not available
        is_valid, error = JavaHandler.verify_changes(original_content, invalid_modification, self.java_file)
        self.assertFalse(is_valid)
        self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
