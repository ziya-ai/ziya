"""Endpoint-level tests for dual-mode HTML export wiring (Card II Stage 2).

Verifies POST /api/export/rendered and /api/export/to-target route HTML through
the dual-mode :func:`export_conversation_html` and surface the mode/fidelity to
the caller, while markdown keeps its existing path.  Handlers are called
directly (matching test_export_rendered.py's style); the exporter is mocked so
no browser is launched.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestRenderedEndpointHtmlDualMode:
    @pytest.mark.asyncio
    async def test_html_uses_dual_mode_exporter_and_reports_mode(self):
        from app.routes.export_routes import export_rendered, RenderedExportRequest

        fake_html = {
            "content": "<!DOCTYPE html><html><body>hi</body></html>",
            "mode": "route", "fidelity": "high", "format": "html",
            "target": "public", "size": 42, "message_count": 1,
            "filename": "x.html",
        }
        req = RenderedExportRequest(
            messages=[{"role": "assistant", "content": "hi"}],
            format="html", theme="light",
        )
        with patch("app.services.html_exporter.export_conversation_html",
                   new_callable=AsyncMock, return_value=fake_html) as mock_html, \
             patch("app.utils.conversation_exporter.render_diagrams_server_side",
                   new_callable=AsyncMock, return_value={}), \
             patch("app.agents.models.ModelManager") as mm, \
             patch("app.utils.version_util.get_current_version", return_value="0.6.0"):
            mm.get_model_alias.return_value = "m"
            result = await export_rendered(req)

        assert result["mode"] == "route"
        assert result["fidelity"] == "high"
        assert mock_html.await_count == 1
        # Option knobs forwarded
        _, kwargs = mock_html.call_args
        assert kwargs["options"]["includeHuman"] is True
        assert kwargs["embed_images"] is True

    @pytest.mark.asyncio
    async def test_html_option_knobs_forwarded(self):
        from app.routes.export_routes import export_rendered, RenderedExportRequest

        req = RenderedExportRequest(
            messages=[{"role": "human", "content": "q"}],
            format="html",
            roundLimit=2, includeHuman=False, includeCollapsed=False,
            embed_images=False, html_mode="python",
        )
        with patch("app.services.html_exporter.export_conversation_html",
                   new_callable=AsyncMock,
                   return_value={"content": "x", "mode": "python",
                                 "fidelity": "fallback", "size": 1}) as mock_html, \
             patch("app.utils.conversation_exporter.render_diagrams_server_side",
                   new_callable=AsyncMock, return_value={}), \
             patch("app.agents.models.ModelManager") as mm, \
             patch("app.utils.version_util.get_current_version", return_value="0.6.0"):
            mm.get_model_alias.return_value = "m"
            await export_rendered(req)

        _, kwargs = mock_html.call_args
        assert kwargs["mode"] == "python"
        assert kwargs["options"]["roundLimit"] == 2
        assert kwargs["options"]["includeHuman"] is False
        assert kwargs["options"]["includeCollapsed"] is False
        assert kwargs["embed_images"] is False

    @pytest.mark.asyncio
    async def test_markdown_keeps_existing_path(self):
        from app.routes.export_routes import export_rendered, RenderedExportRequest

        req = RenderedExportRequest(
            messages=[{"role": "assistant", "content": "```mermaid\ngraph LR\nA-->B\n```"}],
            format="markdown", theme="light",
        )
        with patch("app.utils.conversation_exporter.export_conversation_rendered",
                   new_callable=AsyncMock,
                   return_value={"content": "# md", "diagrams_count": 1}) as mock_md, \
             patch("app.services.html_exporter.export_conversation_html",
                   new_callable=AsyncMock) as mock_html, \
             patch("app.agents.models.ModelManager") as mm, \
             patch("app.utils.version_util.get_current_version", return_value="0.6.0"):
            mm.get_model_alias.return_value = "m"
            result = await export_rendered(req)

        assert mock_md.await_count == 1
        assert mock_html.await_count == 0
        assert result["diagrams_count"] == 1


class TestToTargetHtmlDualMode:
    @pytest.mark.asyncio
    async def test_to_target_html_uses_dual_mode(self):
        from app.routes.export_routes import export_to_target, PluginExportRequest

        fake_provider = MagicMock()
        fake_provider.get_target_info.return_value = {"id": "slack"}
        fake_provider.export = AsyncMock(return_value={"success": True})

        req = PluginExportRequest(
            messages=[{"role": "assistant", "content": "hi"}],
            target_id="slack", format="html",
        )
        with patch("app.plugins.get_export_providers", return_value=[fake_provider]), \
             patch("app.services.html_exporter.export_conversation_html",
                   new_callable=AsyncMock,
                   return_value={"content": "<html></html>", "mode": "route",
                                 "fidelity": "high", "size": 13}) as mock_html, \
             patch("app.utils.conversation_exporter.render_diagrams_server_side",
                   new_callable=AsyncMock, return_value={}), \
             patch("app.agents.models.ModelManager") as mm, \
             patch("app.utils.version_util.get_current_version", return_value="0.6.0"):
            mm.get_model_alias.return_value = "m"
            result = await export_to_target(req)

        assert mock_html.await_count == 1
        # Provider received the route-rendered HTML and the mode metadata.
        _, kwargs = fake_provider.export.call_args
        assert kwargs["content"] == "<html></html>"
        assert kwargs["metadata"]["export_mode"] == "route"
        assert result.get("mode") == "route"
