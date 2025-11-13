"""
API routes for model-initiated conversation and folder management.
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import uuid
import time
import os
import json
from pathlib import Path

from app.utils.logging_utils import logger

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class CreateFolderRequest(BaseModel):
    model_config = {"extra": "allow"}
    name: str = Field(..., description="Name of the folder to create")
    parent_id: Optional[str] = Field(None, description="Parent folder ID (null for root level)")
    system_instructions: Optional[str] = Field(None, description="Additional system instructions for this folder")
    use_global_context: bool = Field(True, description="Whether to use global file context")
    use_global_model: bool = Field(True, description="Whether to use global model config")


class CreateConversationRequest(BaseModel):
    model_config = {"extra": "allow"}
    title: str = Field(..., description="Title of the conversation")
    folder_id: Optional[str] = Field(None, description="Folder ID to place the conversation in")
    initial_message: Optional[str] = Field(None, description="Optional initial message")
    context_files: Optional[List[str]] = Field(None, description="List of files to include in context")


class MoveConversationRequest(BaseModel):
    model_config = {"extra": "allow"}
    conversation_id: str = Field(..., description="ID of the conversation to move")
    target_folder_id: Optional[str] = Field(None, description="Target folder ID (null for root)")


class FolderResponse(BaseModel):
    model_config = {"extra": "allow"}
    id: str
    name: str
    parent_id: Optional[str]
    created_at: int
    updated_at: int
    use_global_context: bool
    use_global_model: bool
    system_instructions: Optional[str]


class ConversationResponse(BaseModel):
    model_config = {"extra": "allow"}
    id: str
    title: str
    folder_id: Optional[str]
    created_at: int
    message_count: int


@router.post("/folders")
async def create_folder(request: CreateFolderRequest) -> FolderResponse:
    """Create a new conversation folder."""
    try:
        # Generate a unique folder ID
        folder_id = str(uuid.uuid4())
        current_time = int(time.time() * 1000)  # Milliseconds for consistency with frontend
        
        # Create folder data structure
        folder_data = {
            "id": folder_id,
            "name": request.name,
            "parentId": request.parent_id,
            "systemInstructions": request.system_instructions,
            "useGlobalContext": request.use_global_context,
            "useGlobalModel": request.use_global_model,
            "createdAt": current_time,
            "updatedAt": current_time
        }
        
        # Get the user's codebase directory for storing metadata
        user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
        folders_file = os.path.join(user_codebase_dir, ".ziya", "folders.json")
        
        # Ensure .ziya directory exists
        os.makedirs(os.path.dirname(folders_file), exist_ok=True)
        
        # Load existing folders
        folders = []
        if os.path.exists(folders_file):
            try:
                with open(folders_file, 'r', encoding='utf-8') as f:
                    folders = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                folders = []
        
        # Add new folder
        folders.append(folder_data)
        
        # Save updated folders
        with open(folders_file, 'w', encoding='utf-8') as f:
            json.dump(folders, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Created folder '{request.name}' with ID {folder_id}")
        
        return FolderResponse(
            id=folder_id,
            name=request.name,
            parent_id=request.parent_id,
            created_at=current_time,
            updated_at=current_time,
            use_global_context=request.use_global_context,
            use_global_model=request.use_global_model,
            system_instructions=request.system_instructions
        )
        
    except Exception as e:
        logger.error(f"Error creating folder: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/conversations")
async def create_conversation(request: CreateConversationRequest) -> ConversationResponse:
    """Create a new conversation."""
    try:
        # Generate a unique conversation ID
        conversation_id = str(uuid.uuid4())
        current_time = int(time.time() * 1000)  # Milliseconds for consistency with frontend
        
        # Create initial messages array
        messages = []
        if request.initial_message:
            messages.append({
                "id": str(uuid.uuid4()),
                "role": "human",
                "content": request.initial_message,
                "_timestamp": current_time
            })
        
        # Create conversation data structure
        conversation_data = {
            "id": conversation_id,
            "title": request.title,
            "messages": messages,
            "folderId": request.folder_id,
            "lastAccessedAt": current_time,
            "isActive": True,
            "_version": current_time,
            "hasUnreadResponse": False,
            "contextFiles": request.context_files or []  # Store context files for this conversation
        }
        
        # Get the user's codebase directory for storing metadata
        user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
        conversations_file = os.path.join(user_codebase_dir, ".ziya", "conversations.json")
        
        # Ensure .ziya directory exists
        os.makedirs(os.path.dirname(conversations_file), exist_ok=True)
        
        # Load existing conversations
        conversations = []
        if os.path.exists(conversations_file):
            try:
                with open(conversations_file, 'r', encoding='utf-8') as f:
                    conversations = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                conversations = []
        
        # Add new conversation
        conversations.append(conversation_data)
        
        # Save updated conversations
        with open(conversations_file, 'w', encoding='utf-8') as f:
            json.dump(conversations, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Created conversation '{request.title}' with ID {conversation_id}")
        
        return ConversationResponse(
            id=conversation_id,
            title=request.title,
            folder_id=request.folder_id,
            created_at=current_time,
            message_count=len(messages)
        )
        
    except Exception as e:
        logger.error(f"Error creating conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/conversations/move")
async def move_conversation(request: MoveConversationRequest) -> Dict[str, Any]:
    """Move a conversation to a different folder."""
    try:
        # Get the user's codebase directory for storing metadata
        user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
        conversations_file = os.path.join(user_codebase_dir, ".ziya", "conversations.json")
        
        # Load existing conversations
        if not os.path.exists(conversations_file):
            raise HTTPException(status_code=404, detail="No conversations found")
        
        with open(conversations_file, 'r', encoding='utf-8') as f:
            conversations = json.load(f)
        
        # Find and update the conversation
        conversation_found = False
        for conv in conversations:
            if conv.get("id") == request.conversation_id:
                conv["folderId"] = request.target_folder_id
                conv["_version"] = int(time.time() * 1000)
                conversation_found = True
                break
        
        if not conversation_found:
            raise HTTPException(status_code=404, detail=f"Conversation {request.conversation_id} not found")
        
        # Save updated conversations
        with open(conversations_file, 'w', encoding='utf-8') as f:
            json.dump(conversations, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Moved conversation {request.conversation_id} to folder {request.target_folder_id}")
        
        return {"success": True, "message": "Conversation moved successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error moving conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))
