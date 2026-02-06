"""
Chat data models.
"""
from pydantic import BaseModel
from typing import List, Optional, Any

class Message(BaseModel):
    id: str
    role: str  # 'human' | 'assistant' | 'system'
    content: str
    timestamp: int
    images: Optional[List[Any]] = None
    muted: Optional[bool] = False

class Chat(BaseModel):
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

class ChatCreate(BaseModel):
    groupId: Optional[str] = None
    contextIds: Optional[List[str]] = None
    skillIds: Optional[List[str]] = None
    additionalFiles: Optional[List[str]] = None
    additionalPrompt: Optional[str] = None
    title: Optional[str] = None

class ChatUpdate(BaseModel):
    title: Optional[str] = None
    groupId: Optional[str] = None
    contextIds: Optional[List[str]] = None
    skillIds: Optional[List[str]] = None
    additionalFiles: Optional[List[str]] = None
    additionalPrompt: Optional[str] = None

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
