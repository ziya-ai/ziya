"""
Tests for the direct whitespace handler.

This module tests the direct whitespace handler functionality to ensure that
it correctly identifies and applies whitespace-only changes in diffs.
"""

import unittest
import os
import tempfile
import shutil

from app.utils.diff_utils.application.whitespace_handler import (
    is_whitespace_only_diff,
)


class TestDirectWhitespaceHandler(unittest.TestCase):
    """Test case for direct whitespace handler functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.test_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.test_dir, "test.py")
        with open(self.test_file, "w") as f:
            f.write(
                "def calculate_total(items):\n"
                '    """\n'
                "    Calculate the total price of all items.\n"
                "    \n"
                "    Args:\n"
                "        items: List of items with 'price' attribute\n"
                "        \n"
                "    Returns:\n"
                "        Total price\n"
                '    """\n'
                "    total = 0\n"
                "    for item in items:\n"
                "        total += item.price\n"
                "    \n"
                "    \n"
                "    return total\n"
                "\n"
                "def apply_discount(total, discount_percent):\n"
                '    """Apply percentage discount to total"""\n'
                "    if discount_percent < 0 or discount_percent > 100:\n"
                '        raise ValueError("Discount must be between 0 and 100")\n'
                "    \n"
                "    discount = total * (discount_percent / 100)\n"
                "    return total - discount\n"
            )
    
    def tearDown(self):
        """Clean up test environment."""
        shutil.rmtree(self.test_dir)

    # ---------------------------------------------------------------
    # is_whitespace_only_diff — still present in the module
    # ---------------------------------------------------------------
    
    def test_is_whitespace_only_diff(self):
        """Test detection of whitespace-only diffs on parsed hunks.
        
        is_whitespace_only_diff now takes a parsed hunk dict (with
        old_block/new_block keys), not a raw diff string.
        """
        # Whitespace-only hunk: removing blank lines
        ws_hunk = {
            'old_block': ['-    ', '-    '],
            'new_block': [],
        }
        self.assertTrue(is_whitespace_only_diff(ws_hunk))

        # Whitespace-only hunk: spaces → tab (same non-ws content)
        ws_hunk2 = {
            'old_block': ['-    discount = total * (discount_percent / 100)'],
            'new_block': ['+\tdiscount = total * (discount_percent / 100)'],
        }
        self.assertTrue(is_whitespace_only_diff(ws_hunk2))

        # Non-whitespace hunk: actual content change
        non_ws_hunk = {
            'old_block': ['-    discount = total * (discount_percent / 100)'],
            'new_block': ['+\tdiscount = total * (discount_percent / 100) * 2  # Changed multiplier'],
        }
        self.assertFalse(is_whitespace_only_diff(non_ws_hunk))

    # ---------------------------------------------------------------
    # apply_whitespace_only_diff — removed during diff_utils refactor.
    # Whitespace-only diffs now flow through the standard pipeline.
    # These tests verify the pipeline handles them correctly.
    # ---------------------------------------------------------------

    def test_apply_whitespace_only_diff_via_pipeline(self):
        """Whitespace-only diffs apply through the standard pipeline."""
        from app.utils.code_util import use_git_to_apply_code_diff

        whitespace_diff = (
            "diff --git a/test.py b/test.py\n"
            "--- a/test.py\n"
            "+++ b/test.py\n"
            "@@ -10,8 +10,6 @@ def calculate_total(items):\n"
            "     total = 0\n"
            "     for item in items:\n"
            "         total += item.price\n"
            "-    \n"
            "-    \n"
            "     return total\n"
        )

        os.environ['ZIYA_USER_CODEBASE_DIR'] = self.test_dir
        result = use_git_to_apply_code_diff(whitespace_diff, self.test_file)

        # The pipeline should succeed (or report already-applied)
        self.assertIn(result["status"], ("success", "already_applied"))

    def test_non_whitespace_diff_changes_content(self):
        """Non-whitespace diffs modify file content as expected."""
        from app.utils.code_util import use_git_to_apply_code_diff

        diff = (
            "diff --git a/test.py b/test.py\n"
            "--- a/test.py\n"
            "+++ b/test.py\n"
            "@@ -12,7 +12,7 @@ def calculate_total(items):\n"
            "     for item in items:\n"
            "         total += item.price\n"
            "     \n"
            "     \n"
            "-    return total\n"
            "+    return total * 2  # double it\n"
            " \n"
            " def apply_discount(total, discount_percent):\n"
        )

        os.environ['ZIYA_USER_CODEBASE_DIR'] = self.test_dir
        result = use_git_to_apply_code_diff(diff, self.test_file)
        self.assertEqual(result["status"], "success")

        with open(self.test_file) as f:
            content = f.read()
        self.assertIn("return total * 2", content)


if __name__ == "__main__":
    unittest.main()
