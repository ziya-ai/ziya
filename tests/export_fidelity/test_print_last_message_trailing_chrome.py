"""
PDF-07 (trailing chrome after the final message) guard.

Each message on the /print route carries an inline separator — a bottom
border, ``marginBottom`` and ``paddingBottom`` — that visually divides it from
the message BELOW it.  On the FINAL message nothing follows but the export
footer (or the document end), so that separator is pure trailing chrome: a
stray horizontal rule at the tail of the document and a small band of trailing
whitespace (a contributor to original defect #5, tracked as PDF-07).

The fix, in ``frontend/src/components/PrintRenderPage.tsx``, zeroes the
separator on the last message only: it computes ``isLastMessage`` from the map
index, tags the wrapper ``data-last-message="true"``, and sets
``marginBottom``/``paddingBottom`` to 0 with no ``borderBottom`` for that one
element while EARLIER messages keep the divider.  Because these are inline
styles (which outrank any author stylesheet), the fix must live in the TSX, not
in print.css.

End-to-end proof lives in the audit (rendered /print DOM: the last
``.print-message`` wrapper carries ``data-last-message="true"`` and
``margin-bottom: 0px; padding-bottom: 0px`` with no border, while the first
keeps ``border-bottom: 1px solid``; the 18-check baseline and 6 QUAL checks
stay green, page_count unchanged).  This is the browser-free STRUCTURAL guard
that the shipped source carries the mechanism (mutation-proven: reverting the
conditional flips these assertions).

The change is in a git-tracked ``.tsx``; the /print route serves from the
build, so end-to-end verification additionally requires a frontend rebuild.
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


def test_last_message_flag_computed():
    """The map computes whether a message is the last one, from the index."""
    src = _source_without_comments()
    assert "isLastMessage" in src, "isLastMessage discriminator missing"
    # It must be derived from the final index of the rendered message list.
    assert re.search(
        r"isLastMessage\s*=\s*i\s*===\s*filteredMessages\.length\s*-\s*1", src
    ), "isLastMessage is not computed as i === filteredMessages.length - 1"


def test_last_message_separator_zeroed():
    """The final message drops its bottom border and zeroes bottom spacing;
    earlier messages keep the 20/12/1px divider."""
    src = _source_without_comments()
    # margin/padding bottom are conditional on isLastMessage -> 0
    assert re.search(r"marginBottom:\s*isLastMessage\s*\?\s*0\s*:\s*20", src), (
        "marginBottom is not zeroed on the last message"
    )
    assert re.search(r"paddingBottom:\s*isLastMessage\s*\?\s*0\s*:\s*12", src), (
        "paddingBottom is not zeroed on the last message"
    )
    # the border is suppressed on the last message and retained otherwise
    assert re.search(
        r"borderBottom:\s*isLastMessage\s*\?\s*undefined\s*:\s*'1px solid", src
    ), "borderBottom is not suppressed on the last message"


def test_last_message_data_attr_marked():
    """The last wrapper is tagged so the rendered-DOM audit can locate it."""
    src = _source_without_comments()
    assert "data-last-message" in src, (
        "data-last-message marker (used by the end-to-end DOM proof) is missing"
    )
