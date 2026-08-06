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

    Scope is hierarchical and ADDITIVE (see ``merge_scopes`` below): a
    deck-level (project-wide) scope, a card-level scope, and every
    ancestor container block's scope (repeat/parallel/until/group/
    schedule) each contribute grants that flow down to every leaf Task
    beneath them.  A more specific layer can only grant more — it
    never revokes what an ancestor granted.  ``cwd`` is the one
    non-additive field: the most specific (innermost) non-null
    ``cwd`` wins, since "working directory" is inherently singular.
    """
    model_config = {"extra": "allow"}

    paths: List[ScopeEntry] = []
    cwd: Optional[str] = None
    tools: List[str] = []
    skills: List[str] = []
    # Model selection for this task and everything beneath it.  Like
    # ``cwd``, this is non-additive — the most specific (innermost)
    # non-null value wins, since a task runs on exactly one model.
    #
    # ``model_tier`` is the RECOMMENDED way to pick a model: a portable
    # rung ("xsmall" | "small" | "base" | "medium" | "large" |
    # "frontier") that resolves to a concrete model per-endpoint via
    # app.config.models_config.resolve_tier_model. This lets a
    # decomposed task run cheap/fast on small tiers with a smarter
    # model supervising, without hardcoding a provider-specific name
    # that breaks when that model is retired or the endpoint changes.
    #
    # ``model_name`` is an escape hatch for users who want a SPECIFIC
    # model (e.g. "sonnet4.6") or an explicit inference-profile ARN via
    # ``model_id_override``. Prefer ``model_tier`` — a literal model
    # name/ID is NOT portable across endpoints or time.
    model_tier: Optional[str] = None
    model_name: Optional[str] = None
    model_id_override: Optional[str] = None
    # Optional endpoint override (bedrock/google/openai/anthropic/zai).
    # Only meaningful alongside model_name/model_id_override — combining
    # model_tier with an endpoint override still resolves portably.
    model_endpoint: Optional[str] = None
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
    # Per-task shell timeout grant, in seconds.  Raises BOTH the
    # ceiling a shell command may request and the default it gets when
    # it requests nothing.
    #
    # Exists because the base ceilings (MAX_COMMAND_TIMEOUT and
    # TOOL_EXEC_TIMEOUT, 300 s each) are shorter than a real frontend
    # production build, so any card whose loop rebuilds a bundle fought
    # the timeout every iteration, retried, and sometimes handed a
    # downstream step a build that had never actually completed.  A
    # card that legitimately needs 20 minutes for one command should be
    # able to say so once, in scope, rather than relying on the model
    # to pass `timeout` correctly on every call.
    #
    # None leaves both the ceiling and the default at their base values.
    shell_timeout_secs: Optional[int] = None


def merge_scopes(*scopes: "Optional[TaskScope]") -> "Optional[TaskScope]":
    """Additively merge scopes from outermost to innermost.

    Callers pass layers in root→leaf order, e.g.
    ``merge_scopes(deck_scope, card_scope, *ancestor_block_scopes, leaf_scope)``.
    ``None`` layers are skipped.  Returns ``None`` only when every layer
    is ``None`` (matches the pre-existing "no scope set -> unrestricted"
    semantics of a bare ``Optional[TaskScope]`` field).

    Union rules:
      - ``paths``: entries are merged by ``path``.  A later layer's
        entry for the same path ORs each boolean flag onto the earlier
        one (so a later "read only" grant cannot silently downgrade an
        earlier "read+write" grant for the same path — union, not
        overwrite).
      - ``tools`` / ``skills`` / ``shell_commands``: set union,
        de-duplicated, order-stable (first-seen order).
      - ``shell_timeout_secs``: MAXIMUM of the non-null values, not
        last-wins.  A timeout is a grant like a path or a command, and
        the additive rule says an inner layer can only add: letting an
        inner 60 s silently undercut an outer 1200 s would revoke a
        grant the outer layer made.
      - ``cwd``: last non-null value wins (most specific).
      - ``model_tier`` / ``model_name`` / ``model_id_override`` /
        ``model_endpoint``: last non-null value wins (most specific),
        same rule as ``cwd`` — a task runs on exactly one model, so a
        leaf's own choice overrides an ancestor's, but an ancestor's
        choice still applies to sibling leaves that set nothing.
    """
    present = [s for s in scopes if s is not None]
    if not present:
        return None
    paths_by_key: Dict[str, ScopeEntry] = {}
    tools: List[str] = []
    skills: List[str] = []
    shell_commands: List[str] = []
    cwd: Optional[str] = None
    shell_timeout_secs: Optional[int] = None
    model_tier: Optional[str] = None
    model_name: Optional[str] = None
    model_id_override: Optional[str] = None
    model_endpoint: Optional[str] = None
    for s in present:
        for entry in s.paths or []:
            key = entry.path
            if key in paths_by_key:
                prev = paths_by_key[key]
                paths_by_key[key] = prev.model_copy(update={
                    "is_dir": prev.is_dir or entry.is_dir,
                    "read": prev.read or entry.read,
                    "write": prev.write or entry.write,
                    "context": prev.context or entry.context,
                })
            else:
                paths_by_key[key] = entry
        for v in (s.tools or []):
            if v not in tools:
                tools.append(v)
        for v in (s.skills or []):
            if v not in skills:
                skills.append(v)
        for v in (s.shell_commands or []):
            if v not in shell_commands:
                shell_commands.append(v)
        _sts = getattr(s, "shell_timeout_secs", None)
        if _sts:
            try:
                _sts_i = int(_sts)
            except (TypeError, ValueError):
                _sts_i = 0
            if _sts_i > (shell_timeout_secs or 0):
                shell_timeout_secs = _sts_i
        if s.cwd:
            cwd = s.cwd
        if getattr(s, "model_tier", None):
            model_tier = s.model_tier
        if getattr(s, "model_name", None):
            model_name = s.model_name
        if getattr(s, "model_id_override", None):
            model_id_override = s.model_id_override
        if getattr(s, "model_endpoint", None):
            model_endpoint = s.model_endpoint
    return TaskScope(
        paths=list(paths_by_key.values()),
        cwd=cwd, tools=tools, skills=skills,
        shell_commands=shell_commands,
        shell_timeout_secs=shell_timeout_secs,
        model_tier=model_tier, model_name=model_name,
        model_id_override=model_id_override, model_endpoint=model_endpoint,
    )


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
    #   call     — invoke a NAMED, separately-defined unit of work: another
    #              task card in the same project, or a named file task from
    #              tasks.yaml.  A leaf from the caller's point of view (its
    #              ``body`` is empty); the callee's tree is resolved and run
    #              inline in the caller's run, and the callee's artifact
    #              becomes this block's artifact.  Permissions do NOT flow
    #              across the boundary in either direction — see
    #              app/agents/block_executor.py::_execute_call.
    block_type: Literal["task", "repeat", "parallel", "until", "schedule",
                        "state", "group", "call"]
    id: str = ""
    name: str = ""

    # Task-only fields
    instructions: Optional[str] = None
    scope: Optional[TaskScope] = None
    emoji: Optional[str] = None

    # Call-only fields.  ``call_target`` names a task card (by id, or by
    # name case-insensitively) or a file task (by name in the merged
    # tasks.yaml set).  ``call_target_kind`` selects which namespace to
    # resolve in; None means "card" (the common case).  Deliberately a
    # NAME rather than an inlined copy: the point of a call is that
    # editing the callee changes every caller, which an inlined subtree
    # could not do.
    call_target_kind: Optional[Literal["card", "file_task"]] = None
    call_target: Optional[str] = None

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

    # Container failure policy — governs the implicit sequence formed by
    # this block's body (group / repeat / until / schedule bodies).
    #   "continue" (default, legacy): a child completing with a failed
    #     artifact does not stop later siblings from running.
    #   "stop": the sequence halts at the first failed child; that
    #     child's artifact (annotated) becomes the sequence's artifact
    #     and remaining siblings are skipped.
    # Parallel is unaffected (children are concurrent).  None == continue.
    on_failure: Optional[Literal["stop", "continue"]] = None

    # Body — used by repeat / parallel / until / schedule (Task ignores)
    body: List["Block"] = []


# Rebuild for forward ref
Block.model_rebuild()


def find_scope_chain(root: Block, target_id: str) -> Optional[List[Optional[TaskScope]]]:
    """Depth-first search for ``target_id``; return the list of scopes
    from ``root`` down to (and including) the matching block, or
    ``None`` if no block with that id exists in the tree.

    Used by every call site that needs the block's EFFECTIVE (merged)
    scope without running the full executor — signing (``ziya-approve
    --task``), the scope-status editor banner, and the compliance
    audit.  Each caller additionally prepends deck/card scope via
    ``merge_scopes(deck_scope, card_scope, *find_scope_chain(...))``.
    """
    if root.id == target_id:
        return [root.scope]
    for child in root.body or []:
        found = find_scope_chain(child, target_id)
        if found is not None:
            return [root.scope] + found
    return None


# ── Task Card (top-level saveable unit) ────────────────────

class TaskCard(BaseModel):
    """A saveable, re-runnable task card.  The root is a Block."""
    model_config = {"extra": "allow"}

    id: str = ""
    name: str = ""
    description: str = ""
    root: Block
    # Card-level scope — a permissions baseline applied to every block in
    # this card (merged additively with the deck-level scope and each
    # block's ancestor chain; see ``merge_scopes`` and
    # app/agents/block_executor.py).  ``None`` grants nothing extra.
    scope: Optional[TaskScope] = None
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
    scope: Optional[TaskScope] = None
    tags: List[str] = []
    is_template: bool = False


class TaskCardUpdate(BaseModel):
    """Request body for updating a task card (partial)."""
    name: Optional[str] = None
    description: Optional[str] = None
    root: Optional[Block] = None
    scope: Optional[TaskScope] = None
    tags: Optional[List[str]] = None
    is_template: Optional[bool] = None


class TaskCardRun(BaseModel):
    """Request body for launching a task card execution."""
    source_conversation_id: Optional[str] = None
    parameter_overrides: Dict[str, Any] = Field(default_factory=dict)
