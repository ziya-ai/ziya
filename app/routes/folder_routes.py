"""
Folder and file management routes.
These routes forward to the existing implementations in server.py to maintain all functionality.
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List
import logging

logger = logging.getLogger("ZIYA")
router = APIRouter(tags=["folders"])


class FolderRequest(BaseModel):
    model_config = {"extra": "allow"}
    directory: str
    max_depth: Optional[int] = None


class FileRequest(BaseModel):
    model_config = {"extra": "allow"}
    file_path: str


class SaveFileRequest(BaseModel):
    model_config = {"extra": "allow"}
    file_path: str
    content: str


@router.post("/folder")
async def get_folder(request: FolderRequest):
    """Get folder structure - forwards to server.py implementation."""
    from app.server import get_folder as server_get_folder
    return await server_get_folder(request)


@router.get("/folder-progress")
async def get_folder_progress():
    """Get folder scan progress - forwards to server.py implementation."""
    from app.server import get_folder_progress as server_get_folder_progress
    return await server_get_folder_progress()


@router.post("/folder-cancel")
async def cancel_folder_scan():
    """Cancel ongoing folder scan - forwards to server.py implementation."""
    from app.server import cancel_folder_scan as server_cancel_folder_scan
    return await server_cancel_folder_scan()


@router.post("/api/clear-folder-cache")
async def clear_folder_cache():
    """Clear folder structure cache - forwards to server.py implementation."""
    from app.server import invalidate_folder_cache
    return invalidate_folder_cache()


@router.post("/file")
async def get_file(request: FileRequest):
    """Get file contents - forwards to server.py implementation."""
    from app.server import get_file as server_get_file
    return await server_get_file(request)


@router.post("/save")
async def save_file(request: SaveFileRequest):
    """Save file contents - forwards to server.py implementation."""
    from app.server import save_file as server_save_file
    return await server_save_file(request)


@router.get('/api/folders')
async def get_folders():
    """Get folder structure - forwards to server.py implementation with all caching and error handling."""
    from app.server import api_get_folders
    return await api_get_folders()


@router.get('/api/default-included-folders')
async def get_default_included_folders():
    """Get default included folders - forwards to server.py implementation."""
    from app.server import get_default_included_folders as server_get_default
    return await server_get_default()


@router.get('/api/folders-cached')
async def get_folders_cached():
    """Get folder structure from cache only - forwards to server.py implementation."""
    from app.server import get_folders_cached as server_get_folders_cached
    return await server_get_folders_cached()


@router.get('/api/folders-with-accurate-tokens')
async def get_folders_with_accurate_tokens():
    """Get folder structure with accurate token counts - forwards to server.py implementation."""
    from app.server import get_folders_with_accurate_tokens as server_get_folders_with_tokens
    return await server_get_folders_with_tokens()
