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


# ── Bare (unfenced) unified-diff recovery ───────────────────────────
#
# When the model omits the ```diff fence, ``extract_diffs`` falls back
# to a strict structural parse via ``_extract_bare_unified_diff``.
# The recovery requires the full git-diff signature so prose that
# merely *quotes* a diff is rejected.


UNFENCED_DIFF = """\
Here's the fix:

diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
 context
-old line
+new line
 context

That should do it."""


def test_unfenced_diff_with_full_structure_is_recovered(applicator):
    """A bare diff with diff --git, ---, +++, @@ and body lines is
    recovered when no fenced block is present."""
    diffs = applicator.extract_diffs(UNFENCED_DIFF)
    assert len(diffs) == 1
    assert diffs[0].file_path == "foo.py"
    assert "+new line" in diffs[0].content
    assert "-old line" in diffs[0].content


def test_prose_with_diff_git_substring_not_recovered(applicator):
    """Prose mentioning ``diff --git`` without the full structural
    signature must not be extracted."""
    response = (
        "The 'diff --git' header is the first line of every git-format "
        "diff. It looks like ``diff --git a/foo b/foo`` but isn't followed "
        "by the rest of the structure here, so this should not be treated "
        "as an applicable diff."
    )
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 0


def test_unfenced_recovery_requires_minus_header(applicator):
    """``diff --git`` + ``+++`` + ``@@`` but no ``---`` → reject."""
    response = (
        "diff --git a/foo.py b/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,3 +1,3 @@\n"
        " context\n"
        "-old\n"
        "+new\n"
    )
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 0


def test_unfenced_recovery_requires_plus_header(applicator):
    """``diff --git`` + ``---`` + ``@@`` but no ``+++`` → reject."""
    response = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "@@ -1,3 +1,3 @@\n"
        " context\n"
        "-old\n"
        "+new\n"
    )
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 0


def test_unfenced_recovery_requires_hunk_header(applicator):
    """All three headers present but no ``@@`` hunk → reject."""
    response = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "just some text after the headers\n"
    )
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 0


def test_fenced_present_skips_unfenced_recovery(applicator):
    """When a fenced diff is present, the bare-diff recovery must
    not fire — no double extraction."""
    response = (
        "Here's a fenced one:\n\n"
        + CLEAN_DIFF
        + "\n\n"
        + "And here's an unfenced one (which should be ignored):\n\n"
        + "diff --git a/bare.py b/bare.py\n"
        + "--- a/bare.py\n"
        + "+++ b/bare.py\n"
        + "@@ -1,1 +1,1 @@\n"
        + "-bare-old\n"
        + "+bare-new\n"
    )
    diffs = applicator.extract_diffs(response)
    # Only the fenced one is extracted.
    assert len(diffs) == 1
    assert diffs[0].file_path == "foo.py"


def test_unfenced_recovery_byte_offsets_bracket_content(applicator):
    """Recovered DiffBlock.start_pos / end_pos must bracket the
    diff content within the original markdown."""
    prefix = "Some preamble text.\n\n"
    diff_text = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    response = prefix + diff_text + "\nTrailing prose."
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 1
    assert response[diffs[0].start_pos:].startswith("diff --git")
    bracketed = response[diffs[0].start_pos:diffs[0].end_pos]
    assert "diff --git" in bracketed
    assert "+new" in bracketed
    assert "Trailing prose" not in bracketed


def test_unfenced_recovery_terminates_at_non_diff_line(applicator):
    """Body collection stops at the first line that isn't a diff body
    line, so trailing prose isn't sucked into the content."""
    response = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,2 +1,2 @@\n"
        " context\n"
        "-removed\n"
        "+added\n"
        "\n"
        "This is regular prose that should not be part of the diff body."
    )
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 1
    assert "regular prose" not in diffs[0].content
    assert "+added" in diffs[0].content


def test_unfenced_recovery_tolerates_index_line(applicator):
    """Real ``git diff`` output emits an ``index abc..def 100644`` line
    between ``diff --git`` and ``--- a/`` — recovery must accept this
    canonical form."""
    response = (
        "diff --git a/foo.py b/foo.py\n"
        "index 1234567..89abcde 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 1
    assert diffs[0].file_path == "foo.py"


def test_unfenced_recovery_at_response_start(applicator):
    """A diff at the very beginning of the response (no preamble)
    should still recover, with ``start_pos == 0``."""
    response = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )
    diffs = applicator.extract_diffs(response)
    assert len(diffs) == 1
    assert diffs[0].start_pos == 0
