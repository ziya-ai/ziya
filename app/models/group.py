"""
Chat group data models.
"""
from pydantic import BaseModel
from typing import List, Optional
from typing import Any, Dict

class ChatGroup(BaseModel):
    model_config = {"extra": "allow"}

    id: str
    name: str
    # Nesting.  The frontend has always written this as an extra field
    # (folderSyncApi.ts); declared so ChatGroupStorage.create/update and
    # the conversation-organisation tools can set it first-class.
    parentId: Optional[str] = None
    # Server-side "pin to top".  Set by the conversation_organize tools;
    # the sidebar's own pins are browser-local (localStorage) and this
    # flag is only honoured once the frontend reads it.
    pinned: Optional[bool] = None
    defaultContextIds: List[str] = []
    defaultSkillIds: List[str] = []
    collapsed: bool = False
    order: int = 0
    createdAt: int
    updatedAt: Optional[int] = None
    # Task description for folder-sticky context (was deferred; now used by TaskPlans)
    systemInstructions: Optional[str] = None
    # Saved per-folder model pin (alias string, e.g. "opus4.5").
    # Conversations in this folder with no conversation-level pin inherit
    # it.  None = inherit project → server default.
    modelPreference: Optional[str] = None
    # TaskPlan fields — None for regular folders. See design/newux-context.md.
    taskPlan: Optional[Dict[str, Any]] = None

class ChatGroupCreate(BaseModel):
    model_config = {"extra": "allow"}
    name: str
    parentId: Optional[str] = None
    defaultContextIds: Optional[List[str]] = None
    defaultSkillIds: Optional[List[str]] = None

class ChatGroupUpdate(BaseModel):
    name: Optional[str] = None
    defaultContextIds: Optional[List[str]] = None
    defaultSkillIds: Optional[List[str]] = None
    collapsed: Optional[bool] = None
    order: Optional[int] = None
    # Explicit None (set, not unset) moves the folder to the top level —
    # ChatGroupStorage.update applies exclude_unset fields only.
    parentId: Optional[str] = None
    pinned: Optional[bool] = None
    # Saved per-folder model pin (see ChatGroup.modelPreference).
    modelPreference: Optional[str] = None

class ChatGroupsFile(BaseModel):
    """The _groups.json file structure."""
    version: int = 1
    groups: List[ChatGroup] = []
