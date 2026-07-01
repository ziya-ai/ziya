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

class ScopeEntry(BaseModel):
    """A single path-permission entry on a Task scope.

    Each entry names a file or directory (relative to the task's
    effective working directory) and three independent permission
    flags:
      - read:    the model may read this path via tools (advisory
                 today; enforced in a later slice).
      - write:   the model may write this path via tools (enforced
                 in a later slice — currently advisory).
      - context: file contents are preloaded into the system prompt.
                 Only meaningful when ``is_dir`` is False.  Directory
                 entries with ``context=True`` are ignored by the
                 preloader (use read for tool-mediated traversal).
    """
    model_config = {"extra": "allow"}

    path: str
    is_dir: bool = False
    read: bool = True
    write: bool = False
    context: bool = False


class TaskScope(BaseModel):
    """A single task's allowed files, tools, and skills.

    Scope does not cascade.  Each task block sets its own scope
    independently; children do not inherit from the parent.
    """
    model_config = {"extra": "allow"}

    paths: List[ScopeEntry] = []
    cwd: Optional[str] = None
    tools: List[str] = []
    skills: List[str] = []
    # Per-task shell command grants.  Each entry is either a literal
    # first-token match (e.g. "pytest" grants any pytest invocation)
    # or, with a "re:" prefix, a regex against the full command line
    # (e.g. "re:^make\\s+test(:\\w+)?$").  The grant is additive over
    # the base shell policy: it bypasses the global allowlist and the
    # destructive-command list, but never overrides ``always_blocked``
    # (sudo, vi, etc.) or redirection blocking.  Empty list preserves
    # pre-Slice-B behavior — no extra commands granted.
    #
    # Slice B: extends the same ``_task_scope`` wire envelope already
    # used for writable/readable path grants.  Plumbing parallels
    # ``paths``: scope set on the ContextVar by ``task_executor``,
    # injected into tool args by ``tool_execution``, consumed by
    # ``shell_server`` / ``ShellWriteChecker``.
    shell_commands: List[str] = []


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
    # Structured self-assessment emitted by the agent at the end of
    # its response.  Shape: {"objective_met": "true"|"false"|
    # "partial"|"unknown", "rationale": "..."}.  Populated by the
    # executor after parsing the final ``<self_assessment .../>``
    # tag the agent is instructed to emit.  None when the agent
    # omitted the tag entirely — distinct from "unknown" which means
    # a tag was present but the verdict value wasn't recognised.
    # See ``app/utils/completion_check.py``.
    self_assessment: Optional[Dict[str, str]] = None


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
    #   group    — neutral run-once sequential container.  Runs its body
    #              top-to-bottom exactly once (the explicit form of the
    #              implicit-sequence rule).  Carries no loop/trigger
    #              semantics; used as the invisible card-root wrapper so
    #              a State can precede a loop without entering its scope.
    #   schedule — recurring trigger decorator (interval / at /
    #              daily_at / cron).  Does NOT execute its body on its
    #              own; the in-process scheduler dispatches each fire as
    #              an independent TaskRun rooted at the body.
    #   state    — read-only declaration of run-scoped named variables
    #              (name -> literal).  A leaf like task.  Placement is
    #              the reset policy: in a once-running body it sets once;
    #              inside a Repeat/Until body it re-applies each cycle.
    block_type: Literal["task", "repeat", "parallel", "until", "schedule", "state", "group"]
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

    # State-only fields.  A read-only map of run-scoped named variables
    # (name -> literal value).  Tasks read them via {{var.NAME}}
    # templating; nothing writes back (read-only preserves the sandbox
    # invariant — only artifacts cross task boundaries).  Placement is
    # the reset policy: a State block in a once-running body applies once
    # per run; the same block inside a Repeat/Until body re-applies its
    # literals at the start of every iteration, resetting to baseline.
    # See app/agents/block_executor.py::_execute_state.
    state_variables: Optional[Dict[str, Any]] = None

    # State prose context — the PRIMARY, conversational form of a State
    # block.  Freeform English givens ("assume prod, migration already
    # ran, flag is off") that flow into every in-scope task's context
    # automatically — no {{var}} templating required.  Surfaced as a
    # standing-context preamble, mirroring how prior-iteration results
    # are surfaced.  ``state_variables`` is the optional formal adjunct
    # for values you want to reference by name; this prose field is the
    # baseline most cards use.  Same placement-is-reset-policy as vars.
    state_context: Optional[str] = None

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
