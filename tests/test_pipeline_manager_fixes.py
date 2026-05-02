"""
Tests for the pipeline_manager and CLIDiffApplicator fixes:

1. apply_diff_pipeline always returns a dict (never a bool), even when
   apply_patch_directly fails on the skip_dry_run path.
2. CLIDiffApplicator records "skipped" entries in diff_results when the
   user explicitly skips a diff.
"""

import os
import sys
import unittest
import tempfile
import shutil
from io import StringIO
from unittest.mock import patch, MagicMock

from app.utils.diff_utils.pipeline.pipeline_manager import apply_diff_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIMPLE_FILE_CONTENT = """\
def hello():
    print("Hello, world!")

def goodbye():
    print("Goodbye, world!")
"""

SIMPLE_DIFF = """\
diff --git a/test_subject.py b/test_subject.py
--- a/test_subject.py
+++ b/test_subject.py
@@ -1,2 +1,2 @@
 def hello():
-    print("Hello, world!")
+    print("Hello, patched!")
"""


# ---------------------------------------------------------------------------
# apply_diff_pipeline return-type tests
# ---------------------------------------------------------------------------

class TestApplyDiffPipelineReturnType(unittest.TestCase):
    """apply_diff_pipeline must always return a dict, never a bool."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.subject = os.path.join(self.temp_dir, "test_subject.py")
        with open(self.subject, "w") as f:
            f.write(SIMPLE_FILE_CONTENT)
        os.environ["ZIYA_USER_CODEBASE_DIR"] = self.temp_dir

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)

    def test_returns_dict_on_success_skip_dry_run(self):
        """Successful skip_dry_run path returns a dict with status key."""
        result = apply_diff_pipeline(SIMPLE_DIFF, self.subject)
        self.assertIsInstance(result, dict,
            "apply_diff_pipeline should return a dict, not a bool")
        self.assertIn("status", result,
            "Result dict must contain a 'status' key")

    def test_returns_dict_when_apply_patch_directly_fails(self):
        """When apply_patch_directly returns False, the function must still
        return a dict — not propagate the boolean."""
        # Force the skip_dry_run path and make apply_patch_directly fail.
        with patch(
            "app.utils.diff_utils.pipeline.pipeline_manager.apply_patch_directly",
            return_value=False,
        ):
            result = apply_diff_pipeline(SIMPLE_DIFF, self.subject)

        self.assertIsInstance(result, dict,
            "apply_diff_pipeline must return a dict even when "
            "apply_patch_directly returns False")
        self.assertIn("status", result)

    def test_result_reflects_failure_when_patch_directly_fails(self):
        """When apply_patch_directly cannot apply the patch (context mismatch),
        the returned dict should not report success."""
        # Context lines that don't exist in the file — patch command will fail.
        bad_diff = (
            "diff --git a/test_subject.py b/test_subject.py\n"
            "--- a/test_subject.py\n"
            "+++ b/test_subject.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def nonexistent_function():\n"
            '-    print("This line does not exist in the file")\n'
            '+    print("Neither does this replacement")\n'
        )
        # Ensure we take the skip_dry_run path (no forced dry-run override).
        os.environ.pop("ZIYA_FORCE_DRY_RUN", None)

        result = apply_diff_pipeline(bad_diff, self.subject)

        self.assertIsInstance(result, dict)
        self.assertNotEqual(
            result.get("status"), "success",
            "Status should not be 'success' when patch context doesn't match the file"
        )


# ---------------------------------------------------------------------------
# CLIDiffApplicator skipped-entry recording
# ---------------------------------------------------------------------------

class TestCLIDiffApplicatorSkippedRecording(unittest.TestCase):
    """CLIDiffApplicator must append a 'skipped' entry to diff_results when
    the user skips a diff via the 's' action."""

    def _make_applicator(self):
        """Import lazily to avoid heavy CLI imports at module level."""
        from app.utils.cli_diff_applicator import CLIDiffApplicator
        return CLIDiffApplicator()

    def test_skip_records_entry_in_diff_results(self):
        """Skipping a diff appends (file_path, 'skipped', ...) to diff_results."""
        applicator = self._make_applicator()

        # Build a minimal response that contains one diff block.
        response = (
            "Here is the change:\n\n"
            "```diff\n"
            + SIMPLE_DIFF +
            "```\n"
        )

        # Simulate the user pressing 's'.
        with patch("builtins.input", return_value="s"), \
             patch("sys.stdout", new_callable=StringIO):
            applicator.process_response(response)

        statuses = [entry[1] for entry in applicator.diff_results]
        self.assertIn(
            "skipped", statuses,
            "diff_results should contain a 'skipped' entry after user presses 's'"
        )

    def test_skip_increments_skipped_count(self):
        """Skipping also increments skipped_count (existing behaviour check)."""
        applicator = self._make_applicator()
        response = (
            "Here is the change:\n\n"
            "```diff\n"
            + SIMPLE_DIFF +
            "```\n"
        )
        with patch("builtins.input", return_value="s"), \
             patch("sys.stdout", new_callable=StringIO):
            applicator.process_response(response)

        self.assertEqual(applicator.skipped_count, 1)

    def test_skip_does_not_record_applied(self):
        """A skipped diff must not appear as 'applied' in diff_results."""
        applicator = self._make_applicator()
        response = (
            "Here is the change:\n\n"
            "```diff\n"
            + SIMPLE_DIFF +
            "```\n"
        )
        with patch("builtins.input", return_value="s"), \
             patch("sys.stdout", new_callable=StringIO):
            applicator.process_response(response)

        applied = [e for e in applicator.diff_results if e[1] == "applied"]
        self.assertEqual(applied, [],
            "A skipped diff should not be recorded as 'applied'")




# ---------------------------------------------------------------------------
# extract_diffs fence-format edge cases
# ---------------------------------------------------------------------------

class TestExtractDiffsFenceFormats(unittest.TestCase):
    """extract_diffs must accept only clean diff fences and reject fences
    that carry extra tokens after 'diff' (e.g. language specifiers)."""

    def _make_applicator(self):
        from app.utils.cli_diff_applicator import CLIDiffApplicator
        return CLIDiffApplicator()

    # Minimal valid diff content (no file path needed for extraction tests).
    DIFF_BODY = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    def _wrap(self, fence_open, fence_close="```"):
        return f"{fence_open}\n{self.DIFF_BODY}{fence_close}\n"

    def test_clean_fence_extracted(self):
        """Standard ```diff fence is extracted."""
        applicator = self._make_applicator()
        result = applicator.extract_diffs(self._wrap("```diff"))
        self.assertEqual(len(result), 1)

    def test_four_backtick_fence_extracted(self):
        """````diff (4 backticks) is also valid."""
        applicator = self._make_applicator()
        result = applicator.extract_diffs(self._wrap("````diff", "````"))
        self.assertEqual(len(result), 1)

    def test_fence_with_language_specifier_rejected(self):
        """```diff python should NOT be extracted — it breaks the regex."""
        applicator = self._make_applicator()
        result = applicator.extract_diffs(self._wrap("```diff python"))
        self.assertEqual(len(result), 0,
            "Fence with extra token after 'diff' must not be extracted")

    def test_fence_with_trailing_text_rejected(self):
        """```diff  (trailing non-whitespace) must not be extracted."""
        applicator = self._make_applicator()
        result = applicator.extract_diffs(self._wrap("```diff # comment"))
        self.assertEqual(len(result), 0)

    def test_fence_with_trailing_whitespace_extracted(self):
        """```diff   (trailing spaces only) is valid."""
        applicator = self._make_applicator()
        result = applicator.extract_diffs(self._wrap("```diff   "))
        self.assertEqual(len(result), 1)

    def test_empty_response_yields_no_diffs(self):
        applicator = self._make_applicator()
        self.assertEqual(applicator.extract_diffs(""), [])

    def test_no_fence_yields_no_diffs(self):
        applicator = self._make_applicator()
        self.assertEqual(applicator.extract_diffs("just some text\nno diff here"), [])

    def test_unclosed_fence_collects_remaining_lines(self):
        """An opening fence with no closing fence collects all remaining content.
        extract_diffs does not require a closing fence — it reads to EOF."""
        applicator = self._make_applicator()
        result = applicator.extract_diffs("```diff\n" + self.DIFF_BODY)
        # The loop exhausts lines without finding a closer, but still appends
        # the collected content as a DiffBlock.
        self.assertEqual(len(result), 1)

    def test_multiple_clean_fences_extracted(self):
        """Two separate diff blocks are both extracted."""
        applicator = self._make_applicator()
        two_blocks = self._wrap("```diff") + "\n" + self._wrap("```diff")
        result = applicator.extract_diffs(two_blocks)
        self.assertEqual(len(result), 2)

    def test_mixed_valid_invalid_fences(self):
        """One clean and one language-tagged fence: only the clean one extracted."""
        applicator = self._make_applicator()
        mixed = self._wrap("```diff") + "\n" + self._wrap("```diff python")
        result = applicator.extract_diffs(mixed)
        self.assertEqual(len(result), 1)


# ---------------------------------------------------------------------------
# Continuation message branch coverage
# ---------------------------------------------------------------------------

class TestContinuationMessageBranches(unittest.TestCase):
    """The continuation message branches in ZiyaREPL are logic-only;
    we test the selection logic directly rather than spinning up the full REPL."""

    def _pick_message(self, diff_results):
        """Reproduce the branch logic from cli.py for isolated testing."""
        skipped = [r for r in diff_results if r[1] == "skipped"]
        applied = [r for r in diff_results if r[1] == "applied"]

        if skipped and not applied:
            return "skipped_only"
        else:
            return "standard"

    def test_all_applied_uses_standard_message(self):
        results = [("a.py", "applied", "ok"), ("b.py", "applied", "ok")]
        self.assertEqual(self._pick_message(results), "standard")

    def test_all_skipped_uses_skipped_only_message(self):
        results = [("a.py", "skipped", "Skipped by user")]
        self.assertEqual(self._pick_message(results), "skipped_only")

    def test_mixed_applied_and_skipped_uses_standard(self):
        """When at least one diff was applied, even with skips, use standard."""
        results = [
            ("a.py", "applied", "ok"),
            ("b.py", "skipped", "Skipped by user"),
        ]
        self.assertEqual(self._pick_message(results), "standard")

    def test_empty_results_uses_standard(self):
        """No diffs at all (e.g. response had no diff blocks) → standard."""
        self.assertEqual(self._pick_message([]), "standard")


if __name__ == "__main__":
    unittest.main()
