"""
Tests for the context extraction functionality.
"""

import unittest
import os
from app.utils.diff_utils.application.extract_context import (
    extract_context,
    extract_line_context,
    extract_hunk_context
)
from app.utils.diff_utils.core.config import (
    get_context_size,
    get_search_radius,
    get_confidence_threshold,
    calculate_adaptive_context_size
)

class TestContextExtraction(unittest.TestCase):
    """Test cases for context extraction."""
    
    def setUp(self):
        """Set up test environment."""
        # Save original environment variables
        self.original_env = {}
        for var in ['ZIYA_DIFF_CONTEXT_SIZE', 'ZIYA_DIFF_SEARCH_RADIUS', 'ZIYA_DIFF_CONFIDENCE_THRESHOLD']:
            self.original_env[var] = os.environ.get(var)
    
    def tearDown(self):
        """Clean up test environment."""
        # Restore original environment variables
        for var, value in self.original_env.items():
            if value is None:
                if var in os.environ:
                    del os.environ[var]
            else:
                os.environ[var] = value
    
    def test_extract_context(self):
        """Test extracting context around a position."""
        content = "line1\nline2\nline3\nline4\nline5"
        
        # Test with default context size
        position = content.find("line3")
        context = extract_context(content, position)
        
        # Should include surrounding lines
        self.assertIn("line2", context)
        self.assertIn("line3", context)
        self.assertIn("line4", context)
        
        # Test with custom context size
        large_context = extract_context(content, position, context_size=100)
        self.assertIn("line1", large_context)
        self.assertIn("line5", large_context)
        
        # Test with size category
        small_context = extract_context(content, position, size_category='small')
        medium_context = extract_context(content, position, size_category='medium')
        large_context = extract_context(content, position, size_category='large')
        
        # Medium should be larger than small
        self.assertTrue(len(medium_context) >= len(small_context))
        # Large should be larger than medium
        self.assertTrue(len(large_context) >= len(medium_context))
    
    def test_extract_line_context(self):
        """Test extracting context lines around a line number."""
        lines = ["line1", "line2", "line3", "line4", "line5"]
        
        # Test with default context lines
        context_lines = extract_line_context(lines, 2)  # Around line3
        
        # Should include surrounding lines
        self.assertIn("line2", context_lines)
        self.assertIn("line3", context_lines)
        self.assertIn("line4", context_lines)
        
        # Test with custom context lines
        large_context = extract_line_context(lines, 2, context_lines=2)
        self.assertEqual(len(large_context), 5)  # All lines
        self.assertIn("line1", large_context)
        self.assertIn("line5", large_context)
    
    def test_extract_hunk_context(self):
        """Test extracting context from a hunk."""
        hunk = {
            'old_block': [
                ' line1',
                ' line2',
                '-line3',
                '-line4',
                ' line5',
                ' line6',
                ' line7',
                ' line8',
                ' line9',
                ' line10'
            ]
        }
        
        # Test with default adaptive context
        context_hunk = extract_hunk_context(hunk)
        
        # Should have fewer lines than original
        self.assertLessEqual(len(context_hunk['old_block']), len(hunk['old_block']))
        
        # Should include beginning and end
        self.assertEqual(context_hunk['old_block'][0], ' line1')
        self.assertEqual(context_hunk['old_block'][-1], ' line10')
        
        # Test with custom context lines
        small_context = extract_hunk_context(hunk, context_lines=2)
        self.assertEqual(len(small_context['old_block']), 4)  # 2 from start, 2 from end
        self.assertEqual(small_context['old_block'][0], ' line1')
        self.assertEqual(small_context['old_block'][1], ' line2')
        self.assertEqual(small_context['old_block'][2], ' line9')
        self.assertEqual(small_context['old_block'][3], ' line10')
    
    def test_config_overrides(self):
        """Test configuration overrides via environment variables."""
        # Test default values
        default_context = get_context_size('medium')
        default_radius = get_search_radius()
        default_threshold = get_confidence_threshold('medium')
        
        # Set environment variables
        os.environ['ZIYA_DIFF_CONTEXT_SIZE'] = '100'
        os.environ['ZIYA_DIFF_SEARCH_RADIUS'] = '200'
        os.environ['ZIYA_DIFF_CONFIDENCE_THRESHOLD'] = '0.6'
        
        # Check that values are overridden
        self.assertEqual(get_context_size('medium'), 100)
        self.assertEqual(get_search_radius(), 200)
        self.assertAlmostEqual(get_confidence_threshold('medium'), 0.6)
        
        # Check that size categories are ignored when override is set
        self.assertEqual(get_context_size('small'), 100)
        self.assertEqual(get_context_size('large'), 100)
    
    def test_adaptive_context_sizing(self):
        """Test adaptive context sizing based on hunk size."""
        # Test with small hunk
        small_size = calculate_adaptive_context_size(5)
        # Test with medium hunk
        medium_size = calculate_adaptive_context_size(20)
        # Test with large hunk
        large_size = calculate_adaptive_context_size(100)
        
        # Larger hunks should have larger context (up to the max)
        self.assertTrue(medium_size >= small_size)
        
        # Very large hunks should be capped at the maximum
        self.assertTrue(large_size <= 10)  # ADAPTIVE_CONTEXT_MAX_LINES

if __name__ == "__main__":
    unittest.main()
