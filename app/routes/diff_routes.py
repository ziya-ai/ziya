"""
Diff application routes.
These routes forward to the existing implementations in server.py.
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from typing import List, Optional
import logging

logger = logging.getLogger("ZIYA")
router = APIRouter(prefix="/api", tags=["diff"])


class ApplyPatchRequest(BaseModel):
    model_config = {"extra": "allow"}
    model_config = {"extra": "allow"}
    patch: str
    conversation_id: str


class CheckFilesRequest(BaseModel):
    model_config = {"extra": "allow"}
    model_config = {"extra": "allow"}
    files: List[str]


class ApplyChangesRequest(BaseModel):
    model_config = {"extra": "allow"}
    model_config = {"extra": "allow"}
    diff: str
    filePath: str = Field(..., description="Path to the file being modified")
    requestId: Optional[str] = Field(None, description="Unique ID to track this specific diff application")
    elementId: Optional[str] = None
    buttonInstanceId: Optional[str] = None


@router.post('/apply_patch')
async def apply_patch(request: ApplyPatchRequest):
    """Apply a patch - forwards to server.py implementation."""
    from app.server import apply_patch as server_apply_patch
    return await server_apply_patch(request)


@router.post('/check-files-in-context')
async def check_files_in_context(request: Request):
    """Check which files are in context - forwards to server.py implementation."""
    from app.server import check_files_in_context as server_check_files
    return await server_check_files(request)


@router.post('/apply-changes')
async def apply_changes(request: Request):
    """Apply changes - forwards to server.py implementation."""
    from app.server import apply_changes as server_apply_changes
    return await server_apply_changes(request)
