"""
Tests for the enhanced fuzzy matching functionality.

This module tests the enhanced fuzzy matching functionality to ensure that
it correctly finds the best position to apply a hunk in a file.
"""

import unittest
from app.utils.diff_utils.application.enhanced_fuzzy_match import (
    find_best_chunk_position_enhanced,
    find_best_chunk_position_with_fallbacks,
    is_whitespace_only_change,
    compare_indentation_patterns
)

class TestEnhancedFuzzyMatch(unittest.TestCase):
    """Test case for enhanced fuzzy matching functionality."""
    
    def test_basic_matching(self):
        """Test basic matching functionality."""
        file_lines = [
            "def test_function():",
            "    x = 1",
            "    y = 2",
            "    return x + y",
            ""
        ]
        
        chunk_lines = [
            "    x = 1",
            "    y = 2"
        ]
        
        # Expected position is 1 (0-based index)
        pos, ratio = find_best_chunk_position_enhanced(file_lines, chunk_lines, 1)
        self.assertEqual(pos, 1)
        self.assertGreaterEqual(ratio, 0.8)
    
    def test_whitespace_only_change(self):
        """Test detection of whitespace-only changes."""
        file_lines = [
            "def test():",
            "    x = 1",
            "    y = 2",
            "    return x + y"
        ]
        
        chunk_lines = [
            "def test():",
            "    x = 1",
            "    y = 2",
            "    return x + y"
        ]
        
        self.assertTrue(is_whitespace_only_change(file_lines, chunk_lines))
        
        # Test with different whitespace
        chunk_lines_with_spaces = [
            "def test():",
            "    x = 1",
            "  y = 2",  # Different indentation
            "    return x + y"
        ]
        
        self.assertTrue(is_whitespace_only_change(file_lines, chunk_lines_with_spaces))
        
        # Test with different content
        chunk_lines_with_diff = [
            "def test():",
            "    x = 1",
            "    z = 3",  # Different variable
            "    return x + y"
        ]
        
        self.assertFalse(is_whitespace_only_change(file_lines, chunk_lines_with_diff))
    
    def test_indentation_pattern_comparison(self):
        """Test comparison of indentation patterns."""
        file_lines = [
            "def test():",
            "    if condition:",
            "        x = 1",
            "    else:",
            "        x = 2"
        ]
        
        # Same indentation pattern
        chunk_lines = [
            "def other():",
            "    if value:",
            "        y = 3",
            "    else:",
            "        y = 4"
        ]
        
        ratio = compare_indentation_patterns(file_lines, chunk_lines)
        self.assertGreaterEqual(ratio, 0.8)
        
        # Different indentation pattern
        chunk_lines_diff = [
            "def other():",
            "    y = 3",
            "    if value:",
            "        y = 4",
            "    else:"
        ]
        
        ratio_diff = compare_indentation_patterns(file_lines, chunk_lines_diff)
        self.assertLess(ratio_diff, 0.5)
    
    def test_fallback_strategies(self):
        """Test fallback strategies for difficult matches."""
        file_lines = [
            "def test_function():",
            "    # This is a comment",
            "    x = 1",
            "    y = 2",
            "    # Another comment",
            "    return x + y",
            ""
        ]
        
        # Chunk with different comments but same code
        chunk_lines = [
            "    # Different comment",
            "    x = 1",
            "    y = 2",
            "    # Yet another comment"
        ]
        
        # Try with regular matching first
        pos, ratio = find_best_chunk_position_enhanced(file_lines, chunk_lines, 1)
        
        # Now try with fallbacks
        fallback_pos, fallback_ratio, details = find_best_chunk_position_with_fallbacks(
            file_lines, chunk_lines, 1
        )
        
        # The fallback should find a better match
        self.assertGreaterEqual(fallback_ratio, ratio)
        self.assertIsNotNone(fallback_pos)
        
        # Check that details contains the expected information
        self.assertIn("primary_match", details)
        self.assertIn("fallbacks_tried", details)
    
    def test_adaptive_threshold(self):
        """Test adaptive confidence thresholds."""
        file_lines = [
            "def test_function():",
            "    x = 1",
            "    y = 2",
            "    return x + y",
            ""
        ]
        
        # Very short chunk (should use lower threshold)
        short_chunk = [
            "    x = 1"
        ]
        
        short_pos, short_ratio = find_best_chunk_position_enhanced(file_lines, short_chunk, 1)
        self.assertEqual(short_pos, 1)
        
        # Whitespace-only change (should use lower threshold)
        whitespace_chunk = [
            "    x =    1"  # Extra spaces
        ]
        
        ws_pos, ws_ratio = find_best_chunk_position_enhanced(file_lines, whitespace_chunk, 1)
        self.assertEqual(ws_pos, 1)

if __name__ == "__main__":
    unittest.main()
