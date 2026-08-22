"""
PDF-09b (over-wide table fit-scaling) structural guard.

A markdown table so wide its cells cannot wrap enough to fit was clipped by the
print layout: headless Chromium's ``page.pdf()`` does not scroll or scale a
table wider than the content margin, so its right-hand columns fall off the
margin and are dropped from the captured pages entirely (the rightmost cell
``WIDECELL_19`` of a 20-column table was absent from the extracted PDF text).

Card I rejected the two CSS remedies WITH EVIDENCE — ``table-layout:fixed``
crushes narrow tables (loses ``MRK_TBLCELL_5t2``, 4->6 pages) and cell
``word-break`` alone still overflows and shifts the leftmost cell.  The shipped
fix mirrors ``fitOversizedFigures``: a JS pass (``fitOverwideTables`` in
``frontend/src/components/PrintRenderPage.tsx``) zoom-scales ONLY a table whose
intrinsic (min-content) width exceeds the printable width by
``OVERWIDE_TABLE_OVERFLOW_RATIO`` (3.0x), so every column reflows within the
margin while narrow/mildly-wide tables — and the document's pagination — are
left untouched.

End-to-end proof lives in the audit (``check_wide_table_completeness`` /
adversarial ``wide_table`` recovering ``WIDECELL_19``); this is the browser-free
STRUCTURAL guard that the shipped source carries the mechanism (mutation-proven:
removing the branch or the guards flips the assertions).

The change is in a git-tracked ``.tsx``; the /print route serves from the build,
so end-to-end verification additionally requires a frontend rebuild.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRINT_PAGE = REPO / "frontend" / "src" / "components" / "PrintRenderPage.tsx"


def _source_without_comments() -> str:
    text = PRINT_PAGE.read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def test_fit_overwide_tables_function_exists():
    src = _source_without_comments()
    assert "function fitOverwideTables(" in src, (
        "the fitOverwideTables JS pass (PDF-09b remedy) is missing from PrintRenderPage.tsx"
    )


def test_overwide_table_is_invoked_in_readiness():
    src = _source_without_comments()
    # It must actually run in the readiness path (not merely defined).
    assert re.search(r"fitOverwideTables\(\s*node\s*\)", src), (
        "fitOverwideTables is defined but never called from finalizeReadiness — "
        "the fix would not take effect at capture time"
    )


def test_overwide_threshold_guards_narrow_tables():
    src = _source_without_comments()
    # A ratio threshold gates the scaling so narrow / mildly-wide tables (the
    # canonical fixture's ~1.9x table) are NOT scaled — the exact regression the
    # rejected CSS approaches caused.
    assert "OVERWIDE_TABLE_OVERFLOW_RATIO" in src, (
        "no overflow-ratio threshold constant — the pass would scale narrow "
        "tables too and repaginate the document (Card I's rejected regression)"
    )
    assert re.search(r"OVERWIDE_TABLE_OVERFLOW_RATIO\s*=\s*3(\.0)?\b", src), (
        "OVERWIDE_TABLE_OVERFLOW_RATIO must be 3.0 to separate the 5.4x "
        "adversarial table from the 1.9x canonical table"
    )
    # The trigger compares intrinsic width against maxW * ratio.
    assert re.search(r"maxW\s*\*\s*OVERWIDE_TABLE_OVERFLOW_RATIO", src)


def test_diff_tables_are_excluded_from_table_fit():
    src = _source_without_comments()
    # Diff tables carry .diff-table and flow/scroll on their own terms (NEW-3);
    # they must be excluded from zoom-scaling.
    assert "classList.contains('diff-table')" in src, (
        "fitOverwideTables must exclude diff tables (.diff-table) so it does not "
        "fight NEW-3's diff-table page-flow"
    )


def test_uses_zoom_not_transform_for_reflow():
    src = _source_without_comments()
    # zoom reflows the table's layout at the smaller size (so columns land
    # within the margin); a CSS transform would keep the layout width and still
    # clip in the PDF.
    assert re.search(r"setProperty\(\s*'zoom'", src), (
        "over-wide table scaling must use CSS `zoom` (which reflows layout), "
        "not a transform (which would still clip in the PDF)"
    )
