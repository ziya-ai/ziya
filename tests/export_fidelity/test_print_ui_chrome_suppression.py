"""
NEW-2b (live-session UI-chrome suppression in export) guard.

The "Auto-added N file(s) to context (…) — available for subsequent queries.
Remove via the A button in the Files panel." banner (MarkdownRenderer's
``contextEnhancementOverlay``), the "⚠️ This diff references files not in
context: …" warning and the "🔄 Checking context..." spinner are live-session
affordances.  They are meaningful in the running app but are noise in an
exported document, and the user's screenshot showed the banner printed
(NEW-2b).

The fix is defence-in-depth in the SHARED /print page so Card II's HTML export
inherits it:

  1. ``frontend/src/components/PrintRenderPage.tsx`` ``applyOptions`` runs
     ``stripLiveSessionChrome`` over every message's content, removing the
     whole CLASS of chrome (regex list ``LIVE_SESSION_CHROME_PATTERNS``) so a
     PERSISTED banner never survives into the PDF/HTML.  This is the path the
     export-audit exercises (a fixture bakes the banner as message text).

  2. ``frontend/src/components/MarkdownRenderer.tsx`` gates the rendered
     ``contextEnhancementOverlay`` behind ``!isPrintExportMode()`` so the live
     component path cannot emit the overlay under /print either.

End-to-end proof lives in the audit (``check_no_ui_chrome`` fail->pass on
``make_ui_chrome_conversation``: leaked_substrings 4->0, answer kept,
page_count 1).  This is the browser-free STRUCTURAL guard that the shipped
source carries both halves of the mechanism.  The change is in git-tracked
files; the /print route serves from the build, so end-to-end verification
additionally requires a frontend rebuild.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRINT_PAGE = REPO / "frontend" / "src" / "components" / "PrintRenderPage.tsx"
RENDERER = REPO / "frontend" / "src" / "components" / "MarkdownRenderer.tsx"


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _print_page_src() -> str:
    return _strip_comments(PRINT_PAGE.read_text())


def _renderer_src() -> str:
    return _strip_comments(RENDERER.read_text())


def test_strip_helper_present_and_wired_in_apply_options():
    """A chrome-stripping helper exists and is invoked from applyOptions."""
    src = _print_page_src()
    assert "stripLiveSessionChrome" in src, (
        "stripLiveSessionChrome helper missing from PrintRenderPage.tsx"
    )
    assert "LIVE_SESSION_CHROME_PATTERNS" in src, (
        "chrome patterns must be a named CLASS list, not one inline banner match"
    )
    # It must actually run inside applyOptions (the shared option-filter path),
    # not be dead code.
    apply = src[src.index("function applyOptions"):]
    assert "stripLiveSessionChrome" in apply[: apply.index("return msgs;") + 12], (
        "stripLiveSessionChrome is not invoked inside applyOptions"
    )


def test_strip_covers_the_reported_banner_and_affordance():
    """The pattern list targets the specific user-reported strings."""
    src = _print_page_src()
    # The auto-added banner start and the "Remove via the A button" affordance.
    assert "Auto-added" in src, "auto-added banner pattern missing"
    assert "Files panel" in src, "Files-panel affordance pattern missing"
    assert "Checking context" in src, "checking-context spinner pattern missing"
    assert "references files not in context" in src, (
        "context-enhancement warning pattern missing"
    )


def test_overlay_gated_behind_print_mode_in_renderer():
    """The rendered contextEnhancementOverlay is suppressed under /print."""
    src = _renderer_src()
    assert "isPrintExportMode" in src
    # The overlay assignment must combine !isPrintExportMode() with the trigger.
    assert re.search(
        r"contextEnhancementOverlay\s*=\s*\(\s*!isPrintExportMode\(\)",
        src,
    ), "contextEnhancementOverlay is not gated behind !isPrintExportMode()"
