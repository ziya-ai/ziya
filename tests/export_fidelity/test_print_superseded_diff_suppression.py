"""
NEW-2a (superseded-diff suppression in export) guard.

When the assistant emits a corrected diff for a file it already diffed earlier
in the same message, the earlier diff is SUPERSEDED.  In the live session the UI
merely fades it (``opacity: 0.45``) so the reader still sees the correction
history — but in an EXPORT that faded diff lands in the PDF as low-contrast
noise a reader cannot distinguish from real content, and it inflates page count
(user defect NEW-2a).

The fix, in ``frontend/src/components/MarkdownRenderer.tsx``, adds an
``isPrintExportMode()`` gate (keyed on ``body.ziya-print-mode``, the class the
/print route sets) and, when true, OMITS a superseded diff entirely instead of
fading it — at both the single-file ``DiffToken`` chokepoint and the per-section
``renderMultiFileDiff`` loop.  Fixing it in the shared /print page (rather than
the PDF driver) means Card II's HTML export inherits the fix automatically.

End-to-end proof lives in the audit (``check_no_superseded_diffs`` fail->pass on
``make_superseded_diff_conversation``: superseded_add 1->0, final_add kept,
page_count 2->1).  This is the browser-free STRUCTURAL guard that the shipped
source carries the mechanism.  The change is in a git-tracked ``.tsx``; the
/print route serves from the build, so end-to-end verification additionally
requires a frontend rebuild.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RENDERER = REPO / "frontend" / "src" / "components" / "MarkdownRenderer.tsx"


def _source_without_comments() -> str:
    text = RENDERER.read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def test_print_export_mode_helper_present():
    """A print-export detector keyed on the shared print class exists."""
    src = _source_without_comments()
    assert "isPrintExportMode" in src, (
        "isPrintExportMode() print-detector missing from MarkdownRenderer.tsx"
    )
    assert "ziya-print-mode" in src, (
        "the export detector must key on body.ziya-print-mode (the /print class)"
    )


def test_single_file_superseded_suppressed_in_print():
    """The single-file DiffToken chokepoint returns null for a superseded diff
    when exporting, rather than only fading it."""
    src = _source_without_comments()
    # The suppression must combine the superseded flag with the print gate.
    assert re.search(
        r"singleFileSuperseded\s*&&\s*isPrintExportMode\(\)", src
    ), "single-file superseded diff is not suppressed in print mode"


def test_multi_file_superseded_section_suppressed_in_print():
    """The per-section multi-file loop skips superseded sections when
    exporting."""
    src = _source_without_comments()
    assert re.search(
        r"supersededFileIndices\.has\(fileIndex\)\s*&&\s*isPrintExportMode\(\)",
        src,
    ), "multi-file superseded section is not suppressed in print mode"


def test_live_session_fade_preserved():
    """Suppression is EXPORT-ONLY: the live-session fade (opacity 0.45) must
    remain so the app still shows the correction history."""
    src = _source_without_comments()
    assert "0.45" in src, (
        "the live-session superseded fade (opacity 0.45) was removed — "
        "suppression must be gated on print mode, not applied globally"
    )
