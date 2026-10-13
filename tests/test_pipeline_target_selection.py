"""A combined diff naming two same-basename files in different directories.

``apply_diff_pipeline`` picks the block(s) for its target from a combined
diff.  It used to accept a basename match alongside an exact one, so
``app/config/write_policy.py`` and ``app/mcp_servers/write_policy.py`` in one
diff were merged and both applied to the target, writing the other file's
hunks into it.  ``select_diffs_for_target`` now matches in tiers: exact
path, path suffix, then basename only as a fallback.

The end-to-end test asserts on the files on disk: the target gets its own
hunk, and the sibling's hunk is in neither file.
"""

import os

import pytest

from app.utils.diff_utils.parsing.diff_parser import split_combined_diff
from app.utils.diff_utils.pipeline.pipeline_manager import (
    apply_diff_pipeline, select_diffs_for_target)


def _block(path, old, new):
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1,3 +1,3 @@\n line one\n-{old}\n+{new}\n line three\n")


A = "app/config/write_policy.py"
B = "app/mcp_servers/write_policy.py"
COMBINED = _block(A, "config two", "CONFIG CHANGED") + _block(B, "shell two", "SHELL CHANGED")


def _targets(blocks):
    return ["CONFIG CHANGED" in b for b in blocks]


class TestSelection:
    def test_combined_diff_splits_into_two(self):
        assert len(split_combined_diff(COMBINED)) == 2

    def test_exact_path_selects_only_its_block(self):
        got = select_diffs_for_target(split_combined_diff(COMBINED), A)
        assert _targets(got) == [True]

    def test_absolute_target_matches_by_suffix_only(self):
        got = select_diffs_for_target(split_combined_diff(COMBINED), "/proj/" + B)
        assert len(got) == 1 and "SHELL CHANGED" in got[0]

    def test_basename_is_a_fallback(self):
        # No block names a compatible path, so the basename match is used.
        got = select_diffs_for_target(split_combined_diff(COMBINED), "other/dir/write_policy.py")
        assert len(got) == 2

    def test_two_blocks_for_the_same_file_still_merge(self):
        twice = _block(A, "config two", "X") + _block(B, "shell two", "Y") + _block(A, "line three", "Z")
        got = select_diffs_for_target(split_combined_diff(twice), A)
        assert len(got) == 2 and all(A in b for b in got)

    def test_bare_basename_block_still_matches(self):
        got = select_diffs_for_target(
            split_combined_diff(_block("write_policy.py", "a", "b") + _block("x.py", "c", "d")), A)
        assert len(got) == 1 and "write_policy.py" in got[0]


@pytest.fixture
def proj(tmp_path):
    for rel, mid in ((A, "config two"), (B, "shell two")):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"line one\n{mid}\nline three\n")
    return tmp_path


def test_pipeline_applies_only_the_target_block(proj):
    result = apply_diff_pipeline(COMBINED, str(proj / A), request_id="sel-1",
                                 user_codebase_dir=str(proj))
    a = (proj / A).read_text()
    b = (proj / B).read_text()
    assert "CONFIG CHANGED" in a, result
    assert "SHELL CHANGED" not in a
    assert b == "line one\nshell two\nline three\n"
