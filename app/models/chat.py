"""
Chat data models.
"""
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from .group import ChatGroup
from .delegate import DelegateMeta

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
    # Delegate fields — None for regular conversations.
    # See design/newux-context.md for DelegateMeta schema.
    delegateMeta: Optional[DelegateMeta] = None
    # Branch lineage — None for trunk/unbranched conversations.  Authored
    # at fork time when splitting from a bead (a parked bead is an un-taken
    # branch point recorded with its message_index seam).  Declared
    # explicitly — rather than relying on extra="allow" — so the round-trip
    # is first-class and documented, matching the projectId/folderId/
    # delegateMeta convention above.  See design/bead-branching.md.
    branchedFrom: Optional[str] = None
    branchedAtMessageIndex: Optional[int] = None
    branchedFromLabel: Optional[str] = None
    # Fork-lineage root for shared bead trees (design/bead-branching.md "b2").
    # A plain fork ("continue this work in a fresh space") stamps this with
    # its lineage's ROOT id; beads live on the root record and every
    # conversation in the lineage resolves to that one shared, state-synced
    # tree.  None on a root/trunk conversation (it is its own root).
    lineageRootId: Optional[str] = None

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
    model_config = {"extra": "allow"}
    id: str
    title: str
    groupId: Optional[str]
    contextIds: List[str]
    skillIds: List[str]
    additionalFiles: List[str]
    messageCount: int
    createdAt: int
    lastActiveAt: int
    delegateMeta: Optional[DelegateMeta] = None
    # Cheap derived "open work" counts for the sidebar indicators.  Always
    # present (default 0); recomputed from the chat record's _beads /
    # _work_items on each summary build.  openWorkItemCount is a correct
    # shell — 0 until the work-item queue exists.
    openBeadCount: int = 0
    openWorkItemCount: int = 0
    _version: Optional[int] = None


class ChatBulkSync(BaseModel):
    """Request body for bulk sync endpoint."""
    chats: List[Chat]


class ChatGroupBulkSync(BaseModel):
    """Request body for bulk group/folder sync endpoint."""
    groups: List[ChatGroup]
