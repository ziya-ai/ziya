"""
Context API endpoints.
"""
from fastapi import APIRouter, HTTPException
from typing import List

from ..models.context import Context, ContextCreate, ContextUpdate
from ..storage.projects import ProjectStorage
from ..storage.contexts import ContextStorage
from ..storage.chats import ChatStorage
from ..services.token_service import TokenService
from ..utils.paths import get_ziya_home, get_project_dir

router = APIRouter(prefix="/api/v1/projects/{project_id}/contexts", tags=["contexts"])

def get_context_storage(project_id: str) -> ContextStorage:
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    project = project_storage.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    token_service = TokenService()
    storage = ContextStorage(get_project_dir(project_id), token_service)
    storage.set_project_path(project.path)
    return storage

@router.get("", response_model=List[Context])
async def list_contexts(project_id: str):
    """List all contexts for a project."""
    storage = get_context_storage(project_id)
    return storage.list()

@router.post("", response_model=Context)
async def create_context(project_id: str, data: ContextCreate):
    """Create a new context."""
    storage = get_context_storage(project_id)
    return storage.create(data)

@router.get("/{context_id}", response_model=Context)
async def get_context(project_id: str, context_id: str):
    """Get a specific context."""
    storage = get_context_storage(project_id)
    context = storage.get(context_id)
    if not context:
        raise HTTPException(status_code=404, detail="Context not found")
    storage.touch(context_id)
    return context

@router.put("/{context_id}", response_model=Context)
async def update_context(
    project_id: str, 
    context_id: str, 
    data: ContextUpdate
):
    """Update a context."""
    storage = get_context_storage(project_id)
    try:
        context = storage.update(context_id, data)
        if not context:
            raise HTTPException(status_code=404, detail="Context not found")
        return context
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{context_id}")
async def delete_context(project_id: str, context_id: str):
    """Delete a context and remove from any chats using it."""
    storage = get_context_storage(project_id)
    
    if not storage.delete(context_id):
        raise HTTPException(status_code=404, detail="Context not found")
    
    # Remove from chats that reference this context
    chat_storage = ChatStorage(get_project_dir(project_id))
    chat_storage.remove_context_from_all_chats(context_id)
    
    return {"deleted": True, "id": context_id}
