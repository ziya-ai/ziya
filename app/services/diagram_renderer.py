"""
Headless diagram rendering service using Playwright.

Launches a persistent headless Chromium instance and navigates to the
/render route of the frontend.  Diagram specs are injected via
page.evaluate(), and the rendered output is captured as PNG or SVG.

This produces pixel-perfect output because it runs the exact same
D3Renderer pipeline, plugins, and post-render enhancers as the chat UI.

Usage from Python:
    renderer = await DiagramRenderer.create(server_port=6969)
    png_bytes = await renderer.render_diagram({
        "type": "mermaid",
        "definition": "graph LR\\n  A-->B",
        "theme": "dark",
    })
    await renderer.close()

Requires: ``pip install playwright`` + ``playwright install chromium``
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

# Playwright is an optional dependency — imported lazily so the rest of
# the application works without it.
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


def build_chromium_launch_args(no_sandbox: bool = False) -> list:
    """Build the Chromium launch args for the headless renderer.

    SECURITY (F-027): the Chromium sandbox is the primary defense against
    renderer-process exploits, and this renderer processes attacker-influenced
    SVG/HTML from model output. ``--no-sandbox`` is therefore OFF by default
    and only added when the operator explicitly opts in via
    ``ZIYA_CHROMIUM_NO_SANDBOX`` (needed when the sandbox cannot run, e.g.
    running as root in a container). The caller logs a warning when it's on.
    """
    args = ["--disable-gpu", "--disable-dev-shm-usage"]
    if no_sandbox:
        args.append("--no-sandbox")
    return args


class DiagramRenderer:
    """Headless Chromium renderer for diagram specs."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._base_url: str = ""
        self._lock = asyncio.Lock()

    # -- Lifecycle ----------------------------------------------------

    @classmethod
    async def create(cls, server_port: int = 6969) -> "DiagramRenderer":
        """Factory that initialises the browser.  Raises ImportError if
        Playwright is not installed."""
        if not _check_playwright():
            raise ImportError(
                "Playwright is required for headless diagram rendering. "
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
        from app.config.env_registry import ziya_env
        self._playwright = await async_playwright().start()
        no_sandbox = ziya_env("ZIYA_CHROMIUM_NO_SANDBOX")
        if no_sandbox:
            logger.warning(
                "Headless Chromium launching with --no-sandbox "
                "(ZIYA_CHROMIUM_NO_SANDBOX is set) — the renderer sandbox is "
                "disabled; this weakens isolation when rendering model-supplied "
                "SVG/HTML."
            )
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=build_chromium_launch_args(no_sandbox=no_sandbox),
        )
        logger.info("Headless Chromium launched for diagram rendering")

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Headless Chromium closed")

    # -- Diagnostics --------------------------------------------------

    async def _collect_diagnostics(
        self,
        page: Any,
        spec: dict[str, Any],
        console_log: list[str],
        pageerror_log: list[str],
    ) -> dict[str, Any]:
        """Best-effort dump of everything the harness page can tell us
        about why a render didn't reach a terminal state."""
        diag: dict[str, Any] = {
            "spec_type": spec.get("type"),
            "console_tail": console_log[-20:],
            "pageerrors": pageerror_log[-10:],
        }
        try:
            diag["render_status"] = await page.get_attribute(
                "#diagram-render-root", "data-render-status"
            )
            diag["page_error"] = await page.get_attribute(
                "#diagram-render-root", "data-error"
            )
            diag["elapsed_ms"] = await page.get_attribute(
                "#diagram-render-root", "data-elapsed-ms"
            )
            diag["last_event"] = await page.get_attribute(
                "#diagram-render-root", "data-last-event"
            )
        except Exception as e:
            diag["attr_read_error"] = repr(e)
        try:
            diag["dom_counts"] = await page.evaluate(
                """() => {
                    const c = document.getElementById('diagram-render-container');
                    if (!c) return {missing_container: true};
                    return {
                        children: c.children.length,
                        svg: c.querySelectorAll('svg').length,
                        canvas: c.querySelectorAll('canvas').length,
                        img: c.querySelectorAll('img').length,
                        html_len: c.innerHTML.length,
                        html_head: c.innerHTML.slice(0, 500),
                    };
                }"""
            )
        except Exception as e:
            diag["dom_eval_error"] = repr(e)
        return diag

    # -- Rendering ----------------------------------------------------

    async def render_diagram(
        self,
        spec: dict[str, Any],
        *,
        format: Literal["png", "svg"] = "png",
        viewport_width: int = 1280,
        viewport_height: int = 960,
        timeout_ms: int = 30_000,
    ) -> bytes:
        """Render a diagram spec and return image bytes.

        Parameters
        ----------
        spec : dict
            Must include ``type`` and ``definition``.  Optional keys:
            ``theme`` ('dark'|'light'), ``width``, ``height``, ``title``.
        format : 'png' | 'svg'
            Output format.  SVG extraction works for SVG-based renderers
            only; falls back to PNG screenshot for canvas-based ones.
        viewport_width, viewport_height : int
            Headless browser viewport dimensions.
        timeout_ms : int
            Maximum time to wait for the render to complete.
        """
        async with self._lock:
            await self._ensure_browser()

        page = await self._browser.new_page(
            viewport={"width": viewport_width, "height": viewport_height},
        )

        # Capture console messages and page errors so we can include them
        # in any diagnostic dump on failure.
        console_log: list[str] = []
        pageerror_log: list[str] = []

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

        try:
            # Navigate to the render harness page
            await page.goto(
                f"{self._base_url}/render",
                wait_until="networkidle",
                timeout=timeout_ms,
            )

            # Inject the spec via the imperative API. Tell the in-page
            # harness exactly how long it has to render so its safety
            # timeout fires before Playwright's wait_for_function does.
            spec_with_timeout = {**spec, "renderTimeoutMs": timeout_ms}
            spec_json = json.dumps(spec_with_timeout)
            success = await page.evaluate(
                f"window.__renderDiagram({json.dumps(spec_json)})"
            )
            if not success:
                error = await page.get_attribute("#diagram-render-root", "data-error")
                raise RuntimeError(f"Spec injection failed: {error}")

            # Wait for the render to complete.
            # The in-page safety timeout (DiagramRenderPage) is 30s and starts
            # after React mounts the container — give Playwright extra headroom
            # so the in-page timer always fires first and sets a terminal status.
            try:
                await page.wait_for_function(
                    """() => {
                        const root = document.getElementById('diagram-render-root');
                        const status = root?.getAttribute('data-render-status');
                        return status === 'complete' || status === 'error';
                    }""",
                    timeout=timeout_ms + 5_000,
                )
            except Exception as wait_err:
                # Playwright's wait timed out before the page reached a
                # terminal status. Pull every diagnostic the page exposes
                # so the caller can see *why*.
                diag = await self._collect_diagnostics(
                    page, spec, console_log, pageerror_log
                )
                logger.error(
                    "Diagram render wait_for_function timed out (type=%s). "
                    "Diagnostics: %s",
                    spec.get("type"),
                    diag,
                )
                raise RuntimeError(
                    f"Diagram render timed out after {timeout_ms + 5000}ms "
                    f"(type={spec.get('type')}). "
                    f"page_status={diag.get('render_status')!r} "
                    f"last_event={diag.get('last_event')!r} "
                    f"elapsed_ms={diag.get('elapsed_ms')!r} "
                    f"page_error={diag.get('page_error')!r} "
                    f"dom_counts={diag.get('dom_counts')!r} "
                    f"console_tail={diag.get('console_tail')!r} "
                    f"pageerrors={diag.get('pageerrors')!r}"
                ) from wait_err

            # Check for errors
            render_status = await page.get_attribute(
                "#diagram-render-root", "data-render-status"
            )
            if render_status == "error":
                error_msg = await page.get_attribute(
                    "#diagram-render-root", "data-error"
                )
                diag = await self._collect_diagnostics(
                    page, spec, console_log, pageerror_log
                )
                logger.error(
                    "Diagram render reported error (type=%s): %s. Diagnostics: %s",
                    spec.get("type"),
                    error_msg,
                    diag,
                )
                raise RuntimeError(f"Diagram render failed: {error_msg}")

            # Capture the output
            container = page.locator("#diagram-render-container")

            if format == "svg":
                svg_content = await container.evaluate(
                    """el => {
                        const svg = el.querySelector('svg');
                        return svg ? svg.outerHTML : null;
                    }"""
                )
                if svg_content:
                    return svg_content.encode("utf-8")
                # Fall through to PNG if no SVG found
                logger.info("No SVG element found, falling back to PNG screenshot")

            # PNG screenshot of just the diagram container
            return await container.screenshot(type="png")

        finally:
            await page.close()


# -- Module-level singleton ----------------------------------------------

_renderer_instance: Optional[DiagramRenderer] = None
_renderer_lock = asyncio.Lock()


async def get_diagram_renderer(server_port: int = 6969) -> DiagramRenderer:
    """Get or create the singleton DiagramRenderer instance."""
    global _renderer_instance
    async with _renderer_lock:
        if _renderer_instance is None:
            _renderer_instance = await DiagramRenderer.create(server_port)
        return _renderer_instance


async def shutdown_diagram_renderer() -> None:
    """Shut down the singleton renderer (call during app shutdown)."""
    global _renderer_instance
    if _renderer_instance:
        await _renderer_instance.close()
        _renderer_instance = None
