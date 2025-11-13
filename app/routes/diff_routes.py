"""
Diff application routes.
These routes forward to the existing implementations in server.py.
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List
import logging

logger = logging.getLogger("ZIYA")
router = APIRouter(prefix="/api", tags=["diff"])


class ApplyPatchRequest(BaseModel):
    patch: str
    conversation_id: str


class CheckFilesRequest(BaseModel):
    files: List[str]


class ApplyChangesRequest(BaseModel):
    changes: str
    conversation_id: str


@router.post('/apply_patch')
async def apply_patch(request: ApplyPatchRequest):
    """Apply a patch - forwards to server.py implementation."""
    from app.server import apply_patch as server_apply_patch
    return await server_apply_patch(request)


@router.post('/check-files-in-context')
async def check_files_in_context(body: CheckFilesRequest):
    """Check which files are in context - forwards to server.py implementation."""
    from app.server import check_files_in_context as server_check_files
    return await server_check_files(body)


@router.post('/apply-changes')
async def apply_changes(request: ApplyChangesRequest):
    """Apply changes - forwards to server.py implementation."""
    from app.server import apply_changes as server_apply_changes
    return await server_apply_changes(request)
