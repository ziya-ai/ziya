"""
Chat group data models.
"""
from pydantic import BaseModel
from typing import List, Optional

class ChatGroup(BaseModel):
    id: str
    name: str
    defaultContextIds: List[str] = []
    defaultSkillIds: List[str] = []
    collapsed: bool = False
    order: int = 0
    createdAt: int

class ChatGroupCreate(BaseModel):
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
