"""
Diff and patch application routes.
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import List, Dict, Any
import logging

logger = logging.getLogger("ZIYA")
router = APIRouter()


class ApplyPatchRequest(BaseModel):
    file_path: str
    diff_content: str


class ApplyChangesRequest(BaseModel):
    changes: List[Dict[str, Any]]


class CheckFilesRequest(BaseModel):
    files: List[str]


@router.post("/apply_patch")
async def apply_patch(request: ApplyPatchRequest):
    """Apply a diff patch to a file."""
    from app.utils.diff_utils.pipeline import apply_diff_pipeline
    
    try:
        result = apply_diff_pipeline(
            request.file_path,
            request.diff_content
        )
        return result
    except Exception as e:
        logger.error(f"Error applying patch: {e}")
        return {"success": False, "error": str(e)}


@router.post('/api/check-files-in-context')
async def check_files_in_context(request: Request, body: CheckFilesRequest):
    """Check which files are currently in context."""
    files_to_check = body.files
    folders = request.app.state.folders
    
    try:
        from app.utils.folder_util import is_file_in_context
        
        results = {}
        for file_path in files_to_check:
            results[file_path] = is_file_in_context(file_path, folders)
        
        return {"files": results}
    except Exception as e:
        logger.error(f"Error checking files in context: {e}")
        return {"error": str(e)}


@router.post('/api/apply-changes')
async def apply_changes(request: ApplyChangesRequest):
    """Apply multiple code changes."""
    from app.utils.diff_utils.pipeline import apply_diff_pipeline
    
    results = []
    for change in request.changes:
        try:
            result = apply_diff_pipeline(
                change.get("file_path"),
                change.get("diff_content")
            )
            results.append({
                "file_path": change.get("file_path"),
                "success": result.get("success", False),
                "result": result
            })
        except Exception as e:
            logger.error(f"Error applying change to {change.get('file_path')}: {e}")
            results.append({
                "file_path": change.get("file_path"),
                "success": False,
                "error": str(e)
            })
    
    return {"results": results}
