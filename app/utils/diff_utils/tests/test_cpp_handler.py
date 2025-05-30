"""
Tests for C++ language handler.
"""

import os
import unittest
import tempfile
import shutil

from app.utils.diff_utils.language_handlers import (
    LanguageHandlerRegistry,
    CppHandler
)


class TestCppHandler(unittest.TestCase):
    """Test cases for C++ language handler."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        
        # Create test files
        self.cpp_file = os.path.join(self.temp_dir, "test.cpp")
        self.header_file = os.path.join(self.temp_dir, "test.h")
        
        # Create a C++ file with functions and classes
        with open(self.cpp_file, "w") as f:
            f.write('''#include <iostream>
#include <string>
#include <vector>

// A simple class
class TestClass {
public:
    TestClass() = default;
    ~TestClass() = default;
    
    void testMethod() {
        std::cout << "Test method" << std::endl;
    }
    
    int calculate(int a, int b) const {
        return a + b;
    }
};

// A function
int testFunction(int x) {
    return x * 2;
}

// Template function
template<typename T>
T add(T a, T b) {
    return a + b;
}

int main() {
    TestClass test;
    test.testMethod();
    std::cout << test.calculate(5, 3) << std::endl;
    std::cout << testFunction(10) << std::endl;
    std::cout << add(5, 3) << std::endl;
    return 0;
}
''')
        
        # Create a header file
        with open(self.header_file, "w") as f:
            f.write('''#pragma once

#include <string>

// A simple class declaration
class HeaderClass {
public:
    HeaderClass();
    ~HeaderClass();
    
    void headerMethod();
    int headerCalculate(int a, int b) const;
};

// A function declaration
int headerFunction(int x);

// Template function
template<typename T>
T headerAdd(T a, T b);
''')
    
    def tearDown(self):
        """Clean up test environment."""
        shutil.rmtree(self.temp_dir)
    
    def test_handler_detection(self):
        """Test that the C++ handler detects C++ files."""
        # C++ handler should handle C++ files
        self.assertTrue(CppHandler.can_handle(self.cpp_file))
        self.assertTrue(CppHandler.can_handle(self.header_file))
        self.assertTrue(CppHandler.can_handle("test.hpp"))
        self.assertTrue(CppHandler.can_handle("test.cc"))
        self.assertTrue(CppHandler.can_handle("test.cxx"))
        
        # C++ handler should not handle other files
        self.assertFalse(CppHandler.can_handle("test.py"))
        self.assertFalse(CppHandler.can_handle("test.js"))
        self.assertFalse(CppHandler.can_handle("test.txt"))
    
    def test_registry_integration(self):
        """Test that the registry returns the C++ handler for C++ files."""
        handler = LanguageHandlerRegistry.get_handler(self.cpp_file)
        self.assertEqual(handler, CppHandler)
        
        handler = LanguageHandlerRegistry.get_handler(self.header_file)
        self.assertEqual(handler, CppHandler)
    
    def test_basic_validation(self):
        """Test basic validation of C++ code."""
        # Valid C++ code should pass validation
        with open(self.cpp_file, "r") as f:
            content = f.read()
        
        is_valid, error = CppHandler._basic_cpp_validation(content)
        self.assertTrue(is_valid)
        self.assertIsNone(error)
        
        # Invalid C++ code should fail validation
        invalid_content = content + "\n{"  # Unclosed brace
        is_valid, error = CppHandler._basic_cpp_validation(invalid_content)
        self.assertFalse(is_valid)
        self.assertIsNotNone(error)
    
    def test_duplicate_detection(self):
        """Test duplicate detection in C++ code."""
        # Create content with a duplicated function
        with open(self.cpp_file, "r") as f:
            original_content = f.read()
        
        duplicated_content = original_content + '''
// Duplicate function
int testFunction(int x) {
    return x * 3;  // Different implementation
}
'''
        
        # C++ handler should detect the duplicate
        has_duplicates, duplicates = CppHandler.detect_duplicates(original_content, duplicated_content)
        self.assertTrue(has_duplicates)
        self.assertIn("testFunction", duplicates)
        
        # No duplicates in original content
        has_duplicates, duplicates = CppHandler.detect_duplicates(original_content, original_content)
        self.assertFalse(has_duplicates)
        self.assertEqual(len(duplicates), 0)
    
    def test_function_extraction(self):
        """Test extraction of function definitions from C++ code."""
        # Create a simpler test file with the add function more clearly defined
        test_cpp = os.path.join(self.temp_dir, "test_add.cpp")
        with open(test_cpp, "w") as f:
            f.write('''#include <iostream>

// Template function
template<typename T>
T add(T a, T b) {
    return a + b;
}

int main() {
    std::cout << add(5, 3) << std::endl;
    return 0;
}
''')
        
        with open(test_cpp, "r") as f:
            content = f.read()
        
        functions = CppHandler._extract_function_definitions(content)
        
        # Check that key functions were extracted
        self.assertIn("add", functions)
        self.assertIn("main", functions)


if __name__ == "__main__":
    unittest.main()
