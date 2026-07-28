"""
API routes for headless diagram rendering.

POST /api/render-diagram  — render a diagram spec to PNG or SVG.
POST /api/render-latex    — render a LaTeX diagram (TikZ, CircuiTikZ, ...) to SVG or PNG.
GET  /api/latex-capability — report which LaTeX render paths are available.

/api/render-diagram requires Playwright (optional dependency) and drives a
headless browser, reusing the frontend's own plugins.  /api/render-latex is a
separate pipeline: it shells out to a local TeX installation, so it shares no
machinery with the browser renderer and degrades independently.
"""
from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["diagrams"])

#: Cap on a single LaTeX body.  The renderer enforces its own limit too; this
#: one rejects an oversized payload before it is parsed into a model.
MAX_LATEX_BODY_CHARS = 64_000


class DiagramRenderRequest(BaseModel):
    """Request body for POST /api/render-diagram."""

    type: str = Field(
        ...,
        description="Diagram type: mermaid, graphviz, vega-lite, drawio, packet, etc.",
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
        description="Output format.  SVG falls back to PNG for canvas-based renderers.",
    )
    width: Optional[int] = Field(
        default=None,
        description="Explicit width in pixels (optional).",
    )
    height: Optional[int] = Field(
        default=None,
        description="Explicit height in pixels (optional).",
    )
    title: Optional[str] = Field(
        default=None,
        description="Optional title shown above the diagram.",
    )


@router.post("/api/render-diagram")
async def render_diagram(request: DiagramRenderRequest) -> Response:
    """Render a diagram spec to an image using the headless browser.

    Returns the image bytes directly with the appropriate content-type.
    """
    try:
        from app.services.diagram_renderer import get_diagram_renderer
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail=(
                "Headless diagram rendering requires Playwright. "
                "Install with: pip install playwright && playwright install chromium"
            ),
        ) from exc

    # Build the spec dict for the renderer
    spec: dict[str, Any] = {
        "type": request.type,
        "definition": request.definition,
        "theme": request.theme,
    }
    if request.width:
        spec["width"] = request.width
    if request.height:
        spec["height"] = request.height
    if request.title:
        spec["title"] = request.title

    try:
        from app.config.env_registry import ziya_env
        renderer = await get_diagram_renderer(server_port=ziya_env("ZIYA_PORT"))
        image_bytes = await renderer.render_diagram(
            spec,
            format=request.format,
        )
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Playwright is not installed.  Run: pip install playwright && playwright install chromium",
        )
    except RuntimeError as exc:
        logger.error("Diagram render failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.error("Unexpected diagram render error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Render failed: {exc}")

    content_type = {
        "png": "image/png",
        "svg": "image/svg+xml",
    }.get(request.format, "image/png")

    return Response(
        content=image_bytes,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="diagram.{request.format}"',
        },
    )


class LatexRenderRequest(BaseModel):
    """Request body for POST /api/render-latex."""

    type: str = Field(
        ...,
        description="LaTeX diagram type: circuitikz, tikz, chemfig, tikz-cd.",
    )
    definition: str = Field(
        ...,
        max_length=MAX_LATEX_BODY_CHARS,
        description="LaTeX body.  The preamble is supplied by the server-side profile.",
    )
    format: Literal["auto", "svg", "png"] = Field(
        default="auto",
        description=(
            "Output format.  'auto' prefers SVG when dvisvgm is installed, "
            "because SVG keeps text selectable and recolourable for dark mode."
        ),
    )


@router.get("/api/latex-capability")
async def latex_capability() -> dict[str, Any]:
    """Report what the local TeX installation can render.

    The frontend calls this before rendering so it can show install
    instructions inline instead of firing a request that is certain to fail.
    Per-profile availability is included because the toolchain can be present
    while an individual package (circuitikz, chemfig) is not.
    """
    from app.services.latex_profiles import PROFILES, install_command
    from app.services.latex_renderer import latex_renderer

    # probe() is cached after the first call, but it shells out to kpsewhich on
    # a cold start, so keep it off the event loop.
    cap = await run_in_threadpool(latex_renderer.probe)

    profiles: dict[str, Any] = {}
    for key, profile in PROFILES.items():
        missing = await run_in_threadpool(latex_renderer.missing_for_profile, profile)
        profiles[key] = {
            "available": cap.available and not missing,
            "missing_packages": list(missing),
            "install_command": install_command(list(missing)) if missing else "",
        }

    return {
        "available": cap.available,
        "preferred_format": cap.preferred_format,
        "sandboxed": cap.has_sandbox,
        "tex_distribution": cap.tex_distribution,
        "missing_toolchain": list(cap.missing_toolchain),
        "profiles": profiles,
    }


@router.post("/api/render-latex")
async def render_latex(request: LatexRenderRequest) -> Response:
    """Compile a LaTeX diagram body to SVG or PNG.

    Error handling is deliberately granular: the frontend renders a very
    different affordance for "you need to install circuitikz" (actionable, with
    a command to copy) than for "your TikZ has a syntax error" (show the TeX
    log) or "this input was rejected" (show why, do not offer a retry).  A flat
    500 would collapse all three into an unhelpful failure.
    """
    from app.services.latex_renderer import latex_renderer

    # The render is synchronous and takes 0.5-3s, so running it inline would
    # block the event loop for every other request for that whole duration.
    result = await run_in_threadpool(
        latex_renderer.render,
        request.type,
        request.definition,
        request.format,
    )

    if result.ok:
        media_type = "image/svg+xml" if result.fmt == "svg" else "image/png"
        return Response(
            content=result.content,
            media_type=media_type,
            headers={
                "Content-Disposition": f'inline; filename="diagram.{result.fmt}"',
                # Content-addressed by construction: the same body always
                # produces the same bytes, so it is safe to cache hard.
                "Cache-Control": "private, max-age=3600",
                "X-Ziya-Latex-Cached": "1" if result.cached else "0",
                "X-Ziya-Latex-Duration-Ms": str(result.duration_ms),
            },
        )

    # 501 Not Implemented: the request was valid but the server lacks the
    # capability.  Distinct from 400 (bad input) so the frontend can offer
    # install instructions rather than blaming the diagram.
    if result.error_kind == "not_installed":
        raise HTTPException(
            status_code=501,
            detail={
                "kind": "not_installed",
                "message": result.error,
                "install_command": result.install_hint,
                "missing_packages": list(result.missing_packages),
            },
        )

    if result.error_kind == "rejected":
        # Log at warning: a rejection means model-authored LaTeX tried
        # something disallowed, which is worth seeing in the server log even
        # though the layered defences stopped it.
        logger.warning(
            "LaTeX render rejected (type=%s): %s", request.type, result.error
        )
        raise HTTPException(
            status_code=400,
            detail={"kind": "rejected", "message": result.error},
        )

    if result.error_kind == "internal":
        # Unknown diagram type — the caller asked for a profile that does not
        # exist, which is a client error, not a server fault.
        raise HTTPException(
            status_code=400,
            detail={"kind": "unsupported_type", "message": result.error},
        )

    if result.error_kind == "timeout":
        # 504: the document is pathological rather than malformed.  Retrying
        # the identical input will time out identically, so say so.
        raise HTTPException(
            status_code=504,
            detail={"kind": "timeout", "message": result.error},
        )

    # Compile failure: surface the extracted message plus the log tail, which
    # is what makes a TeX error diagnosable at all.
    logger.info("LaTeX compile failed (type=%s): %s", request.type, result.error)
    raise HTTPException(
        status_code=422,
        detail={
            "kind": "compile",
            "message": result.error,
            "log_excerpt": result.log_excerpt,
        },
    )
