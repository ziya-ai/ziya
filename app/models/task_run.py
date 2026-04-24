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


class TaskRunBlockState(BaseModel):
    """Per-block runtime state.  For Slice C we track only the root;
    Slice D's loop engine will populate this per iteration / per block.
    """
    model_config = {"extra": "allow"}

    block_id: str
    block_type: str
    status: RunStatus = "queued"
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    artifact: Optional[Artifact] = None
    error: Optional[str] = None


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

    # Top-level artifact produced by the root block
    artifact: Optional[Artifact] = None

    # Per-block state — keyed by block.id
    block_states: Dict[str, TaskRunBlockState] = Field(default_factory=dict)

    # Aggregate metrics
    total_tokens: int = 0
    total_tool_calls: int = 0

    created_at: int = 0
    updated_at: int = 0


class TaskRunCreate(BaseModel):
    """Internal — constructed by the launch endpoint, not user-facing."""
    card_id: str
    source_conversation_id: Optional[str] = None
