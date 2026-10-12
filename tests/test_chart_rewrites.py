"""render_diagram reports the rewrites preprocessing applied to the spec.

Fix-up passes in the frontend log each change as ``🔧 TAG: message``.  The
headless renderer captures the console; this checks the whole path:

  console_log --DiagramRenderer._classify_console_log--> "rewrites"
             --diagnostics["console_rewrites"]--> render_diagram text result

Design: design/harness-small-items.md, item 3.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.utils.chart_rewrites import extract_rewrites, format_rewrites

LOG = [
    "[log] 🔧 VEGA-PREPROCESS: Starting comprehensive preprocessing",
    "[log] 🔧 LOG-SCALE-FIX: Converted x log scale to symlog (has zero values)",
    "[log] 🔧 JOINT-DEBUG: Processing element a",
    "[log] 🔧 VEGA-PREPROCESS: Found shape encoding, analyzing...",
    "[log] 🔧 DUAL-AXIS-FIX: Added independent y-axis scaling",
    "[log] 🔧 LOG-SCALE-FIX: Converted x log scale to symlog (has zero values)",
    "[log] plain message without the mark",
    "[warning] 🔧 SOMETHING: already reported as a warning",
    "[log] 🔧 ENHANCER-INPUT: Received definition: JSHandle@object",
]


class TestExtract:
    def test_keeps_rewrites_in_order_and_drops_noise_and_duplicates(self):
        assert extract_rewrites(LOG) == [
            "LOG-SCALE-FIX: Converted x log scale to symlog (has zero values)",
            "DUAL-AXIS-FIX: Added independent y-axis scaling",
        ]

    def test_no_rewrites_gives_empty_text(self):
        assert extract_rewrites(["[log] hello"]) == []
        assert format_rewrites([]) == ""

    def test_cap_and_trim(self):
        many = [f"[log] 🔧 FIX-{i}: change {i} " + "x" * 400 for i in range(40)]
        out = extract_rewrites(many, limit=5)
        assert len(out) == 6 and out[-1] == "(+35 more)"
        assert all(len(r) <= 240 for r in out)


def test_renderer_classifier_exposes_rewrites():
    from app.services.diagram_renderer import DiagramRenderer
    got = DiagramRenderer._classify_console_log(LOG)
    assert got["rewrites"] == extract_rewrites(LOG)
    # Warnings are still bucketed as before.
    assert got["warnings"] == ["[warning] 🔧 SOMETHING: already reported as a warning"]


@pytest.mark.asyncio
async def test_render_diagram_result_lists_the_rewrites():
    from app.mcp.tools.diagram_render import RenderDiagramTool
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    diag = {"console_warnings": [], "console_errors": [], "pageerrors": [],
            "console_rewrites": extract_rewrites(LOG)}
    with patch("app.services.diagram_renderer.get_diagram_renderer") as get:
        r = AsyncMock()
        r.render_diagram_with_diagnostics = AsyncMock(return_value=(png, diag))
        get.return_value = r
        result = await RenderDiagramTool().execute(type="vega-lite", definition='{"mark":"bar"}')
    text = result["content"][1]["text"]
    assert "Preprocessing changed the spec" in text
    assert "LOG-SCALE-FIX: Converted x log scale to symlog" in text
    assert "JOINT-DEBUG" not in text


@pytest.mark.asyncio
async def test_clean_render_has_no_rewrite_section():
    from app.mcp.tools.diagram_render import RenderDiagramTool
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    with patch("app.services.diagram_renderer.get_diagram_renderer") as get:
        r = AsyncMock()
        r.render_diagram_with_diagnostics = AsyncMock(return_value=(
            png, {"console_warnings": [], "console_errors": [], "pageerrors": []}))
        get.return_value = r
        result = await RenderDiagramTool().execute(type="mermaid", definition="graph LR\n A-->B")
    text = result["content"][1]["text"]
    assert "Rendered mermaid" in text and "Preprocessing changed" not in text
