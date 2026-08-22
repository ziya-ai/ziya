"""
Headless conversation → PDF export service (Playwright).

This is the PDF sibling of ``app/services/diagram_renderer.py``.  Where the
diagram renderer drives the ``/render`` route to rasterise a single diagram,
this module drives the ``/print`` route to render a WHOLE conversation through
the real ``MarkdownRenderer`` pipeline (Prism, KaTeX, react-diff-view, D3
diagrams — the exact chat UI code) and captures the result as a PDF.

Architecture (mirrors DiagramRenderer deliberately):

    ┌─────────────────────────┐        Playwright        ┌──────────────┐
    │ ConversationRenderSession│ ───── goto /print ─────▶ │  /print      │
    │  (SHARED lifecycle)      │  inject via              │ PrintRender  │
    │                          │  window.__renderConv...  │ Page (real   │
    │  .capture_pdf()  (PDF)   │ ◀── data-render-status ──│ MarkdownRend)│
    │  .extract_html() (Card II)│      "complete"          └──────────────┘
    └─────────────────────────┘

SHARED vs PDF-specific
----------------------
* ``ConversationRenderSession`` (browser lifecycle + route driving +
  ``extract_html``) is SHARED infrastructure.  Card II (HTML export) drives
  the same session and calls ``extract_html()`` for a self-contained rendered
  DOM instead of ``capture_pdf()``.
* ``build_chromium_launch_args`` is imported from ``diagram_renderer`` — launch
  arg / sandbox-posture logic is NOT duplicated here.  ``--no-sandbox`` stays
  opt-in via ``ZIYA_CHROMIUM_NO_SANDBOX`` exactly as the diagram path.
* ``export_conversation_pdf`` (page.pdf A4/margins/printBackground, footer) is
  the PDF-specific entry point.

Requires: ``pip install playwright`` + ``playwright install chromium``.  When
Playwright is absent, ``create()``/``export_conversation_pdf`` raise a clear
``ImportError`` (mirrors the diagram renderer import guard) so the rest of the
app keeps working.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Playwright is an optional dependency — imported lazily so the app works
# without it (identical posture to app/services/diagram_renderer.py).
_playwright_available: Optional[bool] = None


def _check_playwright() -> bool:
    global _playwright_available
    if _playwright_available is None:
        try:
            import playwright.async_api  # noqa: F401
            _playwright_available = True
        except ImportError:
            _playwright_available = False
    return _playwright_available


# ---------------------------------------------------------------------------
# Conversation loading (server holds full message bodies)
# ---------------------------------------------------------------------------

def load_conversation_messages(
    project_id: str, chat_id: str
) -> Optional[List[Dict[str, Any]]]:
    """Load a conversation's full message list from server-side storage.

    Returns a list of plain message dicts (role/content/…) or ``None`` when the
    project or chat does not exist.  A server-side exporter can therefore render
    a conversation by id WITHOUT the browser re-supplying its content — the
    channel the client injection path is a fallback for, not a requirement.
    """
    from app.storage.projects import ProjectStorage
    from app.storage.chats import ChatStorage
    from app.utils.paths import get_ziya_home, get_project_dir

    project = ProjectStorage(get_ziya_home()).get(project_id)
    if not project:
        return None
    storage = ChatStorage(get_project_dir(project_id))
    chat = storage.get(chat_id)
    if not chat:
        return None
    # model_dump each Message so downstream (JSON injection) is plain data.
    return [
        m.model_dump() if hasattr(m, "model_dump") else dict(m)
        for m in chat.messages
    ]


# ---------------------------------------------------------------------------
# Render options — mirror the frontend ExportConversationModal semantics
# ---------------------------------------------------------------------------

_DEFAULT_RENDER_OPTIONS: Dict[str, Any] = {
    # None == all rounds; N == keep last N human→assistant rounds
    "roundLimit": None,
    "includeHuman": True,
    "includeCollapsed": True,
    "includeFooter": True,
}


def normalize_render_options(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge caller options over the defaults, dropping unknown keys.

    Filtering is performed IN THE PAGE (PrintRenderPage) so there is a single
    source of truth for option semantics shared by PDF, HTML (Card II) and any
    CLI consumer.  This function only decides which knobs cross the wire.

    ``roundLimit`` is special: ``None`` is a MEANINGFUL value ("all rounds"),
    so an explicitly-supplied ``None`` is honoured rather than treated as
    "unset".  The booleans fall back to their defaults when omitted.
    """
    merged = dict(_DEFAULT_RENDER_OPTIONS)
    if options:
        for k in _DEFAULT_RENDER_OPTIONS:
            if k not in options:
                continue
            if k == "roundLimit":
                merged[k] = options[k]  # None is valid (== all rounds)
            elif options[k] is not None:
                merged[k] = options[k]
    return merged


# ---------------------------------------------------------------------------
# Self-contained HTML serializer (runs IN THE PAGE via page.evaluate)
# ---------------------------------------------------------------------------
#
# This browser-side program turns the live rendered #print-render-root into a
# standalone, inert, offline-viewable HTML document.  It is a plain JS string
# (not f-string / not templated) so it never interpolates untrusted data; its
# only input is the {title, footerHtml, embedImages} arg object Playwright
# passes as the evaluate() argument.
_SELF_CONTAINED_HTML_JS = r"""
(opts) => {
  const title = (opts && opts.title) || 'Ziya Conversation Export';
  const footerHtml = (opts && opts.footerHtml) || '';
  const embedImages = !(opts && opts.embedImages === false);

  // --- 1. Collect CSS from all same-origin stylesheets, INLINING it and
  //        DROPPING any prefers-color-scheme:dark @media block so a saved
  //        file cannot flip dark on a dark-mode host (HTML-04). --------------
  const isDarkMedia = (txt) =>
    /@media[^{]*prefers-color-scheme\s*:\s*dark/i.test(txt || '');
  const cssChunks = [];
  for (const sheet of Array.from(document.styleSheets)) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { rules = null; }
    if (!rules) continue;  // cross-origin sheet: skip (nothing external inlined)
    for (const rule of Array.from(rules)) {
      const txt = rule.cssText || '';
      if (!txt) continue;
      if (rule.type === CSSRule.MEDIA_RULE && isDarkMedia(txt)) continue;
      cssChunks.push(txt);
    }
  }

  // --- 2. Clone the rendered conversation and neutralize it. ----------------
  const src = document.getElementById('print-render-root');
  const root = src ? src.cloneNode(true) : document.createElement('div');

  // Remove every script node (the export is inert; no live JS travels).
  root.querySelectorAll('script').forEach((n) => n.remove());

  // Remove interactive artifacts: buttons, apply/copy/retry controls,
  // toolbars, scroll indicators.  These are chat-UI affordances with no
  // meaning in a static transcript and can carry click handlers.
  const interactiveSelectors = [
    'button',
    '[role="button"]',
    'input', 'textarea', 'select',
    '.copy-button', '.copy-code-button', '.code-copy',
    '.apply-changes', '.apply-button', '.diff-apply',
    '.toolbar', '.code-toolbar', '.editor-toolbar',
    '.scroll-indicator', '.scroll-to-bottom',
    '[contenteditable]',
    '[data-print-hide]',
  ];
  root.querySelectorAll(interactiveSelectors.join(',')).forEach((n) => n.remove());

  // Defense-in-depth: strip inline event handlers and dangerous link schemes
  // from whatever remains (the React DOM is already escaped; this makes the
  // route path provably >= the Python path's link-scheme rejection).
  const DANGER_SCHEME = /^\s*(javascript|vbscript|data\s*:\s*text\/html)\s*:/i;
  root.querySelectorAll('*').forEach((el) => {
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      if (name.startsWith('on')) { el.removeAttribute(attr.name); continue; }
      if ((name === 'href' || name === 'xlink:href' || name === 'src') &&
          DANGER_SCHEME.test(attr.value || '')) {
        el.removeAttribute(attr.name);
      }
    }
  });

  // --- 3. Embed same-origin <img> as data URIs so the file needs no network.
  //        (Diagrams are already inline <svg> or data-URI <img>; this covers
  //        any http(s) same-origin image.)  Cross-origin images are left as-is
  //        rather than fetched — we do NOT enable any new remote loading. -----
  const imageWork = [];
  if (embedImages) {
    root.querySelectorAll('img').forEach((img) => {
      const s = img.getAttribute('src') || '';
      if (!s || s.startsWith('data:')) return;
      let url;
      try { url = new URL(s, document.baseURI); } catch (e) { return; }
      if (url.origin !== location.origin) return;  // never fetch cross-origin
      imageWork.push(
        fetch(url.href).then((r) => r.blob()).then((blob) => new Promise((res) => {
          const fr = new FileReader();
          fr.onloadend = () => { img.setAttribute('src', String(fr.result)); res(); };
          fr.onerror = () => res();
          fr.readAsDataURL(blob);
        })).catch(() => {})
      );
    });
  }

  const finish = () => {
    const body = root.outerHTML;
    // Pin light color scheme; carry the inlined CSS; append the shared footer.
    const doc =
      '<!DOCTYPE html>\n<html lang="en"><head><meta charset="UTF-8">' +
      '<meta name="viewport" content="width=device-width, initial-scale=1.0">' +
      '<title>' + title.replace(/[<>&]/g, (c) => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c])) + '</title>' +
      '<style>:root{color-scheme:light}html,body{color-scheme:light;background:#ffffff}</style>' +
      '<style>' + cssChunks.join('\n') + '</style>' +
      '</head><body class="ziya-html-export">' +
      body + footerHtml +
      '</body></html>';
    return doc;
  };

  if (imageWork.length === 0) return finish();
  return Promise.all(imageWork).then(finish);
}
"""


# ---------------------------------------------------------------------------
# PDF outline / bookmark synthesis (QUAL-01)
# ---------------------------------------------------------------------------
#
# Chromium's ``page.pdf()`` emits NO outline (bookmark tree), so a long
# transcript exports as an unnavigable wall of pages.  Chromium DOES honour the
# CSS GCPM ``bookmark-level`` property, but ONLY on the print-media pagination
# path — and this pipeline deliberately captures with ``media="screen"`` (the
# chat UI's real styling), so that route is unavailable (verified: 0 outline
# items via bookmark-level under screen media).
#
# Instead we synthesise the outline in two capture passes + a pypdf post-pass:
#
#   1. PROBE pass: inject an OUT-OF-FLOW sentinel token (absolutely positioned,
#      ~invisible) at the start of every ``.print-message`` and capture a PDF.
#      Because the sentinel is out of flow it does NOT change pagination, but it
#      DOES land in the text layer, so we can read back which PAGE each message
#      begins on via the extracted text (screen layout width != PDF layout
#      width, so a DOM-side measurement would be wrong — the text layer is the
#      ground truth, matching the PDF-09b lesson).
#   2. CLEAN pass: remove the sentinels and re-capture.  Verified to produce
#      IDENTICAL pagination to the probe pass, with ZERO sentinel leakage into
#      the shipped text layer (so copy-paste / text_quality stays clean).
#   3. pypdf ``add_outline_item`` stamps one bookmark per message onto the clean
#      bytes, its destination the message's start page.
#
# The whole feature is best-effort: any failure (pypdf missing, empty mapping)
# falls back to returning the clean bytes unchanged, so the outline can never
# break a capture that would otherwise have succeeded.

# Injected in the browser to place per-message sentinels.  Plain JS string (no
# interpolation of untrusted data).  Returns [{index, label, token}].
_OUTLINE_SENTINEL_INJECT_JS = r"""
() => {
  const root = document.getElementById('print-render-root');
  if (!root) return [];
  const msgs = Array.from(root.querySelectorAll('.print-message'));
  const out = [];
  msgs.forEach((m, i) => {
    const roleEl = m.querySelector('.print-message-role');
    const label = roleEl ? (roleEl.textContent || '').trim()
                         : (m.getAttribute('data-role') || '');
    const token = 'ZYAOUTLINEANCHOR' + i + 'X';
    const span = document.createElement('span');
    span.textContent = token;
    span.setAttribute('data-zya-outline-anchor', String(i));
    // Out of flow so pagination is unchanged; ~invisible but still rendered
    // into the text layer so pypdf/pdfplumber can read its page.
    span.style.position = 'absolute';
    span.style.left = '0';
    span.style.top = '0';
    span.style.fontSize = '2px';
    span.style.color = 'rgba(255,255,255,0.004)';
    span.style.pointerEvents = 'none';
    span.style.userSelect = 'none';
    if (getComputedStyle(m).position === 'static') m.style.position = 'relative';
    m.insertBefore(span, m.firstChild);
    out.push({ index: i, label, token });
  });
  return out;
};
"""

# Injected in the browser to strip the sentinels before the clean capture.
_OUTLINE_SENTINEL_REMOVE_JS = r"""
() => {
  const nodes = document.querySelectorAll('[data-zya-outline-anchor]');
  nodes.forEach(e => e.remove());
  return document.querySelectorAll('[data-zya-outline-anchor]').length;
};
"""

# Outline bookmark labels cap (a runaway conversation should not produce a
# thousand-entry tree; the check only needs >= 2 with resolvable destinations).
_OUTLINE_MAX_ITEMS = 400


def _map_sentinels_to_pages(
    probe_pdf: bytes, anchors: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Read the probe PDF's text layer and resolve each sentinel to a page.

    Returns [{index, label, page}] for every sentinel whose token was found,
    ordered by page then message index.  Never raises.
    """
    try:
        from pypdf import PdfReader
    except Exception:  # pragma: no cover - pypdf is a required dep for outline
        return []
    try:
        reader = PdfReader(io.BytesIO(probe_pdf))
        # Strip spaces: Chromium can inject stray spaces between glyphs of a
        # tiny token, so compare against a space-collapsed page string.
        page_text = [
            (p.extract_text() or "").replace(" ", "") for p in reader.pages
        ]
    except Exception:
        return []
    mapped: List[Dict[str, Any]] = []
    for a in anchors:
        token = a.get("token")
        if not token:
            continue
        page = next(
            (i for i, t in enumerate(page_text) if token in t), None
        )
        if page is None:
            continue
        mapped.append(
            {"index": a.get("index"), "label": a.get("label") or "", "page": page}
        )
    mapped.sort(key=lambda d: (d["page"], d["index"] if d["index"] is not None else 0))
    return mapped


def _synthesize_outline(clean_pdf: bytes, mapping: List[Dict[str, Any]]) -> bytes:
    """Stamp a per-message bookmark tree onto ``clean_pdf`` using ``mapping``.

    Best-effort: returns ``clean_pdf`` unchanged on any failure or empty
    mapping.  Each bookmark's title is the role label plus the 1-based message
    number; its destination is the message's start page.
    """
    if not mapping:
        return clean_pdf
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception:  # pragma: no cover
        return clean_pdf
    try:
        writer = PdfWriter(clone_from=io.BytesIO(clean_pdf))
        n_pages = len(writer.pages)
        added = 0
        for item in mapping:
            if added >= _OUTLINE_MAX_ITEMS:
                break
            page = item.get("page")
            if page is None or page < 0 or page >= n_pages:
                continue
            label = item.get("label") or "Message"
            idx = item.get("index")
            num = (idx + 1) if isinstance(idx, int) else added + 1
            title = f"{label} (message {num})" if label else f"Message {num}"
            writer.add_outline_item(title, page)
            added += 1
        if added == 0:
            return clean_pdf
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()
    except Exception:
        logger.warning("PDF outline synthesis failed; shipping outline-less PDF",
                       exc_info=True)
        return clean_pdf


# ---------------------------------------------------------------------------
# Document metadata (QUAL-02)
# ---------------------------------------------------------------------------
#
# Chromium's ``page.pdf()`` copies the page's ``<title>`` (the injected app
# shell — 'Ziya - Code Assistant') into /Title, leaves /Creator as 'Chromium',
# and sets NO /Author or /Subject.  A file manager, PDF library, or assistive
# tech then mislabels the document.  We stamp conversation-specific Info-dict
# fields with a pypdf post-pass in the SAME clone-and-write cycle as the QUAL-01
# outline, so a well-made export names itself.
#
# Chromium's default /Title values (case-insensitive) that must be REPLACED by
# the conversation title rather than trusted.
_CHROMIUM_DEFAULT_TITLES = {
    "", "about:blank", "untitled", "chromium", "ziya - code assistant",
    "ziya conversation export", "ziya session transcript",
}
_METADATA_CREATOR = "Ziya PDF Exporter"
_METADATA_SUBJECT = "Ziya conversation export"


def _build_document_metadata(
    *,
    title: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> Dict[str, str]:
    """Assemble the Info-dict fields QUAL-02 stamps on the captured PDF.

    /Title  — the conversation title (falls back to a generic export label only
              when none is supplied); NEVER the app-shell '<title>'.
    /Author — the model/provider that produced the transcript, else 'Ziya'.
    /Subject, /Creator — fixed export-identifying strings.

    Returns only the keys that should be written (never empty values), so the
    caller's ``add_metadata`` merge leaves Chromium's parseable /CreationDate
    intact.
    """
    md: Dict[str, str] = {
        "/Subject": _METADATA_SUBJECT,
        "/Creator": _METADATA_CREATOR,
    }
    clean_title = (title or "").strip()
    if not clean_title or clean_title.lower() in _CHROMIUM_DEFAULT_TITLES:
        clean_title = "Ziya Conversation Export"
    md["/Title"] = clean_title
    model_s = (model or "").strip()
    provider_s = (provider or "").strip()
    generic = {"", "unknown", "test-model", "test-provider"}
    if model_s.lower() not in generic and provider_s.lower() not in generic:
        md["/Author"] = f"{model_s} ({provider_s})"
    elif model_s.lower() not in generic:
        md["/Author"] = model_s
    elif provider_s.lower() not in generic:
        md["/Author"] = provider_s
    else:
        md["/Author"] = "Ziya"
    return md


def _apply_document_metadata(pdf_bytes: bytes, metadata: Dict[str, str]) -> bytes:
    """Merge ``metadata`` into the PDF Info dict via pypdf.

    Best-effort: returns ``pdf_bytes`` unchanged on any failure or empty
    metadata, so metadata stamping can never break an otherwise-good capture.
    ``add_metadata`` MERGES, so any Chromium field not overridden here (notably
    the parseable /CreationDate) is preserved.
    """
    if not metadata:
        return pdf_bytes
    try:
        from pypdf import PdfWriter
    except Exception:  # pragma: no cover
        return pdf_bytes
    try:
        writer = PdfWriter(clone_from=io.BytesIO(pdf_bytes))
        writer.add_metadata(metadata)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()
    except Exception:
        logger.warning("PDF metadata stamping failed; shipping default metadata",
                       exc_info=True)
        return pdf_bytes


# ---------------------------------------------------------------------------
# ConversationRenderSession — SHARED browser lifecycle + route driving
# ---------------------------------------------------------------------------

class ConversationRenderSession:
    """Drives the ``/print`` route in headless Chromium.

    SHARED: Card II reuses this to obtain rendered self-contained HTML via
    :meth:`extract_html`; the PDF path calls :meth:`capture_pdf`.  Both share
    the launch (via :func:`build_chromium_launch_args`), navigation, injection
    and readiness-wait, so neither export launches its own Chromium.
    """

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._base_url: str = ""
        self._lock = asyncio.Lock()

    # -- Lifecycle --------------------------------------------------------

    @classmethod
    async def create(cls, server_port: int = 6969) -> "ConversationRenderSession":
        if not _check_playwright():
            raise ImportError(
                "Playwright is required for headless conversation PDF export. "
                "Install it with:\n"
                "  pip install playwright && playwright install chromium"
            )
        instance = cls()
        instance._base_url = f"http://localhost:{server_port}"
        await instance._ensure_browser()
        return instance

    async def _ensure_browser(self) -> None:
        if self._browser and self._browser.is_connected():
            return
        from playwright.async_api import async_playwright
        # Reuse the diagram renderer's launch-arg + sandbox-posture logic —
        # do NOT duplicate it. --no-sandbox stays opt-in via the same env var.
        from app.services.diagram_renderer import build_chromium_launch_args
        from app.config.env_registry import ziya_env

        self._playwright = await async_playwright().start()
        no_sandbox = ziya_env("ZIYA_CHROMIUM_NO_SANDBOX")
        if no_sandbox:
            logger.warning(
                "Headless Chromium launching with --no-sandbox "
                "(ZIYA_CHROMIUM_NO_SANDBOX is set) — the renderer sandbox is "
                "disabled; this weakens isolation when rendering model-supplied "
                "conversation HTML."
            )
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=build_chromium_launch_args(no_sandbox=no_sandbox),
        )
        logger.info("Headless Chromium launched for conversation PDF export")

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Headless Chromium closed (conversation export)")

    # -- Core: render a conversation on the /print route ------------------

    async def _open_and_render(
        self,
        page: Any,
        payload: Dict[str, Any],
        *,
        timeout_ms: int,
    ) -> Dict[str, Any]:
        """Navigate to /print, inject the payload, wait for completion.

        Returns a diagnostics dict.  Raises RuntimeError on injection failure
        or readiness timeout, with page console/pageerror context attached.
        """
        console_log: List[str] = []
        pageerror_log: List[str] = []

        def _on_console(msg: Any) -> None:
            try:
                console_log.append(f"[{msg.type}] {msg.text}")
            except Exception:  # pragma: no cover - defensive
                pass

        def _on_pageerror(err: Any) -> None:
            try:
                pageerror_log.append(str(err))
            except Exception:  # pragma: no cover - defensive
                pass

        page.on("console", _on_console)
        page.on("pageerror", _on_pageerror)

        response = await page.goto(
            f"{self._base_url}/print",
            wait_until="networkidle",
            timeout=timeout_ms,
        )

        # Fail loudly if the shell itself did not load.  A non-2xx body (e.g. a
        # 404 JSON error when the /print SPA passthrough is missing from
        # app/routes/page_routes.py) reaches "networkidle" instantly, so the
        # goto above looks successful and the failure only surfaces later as an
        # opaque "window.__renderConversation is not a function" TypeError.
        # Checking the status here names the actual problem.
        # Require a real int: a mocked page.goto() yields a Mock whose .status
        # is another Mock, and comparing that to an int raises TypeError.  A
        # driver guard must never be able to crash the render it is protecting.
        status = getattr(response, "status", None) if response is not None else None
        if isinstance(status, int) and not isinstance(status, bool) and status >= 400:
            raise RuntimeError(
                f"GET {self._base_url}/print returned HTTP {status}; the SPA "
                f"shell never loaded, so the /print route could not mount. "
                f"Check that a server-side shell passthrough for /print is "
                f"registered in app/routes/page_routes.py."
            )

        # Force a LIGHT prefers-color-scheme for the whole render (SHARED — this
        # runs before both capture_pdf() and extract_html()).  `prefers-color-
        # scheme` is a NON-REACTIVE theme vector: no amount of React
        # setTheme('light') can override a `@media (prefers-color-scheme: dark)`
        # rule, because it keys off the host/OS colour scheme, not app state.  A
        # headless context that inherits a dark OS/CI colour scheme would
        # otherwise let such a rule paint dark regions onto the light export
        # page (user defect #6).  Emulating light here neutralises that vector
        # for EVERY shared-route consumer; capture_pdf()'s later
        # emulate_media(media="screen") leaves the colour scheme untouched.
        try:
            await page.emulate_media(color_scheme="light")
        except Exception:  # pragma: no cover - older Playwright / defensive
            pass

        # A whole conversation easily exceeds URL length limits, so inject via
        # the imperative API (the large-payload channel /render also exposes).
        payload_with_timeout = {**payload, "renderTimeoutMs": timeout_ms}
        payload_json = json.dumps(payload_with_timeout)

        # Wait for PrintRenderPage to define the injector before calling it.
        # Calling it directly turns *any* mount failure -- 404 shell, a bundle
        # built before /print existed, a lazy-chunk load error, a crash in a
        # provider above the component -- into the same misleading
        # "not a function" TypeError.  Waiting converts all of those into a
        # timeout carrying the page diagnostics that identify the real cause.
        try:
            await page.wait_for_function(
                "() => typeof window.__renderConversation === 'function'",
                timeout=timeout_ms,
            )
        except Exception as inject_err:
            diag = await self._collect_diagnostics(page, console_log, pageerror_log)
            logger.error(
                "window.__renderConversation was never defined. Diagnostics: %s",
                diag,
            )
            raise RuntimeError(
                f"window.__renderConversation was never defined after "
                f"{timeout_ms}ms -- PrintRenderPage did not mount. "
                f"page_url={page.url!r} "
                f"page_error={diag.get('page_error')!r} "
                f"console_tail={diag.get('console_tail')!r} "
                f"pageerrors={diag.get('pageerrors')!r}"
            ) from inject_err

        success = await page.evaluate(
            f"window.__renderConversation({json.dumps(payload_json)})"
        )
        if not success:
            error = await page.get_attribute("#print-render-root", "data-error")
            raise RuntimeError(f"Conversation injection failed: {error}")

        try:
            await page.wait_for_function(
                """() => {
                    const root = document.getElementById('print-render-root');
                    const status = root?.getAttribute('data-render-status');
                    return status === 'complete' || status === 'error';
                }""",
                timeout=timeout_ms + 5_000,
            )
        except Exception as wait_err:
            diag = await self._collect_diagnostics(page, console_log, pageerror_log)
            logger.error(
                "Conversation render wait_for_function timed out. Diagnostics: %s",
                diag,
            )
            raise RuntimeError(
                f"Conversation render timed out after {timeout_ms + 5000}ms. "
                f"page_status={diag.get('render_status')!r} "
                f"last_event={diag.get('last_event')!r} "
                f"elapsed_ms={diag.get('elapsed_ms')!r} "
                f"page_error={diag.get('page_error')!r} "
                f"dom_counts={diag.get('dom_counts')!r} "
                f"console_tail={diag.get('console_tail')!r} "
                f"pageerrors={diag.get('pageerrors')!r}"
            ) from wait_err

        render_status = await page.get_attribute(
            "#print-render-root", "data-render-status"
        )
        if render_status == "error":
            error_msg = await page.get_attribute("#print-render-root", "data-error")
            diag = await self._collect_diagnostics(page, console_log, pageerror_log)
            logger.error(
                "Conversation render reported error: %s. Diagnostics: %s",
                error_msg, diag,
            )
            raise RuntimeError(f"Conversation render failed: {error_msg}")

        return {
            "console_log": console_log,
            "pageerror_log": pageerror_log,
        }

    async def _collect_diagnostics(
        self, page: Any, console_log: List[str], pageerror_log: List[str]
    ) -> Dict[str, Any]:
        diag: Dict[str, Any] = {
            "console_tail": console_log[-20:],
            "pageerrors": pageerror_log[-10:],
        }
        for attr in ("data-render-status", "data-error",
                     "data-elapsed-ms", "data-last-event"):
            key = attr.replace("data-", "").replace("-", "_")
            try:
                diag[key] = await page.get_attribute("#print-render-root", attr)
            except Exception as e:  # pragma: no cover - defensive
                diag[key] = f"<err {e!r}>"
        try:
            diag["dom_counts"] = await page.evaluate(
                """() => {
                    const c = document.getElementById('print-render-content');
                    if (!c) return {missing_container: true};
                    return {
                        svg: c.querySelectorAll('svg').length,
                        img: c.querySelectorAll('img').length,
                        katex: c.querySelectorAll('.katex').length,
                        tokens: c.querySelectorAll('span.token').length,
                        html_len: c.innerHTML.length,
                    };
                }"""
            )
        except Exception as e:  # pragma: no cover - defensive
            diag["dom_eval_error"] = repr(e)
        return diag

    # -- PDF capture (PDF-specific) --------------------------------------

    async def capture_pdf(
        self,
        payload: Dict[str, Any],
        *,
        timeout_ms: int = 60_000,
        margin: Optional[Dict[str, str]] = None,
        outline: bool = True,
        metadata: Optional[Dict[str, str]] = None,
    ) -> bytes:
        """Render the conversation and return PDF bytes.

        Uses ``page.pdf()`` with A4, explicit margins and
        ``print_background=True`` so backgrounds (diff colours, highlight
        spans, code-block fills) are preserved.

        When ``outline`` is true (default) a navigable per-message bookmark tree
        is synthesised (QUAL-01): a PROBE capture with out-of-flow sentinels
        establishes each message's start page, then the sentinels are removed
        and a CLEAN capture is post-processed with pypdf to stamp the outline.
        The outline pass is best-effort — any failure returns the clean bytes
        unchanged, so it can never break an otherwise-successful capture.

        Document metadata (QUAL-02) is always stamped on the returned bytes:
        the conversation title as /Title (never the app-shell <title>), plus
        /Author /Subject /Creator.  ``metadata`` overrides the fields derived
        from the payload; pass an explicit ``{}`` to skip the metadata pass.
        """
        async with self._lock:
            await self._ensure_browser()

        # QUAL-02: conversation-specific document metadata.  Computed from the
        # payload (or the ``metadata`` override) and stamped on the final bytes,
        # so /Title is the conversation title, not the app-shell <title>, and
        # /Author /Subject /Creator are set rather than Chromium defaults.
        doc_metadata = metadata if metadata is not None else _build_document_metadata(
            title=payload.get("title"),
            model=payload.get("model"),
            provider=payload.get("provider"),
        )

        # page.pdf() requires headless Chromium and the "print" media emulation
        # off (we want screen styles, which is what the chat UI uses).
        page = await self._browser.new_page()
        try:
            await self._open_and_render(page, payload, timeout_ms=timeout_ms)
            # Use screen media so the rendered-as-on-screen styling is kept;
            # the /print component itself applies print-friendly light theme.
            await page.emulate_media(media="screen")
            pdf_margin = margin or {
                "top": "12mm", "bottom": "16mm", "left": "10mm", "right": "10mm",
            }

            async def _capture() -> bytes:
                return await page.pdf(
                    format="A4",
                    print_background=True,
                    margin=pdf_margin,
                    prefer_css_page_size=False,
                )

            if not outline:
                return _apply_document_metadata(await _capture(), doc_metadata)

            # QUAL-01: two-pass outline synthesis.  Failures degrade to a plain
            # single-pass capture rather than losing the export.
            anchors: List[Dict[str, Any]] = []
            try:
                anchors = await page.evaluate(_OUTLINE_SENTINEL_INJECT_JS)
            except Exception:  # pragma: no cover - defensive
                logger.warning("outline sentinel injection failed", exc_info=True)
                anchors = []

            if not anchors:
                return _apply_document_metadata(await _capture(), doc_metadata)

            probe_pdf = await _capture()
            try:
                await page.evaluate(_OUTLINE_SENTINEL_REMOVE_JS)
            except Exception:  # pragma: no cover - defensive
                logger.warning("outline sentinel removal failed", exc_info=True)
            clean_pdf = await _capture()

            mapping = _map_sentinels_to_pages(probe_pdf, anchors)
            # QUAL-02: stamp document metadata in the same post-process cycle.
            outlined = _synthesize_outline(clean_pdf, mapping)
            return _apply_document_metadata(outlined, doc_metadata)
        finally:
            await page.close()

    # -- HTML extraction (SHARED — Card II) ------------------------------

    async def extract_html(
        self, payload: Dict[str, Any], *, timeout_ms: int = 60_000
    ) -> str:
        """Render the conversation and return the self-contained rendered DOM.

        SHARED: Card II's HTML export uses this to get the fully-rendered
        conversation (Prism/KaTeX/diff/diagram DOM) as an HTML string, instead
        of re-implementing the render.  Not used by the PDF path.
        """
        async with self._lock:
            await self._ensure_browser()
        page = await self._browser.new_page()
        try:
            await self._open_and_render(page, payload, timeout_ms=timeout_ms)
            return await page.evaluate(
                "() => document.getElementById('print-render-root').outerHTML"
            )
        finally:
            await page.close()

    async def extract_export_html(
        self,
        payload: Dict[str, Any],
        *,
        timeout_ms: int = 60_000,
        embed_images: bool = True,
    ) -> str:
        """Render the conversation and return a SELF-CONTAINED standalone HTML doc.

        This is the Card II route-driven high-fidelity export.  Unlike
        :meth:`extract_html` (which returns only the raw ``#print-render-root``
        fragment for measurement), this produces a complete ``<!DOCTYPE html>``
        document that opens faithfully offline in any browser:

          * every applicable CSS rule from the app's own stylesheets is INLINED
            into a single ``<style>`` (nothing is ``<link>``-ed);
          * ``@media (prefers-color-scheme: dark)`` rules are DROPPED and the
            document is pinned to ``color-scheme: light`` so a downloaded file
            never flips dark on a dark-mode machine (HTML-04);
          * interactive artifacts (buttons, apply-changes controls, toolbars,
            scroll indicators, ``contenteditable``) and ALL ``<script>`` nodes
            are removed — the file is inert;
          * same-origin ``<img>`` are embedded as ``data:`` URIs when
            ``embed_images`` (so no network fetch is needed to view them);
          * ``on*`` inline handlers and ``javascript:``/``vbscript:`` hrefs are
            stripped as a defense-in-depth pass over the already-escaped DOM.

        The heavy lifting runs IN THE PAGE (one ``page.evaluate`` after the
        render completes) so it operates on the live computed DOM.  Security
        posture is inherited from :meth:`_open_and_render` (Chromium sandbox via
        ``build_chromium_launch_args``, no remote-resource loading enabled).
        """
        async with self._lock:
            await self._ensure_browser()
        page = await self._browser.new_page()
        try:
            await self._open_and_render(page, payload, timeout_ms=timeout_ms)
            title = payload.get("title") or "Ziya Conversation Export"
            footer_html = payload.get("footerHtml") or ""
            return await page.evaluate(
                _SELF_CONTAINED_HTML_JS,
                {
                    "title": title,
                    "footerHtml": footer_html,
                    "embedImages": bool(embed_images),
                },
            )
        finally:
            await page.close()


# ---------------------------------------------------------------------------
# Module-level singleton (mirrors diagram_renderer's get/shutdown pattern)
# ---------------------------------------------------------------------------

_session_instance: Optional[ConversationRenderSession] = None
_session_lock = asyncio.Lock()


async def get_render_session(server_port: int = 6969) -> ConversationRenderSession:
    global _session_instance
    async with _session_lock:
        if _session_instance is None:
            _session_instance = await ConversationRenderSession.create(server_port)
        return _session_instance


async def shutdown_render_session() -> None:
    global _session_instance
    if _session_instance:
        await _session_instance.close()
        _session_instance = None


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

def build_print_payload(
    messages: List[Dict[str, Any]],
    *,
    options: Optional[Dict[str, Any]] = None,
    footer_html: Optional[str] = None,
    title: str = "Ziya Session Transcript",
) -> Dict[str, Any]:
    """Assemble the JSON payload injected into the /print route.

    The page performs option-based filtering itself (single source of truth),
    so we pass the raw messages plus the normalized option knobs and, when
    requested, a pre-rendered footer HTML fragment (so the PDF footer matches
    the markdown/html exports byte-for-byte).
    """
    opts = normalize_render_options(options)
    payload: Dict[str, Any] = {
        "title": title,
        "messages": messages,
        "options": opts,
    }
    if opts.get("includeFooter") and footer_html:
        payload["footerHtml"] = footer_html
    return payload


# ---------------------------------------------------------------------------
# Public entry point (PDF-specific)
# ---------------------------------------------------------------------------

async def export_conversation_pdf(
    *,
    messages: Optional[List[Dict[str, Any]]] = None,
    project_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    title: str = "Ziya Session Transcript",
    version: str = "0.3.8",
    model: str = "unknown",
    provider: str = "unknown",
    server_port: int = 6969,
    timeout_ms: int = 60_000,
) -> Tuple[bytes, Dict[str, Any]]:
    """Render a conversation to PDF via the headless /print route.

    Messages come from ``messages`` when supplied, else are loaded server-side
    by ``(project_id, conversation_id)``.  Returns ``(pdf_bytes, meta)``.

    Raises:
        ImportError: Playwright not installed (mirrors diagram renderer).
        LookupError: conversation could not be found server-side.
        ValueError:  no message source supplied.
    """
    if messages is None:
        if not (project_id and conversation_id):
            raise ValueError(
                "export_conversation_pdf requires either messages or "
                "(project_id and conversation_id)"
            )
        messages = load_conversation_messages(project_id, conversation_id)
        if messages is None:
            raise LookupError(
                f"Conversation {conversation_id!r} not found in project "
                f"{project_id!r}"
            )

    opts = normalize_render_options(options)

    footer_html = None
    if opts.get("includeFooter"):
        # Reuse the shared footer logic so PDF header/footer matches the other
        # exports (version/model/provider plumbing).
        from app.utils.conversation_exporter import _create_footer
        footer_html = _create_footer("public", version, model, provider, "html")

    payload = build_print_payload(
        messages, options=opts, footer_html=footer_html, title=title,
    )

    # QUAL-02: derive conversation-specific document metadata from the values
    # export_conversation_pdf already has (title/model/provider), rather than
    # re-plumbing them through the injected payload.
    doc_metadata = _build_document_metadata(
        title=title, model=model, provider=provider,
    )

    session = await get_render_session(server_port)
    pdf_bytes = await session.capture_pdf(
        payload, timeout_ms=timeout_ms, metadata=doc_metadata,
    )

    meta = {
        "message_count": len(messages),
        "size": len(pdf_bytes),
        "options": opts,
        "conversation_id": conversation_id,
    }
    return pdf_bytes, meta
