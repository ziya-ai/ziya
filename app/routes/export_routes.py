"""
Export configuration endpoints.

Allows plugins to extend available export targets.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from typing import List, Dict, Any, Optional
from app.utils.logging_utils import logger
from app.config.env_registry import ziya_env
from pydantic import BaseModel, Field, ConfigDict

router = APIRouter(prefix="/api/export", tags=["export"])

@router.get("/targets")
async def get_export_targets() -> Dict[str, Any]:
    """
    Get available export targets.
    
    Returns base targets (GitHub Gist) plus any plugin-provided targets.
    """
    # Base target always available
    targets = [
        {
            "id": "public",
            "name": "GitHub Gist",
            "url": "https://gist.github.com",
            "icon": "GithubOutlined",
            "description": "Public paste service with markdown support, syntax highlighting, and version control"
        }
    ]
    
    # Get additional targets from plugins
    try:
        from app.plugins import get_active_config_providers

        for provider in get_active_config_providers():
            config = provider.get_defaults()
            if 'export_targets' in config:
                targets.extend(config['export_targets'])
                logger.debug(f"Added export targets from {provider.provider_id}")

        # Also collect targets from ExportProvider plugins
        from app.plugins import get_export_providers
        for ep in get_export_providers():
            try:
                info = ep.get_target_info()
                if not any(t['id'] == info.get('id') for t in targets):
                    targets.append(info)
            except Exception as e:
                logger.warning("ExportProvider %s target_info error: %s", ep.provider_id, e)
    except Exception as e:
        logger.debug(f"Could not load plugin export targets: {e}")

    return {"targets": targets}


class RenderedExportRequest(BaseModel):
    """Request body for POST /api/export/rendered."""

    conversation_id: Optional[str] = None
    messages: List[Dict[str, Any]]
    format: str = Field(default="markdown", pattern="^(markdown|html)$")
    target: str = Field(default="public")
    theme: str = Field(default="light", pattern="^(dark|light)$")
    image_format: str = Field(default="svg", pattern="^(svg|png)$")


@router.post("/rendered")
async def export_rendered(request: RenderedExportRequest) -> Dict[str, Any]:
    """Export a conversation with server-side rendered diagram images.

    Uses the headless Playwright renderer to produce diagram images
    entirely on the server.  For plugin export targets, CLI exports,
    and API consumers that do not have a browser.
    """
    import os
    from app.utils.conversation_exporter import export_conversation_rendered
    from app.agents.models import ModelManager
    from app.utils.version_util import get_current_version

    model_alias = ModelManager.get_model_alias()
    endpoint = ziya_env("ZIYA_ENDPOINT")
    version = get_current_version()
    port = ziya_env("ZIYA_PORT")

    result = await export_conversation_rendered(
        messages=request.messages,
        format_type=request.format,
        target=request.target,
        theme=request.theme,
        version=version,
        model=model_alias,
        provider=endpoint,
        server_port=port,
    )

    return result


class PluginExportRequest(BaseModel):
    """Request body for POST /api/export/to-target."""

    conversation_id: Optional[str] = None
    messages: List[Dict[str, Any]]
    target_id: str
    format: str = Field(default="markdown", pattern="^(markdown|html)$")
    theme: str = Field(default="light", pattern="^(dark|light)$")


@router.post("/to-target")
async def export_to_target(request: PluginExportRequest) -> Dict[str, Any]:
    """Export a conversation directly to a plugin export target.

    Renders diagrams server-side, then calls the ExportProvider's
    ``export()`` method to push to the target service (Slack, Quip, etc.).
    """
    import os
    from app.utils.conversation_exporter import (
        export_conversation_rendered,
        render_diagrams_server_side,
    )
    from app.plugins import get_export_providers
    from app.agents.models import ModelManager
    from app.utils.version_util import get_current_version

    # Find the target provider
    provider = None
    for ep in get_export_providers():
        info = ep.get_target_info()
        if info.get('id') == request.target_id:
            provider = ep
            break

    if not provider:
        return JSONResponse(
            status_code=404,
            content={"error": f"Export target '{request.target_id}' not found"},
        )

    model_alias = ModelManager.get_model_alias()
    endpoint = ziya_env("ZIYA_ENDPOINT")
    version = get_current_version()
    port = ziya_env("ZIYA_PORT")

    # Render the export with server-side diagrams
    export_result = await export_conversation_rendered(
        messages=request.messages,
        format_type=request.format,
        target=request.target_id,
        theme=request.theme,
        version=version,
        model=model_alias,
        provider=endpoint,
        server_port=port,
    )

    # Render diagram images as separate files for targets that need them
    diagram_images = await render_diagrams_server_side(
        request.messages, theme=request.theme, format='png', server_port=port,
    )
    images_dict = {}
    for i, (fp, diag) in enumerate(diagram_images.items()):
        import base64 as b64mod
        data_uri = diag.get('dataUri', '')
        if ',' in data_uri:
            raw = b64mod.b64decode(data_uri.split(',')[1])
            ext = 'svg' if 'svg' in data_uri else 'png'
            images_dict[f"diagram_{i}.{ext}"] = raw

    # Push to the target service
    metadata = {
        'conversation_id': request.conversation_id,
        'model': model_alias,
        'provider': endpoint,
        'version': version,
        'diagrams_count': export_result.get('diagrams_count', 0),
    }

    try:
        push_result = await provider.export(
            content=export_result['content'],
            format_type=request.format,
            metadata=metadata,
            images=images_dict if images_dict else None,
        )
        return push_result
    except Exception as exc:
        logger.error("Export to %s failed: %s", request.target_id, exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"Export failed: {exc}", "success": False},
        )


class PdfExportRequest(BaseModel):
    """Request body for POST /api/export/pdf.

    Mirrors RenderedExportRequest: the caller sends the raw ``messages`` plus
    the option knobs; option-based FILTERING happens in the ``/print`` page
    (single source of truth shared by PDF / HTML / CLI), so this endpoint does
    not pre-filter.  ``project_id`` + ``conversation_id`` let the server load a
    conversation by id when the client does not ship the bodies.
    """

    conversation_id: Optional[str] = None
    project_id: Optional[str] = None
    messages: Optional[List[Dict[str, Any]]] = None
    title: str = Field(default="Ziya Session Transcript")
    # Render options — same semantics as ExportConversationModal.
    round_limit: Optional[int] = Field(default=None, alias="roundLimit")
    include_human: bool = Field(default=True, alias="includeHuman")
    include_collapsed: bool = Field(default=True, alias="includeCollapsed")
    include_footer: bool = Field(default=True, alias="includeFooter")

    # Accept BOTH the camelCase aliases (what the frontend sends) and the
    # snake_case field names when constructed in Python/tests.
    model_config = ConfigDict(populate_by_name=True)


@router.post("/pdf")
async def export_pdf(request: PdfExportRequest):
    """Export a conversation to a high-fidelity PDF via the headless /print route.

    Renders the WHOLE conversation through the real MarkdownRenderer pipeline
    (Prism / KaTeX / react-diff-view / D3 diagrams) in headless Chromium and
    captures it with ``page.pdf()`` (A4, printBackground).  Returns the PDF
    bytes with ``application/pdf`` + a ``Content-Disposition`` attachment.

    Follows the /rendered + /to-target conventions for version/model/provider
    plumbing (ModelManager.get_model_alias, ZIYA_ENDPOINT, get_current_version,
    ZIYA_PORT).  Missing conversations and absent Playwright surface as real
    HTTP errors rather than silent success.
    """
    from fastapi import Response
    from app.agents.models import ModelManager
    from app.utils.version_util import get_current_version

    model_alias = ModelManager.get_model_alias()
    endpoint = ziya_env("ZIYA_ENDPOINT")
    version = get_current_version()
    port = ziya_env("ZIYA_PORT")
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 6969

    options = {
        "roundLimit": request.round_limit,
        "includeHuman": request.include_human,
        "includeCollapsed": request.include_collapsed,
        "includeFooter": request.include_footer,
    }

    # Import lazily so the app (and this module's other endpoints) keep working
    # when Playwright is not installed — the ImportError is surfaced below.
    try:
        from app.services.pdf_exporter import export_conversation_pdf
    except ImportError as exc:  # pragma: no cover - defensive
        logger.error("PDF export unavailable: %s", exc)
        return JSONResponse(
            status_code=501,
            content={"error": f"PDF export unavailable: {exc}", "success": False},
        )

    try:
        pdf_bytes, meta = await export_conversation_pdf(
            messages=request.messages,
            project_id=request.project_id,
            conversation_id=request.conversation_id,
            options=options,
            title=request.title,
            version=version,
            model=model_alias,
            provider=endpoint,
            server_port=port,
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc), "success": False})
    except LookupError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc), "success": False})
    except ImportError as exc:
        logger.error("PDF export requires Playwright: %s", exc)
        return JSONResponse(
            status_code=501,
            content={"error": str(exc), "success": False},
        )
    except Exception as exc:
        logger.error("PDF export failed: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": f"PDF export failed: {exc}", "success": False},
        )

    safe_title = "".join(
        c if c.isalnum() or c in (" ", "-", "_") else "_" for c in request.title
    ).strip().replace(" ", "_") or "ziya_conversation"
    filename = f"{safe_title}.pdf"

    logger.info(
        "PDF export succeeded: %d bytes, %d messages",
        meta.get("size", len(pdf_bytes)), meta.get("message_count", 0),
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
            "X-Ziya-Message-Count": str(meta.get("message_count", 0)),
        },
    )