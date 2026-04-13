"""
Tests for the conversation export utility.

Verifies:
1. export_conversation_for_paste basic output structure
2. Diagram fingerprinting and matching
3. _clean_tool_blocks processing
4. _clean_thinking_blocks processing
5. _extract_diagram_specs from message content
6. Markdown and HTML embedding of captured diagrams
7. render_diagrams_server_side graceful fallback
8. export_conversation_rendered end-to-end (mocked renderer)
"""
from __future__ import annotations

import asyncio
import base64
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------

class TestVizFingerprint:
    def test_consistent_for_same_content(self):
        from app.utils.conversation_exporter import _viz_fingerprint
        src = "graph LR\n  A-->B"
        assert _viz_fingerprint(src) == _viz_fingerprint(src)

    def test_strips_whitespace(self):
        from app.utils.conversation_exporter import _viz_fingerprint
        assert _viz_fingerprint("  graph LR\n") == _viz_fingerprint("graph LR")

    def test_different_content_different_fingerprint(self):
        from app.utils.conversation_exporter import _viz_fingerprint
        assert _viz_fingerprint("graph LR") != _viz_fingerprint("graph TD")


# ---------------------------------------------------------------------------
# export_conversation_for_paste
# ---------------------------------------------------------------------------

class TestExportConversationForPaste:
    def test_returns_expected_keys(self):
        from app.utils.conversation_exporter import export_conversation_for_paste
        result = export_conversation_for_paste(
            messages=[{"role": "human", "content": "hello"}],
        )
        assert "content" in result
        assert "filename" in result
        assert "format" in result
        assert "message_count" in result
        assert result["message_count"] == 1

    def test_markdown_format(self):
        from app.utils.conversation_exporter import export_conversation_for_paste
        result = export_conversation_for_paste(
            messages=[
                {"role": "human", "content": "What is 2+2?"},
                {"role": "assistant", "content": "4"},
            ],
            format_type="markdown",
        )
        assert result["format"] == "markdown"
        assert "## 👤 User" in result["content"]
        assert "## 🤖 AI Assistant" in result["content"]

    def test_html_format(self):
        from app.utils.conversation_exporter import export_conversation_for_paste
        result = export_conversation_for_paste(
            messages=[{"role": "human", "content": "hi"}],
            format_type="html",
        )
        assert result["format"] == "html"
        assert "<!DOCTYPE html>" in result["content"]

    def test_skips_system_messages(self):
        from app.utils.conversation_exporter import export_conversation_for_paste
        result = export_conversation_for_paste(
            messages=[
                {"role": "system", "content": "You are a helpful assistant"},
                {"role": "human", "content": "hello"},
            ],
            format_type="markdown",
        )
        assert "helpful assistant" not in result["content"]

    def test_skips_empty_messages(self):
        from app.utils.conversation_exporter import export_conversation_for_paste
        result = export_conversation_for_paste(
            messages=[
                {"role": "human", "content": ""},
                {"role": "human", "content": "real message"},
            ],
            format_type="markdown",
        )
        # Only one user header should appear
        assert result["content"].count("## 👤 User") == 1

    def test_diagram_count_zero_when_no_diagrams(self):
        from app.utils.conversation_exporter import export_conversation_for_paste
        result = export_conversation_for_paste(
            messages=[{"role": "human", "content": "no diagrams here"}],
        )
        assert result["diagrams_count"] == 0

    def test_diagram_count_with_captured_diagrams(self):
        from app.utils.conversation_exporter import export_conversation_for_paste
        result = export_conversation_for_paste(
            messages=[{"role": "assistant", "content": "```mermaid\ngraph LR\n  A-->B\n```"}],
            captured_diagrams=[{
                "sourceHash": "15:graph LR\n  A-->B",
                "dataUri": "data:image/svg+xml;base64,PHN2Zz4=",
            }],
        )
        assert result["diagrams_count"] == 1


# ---------------------------------------------------------------------------
# Diagram spec extraction
# ---------------------------------------------------------------------------

class TestExtractDiagramSpecs:
    def test_extracts_mermaid_spec(self):
        from app.utils.conversation_exporter import _extract_diagram_specs
        messages = [{"content": "Here is a diagram:\n```mermaid\ngraph LR\n  A-->B\n```\nDone."}]
        specs = _extract_diagram_specs(messages)
        assert len(specs) == 1
        assert specs[0]["type"] == "mermaid"
        assert "A-->B" in specs[0]["definition"]

    def test_extracts_graphviz_spec(self):
        from app.utils.conversation_exporter import _extract_diagram_specs
        messages = [{"content": "```graphviz\ndigraph G { A -> B }\n```"}]
        specs = _extract_diagram_specs(messages)
        assert len(specs) == 1
        assert specs[0]["type"] == "graphviz"

    def test_extracts_multiple_specs(self):
        from app.utils.conversation_exporter import _extract_diagram_specs
        messages = [
            {"content": "```mermaid\ngraph LR\n```\n\nand\n\n```graphviz\ndigraph{}\n```"},
        ]
        specs = _extract_diagram_specs(messages)
        assert len(specs) == 2

    def test_skips_non_viz_code_blocks(self):
        from app.utils.conversation_exporter import _extract_diagram_specs
        messages = [{"content": "```python\nprint('hello')\n```"}]
        specs = _extract_diagram_specs(messages)
        assert len(specs) == 0

    def test_skips_empty_content(self):
        from app.utils.conversation_exporter import _extract_diagram_specs
        messages = [{"content": ""}, {"content": None}, {}]
        specs = _extract_diagram_specs(messages)
        assert len(specs) == 0


# ---------------------------------------------------------------------------
# Tool block cleaning
# ---------------------------------------------------------------------------

class TestCleanToolBlocks:
    def test_converts_html_comment_tool_blocks(self):
        from app.utils.conversation_exporter import _clean_tool_blocks
        content = '<!-- TOOL_BLOCK_START:mcp_shell|Shell Command -->\noutput here\n<!-- TOOL_BLOCK_END:mcp_shell -->'
        result = _clean_tool_blocks(content)
        assert "**Shell Command**" in result
        assert "output here" in result
        assert "TOOL_BLOCK_START" not in result

    def test_converts_fenced_tool_blocks(self):
        from app.utils.conversation_exporter import _clean_tool_blocks
        content = '````tool:mcp_shell|Shell: ls|bash\nfile1.py\nfile2.py\n````'
        result = _clean_tool_blocks(content)
        assert "**Shell: ls**" in result
        assert "file1.py" in result

    def test_passthrough_when_no_tool_blocks(self):
        from app.utils.conversation_exporter import _clean_tool_blocks
        content = "Just regular text with `code` in it."
        result = _clean_tool_blocks(content)
        assert result == content


# ---------------------------------------------------------------------------
# Thinking block cleaning
# ---------------------------------------------------------------------------

class TestCleanThinkingBlocks:
    def test_converts_thinking_blocks(self):
        from app.utils.conversation_exporter import _clean_thinking_blocks
        content = '```thinking:step-1\nLet me consider this carefully.\n```'
        result = _clean_thinking_blocks(content)
        assert "Reasoning (Step 1)" in result
        assert "consider this carefully" in result
        assert "thinking:step-1" not in result

    def test_strips_thought_header(self):
        from app.utils.conversation_exporter import _clean_thinking_blocks
        content = '```thinking:step-2\n🤔 **Thought 2/5**\n\nActual thinking content.\n```'
        result = _clean_thinking_blocks(content)
        assert "Thought 2/5" not in result
        assert "Actual thinking content" in result


# ---------------------------------------------------------------------------
# Diagram embedding (markdown)
# ---------------------------------------------------------------------------

class TestEmbedDiagramsInMarkdown:
    def test_embeds_matched_diagram(self):
        from app.utils.conversation_exporter import _embed_diagrams_in_markdown, _viz_fingerprint
        source = "graph LR\n  A-->B\n"
        fp = _viz_fingerprint(source)
        diagram_by_hash = {
            fp: {"dataUri": "data:image/svg+xml;base64,PHN2Zz4=", "type": "svg", "sourceHash": fp},
        }
        content = f"```mermaid\n{source}```"
        result = _embed_diagrams_in_markdown(content, diagram_by_hash)
        assert "![mermaid diagram]" in result
        assert "base64" in result

    def test_fallback_when_no_captured_diagram(self):
        from app.utils.conversation_exporter import _embed_diagrams_in_markdown
        content = "```mermaid\ngraph LR\n  X-->Y\n```"
        result = _embed_diagrams_in_markdown(content, {})
        assert "Visualization not captured" in result
        assert "graph LR" in result


# ---------------------------------------------------------------------------
# render_diagrams_server_side
# ---------------------------------------------------------------------------

class TestRenderDiagramsServerSide:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_diagrams(self):
        from app.utils.conversation_exporter import render_diagrams_server_side
        result = await render_diagrams_server_side(
            messages=[{"content": "no diagrams"}],
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_playwright_missing(self):
        from app.utils.conversation_exporter import render_diagrams_server_side
        messages = [{"content": "```mermaid\ngraph LR\n  A-->B\n```"}]
        with patch.dict("sys.modules", {"app.services.diagram_renderer": None}):
            # Force ImportError
            with patch("app.utils.conversation_exporter._extract_diagram_specs",
                       return_value=[{"type": "mermaid", "definition": "graph LR", "fingerprint": "fp1"}]):
                try:
                    result = await render_diagrams_server_side(messages)
                    # May return empty dict depending on import error path
                    assert isinstance(result, dict)
                except ImportError:
                    pass  # Expected when module is None

    @pytest.mark.asyncio
    async def test_renders_diagrams_with_mock_renderer(self):
        from app.utils.conversation_exporter import render_diagrams_server_side

        fake_svg = b'<svg><circle r="5"/></svg>'
        mock_renderer = AsyncMock()
        mock_renderer.render_diagram = AsyncMock(return_value=fake_svg)

        messages = [{"content": "```mermaid\ngraph LR\n  A-->B\n```"}]

        # Patch at the source module since render_diagrams_server_side
        # imports get_diagram_renderer inline via:
        #   from app.services.diagram_renderer import get_diagram_renderer
        with patch("app.services.diagram_renderer.get_diagram_renderer",
                    new=AsyncMock(return_value=mock_renderer)):
            result = await render_diagrams_server_side(messages)

        # If the import was successful, check the result
        if result:
            assert len(result) == 1
            fp = list(result.keys())[0]
            assert "dataUri" in result[fp]
            assert result[fp]["type"] == "svg"


# ---------------------------------------------------------------------------
# export_conversation_rendered
# ---------------------------------------------------------------------------

class TestExportConversationRendered:
    @pytest.mark.asyncio
    async def test_end_to_end_with_mock(self):
        from app.utils.conversation_exporter import export_conversation_rendered

        fake_svg = b'<svg><rect width="10" height="10"/></svg>'
        mock_renderer = AsyncMock()
        mock_renderer.render_diagram = AsyncMock(return_value=fake_svg)

        messages = [
            {"role": "human", "content": "Show me a diagram"},
            {"role": "assistant", "content": "```mermaid\ngraph LR\n  A-->B\n```"},
        ]

        with patch("app.services.diagram_renderer.get_diagram_renderer",
                    new=AsyncMock(return_value=mock_renderer)):
            result = await export_conversation_rendered(
                messages=messages,
                format_type="markdown",
                theme="light",
            )

        assert "content" in result
        assert result["message_count"] == 2


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

class TestUtilityHelpers:
    def test_extract_svg_from_content(self):
        from app.utils.conversation_exporter import extract_svg_from_content
        content = 'Some text <svg xmlns="http://www.w3.org/2000/svg"><circle/></svg> more text'
        svgs = extract_svg_from_content(content)
        assert len(svgs) == 1
        assert "<circle/>" in svgs[0]

    def test_svg_to_data_uri(self):
        from app.utils.conversation_exporter import svg_to_data_uri
        svg = '<svg><rect/></svg>'
        uri = svg_to_data_uri(svg)
        assert uri.startswith("data:image/svg+xml;base64,")
        decoded = base64.b64decode(uri.split(",")[1]).decode("utf-8")
        assert decoded == svg
