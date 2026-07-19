"""
Tests for malformed and edge-case hunk handling.

These verify the actual behavior of apply_diff_with_difflib_hybrid_forced:
- Malformed trailing hunks are skipped; valid preceding hunks still apply
- Pure-insertion hunks (@@ -N,0 +N,M @@) are valid and apply correctly
- Pure-deletion hunks (@@ -N,M +0,0 @@) are valid and apply correctly
"""

import os
import tempfile
import pytest
from app.utils.diff_utils.application.patch_apply import apply_diff_with_difflib_hybrid_forced


def _write(path, content):
    with open(path, 'w') as f:
        f.write(content)


def _read(path):
    with open(path) as f:
        return f.read()


def test_malformed_trailing_hunk_skipped():
    """
    A trailing empty hunk header with no body is malformed and gets skipped.
    The valid preceding hunk should still apply successfully.
    """
    original = "def test_function():\n    return 'test'\n"

    diff = (
        "--- a/test_file.py\n"
        "+++ b/test_file.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def test_function():\n"
        "+    print('test')\n"
        "     return 'test'\n"
        "@@ -1,2 +1,3 @@"   # trailing header with no body — malformed, should be skipped
    )

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "test_file.py")
        _write(f, original)
        result_lines = apply_diff_with_difflib_hybrid_forced(f, diff, original.splitlines(True))
        result = "".join(result_lines)

    assert "print('test')" in result, "Valid hunk should have been applied"
    assert "return 'test'" in result, "Existing content should be preserved"


def test_pure_insertion_hunk():
    """
    @@ -N,0 +N,M @@ is valid unified-diff syntax for a pure insertion
    (old_count=0 means no lines are removed). Should apply without error.
    """
    original = "def test_function():\n    return 'test'\n"

    diff = (
        "--- a/test_file.py\n"
        "+++ b/test_file.py\n"
        "@@ -1,0 +1,1 @@\n"
        "+    print('inserted')\n"
    )

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "test_file.py")
        _write(f, original)
        result_lines = apply_diff_with_difflib_hybrid_forced(f, diff, original.splitlines(True))
        result = "".join(result_lines)

    assert "print('inserted')" in result, "Inserted line should be present"
    assert "def test_function" in result, "Original content should be preserved"


def test_pure_deletion_hunk():
    """
    @@ -N,M +0,0 @@ (or equivalent) is valid unified-diff syntax for a
    pure deletion (new_count=0 means no lines are added). Should apply without error.
    """
    original = "def test_function():\n    return 'test'\n"

    diff = (
        "--- a/test_file.py\n"
        "+++ b/test_file.py\n"
        "@@ -1,1 +0,0 @@\n"
        "-def test_function():\n"
    )

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "test_file.py")
        _write(f, original)
        result_lines = apply_diff_with_difflib_hybrid_forced(f, diff, original.splitlines(True))
        result = "".join(result_lines)

    assert "def test_function" not in result, "Deleted line should be gone"
    assert "return 'test'" in result, "Non-deleted content should remain"


def test_pure_add_eof_preserves_preexisting_adjacent_duplicate():
    """
    A pure-addition hunk appended near EOF must NOT cause a legitimate,
    pre-existing pair of adjacent-identical lines elsewhere in the file to be
    collapsed by the function's final consecutive-duplicate cleanup pass.

    Regression guard for the whole-file dedup data-loss bug: the cleanup used
    to drop one of two identical adjacent source lines far from any hunk,
    reporting success (silent data loss). This is the same failure that
    corrupted tests/test_task_run_storage.py while appending a test class.

    Exercises apply_diff_with_difflib_hybrid_forced DIRECTLY — the corpus
    harness routes this shape through the system-patch stage, which does not
    reach this code path (see MRE_pure_add_eof_drops_upstream_dup metadata).
    """
    original = (
        "def setup():\n"
        "    create(id='a')\n"
        "    create(id='a')\n"       # legitimate pre-existing adjacent duplicate
        "    create(id='b')\n"
        "\n"
        "def teardown():\n"
        "    close()\n"
    )
    diff = (
        "--- a/f.py\n"
        "+++ b/f.py\n"
        "@@ -6,2 +6,5 @@\n"
        " def teardown():\n"
        "     close()\n"
        "+\n"
        "+def extra():\n"
        "+    pass\n"
    )
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "f.py")
        _write(f, original)
        result = "".join(
            apply_diff_with_difflib_hybrid_forced(f, diff, original.splitlines(True))
        )

    assert result.count("create(id='a')") == 2, (
        "pre-existing adjacent duplicate was dropped by the dedup pass:\n" + result
    )
    assert "def extra():" in result, "the EOF addition should have applied"
    assert "close()" in result, "existing content should be preserved"


def test_dedup_budget_does_not_disable_seam_cleanup():
    """
    The dedup fix only PRESERVES duplicates present in the source; it does not
    disable the cleanup. A line with zero adjacent-duplicate budget in the
    original keeps its single occurrence (the cleanup would still collapse an
    applier-introduced adjacent pair of it).
    """
    original = (
        "line one\n"
        "MARKER\n"
        "line three\n"
    )
    diff = (
        "--- a/f.py\n"
        "+++ b/f.py\n"
        "@@ -1,1 +1,2 @@\n"
        " line one\n"
        "+inserted\n"
    )
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "f.py")
        _write(f, original)
        result = "".join(
            apply_diff_with_difflib_hybrid_forced(f, diff, original.splitlines(True))
        )

    assert result.count("MARKER") == 1, "single source line must stay single"
    assert "inserted" in result, "the addition should have applied"
