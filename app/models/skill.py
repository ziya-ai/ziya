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
