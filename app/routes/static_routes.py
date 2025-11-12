"""
Static page and asset routes.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, FileResponse
import os
import logging

logger = logging.getLogger("ZIYA")
router = APIRouter()


def get_templates_dir():
    """Get the templates directory path."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))
    templates_dir = os.path.join(project_root, "templates")
    return templates_dir


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the main application page."""
    templates_dir = get_templates_dir()
    index_path = os.path.join(templates_dir, "index.html")
    
    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return HTMLResponse(content=content)
    except Exception as e:
        logger.error(f"Error serving index.html: {e}")
        return HTMLResponse(content="<h1>Error loading application</h1>", status_code=500)


@router.get("/debug", response_class=HTMLResponse)
async def debug(request: Request):
    """Serve debug information page."""
    return HTMLResponse(content="<h1>Debug Page</h1><p>Debug information would go here</p>")


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve favicon."""
    templates_dir = get_templates_dir()
    favicon_path = os.path.join(templates_dir, "favicon.ico")
    
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    else:
        # Return 204 No Content if favicon doesn't exist
        from fastapi.responses import Response
        return Response(status_code=204)
