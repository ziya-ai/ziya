"""
Tests for the improved line calculation functionality.
"""

import unittest
from app.utils.diff_utils.application.line_calculation import (
    calculate_line_positions,
    find_best_position,
    verify_position_with_content,
    EXACT_MATCH_THRESHOLD,
    HIGH_CONFIDENCE_THRESHOLD,
    MEDIUM_CONFIDENCE_THRESHOLD,
    LOW_CONFIDENCE_THRESHOLD
)

class TestImprovedLineCalculation(unittest.TestCase):
    """Test cases for improved line calculation."""
    
    def test_calculate_line_positions_normal(self):
        """Test normal line position calculation."""
        file_lines = ["line1", "line2", "line3", "line4", "line5"]
        hunk = {"old_start": 2, "old_count": 2}
        line_offset = 0
        
        start, end = calculate_line_positions(file_lines, hunk, line_offset)
        
        self.assertEqual(start, 1)  # 0-based index for line2
        self.assertEqual(end, 3)    # end position after line3
    
    def test_calculate_line_positions_with_offset(self):
        """Test line position calculation with offset."""
        file_lines = ["line1", "line2", "line3", "line4", "line5"]
        hunk = {"old_start": 2, "old_count": 2}
        line_offset = 1  # Previous hunks added one line
        
        start, end = calculate_line_positions(file_lines, hunk, line_offset)
        
        self.assertEqual(start, 2)  # 0-based index for line3 (line2 + offset)
        self.assertEqual(end, 4)    # end position after line4
    
    def test_calculate_line_positions_bounds_checking(self):
        """Test bounds checking in line position calculation."""
        file_lines = ["line1", "line2", "line3"]
        hunk = {"old_start": 3, "old_count": 5}  # More lines than available
        line_offset = 0
        
        start, end = calculate_line_positions(file_lines, hunk, line_offset)
        
        self.assertEqual(start, 2)  # 0-based index for line3
        self.assertEqual(end, 3)    # end position at end of file
    
    def test_find_best_position_exact_match(self):
        """Test finding the best position with an exact match."""
        file_lines = ["line1", "line2", "line3", "line4", "line5"]
        hunk = {
            "old_block": [" line2", " line3"]
        }
        expected_pos = 1  # 0-based index for line2
        
        result = find_best_position(file_lines, hunk, expected_pos)
        
        self.assertIsNotNone(result)
        position, confidence = result
        self.assertEqual(position, 1)
        self.assertEqual(confidence, EXACT_MATCH_THRESHOLD)
    
    def test_find_best_position_fuzzy_match(self):
        """Test finding the best position with a fuzzy match."""
        file_lines = ["line1", "line2 with extra text", "line3 modified", "line4", "line5"]
        hunk = {
            "old_block": [" line2", " line3"]
        }
        expected_pos = 1  # 0-based index for line2
        
        result = find_best_position(file_lines, hunk, expected_pos)
        
        self.assertIsNotNone(result)
        position, confidence = result
        # The position might not be exactly 1 due to fuzzy matching, but confidence should be good
        self.assertGreater(confidence, MEDIUM_CONFIDENCE_THRESHOLD)
    
    def test_find_best_position_no_match(self):
        """Test finding the best position with no good match."""
        file_lines = ["completely", "different", "content", "here"]
        hunk = {
            "old_block": [" line2", " line3"]
        }
        expected_pos = 1
        
        result = find_best_position(file_lines, hunk, expected_pos)
        
        self.assertIsNotNone(result)
        position, confidence = result
        self.assertEqual(position, expected_pos)  # Falls back to expected position
        self.assertLess(confidence, MEDIUM_CONFIDENCE_THRESHOLD)
    
    def test_verify_position_with_content_exact_match(self):
        """Test verifying position with exact content match."""
        file_lines = ["line1", "line2", "line3", "line4"]
        hunk = {
            "old_block": [" line2", "-line3"]
        }
        position = 1  # 0-based index for line2
        
        is_valid, confidence = verify_position_with_content(file_lines, hunk, position)
        
        self.assertTrue(is_valid)
        self.assertEqual(confidence, 1.0)
    
    def test_verify_position_with_content_partial_match(self):
        """Test verifying position with partial content match."""
        file_lines = ["line1", "line2 modified", "line3", "line4"]
        hunk = {
            "old_block": [" line2", "-line3"]
        }
        position = 1  # 0-based index for line2
        
        is_valid, confidence = verify_position_with_content(file_lines, hunk, position)
        
        self.assertFalse(is_valid)
        self.assertLess(confidence, MEDIUM_CONFIDENCE_THRESHOLD)
    
    def test_verify_position_with_content_out_of_bounds(self):
        """Test verifying position that is out of bounds."""
        file_lines = ["line1", "line2"]
        hunk = {
            "old_block": [" line3", "-line4"]
        }
        position = 5  # Out of bounds
        
        is_valid, confidence = verify_position_with_content(file_lines, hunk, position)
        
        self.assertFalse(is_valid)
        self.assertEqual(confidence, 0.0)

if __name__ == "__main__":
    unittest.main()
