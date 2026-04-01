"""
Tests for CLI diff applicator error handling.

Guards against regressions of:
  1. Error message propagation — when language validation fails, the actual
     error message (stored under the "message" key in the result details)
     must be displayed to the user,
     not a generic "Unknown error".
"""

import os
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


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
