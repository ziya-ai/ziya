"""Tests for CLIDiffApplicator.extract_diffs edge cases."""
import pytest
from app.utils.cli_diff_applicator import CLIDiffApplicator


@pytest.fixture
def applicator():
    return CLIDiffApplicator()


CLEAN_DIFF = """\
```diff
diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
 context
-old line
+new line
 context
```
"""

def test_clean_fence_extracted(applicator):
    diffs = applicator.extract_diffs(CLEAN_DIFF)
    assert len(diffs) == 1
    assert diffs[0].file_path == "foo.py"


def test_fence_with_language_specifier_not_extracted(applicator):
    """```diff python is not a valid diff fence — extract_diffs should ignore it."""
    response = CLEAN_DIFF.replace("```diff\n", "```diff python\n")
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 0


def test_fence_with_trailing_spaces_extracted(applicator):
    """Trailing whitespace after diff on the fence line is allowed."""
    response = CLEAN_DIFF.replace("```diff\n", "```diff   \n")
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 1


def test_four_backtick_fence_extracted(applicator):
    """Four backticks is also a valid fence."""
    response = CLEAN_DIFF.replace("```diff", "````diff")
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 1


def test_two_backtick_fence_not_extracted(applicator):
    """Two backticks is not a valid fence."""
    response = CLEAN_DIFF.replace("```diff", "``diff")
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 0


def test_multiple_diffs_extracted(applicator):
    response = CLEAN_DIFF + "\nSome text\n\n" + CLEAN_DIFF.replace("foo.py", "bar.py")
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 2


def test_no_diff_blocks_returns_empty(applicator):
    diffs = applicator.extract_diffs("Just some plain text with no diffs.")
    assert len(diffs) == 0


def test_pathless_diff_has_no_file_path(applicator):
    """A diff block without git headers produces a DiffBlock with no file_path."""
    response = "```diff\n-old\n+new\n```\n"
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 1
    assert diffs[0].file_path is None


def test_unclosed_fence_collects_remaining_content(applicator):
    """An unclosed fence is collected to end-of-response (not silently dropped)."""
    response = "```diff\ndiff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n"
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 1
