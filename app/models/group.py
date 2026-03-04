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
    defaultContextIds: List[str] = []
    defaultSkillIds: List[str] = []
    collapsed: bool = False
    order: int = 0
    createdAt: int
    updatedAt: Optional[int] = None
    # Task description for folder-sticky context (was deferred; now used by TaskPlans)
    systemInstructions: Optional[str] = None
    # TaskPlan fields — None for regular folders. See design/newux-context.md.
    taskPlan: Optional[Dict[str, Any]] = None

class ChatGroupCreate(BaseModel):
    model_config = {"extra": "allow"}
    name: str
    defaultContextIds: Optional[List[str]] = None
    defaultSkillIds: Optional[List[str]] = None

class ChatGroupUpdate(BaseModel):
    name: Optional[str] = None
    defaultContextIds: Optional[List[str]] = None
    defaultSkillIds: Optional[List[str]] = None
    collapsed: Optional[bool] = None
    order: Optional[int] = None

class ChatGroupsFile(BaseModel):
    """The _groups.json file structure."""
    version: int = 1
    groups: List[ChatGroup] = []
