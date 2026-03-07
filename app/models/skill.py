"""
Skill data models.
"""
from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class ModelOverrides(BaseModel):
    """Optional model parameter overrides applied when a skill is active."""
    model_config = {"extra": "allow"}

    temperature: Optional[float] = None
    maxOutputTokens: Optional[int] = None
    thinkingMode: Optional[bool] = None
    model: Optional[str] = None


class Skill(BaseModel):
    id: str
    name: str
    description: str
    prompt: str
    color: str
    tokenCount: int
    isBuiltIn: bool = False
    createdAt: int
    lastUsedAt: int
    # Enhanced skill dimensions (all optional for backward compat)
    toolIds: Optional[List[str]] = None
    files: Optional[List[str]] = None
    contextIds: Optional[List[str]] = None
    modelOverrides: Optional[ModelOverrides] = None
    # Discovery metadata
    source: Optional[str] = None  # 'builtin', 'custom', 'project', 'user'
    allowImplicitInvocation: bool = True
    # agentskills.io spec fields
    keywords: Optional[List[str]] = None
    license: Optional[str] = None
    compatibility: Optional[str] = None
    skillMetadata: Optional[Dict[str, str]] = None
    allowedTools: Optional[List[str]] = None
    skillPath: Optional[str] = None  # Filesystem path for project-discovered skills
    hasScripts: bool = False
    hasReferences: bool = False
    hasAssets: bool = False


class SkillCreate(BaseModel):
    name: str
    description: str
    prompt: str

    toolIds: Optional[List[str]] = None
    files: Optional[List[str]] = None
    contextIds: Optional[List[str]] = None
    modelOverrides: Optional[ModelOverrides] = None
    allowImplicitInvocation: Optional[bool] = None


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    prompt: Optional[str] = None

    toolIds: Optional[List[str]] = None
    files: Optional[List[str]] = None
    contextIds: Optional[List[str]] = None
    modelOverrides: Optional[ModelOverrides] = None
    allowImplicitInvocation: Optional[bool] = None
