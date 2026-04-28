"""
Tests for JavaScriptHandler._check_common_issues.

These checks must only flag style issues the diff *introduces*, not
pre-existing characteristics of the file being modified.
"""

import unittest

from app.utils.diff_utils.language_handlers.javascript import JavaScriptHandler


class TestJavaScriptStyleChecks(unittest.TestCase):
    """_check_common_issues should compare original vs modified content."""

    def setUp(self):
        self.handler = JavaScriptHandler()

    # ------------------------------------------------------------------
    # Quote style
    # ------------------------------------------------------------------

    def test_preexisting_mixed_quotes_not_flagged(self):
        """A file that already has mixed quotes must not fail validation
        when the diff doesn't make the mix any worse."""
        # 95% single-quoted, 5% double-quoted in both original and modified.
        singles = "\n".join([f"const k{i} = 'v{i}';" for i in range(19)])
        doubles = 'const kd = "only-double";'
        original = singles + "\n" + doubles
        # Add an unrelated single-quoted line — preserves the ratio.
        modified = original + "\nconst extra = 'x';"

        issues = self.handler._check_common_issues(original, modified)
        self.assertNotIn(
            "Inconsistent quote style (mixing ' and \")",
            issues,
            "Should not flag quote inconsistency already present in the original file",
        )

    def test_diff_that_introduces_mixed_quotes_is_flagged(self):
        """When the original is single-style and the patch drops a minority-style
        line far enough to breach the 20% threshold, flag it."""
        original = "\n".join([f"const k{i} = 'v{i}';" for i in range(20)])
        modified = original + "\n" + 'const d = "x";'
        # Original minority_ratio = 1.0 (no doubles, treated as consistent).
        # Modified: 1 double / 21 total ≈ 4.8% — under 20% and much worse
        # than original, so the validator should flag it.
        issues = self.handler._check_common_issues(original, modified)
        self.assertIn(
            "Inconsistent quote style (mixing ' and \")",
            issues,
        )

    def test_consistent_single_quotes_not_flagged(self):
        content = "\n".join([f"const k{i} = 'v{i}';" for i in range(10)])
        issues = self.handler._check_common_issues(content, content)
        self.assertNotIn(
            "Inconsistent quote style (mixing ' and \")",
            issues,
        )

    # ------------------------------------------------------------------
    # Semicolon style
    # ------------------------------------------------------------------

    def test_preexisting_mixed_semicolons_not_flagged(self):
        """Files with pre-existing semicolon inconsistency pass if the
        patch doesn't worsen the ratio."""
        lines_with = "\n".join([f"const a{i} = {i};" for i in range(20)])
        lines_without = "const b = 1\nconst c = 2"
        original = lines_with + "\n" + lines_without
        modified = original + "\nconst d = 3;"

        issues = self.handler._check_common_issues(original, modified)
        self.assertNotIn(
            "Inconsistent semicolon usage",
            issues,
            "Should not flag semicolon inconsistency already present in the original file",
        )

    # ------------------------------------------------------------------
    # Unrelated checks still fire
    # ------------------------------------------------------------------

    def test_infinite_loop_still_detected(self):
        """Non-style checks must still trigger regardless of original content."""
        original = "// empty"
        modified = "while (true) {\n  doThing();\n}"
        issues = self.handler._check_common_issues(original, modified)
        self.assertIn(
            "Potential infinite loop (while(true) without break)",
            issues,
        )


if __name__ == "__main__":
    unittest.main()
