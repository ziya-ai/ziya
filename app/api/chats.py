"""
Chat and chat group API endpoints.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from datetime import timedelta
import uuid

from ..models.chat import Chat, ChatCreate, ChatUpdate, ChatSummary, Message, ChatBulkSync, ChatGroupBulkSync
from ..models.group import ChatGroup, ChatGroupCreate, ChatGroupUpdate
from ..storage.projects import ProjectStorage
from ..storage.chats import ChatStorage
from ..storage.groups import ChatGroupStorage
from ..utils.paths import get_ziya_home, get_project_dir

router = APIRouter(tags=["chats"])

def get_chat_storage(project_id: str) -> ChatStorage:
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    project = project_storage.get(project_id)
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return ChatStorage(get_project_dir(project_id))

def get_group_storage(project_id: str) -> ChatGroupStorage:
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    project = project_storage.get(project_id)
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return ChatGroupStorage(get_project_dir(project_id))

# Chat Groups

@router.get("/api/v1/projects/{project_id}/chat-groups", response_model=List[ChatGroup])
async def list_chat_groups(project_id: str):
    """List all chat groups."""
    storage = get_group_storage(project_id)
    return storage.list()

@router.post("/api/v1/projects/{project_id}/chat-groups", response_model=ChatGroup)
async def create_chat_group(project_id: str, data: ChatGroupCreate):
    """Create a chat group."""
    storage = get_group_storage(project_id)
    return storage.create(data)

@router.put("/api/v1/projects/{project_id}/chat-groups/{group_id}", response_model=ChatGroup)
async def update_chat_group(project_id: str, group_id: str, data: ChatGroupUpdate):
    """Update a chat group."""
    storage = get_group_storage(project_id)
    group = storage.update(group_id, data)
    
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    
    return group

@router.delete("/api/v1/projects/{project_id}/chat-groups/{group_id}")
async def delete_chat_group(project_id: str, group_id: str):
    """Delete a chat group (chats become ungrouped)."""
    storage = get_group_storage(project_id)
    chat_storage = get_chat_storage(project_id)
    
    # Ungroup all chats in this group
    for chat in chat_storage.list(group_id=group_id):
        chat_storage.update(chat.id, ChatUpdate(groupId=None))
    
    if not storage.delete(group_id):
        raise HTTPException(status_code=404, detail="Group not found")
    
    return {"deleted": True}

@router.put("/api/v1/projects/{project_id}/chat-groups/reorder")
async def reorder_chat_groups(project_id: str, ordered_ids: List[str]):
    """Reorder chat groups."""
    storage = get_group_storage(project_id)
    return storage.reorder(ordered_ids)

@router.post("/api/v1/projects/{project_id}/chat-groups/bulk-sync")
async def bulk_sync_groups(project_id: str, data: ChatGroupBulkSync):
    """
    Bulk upsert chat groups/folders from frontend (cross-port sync).
    For each group: if server version is newer, skip. Otherwise upsert.
    """
    storage = get_group_storage(project_id)
    
    results = {"created": 0, "updated": 0, "skipped": 0, "errors": []}
    
    for group_data in data.groups:
        try:
            existing = storage.get(group_data.id)
            
            if existing:
                incoming_dump = group_data.model_dump()
                existing_dump = existing.model_dump()
                incoming_ver = incoming_dump.get('updatedAt') or existing_dump.get('createdAt') or 0
                existing_ver = existing_dump.get('updatedAt') or existing.createdAt or 0

                if incoming_ver >= existing_ver:
                    groups_file = storage._read_groups_file()
                    groups_file.groups = [
                        ChatGroup(**incoming_dump) if g.id == group_data.id else g
                        for g in groups_file.groups
                    ]
                    storage._write_groups_file(groups_file)
                    results["updated"] += 1
                else:
                    results["skipped"] += 1
            else:
                groups_file = storage._read_groups_file()
                groups_file.groups.append(ChatGroup(**group_data.model_dump()))
                storage._write_groups_file(groups_file)
                results["created"] += 1
        except Exception as e:
            results["errors"].append({"id": group_data.id, "error": str(e)})
    
    return results

# Chats

@router.get("/api/v1/projects/{project_id}/chats")
async def list_chats(
    project_id: str,
    group_id: Optional[str] = Query(None),
    limit: Optional[int] = Query(None),
    offset: Optional[int] = Query(0),
    include_messages: bool = Query(False)
):
    """List all chats for a project. Use include_messages=true for full chat data."""
    storage = get_chat_storage(project_id)
    
    if include_messages:
        chats = storage.list(group_id=group_id)
        if limit:
            chats = chats[offset:offset + limit]
        return chats
    
    summaries = storage.list_summaries(group_id=group_id)
    
    # Apply pagination
    if limit:
        summaries = summaries[offset:offset + limit]
    
    return summaries

@router.post("/api/v1/projects/{project_id}/chats", response_model=Chat)
async def create_chat(project_id: str, data: ChatCreate):
    """Create a new chat."""
    storage = get_chat_storage(project_id)
    group_storage = get_group_storage(project_id)
    
    # Get default contexts/skills from group or project
    default_context_ids = None
    default_skill_ids = None
    
    if data.groupId:
        group = group_storage.get(data.groupId)
        if group:
            default_context_ids = group.defaultContextIds
            default_skill_ids = group.defaultSkillIds
    
    if default_context_ids is None:
        # Use project defaults
        project_storage = ProjectStorage(get_ziya_home())
        project = project_storage.get(project_id)
        if project:
            default_context_ids = project.settings.defaultContextIds
            default_skill_ids = project.settings.defaultSkillIds
    
    return storage.create(data, default_context_ids, default_skill_ids)

@router.post("/api/v1/projects/{project_id}/chats/bulk-sync")
async def bulk_sync_chats(project_id: str, data: ChatBulkSync):
    """
    Bulk upsert chats from frontend (IndexedDB migration).
    For each chat: if it exists on server and server version is newer, skip.
    Otherwise, create or overwrite with the provided data.
    """
    storage = get_chat_storage(project_id)
    
    results = {"created": 0, "updated": 0, "skipped": 0, "errors": []}
    
    for chat_data in data.chats:
        try:
            existing = storage.get(chat_data.id)
            
            if existing:
                # Use _version (frontend's authoritative version counter) for
                # comparison, falling back to lastActiveAt for pre-_version data.
                existing_extra = existing.model_dump()
                incoming_extra = chat_data.model_dump()
                incoming_ver = incoming_extra.get('_version') or chat_data.lastActiveAt or chat_data.lastAccessedAt or 0
                existing_ver = existing_extra.get('_version') or existing.lastActiveAt or 0

                if incoming_ver >= existing_ver:
                    # Overwrite with incoming data
                    storage._write_json(
                        storage._chat_file(chat_data.id),
                        chat_data.model_dump()
                    )
                    results["updated"] += 1
                else:
                    results["skipped"] += 1
            else:
                # Create new
                storage._write_json(
                    storage._chat_file(chat_data.id),
                    chat_data.model_dump()
                )
                results["created"] += 1
        except Exception as e:
            results["errors"].append({"id": chat_data.id, "error": str(e)})
    
    return results

@router.get("/api/v1/projects/{project_id}/chats/{chat_id}", response_model=Chat)
async def get_chat(project_id: str, chat_id: str):
    """Get full chat including messages."""
    storage = get_chat_storage(project_id)
    chat = storage.get(chat_id)
    
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    storage.touch(chat_id)
    return chat

@router.put("/api/v1/projects/{project_id}/chats/{chat_id}", response_model=Chat)
async def update_chat(project_id: str, chat_id: str, data: ChatUpdate):
    """Update chat metadata."""
    storage = get_chat_storage(project_id)
    chat = storage.update(chat_id, data)
    
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    return chat


# ── Retention policy endpoint ────────────────────────────────────────

@router.get("/api/v1/retention-policy")
async def get_retention_policy():
    """
    Return the effective data retention policy.

    The frontend uses this to purge expired conversations from IndexedDB
    and to display retention notices to the user.
    """
    from app.plugins.data_retention import get_retention_enforcer

    enforcer = get_retention_enforcer()
    policy = enforcer.policy

    ttl_seconds = policy.get_ttl_seconds("conversation_data")

    return {
        "conversation_data_ttl_seconds": ttl_seconds,
        "conversation_data_ttl_days": (
            ttl_seconds / 86400.0 if ttl_seconds is not None else None
        ),
        "policy_reason": policy.policy_reason,
        "has_retention_policy": ttl_seconds is not None,
    }

@router.delete("/api/v1/projects/{project_id}/chats/{chat_id}")
async def delete_chat(project_id: str, chat_id: str):
    """Delete a chat."""
    storage = get_chat_storage(project_id)
    
    if not storage.delete(chat_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    
    return {"deleted": True}

@router.post("/api/v1/projects/{project_id}/chats/{chat_id}/messages", response_model=Chat)
async def add_message(project_id: str, chat_id: str, message_data: Message):
    """Add a message to a chat."""
    storage = get_chat_storage(project_id)
    
    # Generate ID if not provided
    if not message_data.id:
        message_data.id = str(uuid.uuid4())
    
    chat = storage.add_message(chat_id, message_data)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    return chat
