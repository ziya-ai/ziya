"""
Tests for the server-side rendered conversation export pipeline.

Covers:
1. Diagram spec extraction from message content
2. Server-side rendering via DiagramRenderer (mocked)
3. The rendered export endpoint
4. ExportProvider plugin interface
5. Graceful fallback when Playwright is not installed
"""
from __future__ import annotations

import asyncio
import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Diagram extraction tests
# ---------------------------------------------------------------------------


class TestDiagramSpecExtraction:
    """Test _extract_diagram_specs finds all diagram code blocks."""

    def test_extracts_mermaid_spec(self):
        from app.utils.conversation_exporter import _extract_diagram_specs

        messages = [
            {"role": "assistant", "content": "Here's a diagram:\n\n```mermaid\ngraph LR\n  A-->B\n```\n"}
        ]
        specs = _extract_diagram_specs(messages)
        assert len(specs) == 1
        assert specs[0]["type"] == "mermaid"
        assert "graph LR" in specs[0]["definition"]
        assert specs[0]["fingerprint"]

    def test_extracts_multiple_types(self):
        from app.utils.conversation_exporter import _extract_diagram_specs

        content = (
            "```graphviz\ndigraph G { A -> B }\n```\n\n"
            "```mermaid\nsequenceDiagram\n  A->>B: hello\n```\n"
        )
        messages = [{"role": "assistant", "content": content}]
        specs = _extract_diagram_specs(messages)
        assert len(specs) == 2
        types = {s["type"] for s in specs}
        assert types == {"graphviz", "mermaid"}

    def test_skips_non_viz_code_blocks(self):
        from app.utils.conversation_exporter import _extract_diagram_specs

        messages = [
            {"role": "assistant", "content": "```python\nprint('hello')\n```\n"}
        ]
        specs = _extract_diagram_specs(messages)
        assert len(specs) == 0

    def test_skips_empty_messages(self):
        from app.utils.conversation_exporter import _extract_diagram_specs

        messages = [
            {"role": "human", "content": ""},
            {"role": "assistant", "content": None},
        ]
        specs = _extract_diagram_specs(messages)
        assert len(specs) == 0

    def test_dedup_by_fingerprint(self):
        """Same diagram in two messages should produce two specs
        (dedup happens during rendering, not extraction)."""
        from app.utils.conversation_exporter import _extract_diagram_specs

        diagram = "```mermaid\ngraph LR\n  A-->B\n```\n"
        messages = [
            {"role": "assistant", "content": diagram},
            {"role": "assistant", "content": diagram},
        ]
        specs = _extract_diagram_specs(messages)
        assert len(specs) == 2
        assert specs[0]["fingerprint"] == specs[1]["fingerprint"]

    def test_extracts_packet_and_drawio(self):
        from app.utils.conversation_exporter import _extract_diagram_specs

        content = (
            "```packet\n{\"bits\": 32}\n```\n\n"
            "```drawio\n<mxfile></mxfile>\n```\n"
        )
        messages = [{"role": "assistant", "content": content}]
        specs = _extract_diagram_specs(messages)
        assert len(specs) == 2
        types = {s["type"] for s in specs}
        assert types == {"packet", "drawio"}


# ---------------------------------------------------------------------------
# Server-side rendering tests
# ---------------------------------------------------------------------------


class TestServerSideRendering:
    """Test render_diagrams_server_side with mocked DiagramRenderer."""

    @pytest.mark.asyncio
    async def test_renders_diagrams_and_returns_data_uris(self):
        from app.utils.conversation_exporter import render_diagrams_server_side

        fake_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>'
        mock_renderer = AsyncMock()
        mock_renderer.render_diagram = AsyncMock(return_value=fake_svg)

        messages = [
            {"role": "assistant", "content": "```mermaid\ngraph LR\n  A-->B\n```\n"}
        ]

        # Patch at the source module since the import is lazy (inside the function body)
        with patch("app.services.diagram_renderer.get_diagram_renderer",
                    new_callable=AsyncMock, return_value=mock_renderer):
            result = await render_diagrams_server_side(messages, theme="dark")

        assert len(result) == 1
        fp = list(result.keys())[0]
        data_uri = result[fp]["dataUri"]
        assert data_uri.startswith("data:image/svg+xml;base64,")
        # Verify the base64 decodes to our SVG
        b64_part = data_uri.split(",")[1]
        decoded = base64.b64decode(b64_part)
        assert decoded == fake_svg

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_diagrams(self):
        from app.utils.conversation_exporter import render_diagrams_server_side

        messages = [{"role": "assistant", "content": "No diagrams here"}]
        result = await render_diagrams_server_side(messages)
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_playwright_missing(self):
        """When the diagram_renderer module can't be imported, return {}."""
        from app.utils.conversation_exporter import render_diagrams_server_side

        messages = [
            {"role": "assistant", "content": "```mermaid\ngraph LR\n  A-->B\n```\n"}
        ]

        # Block the lazy import inside render_diagrams_server_side
        with patch.dict("sys.modules", {"app.services.diagram_renderer": None}):
            result = await render_diagrams_server_side(messages)
            assert result == {}

    @pytest.mark.asyncio
    async def test_deduplicates_identical_diagrams(self):
        from app.utils.conversation_exporter import render_diagrams_server_side

        fake_svg = b'<svg></svg>'
        mock_renderer = AsyncMock()
        mock_renderer.render_diagram = AsyncMock(return_value=fake_svg)

        # Same diagram twice
        diagram = "```mermaid\ngraph LR\n  A-->B\n```\n"
        messages = [
            {"role": "assistant", "content": diagram},
            {"role": "assistant", "content": diagram},
        ]

        with patch("app.services.diagram_renderer.get_diagram_renderer",
                    new_callable=AsyncMock, return_value=mock_renderer):
            result = await render_diagrams_server_side(messages)

        # Should only render once despite two occurrences
        assert len(result) == 1
        assert mock_renderer.render_diagram.call_count == 1

    @pytest.mark.asyncio
    async def test_continues_on_single_diagram_failure(self):
        from app.utils.conversation_exporter import render_diagrams_server_side

        fake_svg = b'<svg></svg>'
        call_count = 0

        async def render_side_effect(spec, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Parse error")
            return fake_svg

        mock_renderer = AsyncMock()
        mock_renderer.render_diagram = AsyncMock(side_effect=render_side_effect)

        content = (
            "```mermaid\ngraph INVALID\n```\n\n"
            "```graphviz\ndigraph G { A -> B }\n```\n"
        )
        messages = [{"role": "assistant", "content": content}]

        with patch("app.services.diagram_renderer.get_diagram_renderer",
                    new_callable=AsyncMock, return_value=mock_renderer):
            result = await render_diagrams_server_side(messages)

        # Should have rendered 1 out of 2 (first failed)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_png_format(self):
        from app.utils.conversation_exporter import render_diagrams_server_side

        fake_png = b'\x89PNG\r\n\x1a\nfake'
        mock_renderer = AsyncMock()
        mock_renderer.render_diagram = AsyncMock(return_value=fake_png)

        messages = [
            {"role": "assistant", "content": "```mermaid\ngraph LR\n  A-->B\n```\n"}
        ]

        with patch("app.services.diagram_renderer.get_diagram_renderer",
                    new_callable=AsyncMock, return_value=mock_renderer):
            result = await render_diagrams_server_side(messages, format='png')

        fp = list(result.keys())[0]
        assert result[fp]["dataUri"].startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# Full rendered export tests
# ---------------------------------------------------------------------------


class TestExportConversationRendered:
    """Test the async export_conversation_rendered function."""

    @pytest.mark.asyncio
    async def test_renders_and_embeds_diagrams(self):
        from app.utils.conversation_exporter import export_conversation_rendered

        fake_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        mock_renderer = AsyncMock()
        mock_renderer.render_diagram = AsyncMock(return_value=fake_svg)

        messages = [
            {"role": "human", "content": "Draw a diagram"},
            {"role": "assistant", "content": "Here:\n\n```mermaid\ngraph LR\n  A-->B\n```\n"},
        ]

        with patch("app.services.diagram_renderer.get_diagram_renderer",
                    new_callable=AsyncMock, return_value=mock_renderer):
            result = await export_conversation_rendered(
                messages=messages,
                format_type="markdown",
                model="test-model",
            )

        assert result["format"] == "markdown"
        assert result["diagrams_count"] == 1
        # The content should have the diagram embedded as an image
        assert "![mermaid diagram]" in result["content"]
        assert "data:image/svg+xml;base64," in result["content"]

    @pytest.mark.asyncio
    async def test_falls_back_to_source_without_playwright(self):
        from app.utils.conversation_exporter import export_conversation_rendered

        messages = [
            {"role": "assistant", "content": "```mermaid\ngraph LR\n  A-->B\n```\n"},
        ]

        # Simulate Playwright not available by blocking the import
        with patch.dict("sys.modules", {"app.services.diagram_renderer": None}):
            result = await export_conversation_rendered(messages=messages)

        assert result["diagrams_count"] == 0
        # Source code should still be present
        assert "graph LR" in result["content"]


# ---------------------------------------------------------------------------
# Export route tests
# ---------------------------------------------------------------------------


class TestRenderedExportEndpoint:
    """Test the /api/export/rendered endpoint."""

    @pytest.mark.asyncio
    async def test_endpoint_calls_rendered_export(self):
        from app.routes.export_routes import export_rendered, RenderedExportRequest

        fake_result = {
            "content": "# Export\n\n![diagram](data:...)",
            "filename": "test.md",
            "format": "markdown",
            "target": "public",
            "size": 100,
            "message_count": 2,
            "diagrams_count": 1,
        }

        request = RenderedExportRequest(
            messages=[
                {"role": "assistant", "content": "```mermaid\ngraph LR\n  A-->B\n```\n"}
            ],
            format="markdown",
            theme="light",
        )

        # Patch the source module for lazy imports inside the endpoint handler
        with patch("app.utils.conversation_exporter.export_conversation_rendered",
                    new_callable=AsyncMock, return_value=fake_result) as mock_export, \
             patch("app.agents.models.ModelManager") as mock_mm, \
             patch("app.utils.version_util.get_current_version", return_value="0.6.0"):
            mock_mm.get_model_alias.return_value = "test-model"
            result = await export_rendered(request)

        assert result["diagrams_count"] == 1


# ---------------------------------------------------------------------------
# ExportProvider interface tests
# ---------------------------------------------------------------------------


class TestExportProviderInterface:
    """Test the ExportProvider plugin interface."""

    def test_interface_is_abstract(self):
        from app.plugins.interfaces import ExportProvider

        # Should not be instantiable directly
        with pytest.raises(TypeError):
            ExportProvider()

    def test_concrete_provider_can_be_registered(self):
        from app.plugins.interfaces import ExportProvider
        from app.plugins import register_export_provider, get_export_providers, _export_providers

        # Save and restore state
        original = _export_providers.copy()

        class TestExport(ExportProvider):
            provider_id = "test-slack"
            priority = 10

            def get_target_info(self):
                return {
                    "id": "test-slack",
                    "name": "Test Slack",
                    "icon": "slack-icon",
                    "description": "Test export target",
                }

            async def export(self, content, format_type, metadata, images=None):
                return {"success": True, "url": "https://slack.example.com/msg/123"}

        try:
            provider = TestExport()
            register_export_provider(provider)
            providers = get_export_providers()
            assert any(p.provider_id == "test-slack" for p in providers)
        finally:
            # Restore
            _export_providers.clear()
            _export_providers.extend(original)


class TestExportTargetsEndpoint:
    """Test that /api/export/targets includes ExportProvider targets."""

    @pytest.mark.asyncio
    async def test_includes_plugin_targets(self):
        from app.routes.export_routes import get_export_targets

        class FakeProvider:
            provider_id = "fake-quip"

            def get_target_info(self):
                return {"id": "quip", "name": "Quip", "icon": "doc-icon", "description": "Quip export"}

            def should_apply(self):
                return True

        # Patch at the source modules since export_routes imports lazily
        with patch("app.plugins.get_export_providers",
                    return_value=[FakeProvider()]), \
             patch("app.plugins.get_active_config_providers",
                    return_value=[]):
            result = await get_export_targets()

        target_ids = [t["id"] for t in result["targets"]]
        assert "public" in target_ids  # base target
        assert "quip" in target_ids    # plugin target
