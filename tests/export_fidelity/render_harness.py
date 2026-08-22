"""
SHARED render harness for the export-fidelity apparatus.

Turns a fixture conversation into a rasterised, text-extractable document that
the analyzers in :mod:`checks` can measure.  The design goal is a single
``RenderedDocument`` abstraction that the check plumbing consumes, so a
NON-PDF backend (Card II's self-contained HTML rendered to an image, say) can
be added later without rewriting a single check.

CRITICAL: the PDF backend invokes the REAL Stage-2 pipeline
(``app.services.pdf_exporter.export_conversation_pdf``) — it does NOT
reimplement rendering.  Measuring a private copy of the pipeline would let the
harness silently drift from production; the whole point of the apparatus is to
measure what ships.

Two entry points into a ``RenderedDocument``:
  * :func:`render_pdf`      — drive the real pipeline (needs a live server whose
                              bundle includes the /print route), then rasterise.
  * :func:`load_pdf`        — rasterise an already-produced PDF (bytes or path).
                              Lets the audit run against captured PDFs without a
                              live server, and lets any future backend that
                              emits a PDF reuse the same path.

Rasterisation is via pypdfium2 (~150 dpi default); text via pdfplumber's text
layer.  PyMuPDF/poppler are intentionally NOT used (not available in this env).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np

# 72 pt/in is the PDF user-space unit; pypdfium2 render scale is relative to it.
_PDF_BASE_DPI = 72.0
DEFAULT_DPI = 150.0


@dataclass
class RenderedPage:
    """One rasterised page plus its extracted text layer."""

    index: int
    rgb: np.ndarray                 # HxWx3 uint8
    text: str                       # pdfplumber text-layer extraction
    width_px: int
    height_px: int
    # Positioned text words: list of {text,x0,x1,top,bottom} in PDF points.
    # Used by page-break analysis to locate elements near a boundary.  Empty
    # for backends that cannot supply positions.
    words: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RenderedDocument:
    """Backend-neutral rendered artefact consumed by every check.

    ``source_format`` records which backend produced it ("pdf" today) so a
    check can opt out for formats it does not apply to, without the plumbing
    changing.  ``raw_bytes`` is the underlying artefact (PDF bytes) for
    checks/callers that want the source.
    """

    pages: List[RenderedPage]
    full_text: str
    source_format: str = "pdf"
    dpi: float = DEFAULT_DPI
    raw_bytes: Optional[bytes] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)


# ---------------------------------------------------------------------------
# Rasterisation (shared by every PDF-producing backend)
# ---------------------------------------------------------------------------

def _rasterize_pdf(pdf_bytes: bytes, dpi: float) -> List[RenderedPage]:
    import pypdfium2 as pdfium

    scale = dpi / _PDF_BASE_DPI
    pages: List[RenderedPage] = []

    # Text layer (positioned) via pdfplumber; rasters via pdfium.  Both read the
    # same bytes so page indices line up.
    import io
    import pdfplumber

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as plumb:
            n = len(doc)
            for i in range(n):
                bmp = doc[i].render(scale=scale).to_numpy()
                if bmp.ndim == 3 and bmp.shape[2] == 4:
                    bmp = bmp[:, :, :3]
                bmp = np.ascontiguousarray(bmp)
                try:
                    pplumb_page = plumb.pages[i]
                    text = pplumb_page.extract_text() or ""
                    words = pplumb_page.extract_words() or []
                except Exception:
                    text, words = "", []
                pages.append(RenderedPage(
                    index=i,
                    rgb=bmp,
                    text=text,
                    width_px=bmp.shape[1],
                    height_px=bmp.shape[0],
                    words=words,
                ))
    finally:
        doc.close()
    return pages


def load_pdf(
    pdf: Union[bytes, bytearray, str, Path],
    *,
    dpi: float = DEFAULT_DPI,
    meta: Optional[Dict[str, Any]] = None,
) -> RenderedDocument:
    """Rasterise an already-produced PDF into a :class:`RenderedDocument`.

    ``pdf`` may be raw bytes or a filesystem path.  This is the seam the audit
    runner uses when PDFs are produced out-of-band (e.g. against a live server),
    and is backend-agnostic: anything that yields a PDF can reuse it.
    """
    if isinstance(pdf, (bytes, bytearray)):
        pdf_bytes = bytes(pdf)
    else:
        pdf_bytes = Path(pdf).read_bytes()
    pages = _rasterize_pdf(pdf_bytes, dpi)
    full_text = "\n".join(p.text for p in pages)
    return RenderedDocument(
        pages=pages,
        full_text=full_text,
        source_format="pdf",
        dpi=dpi,
        raw_bytes=pdf_bytes,
        meta=meta or {},
    )


# ---------------------------------------------------------------------------
# Real-pipeline backend (canonical path — needs a live server with /print)
# ---------------------------------------------------------------------------

def render_pdf(
    messages: List[Dict[str, Any]],
    *,
    options: Optional[Dict[str, Any]] = None,
    title: str = "Ziya Fidelity Fixture",
    server_port: int = 6969,
    dpi: float = DEFAULT_DPI,
    version: str = "0.0.0-test",
    model: str = "test-model",
    provider: str = "test-provider",
) -> RenderedDocument:
    """Render a conversation to a :class:`RenderedDocument` via the REAL pipeline.

    Calls :func:`app.services.pdf_exporter.export_conversation_pdf` (the exact
    function the ``POST /api/export/pdf`` endpoint calls) — no reimplementation.
    Requires a running Ziya server on ``server_port`` whose built frontend
    bundle includes the ``/print`` route (produced by ``npm run build`` after
    the Stage-2 diffs land).  Raises ImportError (Playwright missing) or
    RuntimeError (render failure) straight through so failures are diagnosable.
    """
    from app.services.pdf_exporter import export_conversation_pdf

    async def _run():
        from app.services.pdf_exporter import shutdown_render_session
        try:
            return await export_conversation_pdf(
                messages=messages,
                options=options,
                title=title,
                version=version,
                model=model,
                provider=provider,
                server_port=server_port,
            )
        finally:
            # export_conversation_pdf uses a MODULE-LEVEL singleton session
            # (get_render_session) whose Chromium is bound to the running event
            # loop.  The audit runner calls render_pdf once per variant, and
            # each call spins its own asyncio.run() loop; if the singleton
            # survived, the next variant would reuse a browser bound to a
            # now-closed loop and hang.  The harness therefore OWNS session
            # lifecycle: tear the singleton down at the end of every render so
            # each render_pdf call is self-contained and loop-clean.  (In the
            # live server the endpoint keeps the singleton warm across requests;
            # only this out-of-process harness needs the teardown.)
            await shutdown_render_session()

    pdf_bytes, meta = asyncio.run(_run())
    doc = load_pdf(pdf_bytes, dpi=dpi, meta=dict(meta))
    return doc


# ---------------------------------------------------------------------------
# Backend registry — so a non-PDF backend slots in without touching checks.
# ---------------------------------------------------------------------------

# name -> callable(messages, **kwargs) -> RenderedDocument
RENDER_BACKENDS = {
    "pdf": render_pdf,
}


def render(backend: str, messages: List[Dict[str, Any]], **kwargs) -> RenderedDocument:
    """Dispatch to a named render backend.  Card II can register an "html"
    backend (render to self-contained HTML, screenshot to a raster) here and
    every check keeps working unchanged.
    """
    try:
        fn = RENDER_BACKENDS[backend]
    except KeyError:
        raise ValueError(
            f"unknown render backend {backend!r}; known: {sorted(RENDER_BACKENDS)}"
        )
    return fn(messages, **kwargs)


# ===========================================================================
# HTML backend (Card II) — produce HTML export output from the SAME fixture,
# render it in headless Chromium and screenshot it so the SHARED raster checks
# apply, and expose a computed-style/DOM "probe" so the HTML-specific checks in
# ``checks.py`` measure the rendered document (not just the source string).
#
# TWO MODES, matching the pre-made dual-mode architectural decision:
#   * mode="python" — call the REAL production Python export path
#     (``app.utils.conversation_exporter.export_conversation_for_paste``), the
#     exact function POST /api/export/rendered ends up calling.  No live server
#     needed.  This is the fallback tier whose fidelity ceiling is lower.
#   * mode="route"  — drive the SHARED /print route via
#     ``ConversationRenderSession.extract_html`` (Card-I Stage-2 seam) and wrap
#     the rendered DOM into a self-contained document.  Needs a live Ziya
#     server whose built bundle includes /print.  This is the high-fidelity
#     tier Card II's Stage 2 wires into production; here it is the measurement
#     seam so the audit can grade route output the moment Stage 2 lands.
#
# Like the PDF backend, this NEVER reimplements the exporter — it calls the
# shipping function so the audit measures what ships.
# ===========================================================================

# Default viewport for rasterising the standalone HTML.  The Python exporter
# caps body width at 900px; 1000px viewport leaves a margin without horizontal
# scrollbars.
HTML_VIEWPORT_WIDTH = 1000


def _rgb_str_to_tuple(s: Optional[str]) -> Optional[List[int]]:
    """Parse a CSS ``rgb(...)``/``rgba(...)`` string into ``[r,g,b]`` ints.

    Returns ``None`` for ``transparent``/unset so a check can distinguish
    "no background" from "white background".
    """
    if not s:
        return None
    s = s.strip()
    if s in ("transparent", "rgba(0, 0, 0, 0)"):
        return None
    import re as _re
    m = _re.match(r"rgba?\(([^)]+)\)", s)
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split(",")]
    try:
        r, g, b = int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
    except (ValueError, IndexError):
        return None
    # rgba with alpha 0 == transparent
    if len(parts) >= 4:
        try:
            if float(parts[3]) == 0.0:
                return None
        except ValueError:
            pass
    return [r, g, b]


# The in-browser probe.  A single evaluate() collects everything the HTML
# checks need so they read ``doc.meta['probe']`` rather than each launching a
# browser (mirrors the raster checks reading synthetic numpy — a can-fail test
# builds a synthetic probe dict with NO browser).
_HTML_PROBE_JS = r"""
() => {
  const out = {};
  const bodyStyle = getComputedStyle(document.body);
  out.body_bg = bodyStyle.backgroundColor;
  out.body_color = bodyStyle.color;

  // --- syntax highlighting: distinct computed colors of code token spans ---
  const codeEls = Array.from(document.querySelectorAll('pre code, pre'));
  const tokenSpans = [];
  for (const code of codeEls) {
    for (const sp of code.querySelectorAll('span')) {
      tokenSpans.push(sp);
    }
  }
  const tokenColors = {};
  for (const sp of tokenSpans) {
    const c = getComputedStyle(sp).color;
    tokenColors[c] = (tokenColors[c] || 0) + 1;
  }
  out.token_span_count = tokenSpans.length;
  out.token_distinct_colors = Object.keys(tokenColors);

  // --- diff coloring: per-line add/remove elements + their backgrounds ---
  const diffInsertSel = ['.diff-code-insert', '.diff-line-insert',
                         '[class*="insert"]', 'ins', '.token.inserted'];
  const diffDeleteSel = ['.diff-code-delete', '.diff-line-delete',
                         '[class*="delete"]', 'del', '.token.deleted'];
  const collectBgs = (sels) => {
    const seen = new Set(); const bgs = [];
    for (const sel of sels) {
      let els = [];
      try { els = Array.from(document.querySelectorAll(sel)); } catch (e) { els = []; }
      for (const el of els) {
        if (seen.has(el)) continue; seen.add(el);
        bgs.push(getComputedStyle(el).backgroundColor);
      }
    }
    return bgs;
  };
  out.diff_insert_bgs = collectBgs(diffInsertSel);
  out.diff_delete_bgs = collectBgs(diffDeleteSel);
  out.diff_insert_count = out.diff_insert_bgs.length;
  out.diff_delete_count = out.diff_delete_bgs.length;

  // --- math: KaTeX rendered output vs raw LaTeX source ---
  out.katex_count = document.querySelectorAll('.katex, .katex-display, .katex-html').length;

  // --- tables: real <table> grids vs literal pipe text (HTML-06) ---
  const tables = Array.from(document.querySelectorAll('table'));
  out.table_count = tables.length;
  out.table_cell_count = document.querySelectorAll('td, th').length;
  out.table_row_count = document.querySelectorAll('tr').length;
  // A crude signal that a markdown table was left as literal pipe text: a text
  // node run of the form "| --- | --- |" (delimiter row) surviving into the
  // rendered innerText. A faithful <table> render has none.
  out.literal_pipe_table_rows =
    ((document.body.innerText || '').match(/\|\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?/g) || []).length;

  // --- highlight/mark preservation ---
  const marks = Array.from(document.querySelectorAll('mark, .highlight, .search-highlight'));
  out.mark_count = marks.length;
  out.mark_bgs = marks.map(m => getComputedStyle(m).backgroundColor);

  // --- images actually laid out (naturalWidth>0 or inline svg) ---
  const imgs = Array.from(document.querySelectorAll('img'));
  out.img_count = imgs.length;
  out.img_rendered = imgs.filter(i => i.complete && i.naturalWidth > 0).length;
  out.inline_svg_count = document.querySelectorAll('svg').length;

  // --- XSS canary: did any injected script/handler execute? ---
  out.xss_fired = !!(window.__ziya_xss_fired);

  // --- rendered text a reader sees (drives format-neutral checks) ---
  out.inner_text = document.body.innerText || '';
  return out;
}
"""


async def _render_html_in_browser(
    html: str, *, server_port: int, dpi: float
) -> Dict[str, Any]:
    """Load a standalone HTML string in headless Chromium under BOTH a light
    and a forced-dark ``prefers-color-scheme`` and return rasters + probes.

    Returns a dict with light_rgb / dark_rgb numpy arrays, the light and dark
    computed-style probes, and the rendered inner text.  Reuses the diagram
    renderer's launch-arg posture so we do not duplicate sandbox logic.
    """
    from playwright.async_api import async_playwright
    from app.services.diagram_renderer import build_chromium_launch_args
    from app.config.env_registry import ziya_env

    scale = dpi / 96.0  # CSS px are 96/in; device_scale_factor maps to raster dpi

    no_sandbox = bool(ziya_env("ZIYA_CHROMIUM_NO_SANDBOX"))
    result: Dict[str, Any] = {}
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True, args=build_chromium_launch_args(no_sandbox=no_sandbox)
    )
    try:
        for scheme_key, scheme in (("light", "light"), ("dark", "dark")):
            ctx = await browser.new_context(
                viewport={"width": HTML_VIEWPORT_WIDTH, "height": 1400},
                device_scale_factor=max(1.0, scale),
                color_scheme=scheme,
            )
            page = await ctx.new_page()
            # A canary the XSS checks read: if any <script> in the exported HTML
            # runs, or an <img onerror>/on* handler fires, it flips this flag.
            # Neutralized (escaped) payloads never execute, so it stays false.
            await page.add_init_script(
                "window.__ziya_xss_fired = false;"
                "window.__ziya_mark_xss = function(){ window.__ziya_xss_fired = true; };"
            )
            # Load via a data: URL goto (NOT set_content): add_init_script only
            # applies on a real navigation, so the XSS canary must be installed
            # before a navigation that then runs the document's own inline
            # scripts.  A data: URL is also the closest analogue to opening the
            # file from disk (no dev-server origin), which is exactly the
            # downloaded-file threat model this audit measures.
            import base64 as _b64
            data_url = "data:text/html;base64," + _b64.b64encode(
                html.encode("utf-8")
            ).decode("ascii")
            await page.goto(data_url, wait_until="networkidle")
            probe = await page.evaluate(_HTML_PROBE_JS)
            png = await page.screenshot(full_page=True, type="png")
            import io as _io
            from PIL import Image
            arr = np.array(Image.open(_io.BytesIO(png)).convert("RGB"))
            result[f"{scheme_key}_rgb"] = arr
            result[f"{scheme_key}_probe"] = probe
            await ctx.close()
    finally:
        await browser.close()
        await pw.stop()
    result["inner_text"] = result.get("light_probe", {}).get("inner_text", "")
    return result


def _static_resource_refs(html: str) -> Dict[str, List[str]]:
    """Statically find resource references that will NOT resolve when the file
    is opened from disk: relative src/href paths, localhost/127.0.0.1 URLs,
    ``blob:`` URLs, and non-data external ``http(s)`` src.  Data-URI and inline
    SVG are self-contained and NOT flagged.

    Pure-Python (regex) so the self-containment check is deterministic and
    browser-free-testable.
    """
    import re as _re
    refs = {"relative": [], "localhost": [], "blob": [], "external_http": []}
    # src="..." and href="..." on resource-loading elements.  We scan src on any
    # tag (img/script/link/iframe) and href only on <link> (stylesheet), since
    # <a href> is navigation, not a load-on-open resource.
    for m in _re.finditer(r'\bsrc\s*=\s*"([^"]*)"', html):
        url = m.group(1).strip()
        _classify_ref(url, refs)
    for m in _re.finditer(r'<link\b[^>]*\bhref\s*=\s*"([^"]*)"', html, _re.IGNORECASE):
        url = m.group(1).strip()
        _classify_ref(url, refs)
    return refs


def _classify_ref(url: str, refs: Dict[str, List[str]]) -> None:
    low = url.lower()
    if not url:
        return
    if low.startswith("data:"):
        return  # self-contained
    if low.startswith("#"):
        return  # in-document anchor
    if low.startswith("blob:"):
        refs["blob"].append(url)
    elif "localhost" in low or "127.0.0.1" in low or low.startswith("http://localhost"):
        refs["localhost"].append(url)
    elif low.startswith("http://") or low.startswith("https://"):
        refs["external_http"].append(url)
    elif low.startswith("//"):
        refs["external_http"].append(url)
    else:
        # relative path (./x, ../x, x/y, /abs) — will not resolve from disk
        refs["relative"].append(url)


def _html5lib_parse_errors(html: str) -> List[str]:
    """Return html5lib's list of parse errors (unclosed tags, mis-nesting, …).

    A well-formed document parses with ZERO errors; a document that only parses
    because the browser's error-recovery kicked in produces a non-empty list.
    """
    import html5lib
    parser = html5lib.HTMLParser(strict=False)
    try:
        parser.parse(html)
    except Exception as exc:  # pragma: no cover - defensive
        return [f"parse raised {type(exc).__name__}: {exc}"]
    # parser.errors is a list of (position, code, datavars) tuples.
    return [f"{code}@{pos}" for (pos, code, _dv) in parser.errors]


def render_html(
    messages: List[Dict[str, Any]],
    *,
    mode: str = "python",
    options: Optional[Dict[str, Any]] = None,
    title: str = "Ziya Fidelity Fixture",
    server_port: int = 6969,
    dpi: float = DEFAULT_DPI,
    version: str = "0.0.0-test",
    model: str = "test-model",
    provider: str = "test-provider",
    html_override: Optional[str] = None,
) -> RenderedDocument:
    """Render a conversation to a :class:`RenderedDocument` via the HTML export.

    ``source_format`` is ``"html"``.  ``raw_bytes`` is the exported HTML bytes.
    ``meta`` carries: ``html`` (source str), ``mode``, ``probe`` (light-scheme
    computed-style/DOM probe), ``dark_probe`` (forced-dark-scheme probe),
    ``resource_refs`` (static self-containment scan), ``parse_errors``
    (html5lib), and ``dark_rgb`` (the forced-dark screenshot as an ndarray) so
    the dark-mode-independence check can compare it against the light render.

    ``html_override`` lets a can-fail / adversarial test feed a hand-built HTML
    string through the SAME render+probe path without invoking the exporter.
    """
    if html_override is not None:
        html = html_override
    elif mode == "python":
        from app.utils.conversation_exporter import export_conversation_for_paste
        result = export_conversation_for_paste(
            messages, format_type="html", target="public",
            version=version, model=model, provider=provider,
        )
        html = result["content"]
    elif mode == "route":
        # High-fidelity tier: drive the SHARED /print route and wrap the
        # rendered DOM into a self-contained document.  Card II Stage 2 owns the
        # production wrapper; here we assemble a minimal standalone doc so the
        # audit can grade route output.  Requires a live server with /print.
        html = _render_route_html(
            messages, options=options, title=title, server_port=server_port,
            version=version, model=model, provider=provider,
        )
    else:
        raise ValueError(f"unknown html render mode {mode!r}; use 'python' or 'route'")

    browser_out = asyncio.run(
        _render_html_in_browser(html, server_port=server_port, dpi=dpi)
    )
    light_rgb = browser_out["light_rgb"]
    inner_text = browser_out.get("inner_text", "")

    page = RenderedPage(
        index=0,
        rgb=light_rgb,
        text=inner_text,
        width_px=light_rgb.shape[1],
        height_px=light_rgb.shape[0],
        words=[],
    )
    doc = RenderedDocument(
        pages=[page],
        full_text=inner_text,
        source_format="html",
        dpi=dpi,
        raw_bytes=html.encode("utf-8"),
        meta={
            "mode": mode,
            "html": html,
            "probe": browser_out.get("light_probe", {}),
            "dark_probe": browser_out.get("dark_probe", {}),
            "dark_rgb": browser_out.get("dark_rgb"),
            "resource_refs": _static_resource_refs(html),
            "parse_errors": _html5lib_parse_errors(html),
        },
    )
    return doc


def _render_route_html(
    messages: List[Dict[str, Any]],
    *,
    options: Optional[Dict[str, Any]],
    title: str,
    server_port: int,
    version: str,
    model: str,
    provider: str,
) -> str:
    """Drive the SHARED /print route (extract_html) and wrap the rendered DOM
    into a self-contained HTML document.

    This is the measurement seam for the route-driven mode.  It reuses the
    Card-I Stage-2 ``ConversationRenderSession`` (does NOT launch its own
    Chromium for the render) exactly as the reuse_pointer_for_card_II handoff
    prescribes.  Requires a live Ziya server whose bundle includes /print.
    """
    from app.services.pdf_exporter import (
        get_render_session, shutdown_render_session, build_print_payload,
        normalize_render_options,
    )
    from app.utils.conversation_exporter import _create_footer

    opts = normalize_render_options(options)
    footer_html = None
    if opts.get("includeFooter"):
        footer_html = _create_footer("public", version, model, provider, "html")
    payload = build_print_payload(
        messages, options=opts, footer_html=footer_html, title=title,
    )

    async def _run() -> str:
        try:
            session = await get_render_session(server_port)
            # Card II Stage-2 PRODUCTION serializer: a self-contained standalone
            # document (styles inlined, prefers-color-scheme:dark dropped,
            # images embedded, interactive artifacts + scripts stripped).  The
            # audit grades the SAME bytes the /api/export/rendered route ships.
            return await session.extract_export_html(payload)
        finally:
            await shutdown_render_session()

    return asyncio.run(_run())


RENDER_BACKENDS["html"] = render_html

