"""
Builtin tool: render_diagram

Renders a diagram specification to a PNG/SVG image using the headless
Playwright-based renderer and returns it as a content block the model
can see via its vision capabilities.

IMPORTANT: This tool is NOT for normal diagram output.  To show a
diagram to the user, simply emit a fenced code block (```mermaid,
```graphviz, etc.) in your response — the frontend renders those
inline automatically.

Use this tool ONLY when:
  1. You need to visually inspect a rendered diagram yourself (e.g.
     to verify correctness in an iterative design loop).
  2. The user explicitly asks you to export or capture a rendered
     image of a diagram from the conversation.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool

logger = logging.getLogger(__name__)


class RenderDiagramInput(BaseModel):
    """Input schema for render_diagram."""

    type: str = Field(
        ...,
        description=(
            "Diagram type: mermaid, graphviz, vega-lite, drawio, "
            "packet, joint, d3, circuitikz."
        ),
    )
    definition: str = Field(
        ...,
        description="Diagram source text or JSON specification.",
    )
    theme: Literal["dark", "light"] = Field(
        default="light",
        description="Color theme for rendering.",
    )
    format: Literal["png", "svg"] = Field(
        default="png",
        description="Output format. Use png for vision analysis.",
    )
    width: Optional[int] = Field(
        default=None,
        description="Explicit width in pixels.",
    )
    height: Optional[int] = Field(
        default=None,
        description="Explicit height in pixels.",
    )
    title: Optional[str] = Field(
        default=None,
        description="Optional title shown above the diagram.",
    )


class RenderDiagramTool(BaseMCPTool):
    """Render a diagram and return the image for visual inspection."""

    name: str = "render_diagram"
    description: str = (
        "[DIRECT] Render a diagram spec (mermaid, graphviz, vega-lite, drawio, "
        "packet, etc.) to a PNG image and return it for visual inspection. "
        "The image is returned as a content block you can see and analyze. "
        "Use this to verify rendering correctness or iteratively improve "
        "diagram definitions.\n\n"
        "DO NOT call this tool to show a diagram to the user. Instead, emit "
        "a standard fenced code block (```mermaid, ```graphviz, ```vega-lite, "
        "```drawio, etc.) in your response text — the frontend renders those "
        "inline automatically with full interactivity.\n\n"
        "Call this tool ONLY when:\n"
        "- You need to SEE the rendered result yourself to verify correctness\n"
        "- The user explicitly asks to export or capture a rendered diagram image"
    )

    InputSchema = RenderDiagramInput

    async def execute(self, **kwargs) -> Any:
        """Render the diagram and return image + text content blocks."""
        kwargs.pop("_workspace_path", None)

        diagram_type = kwargs.get("type", "")
        definition = kwargs.get("definition", "")
        theme = kwargs.get("theme", "light")
        fmt = kwargs.get("format", "png")
        width = kwargs.get("width")
        height = kwargs.get("height")
        title = kwargs.get("title")

        if not diagram_type:
            return _error("'type' is required (e.g. mermaid, graphviz, vega-lite)")
        if not definition:
            return _error("'definition' is required")

        logger.info(
            "🎨 render_diagram: type=%s, theme=%s, format=%s, def_len=%d",
            diagram_type, theme, fmt, len(definition),
        )

        try:
            from app.services.diagram_renderer import get_diagram_renderer

            port = int(os.environ.get("ZIYA_PORT", "6969"))
            renderer = await get_diagram_renderer(server_port=port)

            spec: dict[str, Any] = {
                "type": diagram_type,
                "definition": definition,
                "theme": theme,
            }
            if width:
                spec["width"] = width
            if height:
                spec["height"] = height
            if title:
                spec["title"] = title

            image_bytes = await renderer.render_diagram(spec, format=fmt)

            if not image_bytes or len(image_bytes) < 50:
                return _error(
                    f"Renderer returned trivial output ({len(image_bytes or b'')} bytes). "
                    "The diagram spec may be invalid."
                )

            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            media_type = "image/svg+xml" if fmt == "svg" else "image/png"

            size_kb = len(image_bytes) / 1024
            desc = (
                f"Rendered {diagram_type} diagram ({fmt.upper()}, "
                f"{size_kb:.1f} KB). "
                f"Definition: {len(definition)} chars, theme: {theme}."
            )

            logger.info("🎨 render_diagram: success — %s, %.1f KB", fmt, size_kb)

            # Return structured content with image block.
            # The streaming executor preserves _has_image_content results
            # as structured content blocks so the model sees the image
            # via its vision capabilities.
            return {
                "_has_image_content": True,
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": desc,
                    },
                ],
            }

        except ImportError:
            return _error(
                "Playwright is not installed. "
                "Run: pip install playwright && playwright install chromium"
            )
        except RuntimeError as exc:
            return _error(f"Render failed: {exc}")
        except Exception as exc:
            logger.error("render_diagram error: %s", exc, exc_info=True)
            return _error(f"Unexpected error: {exc}")


def _error(msg: str) -> dict:
    """Return a text-only error result."""
    return {"content": [{"type": "text", "text": f"Error: {msg}"}]}
