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
from app.services.latex_profiles import LATEX_DIAGRAM_TYPES

logger = logging.getLogger(__name__)


# Diagram types the frontend renderer (frontend/src/plugins/d3/registry.ts)
# can actually service.  This MUST stay in sync with the plugins registered
# there.  Any `type` outside this set has no plugin, so the browser-side
# orchestrator (D3Renderer.tsx) finds no handler and retries indefinitely
# until the hard render timeout (~35s) instead of failing fast — total data
# loss dressed up as a hang.  Validating here converts that whole class of
# "unsupported / unknown / typo'd type" into an instant, actionable error.
#
# NOTE: LaTeX-family types (circuitikz, tikz, chemfig, tikz-cd) are
# deliberately NOT here.  They have no D3 plugin, and advertising them here is
# what produced the 35s timeout.  They are now handled BEFORE this set is
# consulted, by compiling real LaTeX server-side — see _render_latex_direct.
# Adding them here would route them back into the browser renderer and
# reintroduce the hang.
SUPPORTED_DIAGRAM_TYPES: frozenset = frozenset({
    "mermaid",
    "graphviz",
    "vega-lite", "vegalite",
    "vega",
    "plotly",
    "drawio", "designinspector",
    "packet",
    "joint", "jointjs", "diagram",
    "d2",
    "chord",
    "force-directed", "forcedirected",
    "network",
    "music",
    "basic-chart", "chart", "bar", "line", "scatter", "bubble",
    "d3",
})


class RenderDiagramInput(BaseModel):
    """Input schema for render_diagram."""

    type: str = Field(
        ...,
        description=(
            "Diagram type: mermaid, graphviz, vega-lite, vega, plotly, "
            "drawio, packet, joint, d2, chord, force-directed, network, d3."
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

        # Fail fast on unsupported types.  Without this, an unknown/typo'd/
        # unsupported type (e.g. "circuitikz", "latex", "uml") is forwarded to
        # the headless renderer, where no plugin can handle it; the frontend
        # orchestrator then retries indefinitely and the whole call hangs ~35s
        # before returning an opaque timeout.  Rejecting it here turns that
        # entire class of failure into an instant, actionable error.
        normalized_type = str(diagram_type).strip().lower()

        # LaTeX types bypass the browser entirely.  The renderer is a local
        # binary, so driving Playwright would only add a large dependency and
        # ~2s of startup to reach the same pdflatex call.  Checked before the
        # support gate below so these types are never reported unsupported.
        if normalized_type in LATEX_DIAGRAM_TYPES:
            return await self._render_latex_direct(
                normalized_type, definition, fmt, theme,
            )

        if normalized_type not in SUPPORTED_DIAGRAM_TYPES:
            supported = ", ".join(sorted({
                "mermaid", "graphviz", "vega-lite", "vega", "plotly",
                "drawio", "packet", "joint", "d2", "chord",
                "force-directed", "network", "music", "d3",
            }))
            return _error(
                f"Unsupported diagram type '{diagram_type}'. This renderer has "
                f"no plugin for it, so rendering would hang until timeout. "
                f"Supported types: {supported}. "
                f"(LaTeX/TikZ types such as 'circuitikz', 'tikz', 'chemfig' "
                f"and 'tikz-cd' are handled by the server-side LaTeX renderer "
                f"and do not appear in this list.)"
            )

        logger.info(
            "🎨 render_diagram: type=%s, theme=%s, format=%s, def_len=%d",
            diagram_type, theme, fmt, len(definition),
        )

        try:
            from app.services.diagram_renderer import get_diagram_renderer

            from app.config.env_registry import ziya_env
            port = ziya_env("ZIYA_PORT")
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

            image_bytes, diagnostics = await renderer.render_diagram_with_diagnostics(
                spec, format=fmt,
            )

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

            # Surface fixup-layer / renderer JS warnings and errors even
            # though the render completed — these indicate the output may
            # not faithfully represent the spec (dropped layer, fallback
            # layout, malformed-syntax auto-repair) despite producing an
            # image, and the model has no other way to learn about them.
            console_warnings = diagnostics.get("console_warnings") or []
            console_errors = diagnostics.get("console_errors") or []
            if console_warnings or console_errors:
                lines = ["", "Renderer console output during this render:"]
                for e in console_errors[-10:]:
                    lines.append(f"  {e}")
                for w in console_warnings[-10:]:
                    lines.append(f"  {w}")
                desc += "\n" + "\n".join(lines)

            logger.info("🎨 render_diagram: success — %s, %.1f KB", fmt, size_kb)

            # Return structured content with image block.
            # The streaming executor appends this content-block list to the
            # conversation intact (when the provider supports
            # 'image_tool_results') so the NEXT model call sees the image,
            # then compacts it to the text summary once consumed.
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

    async def _render_latex_direct(
        self, diagram_type: str, definition: str, fmt: str, theme: str,
    ) -> Any:
        """Compile a LaTeX diagram server-side, with no browser involved.

        The Playwright renderer exists because most diagram types are drawn by
        frontend plugins, so the only way to rasterize them headlessly is to
        run those plugins in a real browser.  LaTeX is the opposite case: the
        entire renderer is a local binary, and the browser adds nothing.
        """
        from starlette.concurrency import run_in_threadpool

        from app.services.latex_renderer import latex_renderer

        result = await run_in_threadpool(
            latex_renderer.render, diagram_type, definition,
            "svg" if fmt == "svg" else "png",
        )
        advisory = self._latex_advisory(result)

        if not result.ok:
            # A missing TeX package is not a malformed diagram, so report what
            # to install rather than blaming the definition.
            if result.error_kind == "not_installed":
                hint = (
                    f"\n\nTo enable it, run:\n    sudo {result.install_hint}"
                    if result.install_hint else ""
                )
                return {"content": [{"type": "text", "text": f"{result.error}{hint}"}]}

            if result.error_kind == "rejected":
                # Worth a warning in the server log: model-authored LaTeX tried
                # something disallowed, even though the layered defences held.
                logger.warning(
                    "LaTeX render rejected (type=%s): %s", diagram_type, result.error
                )
                return _error(f"LaTeX rejected for safety: {result.error}")

            detail = f"\n\n{result.log_excerpt}" if result.log_excerpt else ""
            return _error(f"LaTeX render failed: {result.error}{detail}")

        if result.fmt == "svg":
            # SVG is markup, not a raster the vision path can embed; return it
            # as text so the model can read the geometry directly.
            return {
                "content": [{
                    "type": "text",
                    "text": result.content.decode("utf-8", errors="replace"),
                }],
            }

        size_kb = len(result.content) / 1024
        desc = (
            f"Rendered {diagram_type} diagram (PNG, {size_kb:.1f} KB) via the "
            f"server-side LaTeX renderer. Definition: {len(definition)} chars."
        )
        desc += self._latex_advisory(result)
        if theme == "dark":
            # TeX draws black-on-transparent; there is no dark variant, so say
            # so rather than letting the model assume the theme was applied.
            desc += (
                " Note: LaTeX output is theme-independent (black on "
                "transparent); the 'dark' theme was not applied."
            )

        logger.info(
            "🎨 render_diagram: LaTeX success — %s, %.1f KB, cached=%s",
            result.fmt, size_kb, result.cached,
        )

        return {
            "_has_image_content": True,
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(result.content).decode("utf-8"),
                    },
                },
                {"type": "text", "text": desc},
            ],
        }

    @staticmethod
    def _latex_advisory(result: Any) -> str:
        """Notes to append to a SUCCESSFUL render's text block.

        Surfaced to the caller, not merely logged.  A chemfig ring with too
        few bond tokens still compiles and still returns an image, so a
        caller reading only the success status would go on to describe a
        molecule the picture does not show.  Naming the defect at the call
        site is what breaks that loop.
        """
        lines: list[str] = []
        for note in getattr(result, "autofixes", ()) or ():
            lines.append(f"  - auto-corrected: {note}")
        for note in getattr(result, "warnings", ()) or ():
            lines.append(f"  - WARNING: {note}")
        if not lines:
            return ""
        return (
            "\n\nRing-structure notes (the diagram compiled, but verify it "
            "matches the structure you intended):\n" + "\n".join(lines)
        )


def _error(msg: str) -> dict:
    """Return a text-only error result."""
    return {"content": [{"type": "text", "text": f"Error: {msg}"}]}
