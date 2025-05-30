"""
Tests for Unicode handling utilities.
"""

import unittest
from app.utils.diff_utils.core.unicode_handling import (
    contains_invisible_chars,
    normalize_unicode,
    extract_invisible_chars,
    preserve_invisible_chars,
    map_invisible_chars,
    INVISIBLE_UNICODE_CHARS
)

class TestUnicodeHandling(unittest.TestCase):
    """Test cases for Unicode handling utilities."""
    
    def test_contains_invisible_chars(self):
        """Test detection of invisible Unicode characters."""
        # Test with no invisible characters
        self.assertFalse(contains_invisible_chars("Hello, world!"))
        
        # Test with invisible characters
        self.assertTrue(contains_invisible_chars("Hello\u200B, world!"))
        self.assertTrue(contains_invisible_chars("Hello\uFEFF world!"))
        
        # Test with multiple invisible characters
        self.assertTrue(contains_invisible_chars("H\u200Be\u200Cl\u200Dl\u200Eo\u200F!"))
    
    def test_normalize_unicode(self):
        """Test normalization of Unicode text."""
        # Test with no invisible characters
        self.assertEqual(normalize_unicode("Hello, world!"), "Hello, world!")
        
        # Test with invisible characters
        self.assertEqual(normalize_unicode("Hello\u200B, world!"), "Hello, world!")
        self.assertEqual(normalize_unicode("Hello\uFEFF world!"), "Hello world!")
        
        # Test with multiple invisible characters
        self.assertEqual(normalize_unicode("H\u200Be\u200Cl\u200Dl\u200Eo\u200F!"), "Hello!")
    
    def test_extract_invisible_chars(self):
        """Test extraction of invisible Unicode characters."""
        # Test with no invisible characters
        self.assertEqual(extract_invisible_chars("Hello, world!"), "")
        
        # Test with invisible characters
        self.assertEqual(extract_invisible_chars("Hello\u200B, world!"), "\u200B")
        self.assertEqual(extract_invisible_chars("Hello\uFEFF world!"), "\uFEFF")
        
        # Test with multiple invisible characters
        self.assertEqual(extract_invisible_chars("H\u200Be\u200Cl\u200Dl\u200Eo\u200F!"), 
                         "\u200B\u200C\u200D\u200E\u200F")
    
    def test_preserve_invisible_chars(self):
        """Test preservation of invisible Unicode characters."""
        # Test with no invisible characters
        self.assertEqual(preserve_invisible_chars("Hello, world!", "Hi, world!"), "Hi, world!")
        
        # Test with invisible characters, same visible content
        self.assertEqual(preserve_invisible_chars("Hello\u200B, world!", "Hello, world!"), 
                         "Hello\u200B, world!")
        
        # Test with invisible characters, different visible content
        result = preserve_invisible_chars("Hello\u200B, world!", "Hi, there!")
        self.assertTrue("\u200B" in result)
        self.assertTrue(result.startswith("Hi"))
    
    def test_map_invisible_chars(self):
        """Test mapping of invisible Unicode characters."""
        # Test with no invisible characters
        self.assertEqual(map_invisible_chars("Hello, world!", "Hi, world!"), "Hi, world!")
        
        # Test with invisible characters, same visible content
        self.assertEqual(map_invisible_chars("Hello\u200B, world!", "Hello, world!"), 
                         "Hello\u200B, world!")
        
        # Test with invisible characters in the middle
        original = "Hello\u200B, world!"
        modified = "Hello, everyone!"
        result = map_invisible_chars(original, modified)
        self.assertTrue("\u200B" in result)
        self.assertEqual(normalize_unicode(result), "Hello, everyone!")
        
        # Test with multiple invisible characters
        original = "H\u200Be\u200Cl\u200Dl\u200Eo\u200F, world!"
        modified = "Hello, everyone!"
        result = map_invisible_chars(original, modified)
        for char in ["\u200B", "\u200C", "\u200D", "\u200E", "\u200F"]:
            self.assertTrue(char in result)
        self.assertEqual(normalize_unicode(result), "Hello, everyone!")

class TestInvisibleUnicodeEdgeCases(unittest.TestCase):
    """Test edge cases for invisible Unicode character handling."""
    
    def test_empty_strings(self):
        """Test handling of empty strings."""
        self.assertFalse(contains_invisible_chars(""))
        self.assertEqual(normalize_unicode(""), "")
        self.assertEqual(extract_invisible_chars(""), "")
        self.assertEqual(preserve_invisible_chars("", ""), "")
        self.assertEqual(map_invisible_chars("", ""), "")
    
    def test_only_invisible_chars(self):
        """Test handling of strings with only invisible characters."""
        invisible_string = "\u200B\u200C\u200D\u200E\u200F"
        
        self.assertTrue(contains_invisible_chars(invisible_string))
        self.assertEqual(normalize_unicode(invisible_string), "")
        self.assertEqual(extract_invisible_chars(invisible_string), invisible_string)
        
        # When preserving or mapping, if the original is only invisible chars,
        # the result should be the modified string
        self.assertEqual(preserve_invisible_chars(invisible_string, "Hello"), "Hello")
        self.assertEqual(map_invisible_chars(invisible_string, "Hello"), "Hello")
    
    def test_all_invisible_chars_covered(self):
        """Test that all defined invisible characters are handled correctly."""
        # Create a string with all invisible characters
        all_invisible = "".join(INVISIBLE_UNICODE_CHARS)
        
        self.assertTrue(contains_invisible_chars(all_invisible))
        self.assertEqual(normalize_unicode(all_invisible), "")
        self.assertEqual(extract_invisible_chars(all_invisible), all_invisible)
        
        # Test with a mixed string
        mixed = "A" + all_invisible + "B"
        self.assertEqual(normalize_unicode(mixed), "AB")
        self.assertEqual(extract_invisible_chars(mixed), all_invisible)

if __name__ == "__main__":
    unittest.main()
