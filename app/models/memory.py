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
    # Reference layer — addresses of authoritative corpora (wikis, PDFs,
    # local docs, internal pages).  Holds the address and provenance of
    # a source, not its content.  See design/structured-memory-system.md
    # for the rationale.
    "reference",
]

# Lifecycle: probationary entries live in proposals.jsonl (append-only)
# and are promoted to memories.json once corroborated, retrieved-and-used,
# or explicitly saved.  See design/structured-memory-system.md §lifecycle.
MEMORY_STATUSES = ["active", "probationary", "stale", "contested", "archived",
                   "pending", "deprecated"]

LEARNED_FROM_SOURCES = [
    "user_explanation",
    "user_correction",
    "design_discussion",
    "design_failure",
    "observation",
    "auto_extraction",
    "promoted_from_proposal",
    "user_directional_phrase",
    "explicit_save",
]


class MemoryScope(BaseModel):
    """Where a memory is most relevant."""
    domain_node: Optional[str] = None
    project_paths: List[str] = Field(default_factory=list)


class MemoryReference(BaseModel):
    """Address and provenance of a reference corpus.

    Populated only on memories with layer="reference".  Holds enough
    information for the model (or a maintenance pass) to refetch the
    source — never the source's contents.
    """
    model_config = {"extra": "allow"}

    type: str = Field(
        default="url",
        description="One of: wiki, local_file, url, internal_doc, "
                    "confluence, sim, pdf",
    )
    uri: str = ""
    title: Optional[str] = None
    consulted_for: Optional[str] = Field(
        default=None,
        description="Why this reference was added — what topic the user "
                    "pointed the model at.",
    )
    last_verified: Optional[str] = Field(
        default=None,
        description="ISO date of the last successful access check.",
    )


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
    importance: float = Field(
        default=0.5,
        description="0.0-1.0 importance score; rises with use, falls with neglect",
    )
    relations: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Typed relations: supports, contradicts, elaborates, "
                    "depends_on, discovered_from, supersedes → [memory_ids]",
    )
    # ── Lifecycle telemetry ────────────────────────────────────────
    # Populated by the retrieval-feedback signal (Diff 6) and the
    # activity-counter promotion engine (Diff 7).
    last_retrieved_at: Optional[int] = Field(
        default=None,
        description="Unix ms of the last time this memory was loaded into context.",
    )
    retrieval_loaded_count: int = Field(
        default=0,
        description="Total number of times this memory was loaded into context.",
    )
    retrieval_used_count: int = Field(
        default=0,
        description="Number of loads where the response embedding matched "
                    "the memory embedding (used signal).",
    )
    corroborations: int = Field(
        default=0,
        description="Count of independent extractions producing a "
                    "near-duplicate of this memory.",
    )
    corroborated_by: List[str] = Field(
        default_factory=list,
        description="Conversation IDs (capped to last 5) that corroborated "
                    "this memory.  Preserved across proposal-to-memory "
                    "promotion so lineage isn't lost.",
    )
    learned_from_conversation: Optional[str] = None
    learned_from_message: Optional[str] = None
    # ── Reference layer (only set when layer == "reference") ───────
    reference: Optional[MemoryReference] = Field(
        default=None,
        description="Address and provenance, populated for layer='reference'.",
    )


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
    # ── Probationary fields (used by ProposalsStore in Diff 2) ─────
    activity_count_at_proposal: int = Field(
        default=0,
        description="Value of the global activity counter when this proposal "
                    "was created.  Used to age out unpromoted proposals.",
    )
    corroborations: int = Field(default=0)
    scope: MemoryScope = Field(default_factory=MemoryScope)
    learned_from_message: Optional[str] = None
    reference: Optional[MemoryReference] = Field(default=None)
    # Hash-stable ID for parallel-write safety (set by ProposalsStore).
    content_hash: Optional[str] = None


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
