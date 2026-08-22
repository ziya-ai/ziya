"""
NEW-3 (diff header/body binding + diff-table flow) guard.

A diff renders as ``div.diff-view-controls`` (the 'Modify: <path>' header)
sitting directly above ``div.diff-container`` which holds ``table.diff-table``.

Two print.css defects produced the user's "empty band before a wrapped diff":

  1. The atomic-block rule listed a BARE ``body.ziya-print-mode table`` selector,
     which also caught every diff (``table.diff-table``) and forced a page-tall
     diff whole onto a fresh page — leaving a large blank band after its header,
     contradicting that rule block's own stated intent.  The fix narrows the
     selector to ``table:not(.diff-table)`` so genuine content tables stay
     atomic while diff tables FLOW across the page boundary.

  2. Nothing bound the header to its body, so the header could strand at a page
     bottom with the body overleaf.  The fix adds ``break-after: avoid`` on
     ``.diff-view-controls`` so the header travels to the next page with the
     first rows of its body.

Both fixes live in ``frontend/src/styles/print.css`` (directly writable this
card) and are scoped to ``body.ziya-print-mode`` (NOT ``@media print`` —
capture_pdf emulates screen media).  End-to-end proof lives in the audit
(``check_diff_header_binding`` fail->pass on ``make_header_binding_conversation``:
header/body same_page False->True).  This is the browser-free STRUCTURAL guard
that the shipped stylesheet carries the mechanism.  The /print route serves from
the build, so end-to-end verification additionally requires a frontend rebuild.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRINT_CSS = REPO / "frontend" / "src" / "styles" / "print.css"


def _source_without_comments() -> str:
    text = PRINT_CSS.read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text


def test_print_css_exists():
    assert PRINT_CSS.exists(), f"expected print.css at {PRINT_CSS}"


def test_diff_tables_excluded_from_atomic_break_rule():
    """The atomic-block rule must NOT hold diff tables whole: a bare ``table``
    selector is forbidden; diff tables are excluded via ``table:not(.diff-table)``."""
    src = _source_without_comments()
    # The narrowed selector must be present...
    assert re.search(r"body\.ziya-print-mode\s+table:not\(\.diff-table\)", src), (
        "atomic-block rule must scope to table:not(.diff-table) so diff tables flow"
    )
    # ...and a BARE ``body.ziya-print-mode table`` selector (no :not) must NOT
    # remain, or it would re-capture every diff table and reintroduce the band.
    assert not re.search(
        r"body\.ziya-print-mode\s+table\s*(?:,|\{)", src
    ), "a bare 'body.ziya-print-mode table' selector still catches diff tables (NEW-3 regression)"


def test_diff_header_bound_to_body():
    """The diff header (.diff-view-controls) must bind to the body that follows
    it via break-after: avoid so it cannot strand at a page bottom."""
    src = _source_without_comments()
    m = re.search(
        r"body\.ziya-print-mode\s+\.diff-view-controls\s*\{([^}]*)\}", src
    )
    assert m, ".diff-view-controls print rule missing (header/body binding)"
    body = m.group(1)
    assert "break-after: avoid" in body, (
        ".diff-view-controls must set break-after: avoid to bind the header to its body"
    )
    assert "page-break-after: avoid" in body, (
        ".diff-view-controls must also set the legacy page-break-after alias"
    )


def test_binding_rules_scoped_to_print_class_not_media_print():
    """All the NEW-3 rules must be scoped to body.ziya-print-mode, never under
    @media print (capture_pdf emulates screen media, so @media print is inert).

    Checked against the comment-stripped source, since the header comment
    legitimately *explains* why @media print is not used."""
    src = _source_without_comments()
    assert "@media print" not in src, (
        "print.css must not use @media print (capture_pdf uses emulate_media('screen'))"
    )
