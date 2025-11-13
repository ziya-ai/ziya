"""
Static page routes.
These routes forward to the existing implementations in server.py.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, FileResponse
import logging

logger = logging.getLogger("ZIYA")
router = APIRouter(tags=["static"])


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the main application page - forwards to server.py implementation."""
    from app.server import root as server_root
    return await server_root(request)


@router.get("/debug", response_class=HTMLResponse)
async def debug(request: Request):
    """Serve debug page - forwards to server.py implementation."""
    from app.server import debug as server_debug
    return await server_debug(request)


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve favicon - forwards to server.py implementation."""
    from app.server import favicon as server_favicon
    return await server_favicon()
