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


# Maximum pixel extent (in EITHER dimension) of a rendered raster we will
# emit. The downstream model image pipeline (Bedrock / Anthropic vision) hard-
# rejects any image whose width OR height exceeds 8000px with
# "image.source.base64.data: At least one of the image dimensions exceed max
# allowed size: 8000 pixels" (a ValidationException that aborts the whole
# turn). A diagram whose NATURAL layout is enormous — a deep top-to-bottom
# inheritance chain, a very wide record, a huge grid — produces a full-element
# screenshot far past that cap, so the render "succeeds" yet the bytes are
# unusable. We defensively downscale any such raster here, at the single choke
# point every caller (MCP tool + HTTP route) funnels through, so no oversized
# PNG can ever leave the renderer. This is the upper-bound analog of the
# graphviz sub-pixel-collapse clamp (frontend Issue 33).
IMAGE_MAX_DIMENSION_PX = 8000


def clamp_png_dimensions(
    png_bytes: bytes, max_dim: int = IMAGE_MAX_DIMENSION_PX
) -> bytes:
    """Downscale a PNG so neither dimension exceeds ``max_dim`` pixels.

    Aspect ratio is preserved; the largest dimension is scaled down to exactly
    ``max_dim`` and the other dimension scaled proportionally (floored at 1px).
    A PNG already within the cap is returned BYTE-IDENTICAL (no re-encode), so
    this is a gap-fill for the oversized case, never a blanket re-compression.

    This is a PURE, side-effect-free helper (given the same input it always
    returns the same output) so it can be unit-tested without a browser. It is
    also defensive: any decode/encode failure, a non-PNG payload, or a missing
    Pillow install returns the original bytes unchanged — a guard must never be
    able to destroy the image it is protecting.
    """
    if not isinstance(png_bytes, (bytes, bytearray)) or len(png_bytes) < 8:
        return png_bytes
    if not isinstance(max_dim, int) or max_dim < 1:
        return png_bytes
    try:
        import io

        from PIL import Image
    except Exception:  # pragma: no cover - Pillow always present in this app
        return png_bytes

    # These bytes are our OWN trusted render output; the whole point is to
    # decode a deliberately-large screenshot so we can shrink it. Pillow's
    # decompression-bomb guard would otherwise raise on very large rasters and
    # send us down the fallback path — returning the oversized bytes unchanged,
    # which is exactly the failure we are here to prevent. Neutralize the guard
    # for the duration of this decode, restoring the prior value afterwards.
    prev_max_pixels = getattr(Image, "MAX_IMAGE_PIXELS", None)
    try:
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(io.BytesIO(png_bytes)) as img:
            width, height = img.size
            if width <= max_dim and height <= max_dim:
                # Within the cap — do not touch the bytes.
                return png_bytes

            scale = max_dim / float(max(width, height))
            new_width = max(1, int(width * scale))
            new_height = max(1, int(height * scale))
            # Guard against rounding leaving a dimension one pixel over.
            new_width = min(new_width, max_dim)
            new_height = min(new_height, max_dim)

            resized = img.resize((new_width, new_height), Image.LANCZOS)
            out = io.BytesIO()
            resized.save(out, format="PNG")
            logger.info(
                "Downscaled oversized render raster %dx%d -> %dx%d "
                "(max_dim=%d) so the downstream image pipeline accepts it",
                width,
                height,
                new_width,
                new_height,
                max_dim,
            )
            return out.getvalue()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "clamp_png_dimensions failed (%s); returning original bytes", exc
        )
        return png_bytes
    finally:
        # Always restore the process-wide decompression-bomb limit.
        Image.MAX_IMAGE_PIXELS = prev_max_pixels


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

    # -- Page acquisition (for harnesses driving a route other than
    # /render) ---------------------------------------------------------

    @property
    def base_url(self) -> str:
        """Origin this renderer's pages are served from."""
        return self._base_url

    async def acquire_page(
        self, *, viewport_width: int = 1280, viewport_height: int = 960,
    ) -> Any:
        """Return a fresh page on the shared browser.

        Exists so a harness that must screenshot the real application (rather
        than the isolated /render page) can reuse this one warm Chromium
        instead of launching a second: startup is ~1s and a duplicate instance
        doubles the memory floor for the life of the server.  ``new_page``
        gives each caller its own browser context, so localStorage /
        sessionStorage seeded by one caller cannot leak into another -- which
        the chat-message renderer relies on, since it seeds the selected
        conversation id into sessionStorage.

        The caller owns the returned page and MUST close it.
        """
        async with self._lock:
            await self._ensure_browser()
        return await self._browser.new_page(
            viewport={"width": viewport_width, "height": viewport_height},
        )

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

    @staticmethod
    def _classify_console_log(console_log: list[str]) -> dict[str, list[str]]:
        """Split the raw ``[type] text`` console log into warning/error
        buckets, dropping routine ``[log]``/``[info]``/``[debug]`` noise.

        This is what lets a caller distinguish "rendered, but the mermaid
        plugin logged a fixup warning" from a clean render — the console
        listener was already wired up (for the timeout/error diagnostic
        dump), but nothing surfaced it on the SUCCESS path, so a render
        that completes with warnings looked identical to one with none.
        """
        warnings: list[str] = []
        errors: list[str] = []
        for entry in console_log:
            if entry.startswith("[warning]") or entry.startswith("[warn]"):
                warnings.append(entry)
            elif entry.startswith("[error]"):
                errors.append(entry)
        return {"warnings": warnings, "errors": errors}

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
        """Render a diagram spec and return image bytes only.

        Back-compat wrapper around ``render_diagram_with_diagnostics`` for
        callers (HTTP route, conversation exporter) that only want the
        image and have no use for console diagnostics.
        """
        image_bytes, _diagnostics = await self.render_diagram_with_diagnostics(
            spec, format=format, viewport_width=viewport_width,
            viewport_height=viewport_height, timeout_ms=timeout_ms,
        )
        return image_bytes

    async def render_diagram_with_diagnostics(
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
            response = await page.goto(
                f"{self._base_url}/render",
                wait_until="networkidle",
                timeout=timeout_ms,
            )

            # Fail loudly if the shell itself did not load.  A non-2xx body
            # (e.g. a 404 when the /render SPA passthrough is missing from
            # app/routes/page_routes.py) reaches "networkidle" instantly, so
            # the goto looks successful and the failure only surfaces later as
            # an opaque "window.__renderDiagram is not a function" TypeError.
            # Require a real int: a mocked page.goto() yields a Mock whose
            # .status is another Mock, and comparing that to an int raises
            # TypeError.  A driver guard must never be able to crash the
            # render it is protecting.
            status = getattr(response, "status", None) if response is not None else None
            if isinstance(status, int) and not isinstance(status, bool) and status >= 400:
                raise RuntimeError(
                    f"GET {self._base_url}/render returned HTTP {status}; the "
                    f"SPA shell never loaded, so the /render route could not "
                    f"mount. Check that a server-side shell passthrough for "
                    f"/render is registered in app/routes/page_routes.py."
                )

            # Inject the spec via the imperative API. Tell the in-page
            # harness exactly how long it has to render so its safety
            # timeout fires before Playwright's wait_for_function does.
            spec_with_timeout = {**spec, "renderTimeoutMs": timeout_ms}
            spec_json = json.dumps(spec_with_timeout)

            # Wait for DiagramRenderPage to define the injector before calling
            # it.  Calling it directly turns *any* mount failure -- 404 shell,
            # a stale bundle, a lazy-chunk load error, a crash in a provider
            # above the component -- into the same misleading "not a function"
            # TypeError, pointing at the frontend when the fault is elsewhere.
            try:
                await page.wait_for_function(
                    "() => typeof window.__renderDiagram === 'function'",
                    timeout=timeout_ms,
                )
            except Exception as inject_err:
                diag = await self._collect_diagnostics(
                    page, spec, console_log, pageerror_log
                )
                logger.error(
                    "window.__renderDiagram was never defined. Diagnostics: %s",
                    diag,
                )
                raise RuntimeError(
                    f"window.__renderDiagram was never defined after "
                    f"{timeout_ms}ms -- DiagramRenderPage did not mount. "
                    f"page_url={page.url!r} "
                    f"console_tail={diag.get('console_tail')!r} "
                    f"pageerrors={diag.get('pageerrors')!r}"
                ) from inject_err

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

            # Console diagnostics for a render that DID complete. A
            # successful render can still log fixup-layer warnings/errors
            # (e.g. drawio ELK layout fallback, mermaid auto-quote repair)
            # that indicate the output may not faithfully represent the
            # spec even though it produced an image. Surface them here
            # rather than only on the timeout/error diagnostic path.
            console_summary = self._classify_console_log(console_log)
            diagnostics: dict[str, Any] = {
                "console_warnings": console_summary["warnings"],
                "console_errors": console_summary["errors"],
                "pageerrors": pageerror_log,
            }

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
                    return svg_content.encode("utf-8"), diagnostics
                # Fall through to PNG if no SVG found
                logger.info("No SVG element found, falling back to PNG screenshot")

            # PNG screenshot of just the diagram container.
            # A full-element screenshot captures the ENTIRE natural size of the
            # diagram, which for an extreme layout (deep TB inheritance chain,
            # very wide record/method identifier, huge grid) can exceed the
            # downstream 8000px vision-pipeline cap. Clamp before returning so a
            # pathologically large-but-otherwise-valid render is downscaled
            # instead of rejected. See clamp_png_dimensions / Issue 42 defect 3.
            png_bytes = await container.screenshot(type="png")
            return clamp_png_dimensions(png_bytes), diagnostics

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
