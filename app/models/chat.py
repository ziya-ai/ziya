"""
Chat data models.
"""
from pydantic import BaseModel
from typing import List, Optional, Any, Dict

class Message(BaseModel):
    model_config = {"extra": "allow"}
    
    id: str
    role: str  # 'human' | 'assistant' | 'system'
    content: str
    timestamp: int
    images: Optional[List[Any]] = None
    muted: Optional[bool] = False

class Chat(BaseModel):
    model_config = {"extra": "allow"}
    
    id: str
    title: str
    groupId: Optional[str] = None
    contextIds: List[str] = []
    skillIds: List[str] = []
    additionalFiles: List[str] = []
    additionalPrompt: Optional[str] = None
    messages: List[Message] = []
    createdAt: int
    lastActiveAt: int
    # Frontend-specific fields that we preserve for round-tripping
    projectId: Optional[str] = None
    isActive: Optional[bool] = True
    folderId: Optional[str] = None
    hasUnreadResponse: Optional[bool] = False
    displayMode: Optional[str] = None
    lastAccessedAt: Optional[int] = None

class ChatCreate(BaseModel):
    model_config = {"extra": "allow"}
    groupId: Optional[str] = None
    contextIds: Optional[List[str]] = None
    skillIds: Optional[List[str]] = None
    additionalFiles: Optional[List[str]] = None
    additionalPrompt: Optional[str] = None
    title: Optional[str] = None

class ChatUpdate(BaseModel):
    model_config = {"extra": "allow"}
    
    title: Optional[str] = None
    groupId: Optional[str] = None
    contextIds: Optional[List[str]] = None
    skillIds: Optional[List[str]] = None
    additionalFiles: Optional[List[str]] = None
    additionalPrompt: Optional[str] = None
    messages: Optional[List[Message]] = None

class ChatSummary(BaseModel):
    """Chat without messages, for list views."""
    id: str
    title: str
    groupId: Optional[str]
    contextIds: List[str]
    skillIds: List[str]
    additionalFiles: List[str]
    messageCount: int
    createdAt: int
    lastActiveAt: int


class ChatBulkSync(BaseModel):
    """Request body for bulk sync endpoint."""
    chats: List[Chat]
