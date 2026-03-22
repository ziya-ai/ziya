"""
Tests for file deletion diff handling and extract_target_file_from_diff robustness.

Covers:
- extract_target_file_from_diff returning source path (not /dev/null) for deletions
- extract_target_file_from_diff priority: +++ b/ > --- a/ > diff --git fallback
- is_file_deletion detection
- delete_file execution
- Pipeline integration for deletion diffs
"""

import os
import sys
import tempfile
import shutil
import unittest

# Ensure app is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


DELETION_DIFF = """\
diff --git a/obsolete_module.py b/dev/null
deleted file mode 100644
--- a/obsolete_module.py
+++ /dev/null
@@ -1,7 +0,0 @@
-\"\"\"Module that is being deleted.\"\"\"
-
-
-def old_function():
-    \"\"\"This function is no longer needed.\"\"\"
-    return "obsolete"
"""

NORMAL_DIFF = """\
diff --git a/app/utils/helper.py b/app/utils/helper.py
--- a/app/utils/helper.py
+++ b/app/utils/helper.py
@@ -1,3 +1,4 @@
 def hello():
-    return "world"
+    # Updated greeting
+    return "hello world"
"""

NEW_FILE_DIFF = """\
diff --git a/dev/null b/new_module.py
new file mode 100644
--- /dev/null
+++ b/new_module.py
@@ -0,0 +1,3 @@
+\"\"\"Brand new module.\"\"\"
+
+NEW_CONSTANT = 42
"""

RENAME_DIFF = """\
diff --git a/old_name.py b/new_name.py
rename from old_name.py
rename to new_name.py
--- a/old_name.py
+++ b/new_name.py
@@ -1,3 +1,3 @@
 def hello():
-    return "old"
+    return "new"
"""

# Diff with only a diff --git header and no --- / +++ lines (truncated/malformed)
BARE_GIT_HEADER_DIFF = """\
diff --git a/some/path.py b/some/path.py
@@ -1,3 +1,3 @@
 def hello():
-    return "old"
+    return "new"
"""

# Diff without diff --git header (plain unified diff)
PLAIN_UNIFIED_DIFF = """\
--- a/plain/file.py
+++ b/plain/file.py
@@ -1,3 +1,3 @@
 def hello():
-    return "old"
+    return "new"
"""


class TestExtractTargetFileFromDiff(unittest.TestCase):
    """Test that extract_target_file_from_diff handles all diff formats correctly."""

    def _extract(self, diff_content):
        from app.utils.diff_utils.parsing.diff_parser import extract_target_file_from_diff
        return extract_target_file_from_diff(diff_content)

    # --- Deletion diffs ---

    def test_deletion_diff_returns_source_path(self):
        """For a deletion diff, should return the source file, not /dev/null."""
        result = self._extract(DELETION_DIFF)
        self.assertEqual(result, "obsolete_module.py")

    def test_deletion_diff_does_not_return_dev_null(self):
        """Regression: extract_target_file_from_diff must never return /dev/null."""
        result = self._extract(DELETION_DIFF)
        self.assertNotIn("/dev/null", (result or ""))
        self.assertNotEqual(result, "dev/null")

    # --- Normal modification diffs ---

    def test_normal_diff_returns_target_path(self):
        """Normal diffs should return the target (+++ b/) path, not the diff --git fallback."""
        result = self._extract(NORMAL_DIFF)
        self.assertEqual(result, "app/utils/helper.py")

    # --- New file diffs ---

    def test_new_file_diff_returns_target_path(self):
        """New-file diffs should return the target (b/) path."""
        result = self._extract(NEW_FILE_DIFF)
        self.assertEqual(result, "new_module.py")

    # --- Rename diffs ---

    def test_rename_diff_returns_new_path(self):
        """Rename diffs should return the NEW path (+++ b/), not the old one."""
        result = self._extract(RENAME_DIFF)
        self.assertEqual(result, "new_name.py")

    # --- Priority / fallback behaviour ---

    def test_plus_plus_plus_takes_priority_over_diff_git(self):
        """+++ b/ header (line 2+) must win over diff --git (line 0)."""
        result = self._extract(NORMAL_DIFF)
        # If the diff --git fallback fired first, result would still be the
        # same for modifications, but for renames it would be wrong.
        # This test documents the invariant.
        self.assertEqual(result, "app/utils/helper.py")

    def test_bare_git_header_fallback(self):
        """When only diff --git is present (no ---/+++ headers), use it as fallback."""
        result = self._extract(BARE_GIT_HEADER_DIFF)
        self.assertEqual(result, "some/path.py")

    def test_plain_unified_diff(self):
        """Plain unified diff (no diff --git) should still extract from +++ b/."""
        result = self._extract(PLAIN_UNIFIED_DIFF)
        self.assertEqual(result, "plain/file.py")

    # --- Edge cases ---

    def test_empty_input(self):
        result = self._extract("")
        self.assertIsNone(result)

    def test_none_input(self):
        result = self._extract(None)
        self.assertIsNone(result)

    def test_garbage_input(self):
        result = self._extract("this is not a diff at all")
        self.assertIsNone(result)


class TestIsFileDeletion(unittest.TestCase):
    """Test the is_file_deletion detector."""

    def test_detects_deletion(self):
        from app.utils.diff_utils.validation.validators import is_file_deletion

        lines = DELETION_DIFF.splitlines()
        self.assertTrue(is_file_deletion(lines))

    def test_normal_diff_is_not_deletion(self):
        from app.utils.diff_utils.validation.validators import is_file_deletion

        lines = NORMAL_DIFF.splitlines()
        self.assertFalse(is_file_deletion(lines))

    def test_new_file_is_not_deletion(self):
        from app.utils.diff_utils.validation.validators import is_file_deletion

        lines = NEW_FILE_DIFF.splitlines()
        self.assertFalse(is_file_deletion(lines))

    def test_empty_input(self):
        from app.utils.diff_utils.validation.validators import is_file_deletion

        self.assertFalse(is_file_deletion([]))


class TestDeleteFile(unittest.TestCase):
    """Test the delete_file function."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.file_path = os.path.join(self.temp_dir, "obsolete_module.py")
        with open(self.file_path, "w") as f:
            f.write('"""Module that is being deleted."""\n\ndef old_function():\n    return "obsolete"\n')

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_delete_removes_file(self):
        from app.utils.diff_utils.file_ops.file_handlers import delete_file

        self.assertTrue(os.path.exists(self.file_path))
        delete_file(DELETION_DIFF, self.temp_dir)
        self.assertFalse(os.path.exists(self.file_path))

    def test_delete_returns_relative_path(self):
        from app.utils.diff_utils.file_ops.file_handlers import delete_file

        rel_path = delete_file(DELETION_DIFF, self.temp_dir)
        self.assertEqual(rel_path, "obsolete_module.py")

    def test_delete_nonexistent_raises(self):
        from app.utils.diff_utils.file_ops.file_handlers import delete_file

        os.remove(self.file_path)
        with self.assertRaises(FileNotFoundError):
            delete_file(DELETION_DIFF, self.temp_dir)


class TestPipelineDeletion(unittest.TestCase):
    """Integration: apply_diff_pipeline should delete the file for deletion diffs."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        os.environ["ZIYA_USER_CODEBASE_DIR"] = self.temp_dir
        self.file_path = os.path.join(self.temp_dir, "obsolete_module.py")
        with open(self.file_path, "w") as f:
            f.write(
                '"""Module that is being deleted."""\n\n\n'
                "def old_function():\n"
                '    """This function is no longer needed."""\n'
                '    return "obsolete"\n'
            )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)

    def test_pipeline_deletes_file(self):
        from app.utils.diff_utils.pipeline.pipeline_manager import apply_diff_pipeline

        self.assertTrue(os.path.exists(self.file_path))
        result = apply_diff_pipeline(DELETION_DIFF, self.file_path)
        self.assertEqual(result.get("status"), "success")
        self.assertTrue(result.get("is_deletion", False))
        self.assertFalse(os.path.exists(self.file_path))

    def test_pipeline_not_reversible_for_deletion(self):
        from app.utils.diff_utils.pipeline.pipeline_manager import apply_diff_pipeline

        result = apply_diff_pipeline(DELETION_DIFF, self.file_path)
        self.assertFalse(result.get("reversible", True),
                         "File deletions should not be marked as reversible")


if __name__ == "__main__":
    unittest.main()
