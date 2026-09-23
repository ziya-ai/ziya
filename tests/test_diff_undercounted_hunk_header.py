"""
Unit coverage for the two fixes behind the 2026-09-23 kimi-k3 half-apply.
End-to-end coverage lives in tests/diff_test_cases/undercounted_hunk_header_*;
these tests pin the unit contracts, including the deliberate limits.

Incident shape: a single hunk with header "@@ -N,6 +N,9 @@" over a body of
13 old / 16 new lines (3 ctx, 3 add, 7 ctx, 3 remove, 3 add). Two defects:

1. git apply parses exactly the declared counts and treats the rest as
   trailing garbage, exit 0 -> the pipeline reported success while the
   replacement was silently dropped.  Fix: _repair_undercounted_hunk_headers,
   a pure, one-directional recount applied to the text handed to git.

2. On re-apply against the half-applied file, _apply_changes_by_structure
   rebuilt only the span between the first and last CONTEXT anchor, so the
   trailing '-' lines (after the last context line) were never consumed ->
   '+' lines inserted, '-' lines kept, duplicate dict keys.  Fix: widen the
   region over leading/trailing removal runs when the file holds them
   adjacent to the anchors; bail (None) otherwise so the caller's
   already-applied guard decides.
"""

import pytest

from app.utils.diff_utils.application.git_diff import (
    _repair_undercounted_hunk_headers,
    normalize_patch_with_whatthepatch,
    sanitize_patch_for_git_apply,
)
from app.utils.diff_utils.application.patch_apply import (
    _apply_changes_by_structure,
    apply_surgical_changes_by_content,
)
from app.utils.diff_utils.parsing.diff_parser import parse_unified_diff_exact_plus


HEADER = [
    "diff --git a/f.py b/f.py",
    "--- a/f.py",
    "+++ b/f.py",
]

# 3 ctx, 3 add, 7 ctx, 3 rem, 3 add  ->  old 13 / new 16
BODY = (
    [" c1", " c2", " c3"]
    + ["+a1", "+a2", "+a3"]
    + [" c4", " c5", " c6", " c7", " c8", " c9", " c10"]
    + ["-o1", "-o2", "-o3"]
    + ["+n1", "+n2", "+n3"]
)


def _hdrs(lines):
    return [l for l in lines if l.startswith("@@")]


class TestRepairUndercountedHunkHeaders:
    def test_undercounted_header_is_rewritten_to_body_counts(self):
        out = _repair_undercounted_hunk_headers(HEADER + ["@@ -21,6 +21,9 @@"] + BODY)
        assert _hdrs(out) == ["@@ -21,13 +21,16 @@"]
        # Body must be byte-identical: this is a header-only repair.
        assert out[len(HEADER) + 1:] == BODY

    def test_correct_header_is_untouched(self):
        lines = HEADER + ["@@ -21,13 +21,16 @@ def ctx():"] + BODY
        assert _repair_undercounted_hunk_headers(lines) == lines

    def test_overcounted_header_is_left_alone(self):
        # Truncated hunks (declared > body) are git-rejected loudly and handled
        # by the difflib fallback; recounting them changes matching behaviour
        # the regression suite depends on (prompts_manager_log_level_change).
        lines = HEADER + ["@@ -49,7 +49,7 @@", " ", "-old", "+new"]
        assert _repair_undercounted_hunk_headers(lines) == lines

    def test_mixed_direction_raises_only_the_undercounted_side(self):
        # old declared too high (7 > 3) but new declared too low (1 < 4):
        # only the new side is raised; the old side is preserved.
        lines = HEADER + ["@@ -1,7 +1,1 @@", " c1", " c2", " c3", "+a1"]
        assert _hdrs(_repair_undercounted_hunk_headers(lines)) == ["@@ -1,7 +1,4 @@"]

    def test_blank_context_without_leading_space_counts_as_context(self):
        # Models often emit blank context lines as '' rather than ' '. git
        # accepts them; they must be counted or the header undercounts again.
        lines = HEADER + ["@@ -1,1 +1,1 @@", " c1", "", "-o", "+n"]
        assert _hdrs(_repair_undercounted_hunk_headers(lines)) == ["@@ -1,3 +1,3 @@"]

    def test_multi_hunk_only_touches_the_undercounted_one(self):
        lines = (
            HEADER
            + ["@@ -1,2 +1,3 @@", " c1", " c2", "+a"]          # correct
            + ["@@ -10,1 +11,1 @@", " c3", "-o", "+n", " c4"]  # undercounted
        )
        assert _hdrs(_repair_undercounted_hunk_headers(lines)) == [
            "@@ -1,2 +1,3 @@",
            "@@ -10,3 +11,3 @@",
        ]

    def test_header_suffix_and_count_less_form_preserved(self):
        # "-N +M" (no count) means 1; suffix after @@ must survive the rewrite.
        lines = HEADER + ["@@ -5 +5 @@ def foo():", " c", "-o", "+n"]
        assert _hdrs(_repair_undercounted_hunk_headers(lines)) == ["@@ -5,2 +5,2 @@ def foo():"]

    def test_hunk_terminated_by_next_file_header(self):
        lines = (
            HEADER + ["@@ -1,1 +1,1 @@", " c", "-o", "+n"]
            + ["diff --git a/g.py b/g.py", "--- a/g.py", "+++ b/g.py", "@@ -1,1 +1,1 @@", " x"]
        )
        out = _repair_undercounted_hunk_headers(lines)
        assert _hdrs(out) == ["@@ -1,2 +1,2 @@", "@@ -1,1 +1,1 @@"]


class TestRepairReachesGit:
    """The repair must sit on every path into git apply."""

    def test_sanitize_path(self):
        text = "\n".join(HEADER + ["@@ -21,6 +21,9 @@"] + BODY) + "\n"
        assert "@@ -21,13 +21,16 @@" in sanitize_patch_for_git_apply(text)

    def test_normalize_default_path(self):
        text = "\n".join(HEADER + ["@@ -21,6 +21,9 @@"] + BODY) + "\n"
        assert "@@ -21,13 +21,16 @@" in normalize_patch_with_whatthepatch(text)

    def test_normalize_whatthepatch_failure_path(self, monkeypatch):
        # Force the ValueError branch (handle_embedded_diff_markers) and make
        # sure the repair still runs there.
        import whatthepatch

        def boom(_):
            raise ValueError("forced")

        monkeypatch.setattr(whatthepatch, "parse_patch", boom)
        text = "\n".join(HEADER + ["@@ -21,6 +21,9 @@"] + BODY) + "\n"
        out = normalize_patch_with_whatthepatch(text)
        assert "@@ -21,13 +21,16 @@" in out
        # Body must survive intact on this path too.
        assert out.rstrip("\n").splitlines()[-len(BODY):] == BODY


# ---------------------------------------------------------------------------
# _apply_changes_by_structure: removals outside the context-anchor span
# ---------------------------------------------------------------------------

def _hunk(diff_text):
    return list(parse_unified_diff_exact_plus(diff_text, "f.py"))[0]


TRAILING_REPLACE_DIFF = "\n".join(
    HEADER
    + ["@@ -1,7 +1,8 @@"]
    + [" k1", " k2", "+ins", " k3", " k4", "-old1", "-old2", "+new1", "+new2"]
) + "\n"


def _lines(*ls):
    return [l + "\n" for l in ls]


class TestStructureApplyConsumesTrailingRemovals:
    def test_trailing_removals_after_last_context_are_replaced(self):
        # Half-applied state: 'ins' already present, old1/old2 still there.
        # Context lines k1..k4 are all present, so this is the
        # straddling-addition shape that routes into _apply_changes_by_structure.
        original = _lines("k1", "k2", "ins", "k3", "k4", "old1", "old2", "tail")
        h = _hunk(TRAILING_REPLACE_DIFF)
        out = _apply_changes_by_structure(original, h, 0)
        assert out == _lines("k1", "k2", "ins", "k3", "k4", "new1", "new2", "tail"), out
        # The seam the incident slipped through: the old lines must be GONE.
        assert "old1\n" not in out and "old2\n" not in out

    def test_bails_when_trailing_removals_already_gone(self):
        # Fully-applied state: re-apply must NOT duplicate new1/new2. The
        # function declines (None) so the caller's already-applied guard runs.
        original = _lines("k1", "k2", "ins", "k3", "k4", "new1", "new2", "tail")
        h = _hunk(TRAILING_REPLACE_DIFF)
        assert _apply_changes_by_structure(original, h, 0) is None

    def test_bails_when_trailing_removals_not_adjacent(self):
        # File drifted: something sits between the last anchor and the old
        # lines. Guessing here is how additive duplicates happen; decline.
        original = _lines("k1", "k2", "ins", "k3", "k4", "stray", "old1", "old2")
        h = _hunk(TRAILING_REPLACE_DIFF)
        assert _apply_changes_by_structure(original, h, 0) is None

    def test_leading_removals_before_first_context_are_replaced(self):
        diff = "\n".join(
            HEADER
            + ["@@ -1,6 +1,6 @@"]
            + ["-old0", "+new0", " k1", " k2", "+ins", " k3", " k4"]
        ) + "\n"
        original = _lines("old0", "k1", "k2", "ins", "k3", "k4")
        out = _apply_changes_by_structure(original, _hunk(diff), 1)
        assert out == _lines("new0", "k1", "k2", "ins", "k3", "k4"), out

    def test_removals_between_anchors_unchanged_behaviour(self):
        # Removals inside the anchor span were already handled by the region
        # rebuild; this pins that the new logic does not disturb them.
        diff = "\n".join(
            HEADER
            + ["@@ -1,6 +1,6 @@"]
            + [" k1", "+ins", " k2", "-mid", "+midnew", " k3"]
        ) + "\n"
        original = _lines("k1", "ins", "k2", "mid", "k3")
        out = _apply_changes_by_structure(original, _hunk(diff), 0)
        assert out == _lines("k1", "ins", "k2", "midnew", "k3"), out

    def test_entry_point_produces_the_replacement(self):
        # Through the public wrapper the caller actually uses.
        original = _lines("k1", "k2", "ins", "k3", "k4", "old1", "old2", "tail")
        out = apply_surgical_changes_by_content(original, _hunk(TRAILING_REPLACE_DIFF), 0)
        assert out == _lines("k1", "k2", "ins", "k3", "k4", "new1", "new2", "tail"), out
