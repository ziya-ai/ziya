"""
Regression guard for PDF-08 (root cause #1): the /print route MUST opt out of
the single-viewport app-chrome clamp so a whole conversation flows to its
natural height and paginates, instead of being clipped to ONE A4 page.

The mechanism of the defect was:
  * ``frontend/src/index.css`` pins every page to one viewport:
        body { overflow: hidden; height: 100vh; position: fixed; }
    so the app chrome never scrolls.
  * The pre-existing escape hatch ``body.allow-scroll`` (used by /debug, /info)
    only released ``overflow`` and ``position`` — NOT ``height: 100vh`` — so it
    was insufficient for a print/export route that must be content-height tall.
  * ``PrintRenderPage`` (mounted at /print) never opted out at all, so
    Chromium's ``page.pdf()`` captured only the first viewport-height box and
    silently dropped ~half the conversation (page_count == 1).

The behavioural fix is proven end-to-end by the fidelity harness
(``content_completeness`` + ``text_extractability`` go fail->pass, page_count
1 -> 5).  This module is the FAST, browser-free structural guard that the two
halves of the fix stay in place, so a future edit that re-clamps the print
route trips a unit test long before the multi-minute audit.

SHARED note: the ``ziya-print-mode`` seam lives on the shared /print route, so
Card II's ``extract_html()`` consumer inherits the same full-height DOM.  These
assertions are format-neutral (they never render a PDF).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_INDEX_CSS = _ROOT / "frontend" / "src" / "index.css"
_PRINT_PAGE = _ROOT / "frontend" / "src" / "components" / "PrintRenderPage.tsx"


def _read(p: Path) -> str:
    if not p.exists():
        pytest.skip(f"source not present in this checkout: {p}")
    return p.read_text(encoding="utf-8")


def _rule_body(css: str, selector: str) -> str:
    """Return the declaration block for a top-level CSS rule whose selector
    list contains ``selector`` (naive but adequate for these hand-written
    rules)."""
    # Match `<...selector...> { ... }` allowing the selector to be part of a
    # comma list; capture the first block.
    pattern = re.compile(
        r"(^|})\s*([^{}]*\b" + re.escape(selector) + r"\b[^{}]*)\{([^{}]*)\}",
        re.MULTILINE,
    )
    m = pattern.search(css)
    assert m, f"CSS rule for selector {selector!r} not found"
    return m.group(3)


def test_index_css_still_has_the_app_viewport_clamp():
    """Sanity anchor: the clamp we are opting OUT of must still exist, else the
    escape hatch below would be meaningless (and this test stale)."""
    css = _read(_INDEX_CSS)
    body = _rule_body(css, "body")  # first `body { ... }` block is the base rule
    assert "height: 100vh" in body, "base body no longer pins height:100vh"
    assert "overflow: hidden" in body, "base body no longer sets overflow:hidden"
    assert "position: fixed" in body, "base body no longer sets position:fixed"


def test_print_mode_body_rule_releases_the_full_clamp():
    """The shared print-mode body rule must release ALL THREE clamp properties
    — crucially ``height`` (the one ``allow-scroll`` forgot), which is what
    made ``page.pdf()`` clip to a single viewport."""
    css = _read(_INDEX_CSS)
    block = _rule_body(css, "body.ziya-print-mode")
    # height clamp released -> content can grow taller than one viewport
    assert re.search(r"height:\s*auto", block), \
        "body.ziya-print-mode must set height:auto (release the 100vh clamp)"
    # overflow + position released so nothing re-clips / re-pins the flow
    assert re.search(r"overflow:\s*(visible|auto)", block), \
        "body.ziya-print-mode must release overflow (visible/auto)"
    assert re.search(r"position:\s*static", block), \
        "body.ziya-print-mode must release position (static)"


def test_print_render_page_opts_the_route_out_of_the_clamp():
    """PrintRenderPage must actively opt the route out of the clamp (add the
    print-mode class AND null the inline clamp props), not merely paint white."""
    src = _read(_PRINT_PAGE)
    # applies the shared class
    assert "ziya-print-mode" in src, \
        "PrintRenderPage must add the shared body.ziya-print-mode class"
    # belt-and-suspenders inline release (guards against prebuilt-CSS ordering)
    assert re.search(r"body\.style\.height\s*=\s*['\"]auto['\"]", src), \
        "PrintRenderPage must set body height:auto inline"
    assert re.search(r"body\.style\.overflow\s*=\s*['\"]visible['\"]", src), \
        "PrintRenderPage must set body overflow:visible inline"
    assert re.search(r"body\.style\.position\s*=\s*['\"]static['\"]", src), \
        "PrintRenderPage must set body position:static inline"


def test_print_render_page_restores_clamp_on_unmount():
    """The opt-out must be reversible: mounting /print then navigating away in
    the same tab must not leave the whole app permanently scrollable.  We check
    the cleanup restores the saved inline values."""
    src = _read(_PRINT_PAGE)
    assert re.search(r"classList\.remove\(\s*['\"]ziya-print-mode['\"]\s*\)", src), \
        "PrintRenderPage effect cleanup must remove the ziya-print-mode class"
    # saved-and-restored pattern for the inline props
    assert "priorBody" in src and "priorHtml" in src, \
        "PrintRenderPage must snapshot prior inline styles for restoration"
