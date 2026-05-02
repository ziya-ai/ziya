"""
Tests for the low-confidence match diagnostic emitted by patch_apply.

Previously, a low-confidence hunk failure surfaced only as
``{type, hunk, confidence}`` — callers (including LLM-driven retry loops)
couldn't tell *why* the match failed and often re-emitted the same diff
with only the ``@@`` header numbers changed, causing infinite loops.

These tests exercise ``_build_low_confidence_diagnostic`` to verify it
classifies the common mismatch causes correctly and produces per-line
expected-vs-actual data.
"""

import unittest

from app.utils.diff_utils.application.patch_apply import (
    _build_low_confidence_diagnostic,
)


def _hunk(old_block):
    """Build a minimal hunk dict with the fields the diagnostic reads."""
    return {"old_block": old_block}


class TestLowConfidenceDiagnostic(unittest.TestCase):
    """Verify the classifier distinguishes mismatch causes."""

    def test_indentation_mismatch_is_classified(self):
        # Expected has 4-space indent; file has 8-space indent.
        expected = [
            "    logger.error('boom')\n",
            "    return results\n",
        ]
        file_lines = [
            "        logger.error('boom')\n",
            "        return results\n",
        ]
        diag = _build_low_confidence_diagnostic(
            hunk_idx=1,
            h=_hunk(expected),
            final_lines_with_endings=file_lines,
            fuzzy_initial_pos_search=0,
            fuzzy_best_ratio=0.5,
        )
        self.assertEqual(diag["likely_cause"], "indentation_mismatch")
        self.assertIn("indentation", diag["hint"].lower())
        self.assertEqual(diag["expected_lines"], 2)
        self.assertEqual(diag["matching_lines"], 0)
        self.assertTrue(
            all(e["status"] == "indent_differs" for e in diag["per_line"])
        )

    def test_content_mismatch_is_classified(self):
        # Entirely different content at the target region.
        expected = [
            "def compute_total(x):\n",
            "    return x * 2\n",
        ]
        file_lines = [
            "def unrelated_helper():\n",
            "    pass\n",
        ]
        diag = _build_low_confidence_diagnostic(
            hunk_idx=1,
            h=_hunk(expected),
            final_lines_with_endings=file_lines,
            fuzzy_initial_pos_search=0,
            fuzzy_best_ratio=0.2,
        )
        self.assertEqual(diag["likely_cause"], "context_does_not_exist_in_file")
        # Hint must explicitly warn against the @@-header-only retry antipattern
        # that this whole diagnostic exists to prevent.
        self.assertIn("@@", diag["hint"])

    def test_blank_line_mismatch_is_classified(self):
        # Expected has a blank line where the file has content.
        expected = [
            "    logger.error('boom')\n",
            "\n",
            "    return results\n",
        ]
        file_lines = [
            "    logger.error('boom')\n",
            "    extra_call()\n",
            "    return results\n",
        ]
        diag = _build_low_confidence_diagnostic(
            hunk_idx=1,
            h=_hunk(expected),
            final_lines_with_endings=file_lines,
            fuzzy_initial_pos_search=0,
            fuzzy_best_ratio=0.55,
        )
        self.assertEqual(
            diag["likely_cause"], "whitespace_or_blank_line_mismatch"
        )

    def test_per_line_contains_expected_and_actual(self):
        expected = ["    a = 1\n", "    b = 2\n"]
        file_lines = ["    a = 1\n", "    b = 99\n"]
        diag = _build_low_confidence_diagnostic(
            hunk_idx=1,
            h=_hunk(expected),
            final_lines_with_endings=file_lines,
            fuzzy_initial_pos_search=0,
            fuzzy_best_ratio=0.8,
        )
        self.assertEqual(len(diag["per_line"]), 2)
        self.assertEqual(diag["per_line"][0]["status"], "match")
        self.assertEqual(diag["per_line"][1]["status"], "content_differs")
        self.assertEqual(diag["per_line"][1]["expected"], "    b = 2")
        self.assertEqual(diag["per_line"][1]["actual"], "    b = 99")
        # file_line is 1-based
        self.assertEqual(diag["per_line"][1]["file_line"], 2)

    def test_empty_old_block_is_safe(self):
        # Defensive: a hunk with no context lines shouldn't crash the helper.
        diag = _build_low_confidence_diagnostic(
            hunk_idx=1,
            h=_hunk([]),
            final_lines_with_endings=["anything\n"],
            fuzzy_initial_pos_search=0,
            fuzzy_best_ratio=0.0,
        )
        self.assertEqual(diag["expected_lines"], 0)
        self.assertEqual(diag["matching_lines"], 0)
        self.assertEqual(diag["per_line"], [])
        # With no per-line data the classifier falls through to the
        # ambiguous/duplicate-anchor bucket — that's the safe default.
        self.assertEqual(
            diag["likely_cause"], "ambiguous_or_duplicate_anchor"
        )


if __name__ == "__main__":
    unittest.main()
