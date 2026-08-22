"""
Regression guard for PDF-01 (root cause: colour-fidelity safeguard for exports).

WHAT THIS DEFECT REALLY IS (verified empirically this iteration — see
``.ziya/task-runs/*/pdf01_empirical_findings.md``):

* On the PDF path, ``page.pdf(print_background=True)`` on the pinned Chromium
  ALREADY reproduces class-derived backgrounds exactly; ``print-color-adjust``
  is a no-op there (economy == exact, byte-for-byte in a controlled A/B render).
  So the diff/highlight/code-block/Prism colours already survive the PDF export
  (the live audit's ``colorfulness`` and ``expected_color_presence`` pass).

* The safeguard nonetheless MUST exist, because it is SHARED with Card II's HTML
  export.  ``extract_html()`` yields a standalone HTML document; when a user
  opens it and prints via the BROWSER PRINT DIALOG there is no
  ``print_background=True``, and Chromium's default ``print-color-adjust:
  economy`` drops class-derived backgrounds (diff fills, code-block fill, table
  zebra, highlight).  ``-webkit-print-color-adjust: exact`` / ``print-color-
  adjust: exact`` is what forces faithful colour there.  Putting it in the
  shared ``print.css`` (gated on ``body.ziya-print-mode``, NOT ``@media print``,
  because ``capture_pdf`` uses ``emulate_media('screen')``) is the correct
  cross-card seam.

This is a FAST, browser-free structural guard that the safeguard stays shipped
in the shared stylesheet, so a future edit that drops it trips a unit test.
It complements the raster checks (which prove the colours are actually painted
on the PDF path) rather than duplicating them.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PRINT_CSS = _ROOT / "frontend" / "src" / "styles" / "print.css"
_PRINT_PAGE = _ROOT / "frontend" / "src" / "components" / "PrintRenderPage.tsx"


def _read(p: Path) -> str:
    if not p.exists():
        pytest.skip(f"source not present in this checkout: {p}")
    return p.read_text(encoding="utf-8")


def test_print_css_declares_color_adjust_exact():
    """The shared print stylesheet must declare exact colour adjustment on the
    print-mode subtree, both the standard and -webkit- properties (the pinned
    Chromium recognises the -webkit- form)."""
    css = _read(_PRINT_CSS)
    assert re.search(r"-webkit-print-color-adjust:\s*exact", css), \
        "print.css must declare -webkit-print-color-adjust: exact"
    assert re.search(r"(?<!-)(?<!webkit-)print-color-adjust:\s*exact", css), \
        "print.css must declare the standard print-color-adjust: exact"


def test_color_adjust_is_scoped_to_print_mode_not_media_print():
    """The rule MUST be gated on body.ziya-print-mode and MUST NOT rely on an
    @media print block — capture_pdf emulates 'screen' media, so an @media
    print rule would never match on the PDF path, and the HTML consumer keeps
    the same class-scoped rule.  (A commented-out mention of @media print is
    fine; an actual `@media print {` block enclosing the colour rule is not.)"""
    css = _read(_PRINT_CSS)
    # Strip /* … */ comments first: the header prose legitimately MENTIONS
    # "@media print { … }" to explain why it is avoided, and that must not be
    # mistaken for a real block.
    css_no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    assert "body.ziya-print-mode" in css_no_comments, \
        "print.css must scope rules to body.ziya-print-mode"
    # The colour-adjust declaration must live inside a rule block whose selector
    # list contains body.ziya-print-mode.  Walk each `<selector> { <body> }`
    # block and require the one that declares print-color-adjust:exact to be
    # print-mode-scoped.  (Robust to the -webkit- alias appearing first.)
    block_re = re.compile(r"([^{}]*)\{([^{}]*)\}", re.MULTILINE)
    found_scoped = False
    for m in block_re.finditer(css_no_comments):
        selector, body = m.group(1), m.group(2)
        if "print-color-adjust: exact" in body:
            assert "ziya-print-mode" in selector, (
                "the print-color-adjust rule must be under a body.ziya-print-mode "
                f"selector; found selector block: {selector.strip()!r}"
            )
            found_scoped = True
    assert found_scoped, "no CSS block declares print-color-adjust: exact"
    # There must be no real @media print { ... } block (only prose mentions ok).
    assert not re.search(r"@media\s+print\s*\{", css_no_comments), \
        "print.css must not use an @media print block (screen media is emulated)"


def test_print_render_page_imports_the_shared_stylesheet():
    """PrintRenderPage (the shared /print route) must import print.css so the
    safeguard actually loads for both the PDF and HTML consumers."""
    src = _read(_PRINT_PAGE)
    assert re.search(r"import\s+['\"][^'\"]*styles/print\.css['\"]", src), \
        "PrintRenderPage must import ../styles/print.css"
