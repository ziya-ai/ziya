"""
TaskRun — runtime state of a launched task card.

A TaskRun is created when a user launches a TaskCard.  It persists
the run's status, the artifact produced when the root block finishes,
and metrics for observability.

This is distinct from TaskCard (the saved definition).  Many runs can
come from one card.
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal, List, Dict, Any

from .task_card import Artifact


RunStatus = Literal[
    "queued",      # created, not yet started
    "running",     # currently executing
    "done",        # finished successfully with an artifact
    "failed",      # crashed or errored
    "cancelled",   # stopped by user
]


IterationStatus = Literal["passed", "failed", "cancelled"]


class IterationSummary(BaseModel):
    """Lightweight per-iteration record — ~100 bytes, always retained
    for every iteration of a Repeat block regardless of scale.  The
    full Artifact lives in a separate per-iteration file on disk and
    is loaded on demand.  See design/task-cards.md §Iteration result
    storage at scale.
    """
    model_config = {"extra": "allow"}

    index: int
    status: IterationStatus
    signature: Optional[str] = None
    duration_ms: int = 0
    tokens: int = 0
    # True if the full Artifact was persisted alongside this summary.
    # False when the iteration was a passing run beyond the retention
    # cap (50 passes per Repeat block).
    has_artifact: bool = True


class TaskRunBlockState(BaseModel):
    """Per-block runtime state."""
    model_config = {"extra": "allow"}

    block_id: str
    block_type: str
    status: RunStatus = "queued"
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    artifact: Optional[Artifact] = None
    error: Optional[str] = None
    # For Repeat blocks: one summary per iteration.  Empty for Task
    # and Parallel blocks.
    iteration_summaries: List[IterationSummary] = Field(default_factory=list)


class TaskRun(BaseModel):
    """One execution of a TaskCard's block tree."""
    model_config = {"extra": "allow"}

    id: str = ""
    card_id: str
    source_conversation_id: Optional[str] = None
    status: RunStatus = "queued"
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    # Soft-cancel flag.  Block executor checks at iteration and
    # sibling boundaries.  See design/task-cards.md §Cancellation.
    cancel_requested: bool = False

    # Top-level artifact produced by the root block
    artifact: Optional[Artifact] = None

    # Per-block state — keyed by block.id
    block_states: Dict[str, TaskRunBlockState] = Field(default_factory=dict)

    # Aggregate metrics
    total_tokens: int = 0
    total_tool_calls: int = 0

    # Snapshot of effective permissions (write policy + per-block task
    # scopes + project root) captured at launch.  Stored as a dict so
    # the schema can evolve without migrations; see
    # ``app/utils/permissions_snapshot.py`` for the active shape.
    # Populated by ``_launch_run_for_card`` immediately after create.
    permissions_snapshot: Optional[Dict[str, Any]] = None

    created_at: int = 0
    updated_at: int = 0


class TaskRunCreate(BaseModel):
    """Internal — constructed by the launch endpoint, not user-facing."""
    card_id: str
    source_conversation_id: Optional[str] = None
