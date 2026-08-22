"""
Regression guard for PDF-06 (root cause: light theme must be forced
deterministically for the shared /print render, resilient to the two
NON-REACTIVE theme vectors a re-render cannot fix).

WHAT THIS DEFECT REALLY IS (verified empirically this iteration — see
``.ziya/task-runs/*/issue-PDF-06/{probe_report,colorscheme_probe_report}.json``):

* The user report (#6, "dark-mode content not converted to light when
  composited onto the white page") does NOT reproduce on the current PDF path.
  Two controlled A/B renders proved it:
    - seeding ``localStorage['ZIYA_THEME_PREFERENCE']='true'`` (a dark-mode
      user) produced BYTE-IDENTICAL output to the light control, with ZERO
      dark-ish inline ``backgroundColor`` styles in the DOM — because
      ``page.pdf()`` captures the FINAL settled render, by which point every
      ``isDarkMode ? darkColor : lightColor`` inline style has reactively
      recomputed to light (D3Renderer re-renders on theme change; the lazily
      mounted MarkdownRenderer mounts AFTER the theme is forced).
    - forcing Chromium's emulated ``prefers-color-scheme: dark`` (matchMedia
      dark == true) ALSO produced byte-identical dark_fraction to the light
      baseline, because no CSS that applies to the conversation DOM keys off
      ``prefers-color-scheme``.
  So, like PDF-01/PDF-02, PDF-06 is a LATENT defect on the PDF path.

* The hardening is nonetheless correct and SHARED, closing the two vectors that
  a React re-render genuinely CANNOT fix and that a non-page.pdf() consumer
  (Card II may screenshot ``extract_html()`` before the render settles) is
  exposed to:
    1. Forcing light in a PASSIVE post-paint ``useEffect`` relies on lazy-load
       timing; ``useLayoutEffect`` runs synchronously after DOM mutation and
       BEFORE the browser paints, so a dark-preference user never yields a
       capturable dark frame — deterministic for EVERY shared-route consumer.
    2. ``prefers-color-scheme`` is non-reactive to app state; the shared render
       session emulates ``color_scheme='light'`` before rendering so a dark
       host/OS/CI colour scheme cannot paint dark onto the export.

This is a FAST, browser-free structural guard that both halves of the hardening
stay shipped in the shared /print path, complementing the raster checks
(background_whiteness / dark_theme_leak) that prove the page is actually light.
A future edit that reverts either half trips a unit test.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PRINT_PAGE = _ROOT / "frontend" / "src" / "components" / "PrintRenderPage.tsx"
_PDF_EXPORTER = _ROOT / "app" / "services" / "pdf_exporter.py"


def _read(p: Path) -> str:
    if not p.exists():
        pytest.skip(f"source not present in this checkout: {p}")
    return p.read_text(encoding="utf-8")


def _strip_ts_comments(src: str) -> str:
    """Remove /* … */ and // … comments so prose that MENTIONS a construct is
    not mistaken for real code."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"(?m)//.*$", "", src)
    return src


def _strip_py_comments(src: str) -> str:
    return re.sub(r"(?m)#.*$", "", src)


def test_print_page_forces_light_theme_pre_paint_via_layout_effect():
    """The light-theme forcing (``setTheme('light')``) MUST live inside a
    ``useLayoutEffect`` (synchronous, pre-paint) rather than a passive
    ``useEffect``, so a dark-preference user never yields a capturable dark
    frame for a non-page.pdf() consumer.

    Mutation proof: reverting the block to ``useEffect(() => { setTheme('light')``
    makes this assertion fail.
    """
    src = _strip_ts_comments(_read(_PRINT_PAGE))
    # useLayoutEffect must be imported and used to wrap the setTheme('light').
    assert re.search(r"\buseLayoutEffect\b", src), \
        "PrintRenderPage must import/use useLayoutEffect for pre-paint theme forcing"
    # The setTheme('light') call must be inside a useLayoutEffect callback, not a
    # plain useEffect.  Find the effect hook that contains setTheme('light') and
    # require it to be useLayoutEffect.  Scan each `useEffect|useLayoutEffect(()
    # => { ... setTheme('light') ... }` opening.
    m = re.search(r"setTheme\(\s*['\"]light['\"]\s*\)", src)
    assert m, "PrintRenderPage must call setTheme('light')"
    # Look backwards from the setTheme('light') to the nearest effect-hook opener.
    preceding = src[: m.start()]
    hook_iter = list(re.finditer(r"\b(useLayoutEffect|useEffect)\s*\(\s*\(\s*\)\s*=>", preceding))
    assert hook_iter, "setTheme('light') must be inside an effect hook"
    nearest_hook = hook_iter[-1].group(1)
    assert nearest_hook == "useLayoutEffect", (
        "setTheme('light') must run in a useLayoutEffect (pre-paint), not a "
        f"passive useEffect; nearest enclosing hook was {nearest_hook!r}"
    )


def test_render_session_emulates_light_color_scheme():
    """The shared render session (_open_and_render, used by BOTH capture_pdf and
    extract_html) MUST emulate a light ``color_scheme`` so a non-reactive
    ``@media (prefers-color-scheme: dark)`` rule inherited from a dark host
    cannot paint dark onto the export.

    Mutation proof: removing the ``emulate_media(color_scheme="light")`` call
    makes this assertion fail.
    """
    src = _strip_py_comments(_read(_PDF_EXPORTER))
    assert re.search(r"emulate_media\([^)]*color_scheme\s*=\s*['\"]light['\"]", src), \
        ("pdf_exporter must call page.emulate_media(color_scheme='light') in the "
         "shared render path so prefers-color-scheme dark cannot leak")


def test_color_scheme_emulation_is_in_shared_open_and_render():
    """The light color-scheme emulation must be in the SHARED _open_and_render
    (before both capture_pdf and extract_html), not in the PDF-only capture_pdf,
    so Card II's HTML export inherits it too."""
    src = _strip_py_comments(_read(_PDF_EXPORTER))
    # Locate the _open_and_render function body and assert the emulation is in it.
    m = re.search(r"async def _open_and_render\(.*?(?=\n    async def |\n    def |\Z)",
                  src, flags=re.DOTALL)
    assert m, "could not locate _open_and_render in pdf_exporter"
    body = m.group(0)
    assert re.search(r"emulate_media\([^)]*color_scheme\s*=\s*['\"]light['\"]", body), (
        "the light color_scheme emulation must live in the SHARED _open_and_render "
        "(so extract_html inherits it), not only in capture_pdf"
    )
