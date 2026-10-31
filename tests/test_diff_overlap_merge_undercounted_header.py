"""
Overlap merge must size the merged old region by hunk BODIES, not headers.

Follow-up to tests/test_diff_countless_header_backtick_escape.py: the live
fallthrough path (patch and git apply both failed) hands the difflib stage the
ORIGINAL diff text, so the model's header counts reach it unrecounted.  The
parser repairs an undercount only when it is 2+ lines AND both sides are
under; a one-line undercount, or an undercount on one side with an overcount
on the other, keeps the raw header count.

merge_overlapping_hunks computed the merged old region's end from the
header count of the hunk that started last.  The splice loop beneath it
replaces len(old_block) lines per hunk.  When a body ran past its declared
count, the splice extended the region instead of replacing within it, and
every line past the declared end came out twice.  The stage reported
success and a re-apply reported "already applied", so the streaming verifier
could not see it either.

Reproduced shape: hunk A over-declares its range (the truncation shape, so it
"overlaps" B), hunk B under-declares by one.
"""
import os

import pytest

from app.utils.diff_utils.application.overlapping_hunks_fix import fix_overlapping_hunks
from app.utils.diff_utils.parsing.diff_parser import parse_unified_diff_exact_plus
from app.utils.diff_utils.pipeline.diff_pipeline import DiffPipeline
from app.utils.diff_utils.pipeline.pipeline_manager import apply_diff_pipeline, run_difflib_stage

L = [f"line {i:02d} body text here" for i in range(1, 41)]
CONTENT = "\n".join(L) + "\n"
EXPECTED = "\n".join(L[:4] + ["ADDED A1"] + L[4:6] + ["MERGED 7-8"] + L[8:15]
                     + ["REPLACED 16"] + L[16:]) + "\n"
HDR = "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n"


def _hunk_a(header):  # lines 3-10: old 8 / new 8
    return (f"{header}\n {L[2]}\n {L[3]}\n+ADDED A1\n {L[4]}\n {L[5]}\n"
            f"-{L[6]}\n-{L[7]}\n+MERGED 7-8\n {L[8]}\n {L[9]}\n")


def _hunk_b(header):  # lines 14-19: old 6 / new 6
    return (f"{header}\n {L[13]}\n {L[14]}\n-{L[15]}\n+REPLACED 16\n"
            f" {L[16]}\n {L[17]}\n {L[18]}\n")


# A declares 14 old lines for an 8-line body (3..16, so it overlaps B at 14);
# B declares 5 for a 6-line body (14..18, but the body runs to 19).
DIFF = HDR + _hunk_a("@@ -3,14 +3,14 @@") + _hunk_b("@@ -14,5 +14,5 @@")


def _run_raw_difflib(path, diff):
    """Drive the stage the way the live fallthrough does: raw diff text."""
    pipeline = DiffPipeline(path, diff)
    pipeline.initialize_hunks(list(parse_unified_diff_exact_plus(diff, path)))
    ok = run_difflib_stage(pipeline, path, diff, open(path).read().splitlines(True))
    return ok, {h: t.status.value for h, t in pipeline.result.hunks.items()}


def test_merged_region_covers_every_body(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text(CONTENT)
    hunks = parse_unified_diff_exact_plus(DIFF, str(p))
    assert [h["old_count"] for h in hunks] == [14, 5]        # premise: raw counts survive
    merged = fix_overlapping_hunks(hunks, CONTENT)
    assert len(merged) == 1                                   # premise: they do merge
    m = merged[0]
    assert m["old_start"] + m["old_count"] - 1 == 19          # B's body ends at line 19
    # Both hunks are line-count neutral, so old and new regions are 17 each.
    assert len(m["old_block"]) == m["old_count"] == len(m["new_lines"]) == 17
    assert m["new_lines"].count(L[18]) == 1


def test_fallthrough_difflib_stage_does_not_duplicate_tail(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text(CONTENT)
    ok, statuses = _run_raw_difflib(str(p), DIFF)
    assert ok, statuses
    after = p.read_text()
    assert after == EXPECTED
    assert after.count(L[18]) == 1                            # the duplicated line
    # Positive: the stage ran and both hunks were applied by it.
    assert set(statuses.values()) == {"succeeded"}


def test_reapply_after_correct_merge_is_already_applied(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text(CONTENT)
    _run_raw_difflib(str(p), DIFF)
    ok, statuses = _run_raw_difflib(str(p), DIFF)
    assert set(statuses.values()) == {"already_applied"}, statuses
    assert p.read_text() == EXPECTED


def test_full_pipeline_end_to_end(tmp_path):
    # Outermost surface.  Note what this does and does not certify: the
    # pipeline's preprocess pass recounts headers on this route, so the merge
    # sees correct counts here and this passed before the fix.  It pins the
    # recounted route; the three tests above pin the raw (live fallthrough)
    # route, which is the one that was broken.
    os.environ["ZIYA_FORCE_DIFFLIB"] = "1"
    try:
        p = tmp_path / "f.txt"
        p.write_text(CONTENT)
        r = apply_diff_pipeline(DIFF, str(p), user_codebase_dir=str(tmp_path))
        assert r["status"] == "success", r
        assert p.read_text() == EXPECTED
    finally:
        os.environ.pop("ZIYA_FORCE_DIFFLIB", None)
