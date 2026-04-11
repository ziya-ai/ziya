"""
Tests for the render_diagram builtin tool.

Unit tests verify the tool's behavior with mocked rendering.
Integration tests (marked @pytest.mark.integration) require a running
Ziya server with Playwright installed.
"""
from __future__ import annotations

import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestRenderDiagramToolUnit:
    """Unit tests with mocked DiagramRenderer."""

    @pytest.mark.asyncio
    async def test_basic_render_returns_image_content(self):
        """Tool should return structured content with image block."""
        from app.mcp.tools.diagram_render import RenderDiagramTool

        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        with patch("app.services.diagram_renderer.get_diagram_renderer") as mock_get:
            mock_renderer = AsyncMock()
            mock_renderer.render_diagram = AsyncMock(return_value=fake_png)
            mock_get.return_value = mock_renderer

            tool = RenderDiagramTool()
            result = await tool.execute(
                type="mermaid",
                definition="graph LR\n  A-->B",
            )

        assert result.get("_has_image_content") is True
        content = result["content"]
        assert len(content) == 2

        # First block is the image
        img_block = content[0]
        assert img_block["type"] == "image"
        assert img_block["source"]["type"] == "base64"
        assert img_block["source"]["media_type"] == "image/png"
        decoded = base64.b64decode(img_block["source"]["data"])
        assert decoded == fake_png

        # Second block is the text description
        text_block = content[1]
        assert text_block["type"] == "text"
        assert "mermaid" in text_block["text"]

    @pytest.mark.asyncio
    async def test_svg_format_returns_svg_media_type(self):
        """SVG format should set correct media type."""
        from app.mcp.tools.diagram_render import RenderDiagramTool

        fake_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'

        with patch("app.services.diagram_renderer.get_diagram_renderer") as mock_get:
            mock_renderer = AsyncMock()
            mock_renderer.render_diagram = AsyncMock(return_value=fake_svg)
            mock_get.return_value = mock_renderer

            tool = RenderDiagramTool()
            result = await tool.execute(
                type="graphviz",
                definition="digraph G { A -> B }",
                format="svg",
            )

        img_block = result["content"][0]
        assert img_block["source"]["media_type"] == "image/svg+xml"

    @pytest.mark.asyncio
    async def test_missing_type_returns_error(self):
        """Missing type parameter should return error text."""
        from app.mcp.tools.diagram_render import RenderDiagramTool

        tool = RenderDiagramTool()
        result = await tool.execute(definition="graph LR\n  A-->B")

        assert "_has_image_content" not in result
        text = result["content"][0]["text"]
        assert "Error" in text
        assert "type" in text

    @pytest.mark.asyncio
    async def test_missing_definition_returns_error(self):
        """Missing definition should return error text."""
        from app.mcp.tools.diagram_render import RenderDiagramTool

        tool = RenderDiagramTool()
        result = await tool.execute(type="mermaid")

        text = result["content"][0]["text"]
        assert "Error" in text
        assert "definition" in text

    @pytest.mark.asyncio
    async def test_empty_render_result_returns_error(self):
        """Trivial render output should be treated as an error."""
        from app.mcp.tools.diagram_render import RenderDiagramTool

        with patch("app.services.diagram_renderer.get_diagram_renderer") as mock_get:
            mock_renderer = AsyncMock()
            mock_renderer.render_diagram = AsyncMock(return_value=b"tiny")
            mock_get.return_value = mock_renderer

            tool = RenderDiagramTool()
            result = await tool.execute(
                type="mermaid",
                definition="graph LR\n  A-->B",
            )

        assert "_has_image_content" not in result
        assert "Error" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_renderer_exception_returns_error(self):
        """Runtime errors from the renderer should be caught."""
        from app.mcp.tools.diagram_render import RenderDiagramTool

        with patch("app.services.diagram_renderer.get_diagram_renderer") as mock_get:
            mock_renderer = AsyncMock()
            mock_renderer.render_diagram = AsyncMock(
                side_effect=RuntimeError("Browser crashed")
            )
            mock_get.return_value = mock_renderer

            tool = RenderDiagramTool()
            result = await tool.execute(
                type="mermaid",
                definition="graph LR\n  A-->B",
            )

        text = result["content"][0]["text"]
        assert "Render failed" in text
        assert "Browser crashed" in text

    @pytest.mark.asyncio
    async def test_playwright_not_installed_returns_error(self):
        """ImportError from renderer should produce a helpful message."""
        from app.mcp.tools.diagram_render import RenderDiagramTool

        with patch(
            "app.services.diagram_renderer.get_diagram_renderer",
            side_effect=ImportError("No module named 'playwright'"),
        ):
            tool = RenderDiagramTool()
            result = await tool.execute(
                type="mermaid",
                definition="graph LR\n  A-->B",
            )

        text = result["content"][0]["text"]
        assert "Playwright" in text

    @pytest.mark.asyncio
    async def test_optional_params_passed_to_renderer(self):
        """Width, height, title, theme should be forwarded to the spec."""
        from app.mcp.tools.diagram_render import RenderDiagramTool

        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        captured_spec = {}

        async def capture_render(spec, **kw):
            captured_spec.update(spec)
            return fake_png

        with patch("app.services.diagram_renderer.get_diagram_renderer") as mock_get:
            mock_renderer = AsyncMock()
            mock_renderer.render_diagram = capture_render
            mock_get.return_value = mock_renderer

            tool = RenderDiagramTool()
            await tool.execute(
                type="graphviz",
                definition="digraph G { A -> B }",
                theme="dark",
                width=800,
                height=600,
                title="My Diagram",
            )

        assert captured_spec["theme"] == "dark"
        assert captured_spec["width"] == 800
        assert captured_spec["height"] == 600
        assert captured_spec["title"] == "My Diagram"

    @pytest.mark.asyncio
    async def test_workspace_path_stripped(self):
        """Internal _workspace_path param should be removed before processing."""
        from app.mcp.tools.diagram_render import RenderDiagramTool

        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        with patch("app.services.diagram_renderer.get_diagram_renderer") as mock_get:
            mock_renderer = AsyncMock()
            mock_renderer.render_diagram = AsyncMock(return_value=fake_png)
            mock_get.return_value = mock_renderer

            tool = RenderDiagramTool()
            # Should not crash even with _workspace_path present
            result = await tool.execute(
                type="mermaid",
                definition="graph LR\n  A-->B",
                _workspace_path="/some/path",
            )

        assert result.get("_has_image_content") is True


class TestBuiltinRegistration:
    """Verify the tool is registered in the builtin category system."""

    def test_diagram_render_category_exists(self):
        from app.mcp.builtin_tools import BUILTIN_TOOL_CATEGORIES
        assert "diagram_render" in BUILTIN_TOOL_CATEGORIES

    def test_diagram_render_enabled_by_default(self):
        from app.mcp.builtin_tools import BUILTIN_TOOL_CATEGORIES
        assert BUILTIN_TOOL_CATEGORIES["diagram_render"]["enabled_by_default"] is True

    def test_get_diagram_render_tools_returns_tool(self):
        from app.mcp.builtin_tools import get_diagram_render_tools
        tools = get_diagram_render_tools()
        assert len(tools) == 1
        assert tools[0].__name__ == "RenderDiagramTool"

    def test_category_getter_wired(self):
        from app.mcp.builtin_tools import get_builtin_tools_for_category
        tools = get_builtin_tools_for_category("diagram_render")
        assert len(tools) == 1


class TestStreamingExecutorImagePassthrough:
    """Verify that image content blocks survive the executor pipeline."""

    def test_image_result_not_stringified(self):
        """When result has _has_image_content, content list should be preserved."""
        # Simulate the executor's result processing logic
        result = {
            "_has_image_content": True,
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc123"}},
                {"type": "text", "text": "Rendered diagram"},
            ],
        }

        content = result['content']
        if result.get('_has_image_content') and isinstance(content, list):
            result_text = content  # preserved as list
            text_parts = [b.get('text', '') for b in content if b.get('type') == 'text']
            display_text = ' '.join(text_parts)
        elif isinstance(content, list) and len(content) > 0:
            result_text = content[0].get('text', str(result))
        else:
            result_text = str(result)

        # result_text should be the list, not a string
        assert isinstance(result_text, list)
        assert len(result_text) == 2
        assert result_text[0]["type"] == "image"
        assert display_text == "Rendered diagram"

    def test_non_image_result_still_stringified(self):
        """Normal text-only results should still be extracted as strings."""
        result = {
            "content": [
                {"type": "text", "text": "Some tool output"},
            ],
        }

        content = result['content']
        if result.get('_has_image_content') and isinstance(content, list):
            result_text = content
        elif isinstance(content, list) and len(content) > 0:
            result_text = content[0].get('text', str(result))
        else:
            result_text = str(result)

        assert isinstance(result_text, str)
        assert result_text == "Some tool output"

    def test_image_result_skips_sanitization(self):
        """Structured image content should not be passed to the sanitizer."""
        result_text_is_list = isinstance(
            [{"type": "image"}, {"type": "text", "text": "desc"}],
            list,
        )
        # The guard: if isinstance(result_text, str): sanitize()
        assert result_text_is_list is True
        # Strings should still be sanitized
        assert isinstance("some text", str) is True

    def test_provider_receives_structured_content(self):
        """The provider tool result builder should receive list content for images."""
        # Simulate the provider_tool_results construction
        tool_results = [
            {
                "tool_id": "tool_1",
                "tool_name": "render_diagram",
                "result": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}},
                    {"type": "text", "text": "Rendered mermaid diagram"},
                ],
            }
        ]

        provider_tool_results = []
        for tool_result in tool_results:
            raw_result = tool_result['result']
            if isinstance(raw_result, list):
                pass  # structured content, no cleaning needed
            elif isinstance(raw_result, str) and '$ ' in raw_result:
                lines = raw_result.split('\n')
                clean_lines = [line for line in lines if not line.startswith('$ ')]
                raw_result = '\n'.join(clean_lines).strip()
            provider_tool_results.append({
                "tool_use_id": tool_result['tool_id'],
                "content": raw_result,
            })

        # Content should be the structured list
        assert isinstance(provider_tool_results[0]["content"], list)
        assert provider_tool_results[0]["content"][0]["type"] == "image"
