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

class ContextManagementSettings(BaseModel):
    """Per-project automatic context management settings."""
    auto_add_diff_files: bool = True

class ProjectSettings(BaseModel):
    defaultContextIds: List[str] = []
    defaultSkillIds: List[str] = []
    writePolicy: Optional[WritePolicy] = None
    contextManagement: Optional[ContextManagementSettings] = None

class Project(BaseModel):
    id: str
    name: str
    path: str
    createdAt: int
    lastAccessedAt: int
    settings: ProjectSettings

class ProjectCreate(BaseModel):
    path: Optional[str] = None
    name: Optional[str] = None

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    path: Optional[str] = None
    settings: Optional[ProjectSettings] = None

class ProjectListItem(BaseModel):
    """Project with additional computed fields for list view."""
    id: str
    name: str
    path: str
    lastAccessedAt: int
    isCurrentWorkingDirectory: bool = False
    conversationCount: int = 0