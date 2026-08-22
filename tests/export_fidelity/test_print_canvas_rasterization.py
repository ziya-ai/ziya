"""
Regression guard for PDF-05 (root cause: a live ``<canvas>`` renderer output
must be rasterized to an inline ``<img>`` in the SHARED /print readiness path so
its pixels survive BOTH capture backends).

WHAT THIS DEFECT REALLY IS (verified this iteration — see
``.ziya/task-runs/*/issue-PDF-05/``):

* The user report (#2, "renderer images not visible") is currently NON-REPRO on
  the real pipeline for the sources that DO exist: every Ziya renderer emits
  inline ``<svg>`` — mermaid/D3 through D3Renderer, and the VexFlow music
  renderer explicitly uses ``Renderer.Backends.SVG`` (musicPlugin.ts:2017, with
  a comment noting that the CANVAS backend would throw for a ``<div>``
  container).  A raw ``<canvas>`` authored in a markdown message is HTML-escaped
  by MarkdownRenderer's inline-HTML guard (same as raw ``<mark>``), so it never
  becomes a live canvas either.  The ``image_presence`` check therefore PASSES
  on the fixture, but only exercises the inline-SVG path.  So, like
  PDF-01/PDF-02/PDF-06, PDF-05 is a LATENT defect.

* The hardening is nonetheless correct and SHARED.  A ``<canvas>`` is an
  imperative pixel surface whose bitmap lives in the drawing context, NOT in the
  serialized DOM.  If any future renderer (or a permitted HTML block) emits one,
  it would silently blank on BOTH backends that consume this shared route:
    - ``capture_pdf`` → ``page.pdf()``: a WebGL/2D canvas can rasterize BLANK in
      headless Chromium's print path if its backing store is not committed.
    - ``extract_html`` → ``outerHTML`` (Card II): a ``<canvas>`` serializes as an
      EMPTY tag — its bitmap is gone from the standalone HTML file.
  ``rasterizeCanvasesToImages`` converts each ``<canvas>`` to an ``<img>`` (a
  declarative element whose ``src`` carries the pixels) in ``finalizeReadiness``
  BEFORE ``data-render-status="complete"`` — the general seam both PDF and HTML
  exports reuse, mirroring the retired ``pdfExport.ts::convertCanvasElements``.

This is a FAST, browser-free structural guard that the rasterization step stays
shipped in the shared readiness path and runs BEFORE the ``<img>`` load-await
gate (so the generated data-URL image is itself awaited).  A future edit that
drops the step, or moves it after ``data-render-status`` is set, trips a test.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PRINT_PAGE = _ROOT / "frontend" / "src" / "components" / "PrintRenderPage.tsx"


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


def test_print_page_defines_canvas_rasterizer():
    """PrintRenderPage must define a canvas→img rasterization function that
    reads canvas pixels (toDataURL) and replaces the canvas with an <img>.

    Mutation proof: deleting the function definition makes this fail.
    """
    src = _strip_ts_comments(_read(_PRINT_PAGE))
    assert re.search(r"function\s+rasterizeCanvasesToImages\s*\(", src), \
        "PrintRenderPage must define rasterizeCanvasesToImages(root)"
    m = re.search(
        r"function\s+rasterizeCanvasesToImages\s*\(.*?\n\}\n",
        src, flags=re.DOTALL,
    )
    assert m, "could not isolate the rasterizeCanvasesToImages body"
    body = m.group(0)
    # It must select canvases, read their pixels, and replace with an <img>.
    assert re.search(r"querySelectorAll\(\s*['\"]canvas['\"]\s*\)", body), \
        "rasterizer must select <canvas> elements"
    assert "toDataURL" in body, \
        "rasterizer must read canvas pixels via toDataURL (pixels are not in the DOM)"
    assert re.search(r"createElement\(\s*['\"]img['\"]\s*\)", body), \
        "rasterizer must create an <img> to carry the pixels"
    assert re.search(r"\breplaceWith\b|\breplaceChild\b", body), \
        "rasterizer must replace the <canvas> with the <img>"


def test_rasterizer_swallows_tainted_canvas():
    """A tainted/unreadable canvas (toDataURL throws) must be swallowed so one
    bad canvas cannot crash the whole render — the rasterizer body must wrap the
    pixel read in try/catch."""
    src = _strip_ts_comments(_read(_PRINT_PAGE))
    m = re.search(
        r"function\s+rasterizeCanvasesToImages\s*\(.*?\n\}\n",
        src, flags=re.DOTALL,
    )
    assert m, "could not isolate the rasterizeCanvasesToImages body"
    body = m.group(0)
    assert "try" in body and "catch" in body, \
        "rasterizer must guard toDataURL (tainted canvas throws) with try/catch"


def test_rasterization_runs_in_readiness_before_img_await():
    """The rasterizer must be CALLED inside finalizeReadiness, and BEFORE the
    ``<img>`` load-await loop, so the generated data-URL image is itself awaited
    (a call after ``setStatus('complete')`` would let a backend read the DOM
    before the canvas pixels landed).

    Mutation proof: removing the call from finalizeReadiness, or moving it after
    the image gather / after setStatus('complete'), makes this fail.
    """
    src = _strip_ts_comments(_read(_PRINT_PAGE))
    m = re.search(
        r"const\s+finalizeReadiness\s*=\s*useCallback\(async.*?\n    \}, \[\]\);",
        src, flags=re.DOTALL,
    )
    assert m, "could not isolate the finalizeReadiness body"
    body = m.group(0)

    call = re.search(r"rasterizeCanvasesToImages\s*\(\s*node\s*\)", body)
    assert call, "finalizeReadiness must call rasterizeCanvasesToImages(node)"

    img_gather = re.search(r"querySelectorAll\(\s*['\"]img['\"]\s*\)", body)
    assert img_gather, "finalizeReadiness must gather <img> elements to await them"
    assert call.start() < img_gather.start(), (
        "rasterizeCanvasesToImages(node) must run BEFORE the <img> gather/await "
        "so the generated data-URL image is included in the load-await gate"
    )

    complete = re.search(r"setStatus\(\s*['\"]complete['\"]\s*\)", body)
    assert complete, "finalizeReadiness must set status complete"
    assert call.start() < complete.start(), (
        "rasterizeCanvasesToImages(node) must run BEFORE data-render-status is "
        "set to complete (before any backend reads the DOM)"
    )
