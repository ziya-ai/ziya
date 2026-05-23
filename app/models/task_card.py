"""
Task Card data models — see design/task-cards.md for the conceptual
framing.  A Task Card is a saveable, re-runnable tree of blocks.

Block grammar:
  - Task block (atomic action): instructions + scope
  - Repeat block (loop decorator): wraps a body, runs it N times
  - Parallel block: runs its body concurrently
  (Implicit sequence: stacking blocks in a body runs them in order)

The one invariant: a task's conversation never leaves its task.
Only instructions flow down and artifacts flow up.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Dict, Any


# ── Scope (what a task is allowed to touch) ────────────────

class TaskScope(BaseModel):
    """A single task's allowed files, tools, and skills.

    Scope does not cascade.  Each task block sets its own scope
    independently; children do not inherit from the parent.
    """
    model_config = {"extra": "allow"}

    files: List[str] = []
    tools: List[str] = []
    skills: List[str] = []


# ── Artifact (what flows back from a finished task) ────────

class ArtifactPart(BaseModel):
    """One typed piece of artifact content."""
    model_config = {"extra": "allow"}

    part_type: Literal["text", "file", "data"] = "text"
    text: Optional[str] = None
    file_uri: Optional[str] = None
    media_type: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class Artifact(BaseModel):
    """Durable output of a completed task block."""
    model_config = {"extra": "allow"}

    summary: str = ""
    decisions: List[str] = []
    outputs: List[ArtifactPart] = []
    tokens: int = 0
    tool_calls: int = 0
    duration_ms: int = 0
    created_at: float = 0.0
    # Optional error-identity hash — populated only on failure,
    # enables clustering similar failures by signature.  Null on
    # success.  See design/task-cards.md §Runtime semantics.
    signature: Optional[str] = None
    # Whether this artifact represents a failed execution.  The
    # executor sets this when the block exited via an error path.
    failed: bool = False


# ── The recursive Block type ──────────────────────────────

class Block(BaseModel):
    """A single node in a task card's block tree.

    The discriminator is block_type.  Fields relevant to other
    block types are left None; rendering and execution code
    branch on block_type.

    Recursion is supported via the forward-ref body field.
    """
    model_config = {"extra": "allow"}

    # Block taxonomy:
    #   task     — atomic model invocation (the leaf)
    #   repeat   — count / until-substring / for_each loop
    #   parallel — concurrent execution of distinct children
    #   until    — loop until a model-evaluated yes/no condition holds
    #              (separate from repeat's substring-based until — this
    #              one runs an evaluator sub-call on each iteration)
    #   schedule — recurring trigger decorator (interval / at /
    #              daily_at / cron).  Does NOT execute its body on its
    #              own; the in-process scheduler dispatches each fire as
    #              an independent TaskRun rooted at the body.
    block_type: Literal["task", "repeat", "parallel", "until", "schedule"]
    id: str = ""
    name: str = ""

    # Task-only fields
    instructions: Optional[str] = None
    scope: Optional[TaskScope] = None
    emoji: Optional[str] = None

    # Repeat-only fields
    repeat_mode: Optional[Literal["count", "until", "for_each"]] = None
    repeat_count: Optional[int] = None
    repeat_max: Optional[int] = None
    repeat_parallel: bool = False
    repeat_propagate: Literal["none", "last", "all"] = "last"
    repeat_until: Optional[str] = None
    repeat_for_each_source: Optional[str] = None
    repeat_item_template: Optional[str] = None

    # Until-only fields.  A separate block from Repeat-with-until
    # because the evaluation surface is different: Repeat's
    # `repeat_until` is a substring match against artifact.summary;
    # Until uses a small LLM call (mode="model") or an expression
    # evaluator (mode="expression", not yet implemented — UI greys
    # this option out so the shape is reserved).
    until_mode: Optional[Literal["model", "expression"]] = None
    until_condition: Optional[str] = None
    until_max: Optional[int] = None

    # Schedule-only fields.  See app/agents/task_scheduler.py.
    schedule_mode: Optional[Literal["interval", "at", "daily_at", "cron"]] = None
    schedule_interval_value: Optional[int] = None
    schedule_interval_unit: Optional[Literal["minutes", "hours", "days"]] = None
    schedule_at_iso: Optional[str] = None         # one-shot ISO-8601
    schedule_daily_at: Optional[str] = None       # "HH:MM" local
    schedule_cron: Optional[str] = None           # 5-field cron expr
    schedule_timezone: Optional[str] = None       # default: local
    schedule_enabled: bool = True
    schedule_catch_up: bool = True                # run-once-on-recovery
    schedule_max_runs: Optional[int] = None       # None = unlimited

    # Body — used by repeat / parallel / until / schedule (Task ignores)
    body: List["Block"] = []


# Rebuild for forward ref
Block.model_rebuild()


# ── Task Card (top-level saveable unit) ────────────────────

class TaskCard(BaseModel):
    """A saveable, re-runnable task card.  The root is a Block."""
    model_config = {"extra": "allow"}

    id: str = ""
    name: str = ""
    description: str = ""
    root: Block
    tags: List[str] = []
    is_template: bool = False
    source: str = "custom"  # custom | builtin | project
    created_at: int = 0
    updated_at: int = 0
    last_run_at: Optional[int] = None
    run_count: int = 0


# ── CRUD models ───────────────────────────────────────────

class TaskCardCreate(BaseModel):
    """Request body for creating a task card."""
    name: str
    description: str = ""
    root: Block
    tags: List[str] = []
    is_template: bool = False


class TaskCardUpdate(BaseModel):
    """Request body for updating a task card (partial)."""
    name: Optional[str] = None
    description: Optional[str] = None
    root: Optional[Block] = None
    tags: Optional[List[str]] = None
    is_template: Optional[bool] = None


class TaskCardRun(BaseModel):
    """Request body for launching a task card execution."""
    source_conversation_id: Optional[str] = None
    parameter_overrides: Dict[str, Any] = Field(default_factory=dict)
