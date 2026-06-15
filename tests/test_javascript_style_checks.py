"""
Tests for JavaScriptHandler._check_common_issues.

These checks must only flag style issues the diff *introduces*, not
pre-existing characteristics of the file being modified.
"""

import shutil
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

    def test_jsdoc_comment_lines_not_counted_as_missing_semicolons(self):
        """JSDoc/block-comment continuation lines (' * text') must not be
        counted as statements missing a semicolon.

        Regression: a semicolon-majority file gaining a new export whose
        JSDoc block had several ' * ...' body lines was rejected with
        'Inconsistent semicolon usage' because each comment line inflated
        the without-semicolon count.
        """
        original = "\n".join([f"const a{i} = {i};" for i in range(20)])
        modified = original + "\n".join([
            "",
            "/**",
            " * Server-side conversation search. Scans chat files one at a",
            " * time and returns SearchResult-shaped objects, avoiding loading",
            " * every conversation's full message bodies into the browser.",
            " * allProjects=false searches strictly the given project.",
            " */",
            "export function searchChats(projectId, query) {",
            "  const url = base + projectId;",
            "  return fetch(url);",
            "}",
        ])
        issues = self.handler._check_common_issues(original, modified)
        self.assertNotIn(
            "Inconsistent semicolon usage",
            issues,
            "JSDoc ' * ' comment lines should be skipped, not counted as "
            "statements missing a semicolon",
        )

    def test_genuine_semicolon_inconsistency_still_flagged(self):
        """The fix must not mask a real regression: a single-style file that
        the patch makes genuinely mixed should still be flagged."""
        original = "\n".join([f"const a{i} = {i};" for i in range(20)])
        modified = original + "\n" + "\n".join(
            [f"const b{i} = {i}" for i in range(20)]  # 20 new lines, no semicolons
        )
        issues = self.handler._check_common_issues(original, modified)
        self.assertIn(
            "Inconsistent semicolon usage",
            issues,
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

    # ------------------------------------------------------------------
    # Advisory vs blocking partition
    # ------------------------------------------------------------------

    def test_partition_quote_style_is_advisory(self):
        """Quote-style findings are advisory (logged, not blocking)."""
        blocking, advisory = self.handler.partition_issues(
            ["Inconsistent quote style (mixing ' and \")"]
        )
        self.assertEqual(blocking, [])
        self.assertEqual(advisory, ["Inconsistent quote style (mixing ' and \")"])

    def test_partition_semicolon_is_advisory(self):
        blocking, advisory = self.handler.partition_issues(
            ["Inconsistent semicolon usage"]
        )
        self.assertEqual(blocking, [])
        self.assertEqual(advisory, ["Inconsistent semicolon usage"])

    def test_partition_infinite_loop_stays_blocking(self):
        """The infinite-loop heuristic is NOT demoted — it still blocks."""
        blocking, advisory = self.handler.partition_issues(
            ["Potential infinite loop (while(true) without break)"]
        )
        self.assertEqual(advisory, [])
        self.assertEqual(
            blocking, ["Potential infinite loop (while(true) without break)"]
        )

    def test_partition_unknown_issue_stays_blocking(self):
        """Any issue not explicitly advisory blocks (fail-safe default).

        Guards the partition against silently demoting a future
        TS-specific or structural finding just because it was added to
        the issue list — only the two named style strings are advisory.
        """
        blocking, advisory = self.handler.partition_issues(
            ["Some new structural defect", "Inconsistent semicolon usage"]
        )
        self.assertEqual(advisory, ["Inconsistent semicolon usage"])
        self.assertEqual(blocking, ["Some new structural defect"])

    def test_partition_empty(self):
        self.assertEqual(self.handler.partition_issues([]), ([], []))


class TestVerifyChangesAdvisoryGate(unittest.TestCase):
    """End-to-end: verify_changes must NOT block on style-only findings,
    but MUST still block on structural defects.

    Exercises the full gate (real node --check when available, basic
    bracket validation otherwise) rather than the pure partition helper,
    so a future regression that re-blocks style issues is caught here.
    """

    def setUp(self):
        self.handler = JavaScriptHandler()

    def test_style_only_diff_is_not_blocked(self):
        """A diff that worsens semicolon consistency (but is valid JS) applies.

        This is the exact class of change that was wrongly rejected:
        structurally correct, behaviorally correct, only a style drift.
        """
        original = "\n".join([f"const a{i} = {i};" for i in range(20)])
        # Add valid statements WITHOUT semicolons — trips the semicolon
        # heuristic, but node --check accepts it (ASI), so it must apply.
        modified = original + "\n" + "\n".join(
            [f"const b{i} = {i}" for i in range(10)]
        )
        # Sanity: the style check really does flag this as advisory.
        issues = self.handler._check_common_issues(original, modified)
        self.assertIn("Inconsistent semicolon usage", issues)
        blocking, advisory = self.handler.partition_issues(issues)
        self.assertIn("Inconsistent semicolon usage", advisory)
        self.assertEqual(blocking, [])

        ok, error = self.handler.verify_changes(original, modified, "snippet.js")
        self.assertTrue(
            ok,
            f"Style-only diff must apply, but verify_changes blocked it: {error}",
        )
        self.assertIsNone(error)

    def test_quote_style_only_diff_is_not_blocked(self):
        original = "\n".join([f"const k{i} = 'v{i}';" for i in range(20)])
        modified = original + "\n" + 'const d = "x";'
        issues = self.handler._check_common_issues(original, modified)
        self.assertIn("Inconsistent quote style (mixing ' and \")", issues)

        ok, error = self.handler.verify_changes(original, modified, "snippet.js")
        self.assertTrue(ok, f"Quote-style-only diff blocked: {error}")
        self.assertIsNone(error)

    def test_infinite_loop_diff_still_blocked(self):
        """The blocking heuristic must survive the advisory split: a
        while(true) without break is still rejected by verify_changes.

        Skipped when node is unavailable, because the no-node fallback
        (_basic_js_validation) does not run the infinite-loop heuristic —
        that check only fires on the node-check success path.
        """
        if shutil.which("node") is None:
            self.skipTest("node not available; infinite-loop heuristic "
                          "only runs on the node --check success path")
        original = "const x = 1;"
        modified = "while (true) {\n  doThing();\n}"
        ok, error = self.handler.verify_changes(original, modified, "snippet.js")
        self.assertFalse(ok, "Infinite-loop diff must still be blocked")
        self.assertIsNotNone(error)
        self.assertIn("infinite loop", error.lower())

    def test_structurally_broken_diff_still_blocked(self):
        """Unbalanced brackets must be rejected regardless of style state.

        Works on both paths: node --check fails on the syntax error, and
        the no-node fallback's bracket matcher also rejects it.
        """
        original = "const x = 1;"
        modified = "function broken() {\n  return 1;\n"  # missing closing brace
        ok, error = self.handler.verify_changes(original, modified, "snippet.js")
        self.assertFalse(ok, "Structurally broken diff must be blocked")
        self.assertIsNotNone(error)


if __name__ == "__main__":
    unittest.main()
