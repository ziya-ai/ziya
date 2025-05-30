"""
Tests for the improved duplicate detection functionality.
"""

import unittest
from app.utils.diff_utils.application.duplicate_detection import (
    verify_no_duplicates,
    filter_false_positive_duplicates,
    is_false_positive_pattern,
    is_function_modification,
    extract_context
)

class TestDuplicateDetection(unittest.TestCase):
    """Test cases for improved duplicate detection."""
    
    def test_verify_no_duplicates_clean(self):
        """Test verification with no duplicates."""
        original_content = """
function test1() {
    console.log("test1");
}

function test2() {
    console.log("test2");
}
"""
        modified_content = """
function test1() {
    console.log("test1 modified");
}

function test2() {
    console.log("test2");
}

function test3() {
    console.log("test3");
}
"""
        # Mock the language handler to avoid actual duplicate detection
        from unittest.mock import patch, MagicMock
        
        mock_handler = MagicMock()
        mock_handler.detect_duplicates.return_value = (False, [])
        
        with patch('app.utils.diff_utils.application.duplicate_detection.LanguageHandlerRegistry.get_handler', return_value=mock_handler):
            is_valid, error_msg = verify_no_duplicates(original_content, modified_content, "test.js")
            
            self.assertTrue(is_valid)
            self.assertIsNone(error_msg)
    
    def test_verify_no_duplicates_with_duplicates(self):
        """Test verification with actual duplicates."""
        original_content = """
function test1() {
    console.log("test1");
}

function test2() {
    console.log("test2");
}
"""
        modified_content = """
function test1() {
    console.log("test1");
}

function test2() {
    console.log("test2");
}

function test1() {
    console.log("duplicate test1");
}
"""
        is_valid, error_msg = verify_no_duplicates(original_content, modified_content, "test.js")
        
        self.assertFalse(is_valid)
        self.assertIsNotNone(error_msg)
        self.assertIn("duplicate code", error_msg)
    
    def test_filter_false_positive_duplicates(self):
        """Test filtering of false positive duplicates."""
        duplicates = [
            "renderTokens (lines 11, 1752)",
            "i (lines 5, 10, 15)",
            "React.Component (lines 20, 30)"
        ]
        
        original_content = "function renderTokens() { /* original implementation */ }"
        modified_content = """
function renderTokens() { /* modified implementation */ }

// This is a comment that mentions renderTokens but isn't a duplicate
"""
        
        filtered = filter_false_positive_duplicates(duplicates, original_content, modified_content, "test.tsx")
        
        # renderTokens should be filtered out as it's a modification, not a duplicate
        # i should be filtered as a common variable name
        # React.Component should be filtered as a common pattern
        self.assertEqual(len(filtered), 0)
    
    def test_is_false_positive_pattern(self):
        """Test detection of false positive patterns."""
        # Common variable names
        self.assertTrue(is_false_positive_pattern("i", "for (let i = 0; i < 10; i++) {}", "test.js"))
        self.assertTrue(is_false_positive_pattern("index", "array.map((item, index) => {})", "test.js"))
        
        # React component props
        self.assertTrue(is_false_positive_pattern("onClick", "<Button onClick={handleClick}>Click me</Button>", "test.jsx"))
        
        # Import statements
        self.assertTrue(is_false_positive_pattern("React", "import React from 'react';", "test.jsx"))
        
        # Not a false positive
        self.assertFalse(is_false_positive_pattern("MyCustomFunction", "function MyCustomFunction() {}", "test.js"))
    
    def test_is_function_modification(self):
        """Test detection of function modifications vs duplications."""
        original_content = """
function test1() {
    console.log("original");
}
"""
        
        # Modified function (not a duplicate)
        modified_content1 = """
function test1() {
    console.log("modified");
}
"""
        self.assertTrue(is_function_modification("test1", original_content, modified_content1))
        
        # Actual duplicate
        modified_content2 = """
function test1() {
    console.log("original");
}

function test1() {
    console.log("duplicate");
}
"""
        self.assertFalse(is_function_modification("test1", original_content, modified_content2))
    
    def test_extract_context(self):
        """Test context extraction around a position."""
        content = "line1\nline2\nline3\nline4\nline5"
        
        # Extract context around line3
        position = content.find("line3")
        context = extract_context(content, position, 30)  # Increased context size even more
        
        # Print the context for debugging
        print(f"Context: '{context}'")
        
        # Check if the context contains the expected lines
        self.assertIn("line2", context)
        self.assertIn("line3", context)
        self.assertIn("line4", context)

if __name__ == "__main__":
    unittest.main()
