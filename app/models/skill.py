"""
Skill data models.
"""
from pydantic import BaseModel
from typing import Optional

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

class SkillCreate(BaseModel):
    name: str
    description: str
    prompt: str

class SkillUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    prompt: Optional[str] = None
