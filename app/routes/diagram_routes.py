"""
API routes for headless diagram rendering.

POST /api/render-diagram  — render a diagram spec to PNG or SVG.

Requires Playwright to be installed (optional dependency).
"""
from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["diagrams"])


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
        renderer = await get_diagram_renderer()
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
