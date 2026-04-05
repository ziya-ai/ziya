"""
Memory data models for the structured memory system (Phase 0).

Flat-store memories with layered classification, dual-purpose tags
(search + future cell-division), and scope metadata for project-hint
weighting.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import time
import uuid


MEMORY_LAYERS = [
    "domain_context",
    "architecture",
    "lexicon",
    "decision",
    "active_thread",
    "process",
    "preference",
    "negative_constraint",
]

MEMORY_STATUSES = ["active", "pending", "deprecated", "archived"]

LEARNED_FROM_SOURCES = [
    "user_explanation",
    "user_correction",
    "design_discussion",
    "design_failure",
    "observation",
    "explicit_save",
]


class MemoryScope(BaseModel):
    """Where a memory is most relevant."""
    domain_node: Optional[str] = None
    project_paths: List[str] = Field(default_factory=list)


class Memory(BaseModel):
    """A single memory entry in the flat store."""
    model_config = {"extra": "allow"}

    id: str = Field(default_factory=lambda: f"m_{uuid.uuid4().hex[:8]}")
    content: str
    layer: str = "domain_context"
    tags: List[str] = Field(default_factory=list)
    learned_from: str = "observation"
    created: str = Field(default_factory=lambda: time.strftime("%Y-%m-%d"))
    last_accessed: str = Field(default_factory=lambda: time.strftime("%Y-%m-%d"))
    status: str = "active"
    scope: MemoryScope = Field(default_factory=MemoryScope)
    related: List[str] = Field(default_factory=list)


class MemoryProposal(BaseModel):
    """A memory proposed by the agent, awaiting user approval."""
    model_config = {"extra": "allow"}

    id: str = Field(default_factory=lambda: f"prop_{uuid.uuid4().hex[:8]}")
    content: str
    layer: str = "domain_context"
    tags: List[str] = Field(default_factory=list)
    learned_from: str = "observation"
    proposed_at: int = Field(default_factory=lambda: int(time.time() * 1000))
    conversation_id: Optional[str] = None


class MemoryProfile(BaseModel):
    """User profile — preferences, communication style, meta."""
    model_config = {"extra": "allow"}

    preferred_detail_level: str = "concise"
    communication_style: Optional[str] = None
    expertise_areas: List[str] = Field(default_factory=list)
    custom: Dict[str, Any] = Field(default_factory=dict)


class ProjectHints(BaseModel):
    """Learned associations between a project directory and memory domains."""
    model_config = {"extra": "allow"}

    project_path: str
    learned_associations: Dict[str, List[str]] = Field(
        default_factory=lambda: {"high": [], "medium": [], "low": []}
    )
    last_updated: str = Field(default_factory=lambda: time.strftime("%Y-%m-%d"))


class MindMapNode(BaseModel):
    """A node in the mind-map tree — compact handle over grouped memories."""
    model_config = {"extra": "allow"}

    id: str
    handle: str = Field(
        ...,
        description="Summary compact enough to scan cheaply (~30 tokens).",
    )
    parent: Optional[str] = None
    children: List[str] = Field(default_factory=list)
    cross_links: List[str] = Field(default_factory=list)
    memory_refs: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    access_count: int = 0
    last_accessed: str = Field(default_factory=lambda: time.strftime("%Y-%m-%d"))
