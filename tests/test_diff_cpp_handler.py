"""
Tests for C++ language handler.

Moved from app/utils/diff_utils/tests/test_cpp_handler.py to be visible
to pytest (testpaths = tests).
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
        self.temp_dir = tempfile.mkdtemp()
        self.cpp_file = os.path.join(self.temp_dir, "test.cpp")
        self.header_file = os.path.join(self.temp_dir, "test.h")
        
        with open(self.cpp_file, "w") as f:
            f.write('''#include <iostream>
#include <string>
#include <vector>

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

int testFunction(int x) {
    return x * 2;
}

template<typename T>
T add(T a, T b) {
    return a + b;
}

int main() {
    TestClass test;
    test.testMethod();
    return 0;
}
''')
        
        with open(self.header_file, "w") as f:
            f.write('''#pragma once
#include <string>

class HeaderClass {
public:
    HeaderClass();
    ~HeaderClass();
    void headerMethod();
};
''')
    
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
    
    def test_handler_detection(self):
        self.assertTrue(CppHandler.can_handle(self.cpp_file))
        self.assertTrue(CppHandler.can_handle(self.header_file))
        self.assertTrue(CppHandler.can_handle("test.hpp"))
        self.assertTrue(CppHandler.can_handle("test.cc"))
        self.assertFalse(CppHandler.can_handle("test.py"))
    
    def test_registry_integration(self):
        handler = LanguageHandlerRegistry.get_handler(self.cpp_file)
        self.assertEqual(handler, CppHandler)
    
    def test_basic_validation(self):
        with open(self.cpp_file, "r") as f:
            content = f.read()
        is_valid, error = CppHandler._basic_cpp_validation(content)
        self.assertTrue(is_valid)
        
        invalid_content = content + "\n{"
        is_valid, error = CppHandler._basic_cpp_validation(invalid_content)
        self.assertFalse(is_valid)
    
    def test_duplicate_detection(self):
        with open(self.cpp_file, "r") as f:
            original_content = f.read()
        
        duplicated_content = original_content + '''
int testFunction(int x) {
    return x * 3;
}
'''
        has_duplicates, duplicates = CppHandler.detect_duplicates(original_content, duplicated_content)
        self.assertTrue(has_duplicates)
        self.assertIn("testFunction", duplicates)
        
        has_duplicates, duplicates = CppHandler.detect_duplicates(original_content, original_content)
        self.assertFalse(has_duplicates)


if __name__ == "__main__":
    unittest.main()
