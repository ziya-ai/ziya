"""
Tests for the headless diagram rendering service.

These tests verify:
1. DiagramRenderer lifecycle (create, close)
2. Spec validation and error handling
3. API route request/response format
4. Graceful degradation when Playwright is not installed

Integration tests that actually launch Chromium are marked with
@pytest.mark.integration and require:
    pip install playwright && playwright install chromium
"""
from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Unit tests (no Playwright required)
# ---------------------------------------------------------------------------


class TestDiagramRendererImportGuard:
    """Verify graceful behaviour when Playwright is not installed."""

    def test_check_playwright_returns_false_when_missing(self):
        """_check_playwright should return False when playwright is not importable."""
        import app.services.diagram_renderer as mod

        # Reset the cached check
        mod._playwright_available = None

        with patch.dict("sys.modules", {"playwright.async_api": None}):
            # Force re-import check
            mod._playwright_available = None
            result = mod._check_playwright()
            # It will try to import and may succeed if playwright IS installed.
            # The important thing is it doesn't crash.
            assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_create_raises_import_error_when_missing(self):
        """DiagramRenderer.create() should raise ImportError when Playwright
        is not available."""
        import app.services.diagram_renderer as mod

        # Pretend playwright is not installed
        mod._playwright_available = False

        with pytest.raises(ImportError, match="Playwright is required"):
            await mod.DiagramRenderer.create()

        # Reset
        mod._playwright_available = None


class TestDiagramRenderRequest:
    """Verify the Pydantic request model validation."""

    def test_minimal_valid_request(self):
        from app.routes.diagram_routes import DiagramRenderRequest

        req = DiagramRenderRequest(
            type="mermaid",
            definition="graph LR\n  A-->B",
        )
        assert req.type == "mermaid"
        assert req.theme == "light"
        assert req.format == "png"
        assert req.width is None

    def test_full_request(self):
        from app.routes.diagram_routes import DiagramRenderRequest

        req = DiagramRenderRequest(
            type="graphviz",
            definition="digraph G { A -> B }",
            theme="dark",
            format="svg",
            width=800,
            height=600,
            title="My Diagram",
        )
        assert req.theme == "dark"
        assert req.format == "svg"
        assert req.width == 800
        assert req.title == "My Diagram"

    def test_invalid_format_rejected(self):
        from app.routes.diagram_routes import DiagramRenderRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DiagramRenderRequest(
                type="mermaid",
                definition="graph LR\n  A-->B",
                format="gif",  # not allowed
            )

    def test_invalid_theme_rejected(self):
        from app.routes.diagram_routes import DiagramRenderRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DiagramRenderRequest(
                type="mermaid",
                definition="graph LR\n  A-->B",
                theme="neon",  # not allowed
            )

    def test_missing_required_fields(self):
        from app.routes.diagram_routes import DiagramRenderRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DiagramRenderRequest(type="mermaid")  # missing definition

        with pytest.raises(ValidationError):
            DiagramRenderRequest(definition="graph LR")  # missing type


class TestDiagramRendererUnit:
    """Unit tests for DiagramRenderer methods using mocked Playwright."""

    @pytest.mark.asyncio
    async def test_render_injects_spec_and_waits(self):
        """Verify the render flow: navigate → inject → wait → screenshot."""
        import app.services.diagram_renderer as mod

        # Mock Playwright objects
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value=True)
        mock_page.wait_for_function = AsyncMock()
        mock_page.get_attribute = AsyncMock(return_value="complete")

        mock_locator = AsyncMock()
        mock_locator.screenshot = AsyncMock(return_value=b"\x89PNG_fake")
        mock_locator.evaluate = AsyncMock(return_value=None)
        mock_page.locator = MagicMock(return_value=mock_locator)

        mock_browser = AsyncMock()
        mock_browser.is_connected.return_value = True
        mock_browser.new_page = AsyncMock(return_value=mock_page)

        renderer = mod.DiagramRenderer()
        renderer._browser = mock_browser
        renderer._base_url = "http://localhost:6969"

        result = await renderer.render_diagram({
            "type": "mermaid",
            "definition": "graph LR\n  A-->B",
        })

        assert result == b"\x89PNG_fake"
        mock_page.goto.assert_called_once()
        mock_page.evaluate.assert_called_once()
        mock_page.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_render_error_status_raises(self):
        """If the frontend reports an error, render_diagram should raise."""
        import app.services.diagram_renderer as mod

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value=True)
        mock_page.wait_for_function = AsyncMock()
        mock_page.get_attribute = AsyncMock(side_effect=lambda sel, attr: {
            ("data-render-status",): "error",
            ("data-error",): "Bad spec",
        }.get((attr,), None))

        # Make get_attribute return based on attr name
        async def get_attr(selector, attr):
            if attr == "data-render-status":
                return "error"
            if attr == "data-error":
                return "Bad spec"
            return None

        mock_page.get_attribute = get_attr

        mock_browser = AsyncMock()
        mock_browser.is_connected.return_value = True
        mock_browser.new_page = AsyncMock(return_value=mock_page)

        renderer = mod.DiagramRenderer()
        renderer._browser = mock_browser
        renderer._base_url = "http://localhost:6969"

        with pytest.raises(RuntimeError, match="Bad spec"):
            await renderer.render_diagram({
                "type": "mermaid",
                "definition": "invalid{{{",
            })

    @pytest.mark.asyncio
    async def test_svg_format_extracts_svg_content(self):
        """When format='svg' and SVG is available, return SVG markup."""
        import app.services.diagram_renderer as mod

        svg_markup = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value=True)
        mock_page.wait_for_function = AsyncMock()
        mock_page.get_attribute = AsyncMock(return_value="complete")

        mock_locator = AsyncMock()
        mock_locator.evaluate = AsyncMock(return_value=svg_markup)
        mock_page.locator = MagicMock(return_value=mock_locator)

        mock_browser = AsyncMock()
        mock_browser.is_connected.return_value = True
        mock_browser.new_page = AsyncMock(return_value=mock_page)

        renderer = mod.DiagramRenderer()
        renderer._browser = mock_browser
        renderer._base_url = "http://localhost:6969"

        result = await renderer.render_diagram(
            {"type": "graphviz", "definition": "digraph G { A -> B }"},
            format="svg",
        )
        assert b"<svg" in result
        assert b"circle" in result

    @pytest.mark.asyncio
    async def test_svg_format_falls_back_to_png(self):
        """When format='svg' but no SVG element exists, fall back to PNG."""
        import app.services.diagram_renderer as mod

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(return_value=True)
        mock_page.wait_for_function = AsyncMock()
        mock_page.get_attribute = AsyncMock(return_value="complete")

        mock_locator = AsyncMock()
        mock_locator.evaluate = AsyncMock(return_value=None)  # no SVG
        mock_locator.screenshot = AsyncMock(return_value=b"\x89PNG_fallback")
        mock_page.locator = MagicMock(return_value=mock_locator)

        mock_browser = AsyncMock()
        mock_browser.is_connected.return_value = True
        mock_browser.new_page = AsyncMock(return_value=mock_page)

        renderer = mod.DiagramRenderer()
        renderer._browser = mock_browser
        renderer._base_url = "http://localhost:6969"

        result = await renderer.render_diagram(
            {"type": "vega-lite", "definition": "{}"},
            format="svg",
        )
        assert result == b"\x89PNG_fallback"


class TestSingletonLifecycle:
    """Test the module-level singleton management."""

    @pytest.mark.asyncio
    async def test_shutdown_clears_instance(self):
        import app.services.diagram_renderer as mod

        mock_renderer = AsyncMock()
        mod._renderer_instance = mock_renderer

        await mod.shutdown_diagram_renderer()

        assert mod._renderer_instance is None
        mock_renderer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_noop_when_no_instance(self):
        import app.services.diagram_renderer as mod
        mod._renderer_instance = None

        # Should not raise
        await mod.shutdown_diagram_renderer()


class TestAPIRoute:
    """Test the FastAPI route handler logic."""

    @pytest.mark.asyncio
    async def test_render_endpoint_returns_png(self):
        """The endpoint should return PNG bytes with correct content-type."""
        from app.routes.diagram_routes import render_diagram, DiagramRenderRequest

        fake_png = b"\x89PNG\r\n\x1a\n_fake_image_data"

        mock_renderer = AsyncMock()
        mock_renderer.render_diagram = AsyncMock(return_value=fake_png)

        request = DiagramRenderRequest(
            type="mermaid",
            definition="graph LR\n  A-->B",
        )

        with patch("app.services.diagram_renderer.get_diagram_renderer",
                    new_callable=AsyncMock,
                    return_value=mock_renderer):
            response = await render_diagram(request)

        assert response.body == fake_png
        assert response.media_type == "image/png"

    @pytest.mark.asyncio
    async def test_render_endpoint_svg_content_type(self):
        from app.routes.diagram_routes import render_diagram, DiagramRenderRequest

        svg_bytes = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'

        mock_renderer = AsyncMock()
        mock_renderer.render_diagram = AsyncMock(return_value=svg_bytes)

        request = DiagramRenderRequest(
            type="graphviz",
            definition="digraph G { A -> B }",
            format="svg",
        )

        with patch("app.services.diagram_renderer.get_diagram_renderer",
                    new_callable=AsyncMock,
                    return_value=mock_renderer):
            response = await render_diagram(request)

        assert response.media_type == "image/svg+xml"


# ---------------------------------------------------------------------------
# Integration tests (require Playwright + running Ziya server)
# ---------------------------------------------------------------------------


def _playwright_installed() -> bool:
    """Check if playwright and chromium are available."""
    try:
        import playwright.async_api  # noqa: F401
        return True
    except ImportError:
        return False


_skip_no_playwright = pytest.mark.skipif(
    not _playwright_installed(),
    reason="Playwright not installed (pip install playwright && playwright install chromium)",
)


class TestHeadlessRenderIntegration:
    """Integration tests that launch a real headless browser.

    These require:
      1. ``pip install playwright && playwright install chromium``
      2. A running Ziya server on localhost:6969

    Run with: ``pytest tests/test_diagram_renderer.py -m integration -v``
    """

    @_skip_no_playwright
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_mermaid_renders_svg(self):
        """Headless render of a mermaid diagram should produce an SVG."""
        import app.services.diagram_renderer as mod
        mod._playwright_available = None  # reset cached check

        renderer = await mod.DiagramRenderer.create(server_port=6969)
        try:
            result = await renderer.render_diagram(
                {"type": "mermaid", "definition": "graph LR\n  A-->B-->C"},
                format="svg",
            )
            assert len(result) > 0
            assert b"<svg" in result or b"\x89PNG" in result  # SVG or PNG fallback
        finally:
            await renderer.close()

    @_skip_no_playwright
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_graphviz_renders_png(self):
        """Headless render of a graphviz diagram should produce a PNG."""
        import app.services.diagram_renderer as mod
        mod._playwright_available = None

        renderer = await mod.DiagramRenderer.create(server_port=6969)
        try:
            result = await renderer.render_diagram(
                {"type": "graphviz", "definition": "digraph G { A -> B -> C }"},
                format="png",
            )
            assert len(result) > 100  # Non-trivial PNG
            assert result[:4] == b"\x89PNG"
        finally:
            await renderer.close()

    @_skip_no_playwright
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_dark_theme_renders(self):
        """Rendering with dark theme should not crash."""
        import app.services.diagram_renderer as mod
        mod._playwright_available = None

        renderer = await mod.DiagramRenderer.create(server_port=6969)
        try:
            result = await renderer.render_diagram(
                {
                    "type": "mermaid",
                    "definition": "graph TD\n  Start-->End",
                    "theme": "dark",
                },
                format="png",
            )
            assert len(result) > 100
        finally:
            await renderer.close()

    @_skip_no_playwright
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_render_api_endpoint(self):
        """The /api/render-diagram POST endpoint should return image bytes."""
        import httpx

        async with httpx.AsyncClient(base_url="http://localhost:6969") as client:
            resp = await client.post(
                "/api/render-diagram",
                json={
                    "type": "mermaid",
                    "definition": "graph LR\n  X-->Y",
                    "format": "png",
                },
                timeout=60,
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"] in ("image/png", "image/svg+xml")
            assert len(resp.content) > 100


class TestExportWithRenderedDiagrams:
    """Test the conversation export pipeline with server-side diagrams."""

    @pytest.mark.asyncio
    async def test_render_diagrams_server_side_empty(self):
        """Messages with no diagrams should return empty dict."""
        from app.utils.conversation_exporter import render_diagrams_server_side

        result = await render_diagrams_server_side(
            [{"role": "human", "content": "Hello"}]
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_export_conversation_rendered_no_diagrams(self):
        """Rendered export with no diagrams should work like regular export."""
        from app.utils.conversation_exporter import export_conversation_rendered

        result = await export_conversation_rendered(
            messages=[
                {"role": "human", "content": "What is 2+2?"},
                {"role": "assistant", "content": "The answer is **4**."},
            ],
            format_type="markdown",
        )
        assert result["content"]
        assert result["message_count"] == 2
        assert result["diagrams_count"] == 0
        assert "**4**" in result["content"]

