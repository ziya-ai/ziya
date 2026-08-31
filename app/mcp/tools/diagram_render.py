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
import json
import logging
from typing import Any, Dict, List, Literal, Optional, Union

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
    "railroad",
    "wavedrom",
    "flamegraph",
    "joint", "jointjs", "diagram",
    "d2",
    "chord",
    "force-directed", "forcedirected",
    "network",
    "music",
    "basic-chart", "chart", "bar", "line", "scatter", "bubble",
    "d3",
})

#: Markdown rendered through the REAL chat UI and screenshotted, rather than
#: a diagram drawn by a plugin.  Has no D3 plugin, so -- exactly like the
#: LaTeX family above -- it must be dispatched BEFORE the support gate or it
#: would be reported unsupported.
CHAT_MESSAGE_TYPES: frozenset = frozenset({
    "chat-message", "chat-markdown", "markdown",
})


class RenderDiagramInput(BaseModel):
    """Input schema for render_diagram."""

    type: str = Field(
        ...,
        description=(
            "Diagram type: mermaid, graphviz, vega-lite, vega, plotly, "
            "drawio, packet, railroad, wavedrom, flamegraph, joint, d2, chord, "
            "force-directed, network, d3. "
            "Also 'chat-message', which is not a diagram at all: it renders "
            "MARKDOWN (KaTeX math, fences, tables) through the real chat UI "
            "and screenshots it, so you can see how a message will actually "
            "look to the user."
        ),
    )
    definition: Union[str, Dict[str, Any], List[Any]] = Field(
        ...,
        description=(
            "Diagram source text, or the JSON specification itself. "
            "Structured types (vega-lite, vega, plotly, packet, railroad, wavedrom, "
            "flamegraph, music, "
            "joint, chord, network, d3) accept either a JSON object/array "
            "or its serialized string — both are handled."
        ),
    )
    theme: Literal["dark", "light"] = Field(
        default="light",
        description="Color theme for rendering.",
    )
    role: Literal["assistant", "human"] = Field(
        default="assistant",
        description=(
            "For 'chat-message' only: whose message bubble to render. "
            "Affects styling and paragraph whitespace handling."
        ),
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
    retain: Literal["auto", "turn", "pin"] = Field(
        default="auto",
        description=(
            "How long the rendered image should stay visible to you. "
            "'auto' (default) keeps it for a short window of tool "
            "iterations, then replaces it with its text summary to save "
            "context. 'turn' widens that window — use it when you are "
            "iterating on a diagram and need to compare successive "
            "attempts. 'pin' holds it as long as the byte budget allows; "
            "use it only when the image is the reference you keep checking "
            "against. Elision does not invalidate what you already saw: "
            "conclusions you drew while the image was in view remain "
            "valid. To keep a render permanently, use emit_artifact "
            "instead — it freezes the PNG with the run."
        ),
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
        "\n\n"
        "Image lifetime: the returned image stays in your context for a "
        "short window of tool iterations and is then replaced by its text "
        "summary to bound context growth. This does NOT mean the "
        "observation was unreliable — anything you concluded while the "
        "image was in view was based on direct observation and stands. Do "
        "not retract or re-examine an earlier visual finding just because "
        "the image is no longer displayed; re-run this tool if you "
        "genuinely need another look, or pass retain='turn'/'pin' up front "
        "when you know you will need to compare renders."
    )

    InputSchema = RenderDiagramInput

    async def execute(self, **kwargs) -> Any:
        """Render the diagram and return image + text content blocks."""
        # Retained rather than discarded: the chat-message renderer needs it
        # to resolve which project to seed the throwaway conversation into.
        workspace_path = kwargs.pop("_workspace_path", None)

        diagram_type = kwargs.get("type", "")
        definition = kwargs.get("definition", "")
        theme = kwargs.get("theme", "light")
        fmt = kwargs.get("format", "png")
        width = kwargs.get("width")
        height = kwargs.get("height")
        title = kwargs.get("title")
        # Retention is requested BEFORE the render so a slow or failing
        # render still leaves the caller's intent recorded for the sweep
        # that follows this iteration.
        retain = kwargs.get("retain", "auto")
        if retain and retain != "auto":
            from app.utils.image_pin_context import request_image_retain
            request_image_retain(retain)

        if not diagram_type:
            return _error("'type' is required (e.g. mermaid, graphviz, vega-lite)")
        if not definition:
            return _error("'definition' is required")
        # Structured types have JSON-object specs, and a caller holding one
        # passes the object as readily as its serialized string.  Normalize
        # once here so the LaTeX path, the char counts in the result text,
        # and the browser (whose plugins JSON.parse this field) all agree.
        if isinstance(definition, (dict, list)):
            try:
                definition = json.dumps(definition)
            except (TypeError, ValueError) as exc:
                return _error(f"'definition' is not JSON-serializable: {exc}")

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
                normalized_type, definition, fmt, theme, width, height,
            )

        # A chat-message render is neither a plugin diagram nor LaTeX: it
        # drives the REAL application at "/" and photographs the actual
        # .message element, so no second render path has to be kept faithful
        # to the chat pipeline -- the artifact IS the product.  Dispatched
        # before the support gate for the same reason as LaTeX: it has no D3
        # plugin, so falling through would report it unsupported.
        if normalized_type in CHAT_MESSAGE_TYPES:
            return await self._render_chat_message(
                definition, theme=theme,
                role=str(kwargs.get("role") or "assistant"),
                workspace_path=workspace_path,
            )

        if normalized_type not in SUPPORTED_DIAGRAM_TYPES:
            supported = ", ".join(sorted({
                "mermaid", "graphviz", "vega-lite", "vega", "plotly",
                "drawio", "packet", "railroad", "wavedrom", "flamegraph",
                "joint", "d2", "chord",
                "force-directed", "network", "music", "d3",
            }))
            return _error(
                f"Unsupported diagram type '{diagram_type}'. This renderer has "
                f"no plugin for it, so rendering would hang until timeout. "
                f"Supported types: {supported}. "
                f"(LaTeX/TikZ types such as 'circuitikz', 'tikz', 'chemfig' "
                f"and 'tikz-cd' are handled by the server-side LaTeX renderer "
                f"and do not appear in this list, and nor does 'chat-message', "
                f"which screenshots markdown through the real chat UI.)"
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

    async def _render_chat_message(
        self, definition: str, *, theme: str, role: str,
        workspace_path: Optional[str],
    ) -> Any:
        """Screenshot markdown as the production chat UI renders it.

        Unlike every other branch here, this renders no diagram: it seeds a
        throwaway conversation, drives the real app at "/", and photographs
        the actual .message element.  That is the whole point -- mounting
        MarkdownRenderer on the isolated /render harness with copied props
        would create a second render path needing hand-sync with the chat
        call site, and a verdict about math rendering is only worth having
        if the thing photographed is the thing users see.
        """
        from app.config.env_registry import ziya_env
        from app.utils.chat_screenshot import render_chat_message

        try:
            png, diag = await render_chat_message(
                definition, role=role, theme=theme,
                server_port=ziya_env("ZIYA_PORT"),
                workspace_path=workspace_path,
            )
        except RuntimeError as exc:
            return _error(str(exc))
        except Exception as exc:
            logger.error("chat-message render error: %s", exc, exc_info=True)
            return _error(f"Chat-message render failed: {exc}")

        dom = diag.get("dom") or {}
        lines = [
            f"Rendered markdown through the real chat UI (PNG, "
            f"{len(png) / 1024:.1f} KB). Definition: {len(definition)} "
            f"chars, role: {role}, theme: {theme}.",
        ]
        # Structural findings ride WITH the image rather than only being
        # logged: a leaked marker or a KaTeX error span is unambiguous in the
        # DOM and easy to miss by eye at chat font sizes.
        if not diag.get("rendered_confirmed"):
            lines.append(
                "WARNING: the render never confirmed (no typeset math "
                "appeared before the timeout). The image is included anyway "
                "because dropped math is exactly what it would show."
            )
        if dom.get("is_lazy_placeholder"):
            lines.append(
                "WARNING: the message was still a lazy-mount placeholder, so "
                "the image does not show rendered content."
            )
        if dom.get("katex_error"):
            lines.append(f"KaTeX error spans: {dom['katex_error']}.")
        if dom.get("math_fallback"):
            lines.append(
                f"Math fell back to monospace in {dom['math_fallback']} place(s)."
            )
        if dom.get("leaked_math_marker") or dom.get("leaked_encoded_div"):
            lines.append(
                "Internal math markers leaked into the output: the marker "
                "round-trip in MarkdownRenderer did not complete."
            )
        if dom:
            lines.append(
                f"DOM: katex={dom.get('katex')}, "
                f"code_blocks={dom.get('code_blocks')}, "
                f"tables={dom.get('tables')}, "
                f"chat_chrome={dom.get('has_chat_chrome')}."
            )
        for err in (diag.get("console_errors") or [])[:5]:
            lines.append(f"console: {err}")

        logger.info(
            "🎨 render_diagram: chat-message success — %.1f KB, confirmed=%s",
            len(png) / 1024, diag.get("rendered_confirmed"),
        )
        return {
            "_has_image_content": True,
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(png).decode("utf-8"),
                    },
                },
                {"type": "text", "text": "\n".join(lines)},
            ],
        }

    async def _render_latex_direct(
        self, diagram_type: str, definition: str, fmt: str, theme: str,
        width: Optional[int] = None, height: Optional[int] = None,
    ) -> Any:
        """Compile a LaTeX diagram server-side, with no browser involved.

        The Playwright renderer exists because most diagram types are drawn by
        frontend plugins, so the only way to rasterize them headlessly is to
        run those plugins in a real browser.  LaTeX is the opposite case: the
        entire renderer is a local binary, and the browser adds nothing.

        ``width``/``height`` (the caller's requested pixel bounds) were
        previously dropped here -- only type/definition/fmt/theme were
        forwarded -- so a dense or extreme-aspect LaTeX diagram had no size
        escape hatch at all (D-006).  They are now threaded to the renderer,
        which scales the pdf->png resolution to fit them.
        """
        from starlette.concurrency import run_in_threadpool

        from app.services.latex_renderer import latex_renderer

        result = await run_in_threadpool(
            latex_renderer.render, diagram_type, definition,
            "svg" if fmt == "svg" else "png", True, theme, width, height,
        )
        # The advisory is deliberately NOT computed here: every failure path
        # below returns early, and _latex_advisory describes a render that
        # SUCCEEDED but may not match the intended structure.  Attaching ring
        # notes to a "TeX not installed" error would be noise.  It is built
        # once on the success path, where it is actually read.

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
            # The renderer now bakes a themed surface into the PNG (dark page,
            # light default ink), so the dark theme IS applied -- say so.
            desc += (
                " The 'dark' theme was applied: rendered as light default ink "
                "on a dark background."
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


class RecallImageInput(BaseModel):
    """Input schema for recall_image."""

    handle: str = Field(
        ...,
        description=(
            "The handle from an elided image's placeholder text, e.g. "
            "'img-3f9a1c04'."
        ),
    )


class RecallImageTool(BaseMCPTool):
    """Bring a previously-elided image back into view."""

    name: str = "recall_image"
    description: str = (
        "[DIRECT] Bring an image you saw earlier back into view. When an "
        "image is dropped from your context to save space, its placeholder "
        "text carries a handle like 'img-3f9a1c04'; pass that handle here "
        "to see the ORIGINAL pixels again.\n\n"
        "This returns the exact bytes that were rendered before — not a "
        "re-render — so it can never disagree with what you saw the first "
        "time. Use it when you need to re-examine detail in a prior render, "
        "or compare an earlier attempt against a current one.\n\n"
        "You do NOT need this merely to trust an earlier conclusion: a "
        "finding you made while an image was in view was based on direct "
        "observation and remains valid whether or not the pixels are still "
        "displayed. Recall when you need NEW detail, not reassurance."
    )

    InputSchema = RecallImageInput

    async def execute(self, **kwargs) -> Any:
        kwargs.pop("_workspace_path", None)
        handle = (kwargs.get("handle") or "").strip()
        if not handle:
            return _error("'handle' is required (e.g. 'img-3f9a1c04')")

        from app.utils import image_recall

        scope = None
        try:
            from app.context import get_conversation_id_or_none
            scope = get_conversation_id_or_none()
        except Exception:  # noqa: BLE001 — scope is a guard, not a requirement
            pass

        content = image_recall.retrieve(handle, scope=scope)
        if content is None:
            # Expiry is a normal outcome, so say what it does and does not
            # imply.  Without this the model can read a miss as evidence
            # that its earlier observation was unfounded — the exact failure
            # the recall mechanism exists to prevent.
            return _error(
                f"No retained image for handle {handle!r}. It may have "
                f"expired or been evicted to free memory. This does NOT "
                f"invalidate anything you concluded while that image was in "
                f"view — those observations stand. If you need to see it "
                f"again, re-run render_diagram with the same definition."
            )

        logger.info("🖼️ recall_image: served %s", handle)
        label = image_recall.describe(handle) or ""
        note = (
            f"Recalled image {handle} — the original pixels, not a re-render."
            + (f" Originally described as: {label}" if label else "")
        )
        # Re-served images are the newest thing in context, so the normal
        # retention window applies to them from here; no pin is implied.
        return {
            "_has_image_content": True,
            "content": [
                *[b for b in content
                  if isinstance(b, dict) and b.get("type") == "image"],
                {"type": "text", "text": note},
            ],
        }


def _error(msg: str) -> dict:
    """Return a text-only error result."""
    return {"content": [{"type": "text", "text": f"Error: {msg}"}]}
