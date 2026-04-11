"""
Export configuration endpoints.

Allows plugins to extend available export targets.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from typing import List, Dict, Any, Optional
from app.utils.logging_utils import logger
from pydantic import BaseModel, Field

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
    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
    version = get_current_version()
    port = int(os.environ.get("ZIYA_PORT", "6969"))

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
    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
    version = get_current_version()
    port = int(os.environ.get("ZIYA_PORT", "6969"))

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