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
    "paused",      # held at a boundary by the user; resumable
    # Stopped by an infrastructure fault — expired credentials, a lost
    # endpoint, exhausted throttling retries — rather than by anything
    # the card's own work decided.  Kept distinct from "failed" because
    # the two ask for different responses: a failed run needs its card
    # or the code fixed, whereas a held run needs only the
    # infrastructure back before it can be continued.
    #
    # Terminal for THIS run object (the executor coroutine has already
    # unwound, so there is no frame left to hold), but resumable:
    # ``held_at_block_id`` records where to continue from via the
    # resume-from-block endpoint, which replays completed blocks'
    # artifacts rather than re-running them.
    "held",
    "done",        # finished successfully with an artifact
    # Stopped after real progress: at least one block completed AND
    # work was left unfinished.  DERIVED at the terminal write by
    # app.utils.run_outcome.classify_terminal_status, not written by
    # the executor — its error paths still produce failed/cancelled.
    #
    # Exists because a flat "failed" on a run that got 4 of 7 stages in
    # (writing files along the way) is actively harmful: it reads as
    # "nothing happened" for a run that may have MATERIALLY CHANGED the
    # workspace, discouraging the user from looking for the changes it
    # left behind.  A zero-progress stop stays failed/cancelled so a
    # genuine total loss keeps its own distinct signal.
    "partial",
    "failed",      # crashed or errored
    "cancelled",   # stopped by user
]


# How a run came to exist.  ``initial`` is a plain launch; the rest are
# user-driven continuations of an earlier attempt in the same lineage.
# ``*_iteration`` are the mid-loop variants: the resume point is an
# iteration INDEX within a loop block, not a block in the deck.  Kept as
# distinct kinds rather than reusing the block-level names so the UI can
# say "retried from iteration 3" — the two are different acts and a
# reader who cannot tell them apart cannot tell what was preserved.
ResumeKind = Literal[
    "initial", "retry_from", "continue_from", "rerun",
    "retry_iteration", "continue_iteration",
]


# Per-block lifecycle status — a superset of RunStatus.  "skipped"
# marks a sibling that never ran because an earlier sibling failed
# under the container's on_failure="stop" policy.  Drives the run map
# (frontend/src/components/TaskCard/TaskRunMap.tsx).
#
# "held" marks the block an infrastructure fault was raised at.  Without
# it, a held run's faulting block is written as "failed" — identical to a
# genuine failure of the work — so the run map cannot show WHERE on the
# tree the hold is, and every node has to be opened to find out.  The
# distinction is the same one RunStatus already draws: "failed" means fix
# the card, "held" means fix the environment and resume.
BlockStatus = Literal[
    "queued", "running", "done", "failed", "cancelled", "skipped", "held",
]


IterationStatus = Literal["passed", "failed", "cancelled"]


class ProgressNote(BaseModel):
    """One entry in a run's progress trail.

    ``progress_note`` alone is a single slot, so the narrative of a long
    run was destroyed as it was written: each note overwrote the last and
    nothing survived to the whole-run view.  A model-authored note
    ("reviewed 12/30 diffs; grouping into 3 commits") is exactly the kind
    of thing a user wants to read back AFTER the run, and it was the
    first casualty — the next tool call, typically a second or two later,
    replaced it.

    ``source`` distinguishes a model-authored ``<progress note=.../>``
    tag from a tool-derived line ("ran grep: ...").  Kept so the UI can
    show the richer kind without discarding the other.
    """
    note: str
    at: float
    source: Optional[str] = None


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
    # True when this record was CARRIED from an earlier attempt rather
    # than executed by this run.  A mid-loop resume replays iterations
    # before its start index (see block_executor._execute_repeat), and
    # until now it recorded nothing for them — so a run resumed at 3 of 5
    # had two summaries, and the run map's dot strip restarted its count
    # at 1, reading as though the banked work had been discarded.  The
    # prefix is seeded at launch (api.task_runs.resume_run_from_iteration
    # -> api.task_cards._launch_run_for_card) so the resumed run is a
    # self-contained record of the whole loop.
    #
    # Load-bearing as an EXCLUSION: every consumer that counts iterations
    # as work this run performed must skip these, or a resume would
    # inflate its own progress with a prior attempt's results.  See
    # run_outcome._iteration_statuses, partialOutcome.progressCounts,
    # iterationClusters.analyzeFailures, and the tile's iterCounts.
    replayed: bool = False


class SupersededBlockState(BaseModel):
    """A block's state from an EARLIER attempt, displaced by a re-run.

    ``block_states`` holds one slot per block id, so re-executing a
    block on a later attempt overwrites the record of what it did on the
    earlier one.  That was survivable while every attempt was its own
    run file — the prior record lived in the prior file — and becomes
    outright data loss once attempts share a run, which is what in-place
    continuation does.

    Ordered oldest first, so ``history[0]`` is the block's first outcome
    and the live fields on ``TaskRunBlockState`` are always its current
    one.  A reader asking "what happened to this block" gets the whole
    sequence from one record.
    """
    model_config = {"extra": "allow"}

    # Which attempt produced this outcome.  Load-bearing for display:
    # without it an entry has no position in the narrative and cannot be
    # labelled ("attempt 1: failed — timeout").
    attempt: int = 1
    status: BlockStatus = "queued"
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    artifact: Optional[Artifact] = None
    error: Optional[str] = None
    # Displaced by the same overwrite that displaces the status, so it
    # has to be carried here too: a loop that ran 4 of 5 iterations,
    # held, then re-ran would otherwise lose the four.
    iteration_summaries: List[IterationSummary] = Field(default_factory=list)


class AttemptRecord(BaseModel):
    """One attempt at executing a run, as an audit record.

    Attempt identity used to be welded to run-object identity: a resume
    created a NEW run file so the faulted attempt stayed immutable.  That
    bought the audit trail at the price of resetting ``block_states`` on
    every continuation — which is why a resumed run replayed its
    completed blocks as ``skipped`` and drew them struck through.  Prior
    state WAS retained, but under a different id, so the tile could only
    ever show the reconstruction rather than the thing itself.

    Separating the two puts the audit data here, append-only, and lets
    ``block_states`` persist across attempts and simply keep being true.
    Every fact the separate-file design recorded per attempt is a field
    below; none of them needed a separate file to hold them.
    """
    model_config = {"extra": "allow"}

    attempt: int = 1
    # Why this attempt exists.  "initial" for the original launch.
    resume_kind: Optional[ResumeKind] = None
    resumed_from_block_id: Optional[str] = None
    resume_from_iteration: Optional[int] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    # Terminal status THIS attempt reached.  Left None while it is the
    # live attempt: the run's own ``status`` is the live answer, and
    # duplicating it would create two sources of truth for one fact.
    status: Optional[RunStatus] = None
    error: Optional[str] = None
    # Hold facts, per attempt.  These live on TaskRun today, where a
    # continuation necessarily clears them — the run is no longer held.
    # Recording them here is what keeps "attempt 2 held on an expired
    # credential" legible after attempt 3 succeeded.
    held_reason: Optional[str] = None
    held_at_block_id: Optional[str] = None
    held_faults: Optional[Dict[str, Any]] = None
    held_gate_reason: Optional[str] = None
    # Captured per attempt because both can legitimately differ between
    # attempts: the write policy may have been widened to unblock the
    # retry, and the card may have been edited in between.  A single
    # run-level snapshot would attribute the CURRENT values to every
    # attempt, which is the opposite of an audit trail.
    permissions_snapshot: Optional[Dict[str, Any]] = None
    card_snapshot: Optional[Dict[str, Any]] = None
    parameter_overrides: Dict[str, Any] = Field(default_factory=dict)


class TaskRunBlockState(BaseModel):
    """Per-block runtime state."""
    model_config = {"extra": "allow"}

    block_id: str
    block_type: str
    status: BlockStatus = "queued"
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    artifact: Optional[Artifact] = None
    error: Optional[str] = None
    # For Repeat blocks: one summary per iteration.  Empty for Task
    # and Parallel blocks.
    iteration_summaries: List[IterationSummary] = Field(default_factory=list)
    # Outcomes displaced by a later attempt re-running this block,
    # oldest first.  Empty on the common path.  Populated only by
    # ``TaskRunStorage.update_block_status``, which pushes the current
    # record down before overwriting it — see SupersededBlockState for
    # why the overwrite is otherwise data loss.
    #
    # Load-bearing as an EXCLUSION, the same way IterationSummary.
    # replayed is: a consumer counting what a run accomplished must read
    # the live fields only, or a retried block would be counted twice.
    history: List[SupersededBlockState] = Field(default_factory=list)


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
    # Populated only when ``status == "held"``: the classified fault
    # kind (e.g. "authentication_error") and the block the run stopped
    # at, so a continuation knows where to pick up without the user
    # reconstructing it by eye from the run map.
    held_reason: Optional[str] = None
    held_at_block_id: Optional[str] = None
    # Aggregate fault record for the hold, from
    # app.utils.infra_gate.summarize.  Exists because ``held_reason`` and
    # ``held_at_block_id`` describe ONE fault, and a hold in a fan-out is
    # a progressive collapse rather than an instant: reporting the first
    # subagent's fault as though it were the whole event leaves the three
    # questions a reader actually has unanswered — which card, which
    # subagent, and how widespread.
    #
    # Keys: fault_count, fanout_width, primary_kind, kinds (histogram),
    # call_path (outermost card -> faulting subagent), fleet_wide,
    # block_ids.  ``call_path`` is what lets the run tile show
    # "CL0 → CL1 → audit-mcp-security" without the user expanding
    # anything, and ``fleet_wide`` distinguishes a dead credential from
    # one throttled sibling — a distinction ``held_reason`` alone cannot
    # carry, since both are infra kinds but call for opposite responses.
    held_faults: Optional[Dict[str, Any]] = None
    # Why the gate fired, in prose, from app.utils.infra_gate.gate_reason.
    # Null when the run held without a fan-out gate (a single task's
    # fault), so its presence means "this stopped the fleet, not just me".
    held_gate_reason: Optional[str] = None
    # Soft-cancel flag.  Block executor checks at iteration and
    # sibling boundaries.  See design/task-cards.md §Cancellation.
    cancel_requested: bool = False
    # Soft-pause flag.  Checked at the SAME boundaries as cancel
    # (between Repeat iterations, between sequence siblings, between
    # until loops).  When set, the executor holds at the next boundary
    # and flips status to "paused" until the flag is cleared (resume).
    # An in-flight Task/LLM invocation is never interrupted — pause
    # lands at the next boundary, exactly like soft-cancel.
    pause_requested: bool = False
    # Step-debug budget: the number of boundaries the executor may cross
    # while ``pause_requested`` is still set.  Zero means a pause holds
    # indefinitely (the ordinary pause behaviour).  The step endpoint
    # raises it; ``block_executor._wait_if_paused`` decrements it by one
    # each time it lets a boundary through, then holds again at the next
    # one because ``pause_requested`` was never cleared.
    #
    # This deliberately reuses the pause boundaries rather than adding
    # new ones: a step advances to the next sequence sibling, Repeat
    # iteration, or until-loop iteration — the same three points pause
    # can land on.  It is NOT an instruction-level or tool-call-level
    # step; an in-flight Task/LLM invocation always runs to completion,
    # exactly as with pause and soft-cancel.
    #
    # Distinct from clearing ``pause_requested`` (resume), which lets
    # the run go to completion.  Stepping keeps the run held so a
    # complex card can be walked one block at a time while it is being
    # built, instead of being launched and left to run until it dies.
    step_budget: int = 0

    # Top-level artifact produced by the root block
    artifact: Optional[Artifact] = None

    # Per-block state — keyed by block.id
    block_states: Dict[str, TaskRunBlockState] = Field(default_factory=dict)

    # Aggregate metrics
    total_tokens: int = 0
    total_tool_calls: int = 0

    # Live-progress surface ("what is it up to right now").
    # last_activity_at: wall-clock seconds of the most recent executor
    # event (tool call / text delta), throttled to ~one disk write per
    # 5s.  progress_note: short human-readable line derived from the
    # most recent tool invocation (e.g. "ran run_shell_command: git
    # status").  Both are readable via GET /task-runs/{id} so REST
    # pollers can distinguish "slow but alive" from "hung".
    last_activity_at: Optional[float] = None
    progress_note: Optional[str] = None
    # Bounded trail of the notes above, oldest first.  Exists because the
    # single ``progress_note`` slot is destroyed on every update, so a
    # finished run could say what it was doing LAST but never what it had
    # been doing — the whole-run view had no progress narrative at all.
    # Capped in ``TaskRunStorage.record_activity``; a run that emits
    # thousands of notes keeps only the most recent window.
    progress_notes: List[ProgressNote] = Field(default_factory=list)

    # Snapshot of effective permissions (write policy + per-block task
    # scopes + project root) captured at launch.  Stored as a dict so
    # the schema can evolve without migrations; see
    # ``app/utils/permissions_snapshot.py`` for the active shape.
    # Populated by ``_launch_run_for_card`` immediately after create.
    permissions_snapshot: Optional[Dict[str, Any]] = None

    # Snapshot of the card definition (name, description, root block tree)
    # captured at launch, so later edits to the card don't retroactively
    # rewrite what a completed run is shown to have executed.  The block
    # ids stored here are the ones this run's block_states reference
    # (a card edit reassigns block ids), so displaying the run map from
    # the snapshot rather than the live card also keeps it consistent.
    # Absent on runs created before snapshotting existed — callers fall
    # back to the live card in that case.
    card_snapshot: Optional[Dict[str, Any]] = None

    # Resolved Call-block targets, keyed by the CALL block's id:
    #   {"<call_block_id>": {"target": str, "kind": str, "key": str,
    #                        "root": <block tree dict>}}
    # A Call names its target and resolves it at run time, so the callee's
    # tree is in neither the card nor ``card_snapshot``.  Without this the
    # run map can only draw the call row itself — the callee's blocks
    # stream status events and persist in ``block_states``, but there is
    # no tree to place them in, so the run displays a call that produced
    # an artifact out of nothing.  Recorded when the call executes, and
    # never rewritten afterwards, so it is a record of what this run
    # actually invoked rather than of what the target says today.
    call_snapshots: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    # Launch-time variable overrides (TaskCardRun.parameter_overrides),
    # recorded so a run is reproducible from its own record alone.
    # ExecutionContext.overrides is in-memory only and outranks State
    # blocks, so without this a resumed or replayed run would silently
    # fall back to the card's authored baselines and report success with
    # the wrong inputs.  Part of the same audit-trail guarantee as
    # card_snapshot / permissions_snapshot: captured once at launch and
    # never rewritten.  Empty dict on runs created before this existed,
    # which is indistinguishable from "launched with no overrides" —
    # acceptable, since that is the common case.
    parameter_overrides: Dict[str, Any] = Field(default_factory=dict)

    # ── Attempt lineage ────────────────────────────────────────────
    # A resumed run is a NEW run (the source stays an immutable record),
    # but until now nothing recorded that relationship, so the GUI could
    # only show a second tile materializing beside the first with no
    # stated connection — leaving the user unable to tell whether prior
    # state had been preserved or thrown away.  It IS preserved
    # (resume_artifacts replays every completed block), and these fields
    # let the UI say so.
    #
    # ``root_run_id`` is the lineage key: every attempt shares the FIRST
    # run's id, so the whole chain is one list() filter rather than a
    # parent-pointer walk.  Self on an initial run.
    root_run_id: Optional[str] = None
    # Immediate predecessor — the run whose artifacts this one replays.
    parent_run_id: Optional[str] = None
    # 1-based position in the lineage; displayed as "attempt N of M".
    attempt: int = 1
    # Append-only audit record, one entry per attempt, oldest first.
    # ``attempt`` above is the count; this is the evidence.
    #
    # Exists so a continuation can execute IN PLACE — same run id, same
    # block_states — without discarding what the earlier attempts did.
    # The previous design got its audit trail from run-object identity
    # (one file per attempt), which forced every continuation to start
    # from an empty block_states and rebuild it by replay; the replay
    # wrote ``skipped``, and the run map struck through work that had in
    # fact completed.  The trail is the same set of facts either way, so
    # it moves here and the run object stops being per-attempt.
    #
    # Empty on runs written before this existed.  Consumers must treat
    # that as "one attempt, described by the run's own fields" rather
    # than as "no attempts".
    attempts: List[AttemptRecord] = Field(default_factory=list)
    # Why this run exists.  None on records written before lineage
    # tracking, which is indistinguishable from "initial" — acceptable,
    # since that is what those runs were.
    resume_kind: Optional[ResumeKind] = None
    # The block the user pointed at.  For ``retry_from`` this block is
    # re-executed; for ``continue_from`` its recorded outcome is
    # accepted and execution starts at the following block.
    resumed_from_block_id: Optional[str] = None
    # Mid-loop resume: the iteration index the loop named by
    # ``resumed_from_block_id`` restarts at.  Iterations before it replay
    # their recorded artifacts instead of executing, so the propagation
    # chain ({{previous}}, {{all}}) is intact for the ones that do run.
    #
    # Exists because a loop was only ever resumable at index 0: a
    # five-iteration campaign that lost iteration 5 to expired
    # credentials had to re-pay all five, and the four banked passes were
    # discarded — the single most expensive form of lost work the task
    # system had, since a long loop is exactly where a run is most likely
    # to outlive a credential.
    #
    # None means "start at 0", which is the whole-loop behaviour every
    # pre-existing run and every block-level resume has.
    resume_from_iteration: Optional[int] = None
    # Iteration artifacts carried from the source run, keyed by index, for
    # the loop being resumed.  Read from the source run's per-iteration
    # files at launch rather than re-read during execution, so a run whose
    # source was since deleted still resumes.  Only indices with
    # ``has_artifact`` are present — see the retention cap.
    resume_iteration_artifacts: Dict[int, Any] = Field(default_factory=dict)

    created_at: int = 0
    updated_at: int = 0


class TaskRunCreate(BaseModel):
    """Internal — constructed by the launch endpoint, not user-facing."""
    card_id: str
    source_conversation_id: Optional[str] = None
    parameter_overrides: Dict[str, Any] = Field(default_factory=dict)
    # Lineage, supplied only by the resume path.  A plain launch leaves
    # these unset and storage.create() seeds root_run_id to the new
    # run's own id, making every initial run the root of its lineage.
    parent_run_id: Optional[str] = None
    root_run_id: Optional[str] = None
    attempt: int = 1
    resume_kind: Optional[ResumeKind] = None
    resumed_from_block_id: Optional[str] = None
    # Mid-loop resume position; see the same fields on TaskRun.  Named
    # here because create() copies fields one by one.
    resume_from_iteration: Optional[int] = None
    resume_iteration_artifacts: Dict[int, Any] = Field(default_factory=dict)
