"""
Context data models.
"""
from pydantic import BaseModel
from typing import List, Optional

class Context(BaseModel):
    id: str
    name: str
    files: List[str]
    color: str
    tokenCount: int
    tokenCountUpdatedAt: int
    createdAt: int
    lastUsedAt: int

class ContextCreate(BaseModel):
    name: str
    files: List[str]

class ContextUpdate(BaseModel):
    name: Optional[str] = None
    files: Optional[List[str]] = None
