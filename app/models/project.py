"""
Project data models.
"""
from pydantic import BaseModel
from typing import List, Optional

class WritePolicy(BaseModel):
    """Per-project write policy overrides."""
    safe_write_paths: List[str] = []
    allowed_write_patterns: List[str] = []
    allowed_interpreters: List[str] = []
    always_blocked: List[str] = []

class ProjectSettings(BaseModel):
    defaultContextIds: List[str] = []
    defaultSkillIds: List[str] = []
    writePolicy: Optional[WritePolicy] = None

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
