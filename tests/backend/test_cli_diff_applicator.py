"""
Tests for CLI diff applicator error handling.

Guards against regressions of:
  1. Error message propagation — when language validation fails, the actual
     error message (stored under the "message" key in the result details)
     must be displayed to the user,
     not a generic "Unknown error".
  2. Supersession detection — when the model revises a diff, the earlier
     version must be dropped.  A one-line substitution was previously
     misclassified as a "sequential" (complementary) change and both
     versions were kept, causing the stale diff to be applied.
"""

import os
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


class TestSequentialPairDetection(unittest.TestCase):
    """
    _is_sequential_pair must treat two diffs that remove the SAME source
    line as competing revisions; only disjoint removals are complementary.
    """

    @staticmethod
    def _hunk(header, body):
        return "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n" + header + "\n" + body

    def setUp(self):
        from app.utils.cli_diff_applicator import CLIDiffApplicator
        self.fn = CLIDiffApplicator._is_sequential_pair
        # A later diff that adds content, used as the second half of each pair.
        self.later_add = self._hunk("@@ -10,3 +10,5 @@",
                                    " keep\n+new1\n+new2\n keep2\n")

    def test_one_line_substitution_is_not_sequential(self):
        """A 1-remove/1-add rewrite is a revision, not a preparatory step."""
        earlier = self._hunk("@@ -303,7 +303,7 @@",
                             " ctx\n-        pf = Path.home()\n+        pf = get_ziya_home()\n ctx2\n")
        later = earlier.replace("@@ -303,7 +303,7 @@", "@@ -305,7 +305,7 @@")
        self.assertFalse(self.fn(earlier, later))

    def test_balanced_rewrite_of_same_lines_is_not_sequential(self):
        """-2/+2 twice over the SAME removed lines is a revision."""
        earlier = self._hunk("@@ -10,6 +10,6 @@",
                             " keep\n-old1\n-old2\n+v1a\n+v1b\n keep2\n")
        later = self._hunk("@@ -12,6 +12,6 @@",
                           " keep\n-old1\n-old2\n+v2a\n+v2b\n keep2\n")
        self.assertFalse(self.fn(earlier, later))

    def test_balanced_rewrite_of_disjoint_lines_is_sequential(self):
        """-2/+2 twice over DIFFERENT lines is two real edits, not a revision.

        The previous count-based rule (earlier_removes > earlier_adds) got
        this wrong: it dropped the earlier diff purely because the counts
        were balanced, regardless of which lines were touched.
        """
        earlier = self._hunk("@@ -10,6 +10,6 @@",
                             " keep\n-alpha1\n-alpha2\n+mid1\n+mid2\n keep2\n")
        later = self._hunk("@@ -11,6 +11,6 @@",
                           " keep\n-beta1\n-beta2\n+end1\n+end2\n keep2\n")
        self.assertTrue(self.fn(earlier, later))

    def test_revised_pure_addition_is_not_sequential(self):
        """An earlier diff that removes nothing is not clearing the way."""
        earlier = self._hunk("@@ -10,3 +10,4 @@", " keep\n+import a\n keep2\n")
        later = self._hunk("@@ -10,3 +10,4 @@", " keep\n+import b\n keep2\n")
        self.assertFalse(self.fn(earlier, later))

    def test_pure_deletion_is_sequential(self):
        """Removing code to make way for a later addition is complementary."""
        earlier = self._hunk("@@ -10,6 +10,3 @@",
                             " keep\n-gone1\n-gone2\n-gone3\n keep2\n")
        self.assertTrue(self.fn(earlier, self.later_add))

    def test_net_subtractive_hunk_is_sequential(self):
        """A deletion that leaves one replacement line still counts."""
        earlier = self._hunk("@@ -10,6 +10,4 @@",
                             " keep\n-gone1\n-gone2\n-gone3\n+placeholder\n keep2\n")
        self.assertTrue(self.fn(earlier, self.later_add))

    def test_later_diff_must_add_content(self):
        """Two deletions are not a prepare-then-add pair."""
        earlier = self._hunk("@@ -10,6 +10,3 @@",
                             " keep\n-gone1\n-gone2\n-gone3\n keep2\n")
        later_del = self._hunk("@@ -20,5 +20,3 @@",
                               " keep\n-x\n-y\n keep2\n")
        self.assertFalse(self.fn(earlier, later_del))


class TestErrorMessagePropagation(unittest.TestCase):
    """
    When apply_diff_atomically returns a language_validation error,
    the _apply_diff method must surface the actual error message.
    """

    def _make_diff_block(self, file_path='src/test.tsx', content='--- a/test\n+++ b/test\n@@ -1 +1 @@\n-old\n+new'):
        """Create a minimal DiffBlock-like object."""
        block = mock.Mock()
        block.file_path = file_path
        block.content = content
        block.is_deletion = False
        return block

    def test_language_validation_error_uses_message_key(self):
        """Error details with 'message' key must not produce 'Unknown error'."""
        from app.utils.cli_diff_applicator import CLIDiffApplicator

        applicator = CLIDiffApplicator()
        diff = self._make_diff_block()

        error_result = {
            "status": "error",
            "details": {
                "type": "language_validation",
                "message": "TS1109: Expression expected at line 42"
            }
        }

        # Patch at the source — _apply_diff imports from git_diff inside the method
        with mock.patch('app.utils.diff_utils.application.git_diff.apply_diff_atomically', return_value=error_result), \
             mock.patch.dict(os.environ, {"ZIYA_USER_CODEBASE_DIR": "/tmp"}):
            success, message = applicator._apply_diff(diff)

        self.assertFalse(success)
        self.assertIn("TS1109", message,
                       f"Expected actual error message, got: {message!r}")
        self.assertNotIn("Unknown error", message,
                         "Must not fall back to 'Unknown error' when 'message' key is present")

    def test_error_key_still_works(self):
        """Error details with 'error' key (legacy format) must still work."""
        from app.utils.cli_diff_applicator import CLIDiffApplicator

        applicator = CLIDiffApplicator()
        diff = self._make_diff_block()

        error_result = {
            "status": "error",
            "details": {
                "type": "some_error",
                "error": "Something went wrong"
            }
        }

        with mock.patch('app.utils.diff_utils.application.git_diff.apply_diff_atomically', return_value=error_result), \
             mock.patch.dict(os.environ, {"ZIYA_USER_CODEBASE_DIR": "/tmp"}):
            success, message = applicator._apply_diff(diff)

        self.assertFalse(success)
        self.assertIn("Something went wrong", message)

    def test_empty_details_gives_unknown_error(self):
        """When details has neither 'message' nor 'error', fall back gracefully."""
        from app.utils.cli_diff_applicator import CLIDiffApplicator

        applicator = CLIDiffApplicator()
        diff = self._make_diff_block()

        error_result = {
            "status": "error",
            "details": {"type": "something"}
        }

        with mock.patch('app.utils.diff_utils.application.git_diff.apply_diff_atomically', return_value=error_result), \
             mock.patch.dict(os.environ, {"ZIYA_USER_CODEBASE_DIR": "/tmp"}):
            success, message = applicator._apply_diff(diff)

        self.assertFalse(success)
        self.assertIn("Unknown error", message)


if __name__ == '__main__':
    unittest.main()
