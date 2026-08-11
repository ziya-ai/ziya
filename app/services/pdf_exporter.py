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

        await page.goto(
            f"{self._base_url}/print",
            wait_until="networkidle",
            timeout=timeout_ms,
        )

        # A whole conversation easily exceeds URL length limits, so inject via
        # the imperative API (the large-payload channel /render also exposes).
        payload_with_timeout = {**payload, "renderTimeoutMs": timeout_ms}
        payload_json = json.dumps(payload_with_timeout)
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
    ) -> bytes:
        """Render the conversation and return PDF bytes.

        Uses ``page.pdf()`` with A4, explicit margins and
        ``print_background=True`` so backgrounds (diff colours, highlight
        spans, code-block fills) are preserved.
        """
        async with self._lock:
            await self._ensure_browser()

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
            return await page.pdf(
                format="A4",
                print_background=True,
                margin=pdf_margin,
                prefer_css_page_size=False,
            )
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

    session = await get_render_session(server_port)
    pdf_bytes = await session.capture_pdf(payload, timeout_ms=timeout_ms)

    meta = {
        "message_count": len(messages),
        "size": len(pdf_bytes),
        "options": opts,
        "conversation_id": conversation_id,
    }
    return pdf_bytes, meta
