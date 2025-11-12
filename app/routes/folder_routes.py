"""
Folder and file management routes.
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional, List
import logging

logger = logging.getLogger("ZIYA")
router = APIRouter()


class FolderRequest(BaseModel):
    folder_path: str
    max_depth: Optional[int] = None
    exclude_patterns: Optional[List[str]] = None


class FileRequest(BaseModel):
    file_path: str


class FileContentRequest(BaseModel):
    file_path: str
    content: str


@router.post("/folder")
async def get_folder(request: FolderRequest):
    """Get folder structure."""
    from app.utils.folder_util import get_folder_structure
    
    folder_path = request.folder_path
    max_depth = request.max_depth
    exclude_patterns = request.exclude_patterns or []
    
    try:
        result = get_folder_structure(folder_path, max_depth, exclude_patterns)
        return result
    except Exception as e:
        logger.error(f"Error getting folder structure: {e}")
        return {"error": str(e)}


@router.get("/folder-progress")
async def get_folder_progress():
    """Get folder scan progress."""
    from app.utils.folder_util import get_scan_progress
    return get_scan_progress()


@router.post("/folder-cancel")
async def cancel_folder_scan():
    """Cancel ongoing folder scan."""
    from app.utils.folder_util import cancel_scan
    cancel_scan()
    return {"success": True}


@router.post("/api/clear-folder-cache")
async def clear_folder_cache():
    """Clear folder cache."""
    from app.utils.folder_util import invalidate_folder_cache
    invalidate_folder_cache()
    return {"success": True}


@router.post("/file")
async def get_file(request: FileRequest):
    """Get file content."""
    import os
    
    file_path = request.file_path
    try:
        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path}"}
            
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        logger.error(f"Error reading file: {e}")
        return {"error": str(e)}


@router.post("/save")
async def save_file(request: FileContentRequest):
    """Save file content."""
    import os
    
    file_path = request.file_path
    content = request.content
    
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error saving file: {e}")
        return {"error": str(e)}


@router.get('/api/folders')
async def get_folders(request: Request):
    """Get configured folders."""
    folders = request.app.state.folders
    return {"folders": folders}


@router.get('/api/default-included-folders')
async def get_default_included_folders(request: Request):
    """Get default included folders."""
    folders = request.app.state.folders
    return {"folders": folders.get("include", [])}


@router.get('/api/folders-cached')
async def get_folders_cached(request: Request):
    """Get cached folder structure."""
    from app.utils.folder_util import get_cached_folder_structure
    folders = request.app.state.folders
    return get_cached_folder_structure(folders)


@router.get('/api/folders-with-accurate-tokens')
async def get_folders_with_accurate_tokens(request: Request):
    """Get folder structure with accurate token counts."""
    from app.utils.folder_util import get_folder_structure_with_accurate_tokens
    folders = request.app.state.folders
    model_manager = request.app.state.model_manager
    return get_folder_structure_with_accurate_tokens(folders, model_manager)
