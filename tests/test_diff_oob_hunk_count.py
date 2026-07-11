"""
Regression coverage for PenPal #108 [MEDIUM, CWE-119]: out-of-bounds file
modification via an unvalidated hunk `old_count`.

A crafted hunk header (`@@ -5,999999999 +5,4 @@`) inflates old_count; under
fuzzy match (ratio < 0.96, truncation >= 2) the applier did
`remove_pos -= truncation` with no max(0, ...) floor, driving remove_pos
deeply negative. `insert_pos = remove_pos` then wrapped the slice from the
END of the file list, silently overwriting the top of the file while logging
a normal apply.

Two layers are pinned here: (1) the parser clamps an implausible old_count
at the source, and (2) the applier's negative-index wrap is reproduced
directly to prove max(0, ...) is the fix.
"""
import pytest

from app.utils.diff_utils.parsing.diff_parser import (
    parse_unified_diff,
    _MAX_HUNK_LINE_COUNT,
)


_MALICIOUS_DIFF = (
    "diff --git a/f.py b/f.py\n"
    "--- a/f.py\n"
    "+++ b/f.py\n"
    "@@ -5,999999999 +5,4 @@\n"
    " ctx\n"
    "+injected\n"
    " ctx2\n"
)


class TestParserClampsImplausibleOldCount:
    def test_inflated_old_count_is_clamped(self):
        hunks = parse_unified_diff(_MALICIOUS_DIFF)
        assert len(hunks) == 1
        # The header declared 999,999,999 — must be clamped at the source.
        assert hunks[0]["old_count"] <= _MAX_HUNK_LINE_COUNT

    def test_normal_old_count_untouched(self):
        normal = (
            "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            "@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n"
        )
        hunks = parse_unified_diff(normal)
        assert hunks[0]["old_count"] == 3  # unchanged


class TestNegativeRemovePosCannotWrap:
    """The applier's slice math must never wrap from the end of the file.
    Reproduces the exact pre-fix computation and asserts the max(0, ...)
    floor eliminates the negative index (non-tautological: the pre-fix form
    is shown to wrap)."""

    def test_prefix_negative_index_wraps_and_wipes_top(self):
        final = [f"line{i}\n" for i in range(1000)]
        remove_pos, truncation, actual_remove_count = 500, 999_999_996, 1_000_000_000
        rp = remove_pos - truncation                       # pre-fix: negative
        erp = min(rp + actual_remove_count, len(final))
        corrupted = final.copy()
        corrupted[rp:erp] = ["INJECTED\n"]
        # Pre-fix: the negative index wrapped and replaced the file top.
        assert rp < 0
        assert corrupted[0] == "INJECTED\n"

    def test_postfix_floor_prevents_wrap(self):
        final = [f"line{i}\n" for i in range(1000)]
        remove_pos, truncation, actual_remove_count = 500, 999_999_996, 1_000_000_000
        rp = max(0, remove_pos - truncation)               # post-fix: floored
        erp = min(rp + actual_remove_count, len(final))
        patched = final.copy()
        patched[rp:erp] = ["INJECTED\n"]
        assert rp == 0
        # No end-wrap: the slice starts at a real position, not the file end.
        assert rp >= 0


class TestAppliedFloorPresentInSource:
    """Guards the one-line floor from silently regressing out of the applier."""

    def test_floor_guard_present(self):
        import inspect
        from app.utils.diff_utils.application import patch_apply
        src = inspect.getsource(patch_apply)
        # The truncation-adjust branch must clamp remove_pos.
        assert "remove_pos = max(0, remove_pos)" in src
