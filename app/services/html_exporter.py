"""
Dual-mode conversation → HTML export service.

HTML export is DUAL-MODE (Card II architectural decision):

  * **route mode** (high fidelity) — when Playwright/Chromium is available,
    drive the SHARED ``/print`` route through
    :class:`app.services.pdf_exporter.ConversationRenderSession` and extract a
    self-contained standalone HTML document from the real React renderer
    (Prism syntax highlighting, KaTeX math, react-diff-view per-line diff
    colouring, D3/mermaid diagrams).  Styles are inlined, ``prefers-color-
    scheme: dark`` rules are dropped, images are embedded as data URIs, and
    interactive artifacts + scripts are stripped.

  * **python mode** (fallback) — when Playwright is absent (no browser
    installed) the existing regex-based
    :func:`app.utils.conversation_exporter.export_conversation_for_paste`
    path is used.  Its fidelity ceiling is lower (Pygments highlighting,
    MathML math, background-only diff colouring) but it never requires a
    browser, so HTML export NEVER hard-fails merely because Chromium is
    missing.

Mode selection is EXPLICIT and TESTABLE (:func:`select_html_mode`): an
explicit ``mode=`` argument wins, else the ``ZIYA_HTML_EXPORT_MODE`` env var,
else auto-detect (route when Playwright is importable, python otherwise).  The
returned payload always reports which mode actually produced the output and
whether the lower-fidelity fallback was used (``mode``, ``fidelity``,
``fallback_reason``) so the caller — and the UI — can tell.

If ``route`` is selected/forced but the render fails at runtime (Playwright
absent, ``/print`` route missing from the bundle, render timeout), the service
DEGRADES to python mode and records the reason rather than raising, unless
``mode="route"`` was forced AND the caller wants hard failures.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.utils.logging_utils import logger

# Paste targets (GitHub Gist, plugin services) reject very large payloads.  A
# route-mode export inlines the whole app stylesheet + data-URI images, which
# can be large.  This soft cap lets callers request a size guard; when the
# route output exceeds it, ``export_conversation_html`` records a
# ``size_warning`` (and, for paste use, callers may choose python mode which
# is smaller).  Bytes; ~ conservative for a Gist.
DEFAULT_PASTE_SIZE_LIMIT = 10 * 1024 * 1024  # 10 MiB

_VALID_MODES = ("route", "python", "auto")


def _playwright_available() -> bool:
    """True when Playwright is importable (the browser tier can be attempted)."""
    try:
        from app.services.pdf_exporter import _check_playwright
        return _check_playwright()
    except Exception:  # pragma: no cover - defensive
        return False


def select_html_mode(explicit: Optional[str] = None) -> str:
    """Resolve the HTML export mode deterministically.

    Precedence (highest first):
      1. ``explicit`` argument (``"route"`` | ``"python"`` | ``"auto"``)
      2. ``ZIYA_HTML_EXPORT_MODE`` environment variable (same values)
      3. auto: ``"route"`` when Playwright is importable, else ``"python"``

    Returns a CONCRETE mode — never ``"auto"`` — so the result is directly
    actionable and testable.  ``"route"`` is returned even when Playwright is
    absent IF explicitly forced, so a forced route request surfaces its own
    runtime failure (and then degrades) rather than being silently rewritten.
    """
    candidate = (explicit or os.environ.get("ZIYA_HTML_EXPORT_MODE") or "auto").strip().lower()
    if candidate not in _VALID_MODES:
        logger.warning("Unknown ZIYA HTML export mode %r; using auto", candidate)
        candidate = "auto"

    if candidate == "auto":
        return "route" if _playwright_available() else "python"
    return candidate


def _python_export(
    messages: List[Dict[str, Any]],
    *,
    target: str,
    version: str,
    model: str,
    provider: str,
    captured_diagrams: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Run the regex/Python fallback exporter and shape the result."""
    from app.utils.conversation_exporter import export_conversation_for_paste

    result = export_conversation_for_paste(
        messages,
        format_type="html",
        target=target,
        captured_diagrams=captured_diagrams,
        version=version,
        model=model,
        provider=provider,
    )
    result["mode"] = "python"
    result["fidelity"] = "fallback"
    return result


async def export_conversation_html(
    messages: List[Dict[str, Any]],
    *,
    mode: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    target: str = "public",
    title: str = "Ziya Conversation Export",
    version: str = "0.3.8",
    model: str = "unknown",
    provider: str = "unknown",
    server_port: int = 6969,
    embed_images: bool = True,
    captured_diagrams: Optional[List[Dict[str, Any]]] = None,
    size_limit: Optional[int] = None,
    timeout_ms: int = 60_000,
) -> Dict[str, Any]:
    """Export a conversation to self-contained HTML using the selected mode.

    Returns a dict with:
      * ``content``          — the HTML string
      * ``mode``             — the mode that actually produced the output
                               (``"route"`` or ``"python"``)
      * ``fidelity``         — ``"high"`` (route) or ``"fallback"`` (python)
      * ``fallback_reason``  — present only when a route attempt degraded to
                               python; a short human-readable reason
      * ``size``             — byte length of ``content``
      * ``size_warning``     — present when ``size`` exceeds ``size_limit``
      * ``filename``, ``format``, ``target``, ``message_count``

    Never raises for a missing browser: a failed/unavailable route render
    degrades to the python fallback with ``fallback_reason`` recorded.
    """
    resolved = select_html_mode(mode)
    fallback_reason: Optional[str] = None

    if resolved == "route":
        try:
            content = await _route_export(
                messages,
                options=options,
                title=title,
                version=version,
                model=model,
                provider=provider,
                server_port=server_port,
                embed_images=embed_images,
                timeout_ms=timeout_ms,
            )
            from datetime import datetime
            result = {
                "content": content,
                "mode": "route",
                "fidelity": "high",
                "format": "html",
                "target": target,
                "filename": f"ziya_conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html",
                "size": len(content),
                "message_count": len(messages),
            }
        except Exception as exc:  # ImportError / RuntimeError / timeout
            # Route tier failed at runtime — DEGRADE to python so HTML export
            # never hard-fails on a missing/broken browser.  Record why.
            fallback_reason = f"route mode unavailable: {exc}"
            logger.warning("HTML route export degraded to python: %s", exc)
            result = _python_export(
                messages, target=target, version=version, model=model,
                provider=provider, captured_diagrams=captured_diagrams,
            )
            result["fallback_reason"] = fallback_reason
    else:
        result = _python_export(
            messages, target=target, version=version, model=model,
            provider=provider, captured_diagrams=captured_diagrams,
        )

    # Paste-target size guard: inlining styles + data-URI images can push a
    # route export past what a Gist / plugin target accepts.  Flag it rather
    # than discovering a runtime rejection.
    limit = size_limit if size_limit is not None else DEFAULT_PASTE_SIZE_LIMIT
    if limit and result.get("size", 0) > limit:
        result["size_warning"] = (
            f"Export is {result['size']} bytes, exceeding the {limit}-byte "
            f"paste-target guard. Consider the .html download instead, or "
            f"disable image embedding."
        )
        logger.info(
            "HTML export size %d exceeds guard %d (mode=%s)",
            result["size"], limit, result.get("mode"),
        )

    return result


async def _route_export(
    messages: List[Dict[str, Any]],
    *,
    options: Optional[Dict[str, Any]],
    title: str,
    version: str,
    model: str,
    provider: str,
    server_port: int,
    embed_images: bool,
    timeout_ms: int,
) -> str:
    """Drive the SHARED /print route and return a self-contained HTML document.

    Reuses Card I's :class:`ConversationRenderSession` (browser lifecycle +
    route driving) — does NOT launch its own Chromium.  Deterministic light
    theme + dark-rule neutralization + self-containment happen in
    ``extract_export_html``.
    """
    from app.services.pdf_exporter import (
        get_render_session,
        build_print_payload,
        normalize_render_options,
    )
    from app.utils.conversation_exporter import _create_footer

    opts = normalize_render_options(options)
    footer_html = None
    if opts.get("includeFooter"):
        # Reuse the shared footer so route/python/PDF exports match.
        footer_html = _create_footer("public", version, model, provider, "html")

    payload = build_print_payload(
        messages, options=opts, footer_html=footer_html, title=title,
    )
    session = await get_render_session(server_port)
    return await session.extract_export_html(
        payload, timeout_ms=timeout_ms, embed_images=embed_images,
    )
