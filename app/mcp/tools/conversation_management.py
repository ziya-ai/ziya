"""
MCP tools for conversation and folder management.

These tools allow the model to create and organize conversations and folders
for complex multi-threaded tasks.
"""

import asyncio
import json
import os
import uuid
import time
from typing import Dict, List, Optional, Any

import httpx
from pydantic import BaseModel, Field

from app.utils.logging_utils import logger
from app.mcp.tools.base import BaseMCPTool


class CreateFolderTool(BaseMCPTool):
    """Tool for creating conversation folders."""
    
    name = "create_conversation_folder"
    description = """Create a new conversation folder to organize related conversations.
    
    This tool allows you to create folders for organizing conversations around specific topics,
    projects, or task hierarchies. Folders can be nested and have their own system instructions.
    
    Use this when:
    - Starting work on a complex multi-part task that needs organization
    - Creating topic-based conversation groups
    - Setting up project hierarchies with different contexts
    """
    
    class InputSchema(BaseModel):
        name: str = Field(..., description="Name of the folder (e.g., 'Database Migration Tasks', 'API Development')")
        parent_id: Optional[str] = Field(None, description="Parent folder ID for nested folders (null for root level)")
        system_instructions: Optional[str] = Field(None, description="Additional system instructions specific to this folder's conversations")
        use_global_context: bool = Field(True, description="Whether conversations in this folder should use the global file context")
        use_global_model: bool = Field(True, description="Whether conversations in this folder should use the global model settings")
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            # Validate inputs
            input_data = self.InputSchema.model_validate(kwargs)
            
            # Make request to the API endpoint
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "http://localhost:8000/api/conversations/folders",
                    json={
                        "name": input_data.name,
                        "parent_id": input_data.parent_id,
                        "system_instructions": input_data.system_instructions,
                        "use_global_context": input_data.use_global_context,
                        "use_global_model": input_data.use_global_model
                    },
                    timeout=30.0
                )
                
                if response.status_code != 200:
                    return {
                        "error": True,
                        "message": f"Failed to create folder: {response.status_code} {response.reason_phrase}"
                    }
                
                folder_data = response.json()
                
                return {
                    "success": True,
                    "folder": {
                        "id": folder_data["id"],
                        "name": folder_data["name"],
                        "parent_id": folder_data["parent_id"],
                        "system_instructions": folder_data["system_instructions"]
                    },
                    "message": f"Created folder '{input_data.name}' with ID {folder_data['id']}"
                }
                
        except Exception as e:
            logger.error(f"Error creating folder: {e}")
            return {
                "error": True,
                "message": f"Error creating folder: {str(e)}"
            }


class CreateConversationTool(BaseMCPTool):
    """Tool for creating new conversations."""
    
    name = "create_conversation"
    description = """Create a new conversation thread for a specific sub-task or topic.
    
    This tool allows you to spawn new conversation threads for organizing complex tasks
    into manageable sub-tasks. Each conversation can have its own context files and
    initial message to get started.
    
    Use this when:
    - Breaking down a complex task into sub-tasks that need separate threads
    - Creating focused conversations for specific components or features
    - Setting up parallel work streams for different aspects of a project
    """
    
    class InputSchema(BaseModel):
        title: str = Field(..., description="Title of the conversation (e.g., 'Database Schema Design', 'Unit Tests for API')")
        folder_id: Optional[str] = Field(None, description="Folder ID to place the conversation in (use folder ID from create_conversation_folder)")
        initial_message: Optional[str] = Field(None, description="Initial message to start the conversation")
        context_files: Optional[List[str]] = Field(None, description="List of file paths to include in the conversation context")
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            # Validate inputs
            input_data = self.InputSchema.model_validate(kwargs)
            
            # Make request to the API endpoint
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "http://localhost:8000/api/conversations/conversations",
                    json={
                        "title": input_data.title,
                        "folder_id": input_data.folder_id,
                        "initial_message": input_data.initial_message,
                        "context_files": input_data.context_files
                    },
                    timeout=30.0
                )
                
                if response.status_code != 200:
                    return {
                        "error": True,
                        "message": f"Failed to create conversation: {response.status_code} {response.reason_phrase}"
                    }
                
                conversation_data = response.json()
                
                return {
                    "success": True,
                    "conversation": {
                        "id": conversation_data["id"],
                        "title": conversation_data["title"],
                        "folder_id": conversation_data["folder_id"],
                        "url": f"http://localhost:8000/?conversation={conversation_data['id']}"
                    },
                    "message": f"Created conversation '{input_data.title}' with ID {conversation_data['id']}"
                }
                
        except Exception as e:
            logger.error(f"Error creating conversation: {e}")
            return {
                "error": True,
                "message": f"Error creating conversation: {str(e)}"
            }


class ListFoldersAndConversationsTool(BaseMCPTool):
    """Tool for listing existing folders and conversations."""
    
    name = "list_folders_and_conversations"
    description = """List all existing conversation folders and conversations to understand the current organization.
    
    This tool helps you see the current folder structure and conversations to avoid
    duplicates and understand the existing organization before creating new ones.
    
    Use this when:
    - Planning how to organize new tasks within existing structure  
    - Checking if a folder or conversation for a topic already exists
    - Understanding the current project organization
    """
    
    class InputSchema(BaseModel):
        include_messages: bool = Field(False, description="Whether to include message counts for conversations")
        folder_filter: Optional[str] = Field(None, description="Filter folders by name pattern")
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            # Validate inputs
            input_data = self.InputSchema.model_validate(kwargs)
            
            # Get the user's codebase directory
            user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
            folders_file = os.path.join(user_codebase_dir, ".ziya", "folders.json")
            conversations_file = os.path.join(user_codebase_dir, ".ziya", "conversations.json")
            
            # Load folders
            folders = []
            if os.path.exists(folders_file):
                try:
                    with open(folders_file, 'r', encoding='utf-8') as f:
                        folders = json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    folders = []
            
            # Load conversations
            conversations = []
            if os.path.exists(conversations_file):
                try:
                    with open(conversations_file, 'r', encoding='utf-8') as f:
                        conversations = json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    conversations = []
            
            # Filter active conversations
            active_conversations = [c for c in conversations if c.get("isActive", True)]
            
            # Apply folder filter if provided
            if input_data.folder_filter:
                folders = [f for f in folders if input_data.folder_filter.lower() in f.get("name", "").lower()]
            
            # Group conversations by folder
            conversations_by_folder = {}
            for conv in active_conversations:
                folder_id = conv.get("folderId")
                if folder_id not in conversations_by_folder:
                    conversations_by_folder[folder_id] = []
                conversations_by_folder[folder_id].append(conv)
            
            # Build folder hierarchy
            folder_hierarchy = []
            
            def build_folder_tree(parent_id=None, indent=0):
                result = []
                for folder in folders:
                    if folder.get("parentId") == parent_id:
                        folder_convs = conversations_by_folder.get(folder["id"], [])
                        folder_info = {
                            "id": folder["id"],
                            "name": folder["name"],
                            "indent": "  " * indent,
                            "conversation_count": len(folder_convs),
                            "system_instructions": folder.get("systemInstructions"),
                            "conversations": []
                        }
                        
                        # Add conversations in this folder
                        for conv in folder_convs:
                            conv_info = {
                                "id": conv["id"],
                                "title": conv["title"],
                                "message_count": len(conv.get("messages", [])) if input_data.include_messages else None,
                                "last_accessed": conv.get("lastAccessedAt"),
                                "context_files": conv.get("contextFiles", [])
                            }
                            folder_info["conversations"].append(conv_info)
                        
                        result.append(folder_info)
                        
                        # Add subfolders recursively
                        subfolders = build_folder_tree(folder["id"], indent + 1)
                        result.extend(subfolders)
                        
                return result
            
            folder_hierarchy = build_folder_tree()
            
            # Add root-level conversations (not in any folder)
            root_conversations = conversations_by_folder.get(None, [])
            
            return {
                "success": True,
                "folders": folder_hierarchy,
                "root_conversations": [
                    {
                        "id": conv["id"],
                        "title": conv["title"],
                        "message_count": len(conv.get("messages", [])) if input_data.include_messages else None,
                        "last_accessed": conv.get("lastAccessedAt"),
                        "context_files": conv.get("contextFiles", [])
                    }
                    for conv in root_conversations
                ],
                "summary": {
                    "total_folders": len(folders),
                    "total_conversations": len(active_conversations),
                    "conversations_in_folders": len(active_conversations) - len(root_conversations),
                    "root_level_conversations": len(root_conversations)
                }
            }
            
        except Exception as e:
            logger.error(f"Error listing folders and conversations: {e}")
            return {
                "error": True,
                "message": f"Error listing folders and conversations: {str(e)}"
            }


class MoveConversationTool(BaseMCPTool):
    """Tool for moving conversations between folders."""
    
    name = "move_conversation"
    description = """Move an existing conversation to a different folder for better organization.
    
    This tool allows you to reorganize conversations by moving them between folders
    as tasks evolve or when you want to restructure the organization.
    
    Use this when:
    - Reorganizing conversations as project structure changes
    - Moving completed sub-tasks to an archive folder
    - Grouping related conversations that were created separately
    """
    
    class InputSchema(BaseModel):
        conversation_id: str = Field(..., description="ID of the conversation to move")
        target_folder_id: Optional[str] = Field(None, description="Target folder ID (null to move to root level)")
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            # Validate inputs
            input_data = self.InputSchema.model_validate(kwargs)
            
            # Make request to the API endpoint
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "http://localhost:8000/api/conversations/conversations/move",
                    json={
                        "conversation_id": input_data.conversation_id,
                        "target_folder_id": input_data.target_folder_id
                    },
                    timeout=30.0
                )
                
                if response.status_code != 200:
                    return {
                        "error": True,
                        "message": f"Failed to move conversation: {response.status_code} {response.reason_phrase}"
                    }
                
                result = response.json()
                
                return {
                    "success": True,
                    "message": f"Successfully moved conversation to {'root level' if not input_data.target_folder_id else f'folder {input_data.target_folder_id}'}"
                }
                
        except Exception as e:
            logger.error(f"Error moving conversation: {e}")
            return {
                "error": True,
                "message": f"Error moving conversation: {str(e)}"
            }


# Export the tools for registration
__all__ = ["CreateFolderTool", "CreateConversationTool", "ListFoldersAndConversationsTool", "MoveConversationTool"]
