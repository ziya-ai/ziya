"""
Tests for context extraction functionality.

Updated: extract_context moved from application.extract_context to
hunk_line_correction.extract_context_from_hunk with a different API.
Config functions remain in core.config.
"""

import unittest
import os
from app.utils.diff_utils.application.hunk_line_correction import extract_context_from_hunk
from app.utils.diff_utils.core.config import (
    get_context_size,
    get_search_radius,
    get_confidence_threshold,
    calculate_adaptive_context_size,
)


class TestContextConfig(unittest.TestCase):
    """Test cases for context configuration."""

    def setUp(self):
        self.original_env = {}
        for var in ['ZIYA_DIFF_CONTEXT_SIZE', 'ZIYA_DIFF_SEARCH_RADIUS', 'ZIYA_DIFF_CONFIDENCE_THRESHOLD']:
            self.original_env[var] = os.environ.get(var)

    def tearDown(self):
        for var, value in self.original_env.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value

    def test_get_context_size_default(self):
        """Default context size should be a positive integer."""
        os.environ.pop('ZIYA_DIFF_CONTEXT_SIZE', None)
        size = get_context_size()
        self.assertIsInstance(size, int)
        self.assertGreater(size, 0)

    def test_get_context_size_from_env(self):
        """Context size should be configurable via env var."""
        os.environ['ZIYA_DIFF_CONTEXT_SIZE'] = '10'
        size = get_context_size()
        self.assertEqual(size, 10)

    def test_get_search_radius_default(self):
        """Default search radius should be a positive integer."""
        os.environ.pop('ZIYA_DIFF_SEARCH_RADIUS', None)
        radius = get_search_radius()
        self.assertIsInstance(radius, int)
        self.assertGreater(radius, 0)

    def test_get_confidence_threshold_default(self):
        """Default confidence threshold should be between 0 and 1."""
        os.environ.pop('ZIYA_DIFF_CONFIDENCE_THRESHOLD', None)
        threshold = get_confidence_threshold()
        self.assertIsInstance(threshold, float)
        self.assertGreaterEqual(threshold, 0.0)
        self.assertLessEqual(threshold, 1.0)

    def test_calculate_adaptive_context_size(self):
        """Adaptive context size should vary with file size."""
        small = calculate_adaptive_context_size(10)
        large = calculate_adaptive_context_size(10000)
        self.assertIsInstance(small, int)
        self.assertIsInstance(large, int)
        self.assertGreater(small, 0)
        self.assertGreater(large, 0)


class TestExtractContextFromHunk(unittest.TestCase):
    """Test extract_context_from_hunk with the current API."""

    def test_extracts_context_lines(self):
        """Should extract context (unchanged) lines from a hunk."""
        hunk = {
            'lines': [
                ' context line 1',
                '-removed line',
                '+added line',
                ' context line 2',
            ]
        }
        result = extract_context_from_hunk(hunk)
        self.assertIsInstance(result, list)

    def test_empty_hunk(self):
        """Should handle empty hunk gracefully."""
        hunk = {'lines': []}
        result = extract_context_from_hunk(hunk)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    def test_hunk_with_only_additions(self):
        """Should handle hunk with only additions (no context)."""
        hunk = {
            'lines': [
                '+new line 1',
                '+new line 2',
            ]
        }
        result = extract_context_from_hunk(hunk)
        self.assertIsInstance(result, list)


if __name__ == '__main__':
    unittest.main()
