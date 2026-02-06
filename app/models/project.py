"""
Project data models.
"""
from pydantic import BaseModel
from typing import List, Optional

class ProjectSettings(BaseModel):
    defaultContextIds: List[str] = []
    defaultSkillIds: List[str] = []

class Project(BaseModel):
    id: str
    name: str
    path: str
    createdAt: int
    lastAccessedAt: int
    settings: ProjectSettings

class ProjectCreate(BaseModel):
    path: str
    name: Optional[str] = None

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    settings: Optional[ProjectSettings] = None

class ProjectListItem(BaseModel):
    """Project with additional computed fields for list view."""
    id: str
    name: str
    path: str
    lastAccessedAt: int
    isCurrentWorkingDirectory: bool = False
