"""
Regression tests for absolute-path preservation in diff parsing.

Git folds a path's leading slash into the a/ b/ prefix, so an absolute
target such as /Users/x/y.py appears as '+++ b/Users/x/y.py' — ambiguous
with a project-relative 'Users/x/y.py'. The author's expressed path must
survive the round-trip: display and every apply site must agree on the same
path, decided from a fixed root allowlist rather than from disk state.

The previous implementation gated the slash restore on os.path.exists(),
which (a) made the extracted path depend on unrelated files and (b) failed
to restore the slash for a not-yet-created new file, silently nesting it
under the project root. These tests pin the deterministic behavior and use
paths that do NOT exist on disk so a regression to the exists-gate fails.
"""

import unittest

from app.utils.diff_utils.parsing.diff_parser import (
    restore_leading_slash,
    extract_target_file_from_diff,
)

# Deliberately nonexistent so an os.path.exists-based restore would NOT fire.
GHOST = "Users/no_such_user_zzz/nope/ghost.py"


class TestRestoreLeadingSlash(unittest.TestCase):
    def test_absolute_root_restored(self):
        self.assertEqual(restore_leading_slash(GHOST), "/" + GHOST)

    def test_project_relative_untouched(self):
        self.assertEqual(restore_leading_slash("app/data/x.py"), "app/data/x.py")

    def test_already_absolute_untouched(self):
        self.assertEqual(restore_leading_slash("/already/abs.py"), "/already/abs.py")

    def test_empty_untouched(self):
        self.assertEqual(restore_leading_slash(""), "")

    def test_all_known_roots(self):
        for root in ("home/", "opt/", "var/", "usr/", "tmp/", "etc/", "srv/", "private/"):
            self.assertEqual(restore_leading_slash(root + "f"), "/" + root + "f")


class TestExtractTargetPreservesAbsolute(unittest.TestCase):
    def test_modify_absolute_roundtrip(self):
        diff = (
            f"diff --git a/{GHOST} b/{GHOST}\n"
            f"--- a/{GHOST}\n"
            f"+++ b/{GHOST}\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
        )
        self.assertEqual(extract_target_file_from_diff(diff), "/" + GHOST)

    def test_new_file_absolute_restored(self):
        # +++ b/ target; file does not exist yet — exists-gate would fail here.
        diff = (
            f"diff --git a/{GHOST} b/{GHOST}\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            f"+++ b/{GHOST}\n"
            "@@ -0,0 +1 @@\n"
            "+x\n"
        )
        self.assertEqual(extract_target_file_from_diff(diff), "/" + GHOST)

    def test_deletion_source_absolute_restored(self):
        diff = (
            f"diff --git a/{GHOST} b/{GHOST}\n"
            "deleted file mode 100644\n"
            f"--- a/{GHOST}\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-x\n"
        )
        self.assertEqual(extract_target_file_from_diff(diff), "/" + GHOST)

    def test_project_relative_unaffected(self):
        diff = (
            "diff --git a/app/data/x.py b/app/data/x.py\n"
            "--- a/app/data/x.py\n"
            "+++ b/app/data/x.py\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
        )
        self.assertEqual(extract_target_file_from_diff(diff), "app/data/x.py")


if __name__ == "__main__":
    unittest.main()
