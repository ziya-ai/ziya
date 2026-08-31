"""
Block executor — the loop controller for Task Card block trees.

import hashlib
Implements the runtime semantics defined in design/task-cards.md
§Runtime semantics:

- Task    : delegates to app.agents.task_executor.execute_task_block
- Repeat  : count / until / for_each, serial or parallel
- Parallel: concurrent execution of different child blocks
- Sequence (implicit in a body list): top-to-bottom, returns last

Soft cancel is checked between iterations of a Repeat and between
siblings of a sequence.  In-flight Task invocations are not
interrupted (hard cancel is deferred — see the design note).

Passing-iteration retention cap: per-block, up to
PASS_ARTIFACT_RETENTION_CAP (50) passing iteration artifacts are
persisted in full; beyond that, only the lightweight summary record
is kept.  Every failing iteration is always persisted in full.
"""

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import traceback
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

from ..models.task_card import Artifact, ArtifactPart, Block, TaskScope, merge_scopes
from ..models.task_run import IterationStatus, IterationSummary, TaskRunBlockState
from ..context import (
    set_task_iteration_context,
    reset_task_iteration_context,
)
from ..storage.task_runs import TaskRunStorage
from ..utils.roster_keys import derive_item_key, roster_key_problems
from . import task_templating
from .task_executor import TaskExecutorError, execute_task_block
from . import task_run_stream_relay as _relay
from .until_evaluator import evaluate_condition as _evaluate_until_condition_with_model

logger = logging.getLogger(__name__)


PASS_ARTIFACT_RETENTION_CAP = 50
"""Max passing iterations whose full Artifact is persisted per Repeat."""

MAX_CALL_DEPTH = 5
"""Maximum nesting of Call blocks within a single run.

A cap is needed in addition to cycle detection: an acyclic call graph can
still be arbitrarily deep (A→B→C→…), and each level multiplies the run's
cost while the operator sees one tile.  Exceeding it produces a failed
artifact rather than an exception, so ``on_failure`` decides what happens
to the surrounding sequence — the same treatment an unresolvable target
gets.
"""


@dataclass
class ExecutionContext:
    """Non-structural state threaded through the recursive walk.

    Kept separate from Block so the same block tree can be executed
    by different runs with different project roots or storage targets.
    """

    run_id: str
    project_root: Optional[str] = None
    # Project id — distinct from project_root.  Required for
    # resolving scope.skills (which live under the project's
    # ~/.ziya/projects/{project_id}/skills directory).
    project_id: Optional[str] = None
    # Deck-level (project-wide) scope — the outermost permissions layer,
    # sourced from the project's ``settings.taskScope``.  Merged
    # additively with ``card_scope`` and every ancestor container
    # block's own scope before reaching a leaf Task.  See
    # app.models.task_card.merge_scopes.
    deck_scope: Optional[TaskScope] = None
    # Card-level scope — the second-outermost layer, sourced from
    # ``TaskCard.scope``.
    card_scope: Optional[TaskScope] = None
    # Stack of ancestor container blocks' own scopes, pushed/popped by
    # execute_block as the tree is walked.  Root→leaf order.
    scope_stack: List[Optional[TaskScope]] = field(default_factory=list)
    storage: Optional[TaskRunStorage] = None
    # Per-block pass-retention counters.  Keyed by block.id.
    pass_counts: Dict[str, int] = field(default_factory=dict)
    # Run-wide count of self-improvement card edits applied, across every
    # improving level this run.  The per-block ``improve_max`` bounds each
    # level; this bounds the PRODUCT of nested levels (see
    # app.utils.self_improve.run_improve_ceiling).
    improve_edits_used: int = 0
    # Stack of active iteration bindings.  The innermost Repeat block
    # pushes its per-iteration bindings before dispatching the body;
    # nested Repeats stack so an inner iteration can still see the
    # outer {{index}} / {{item}}.  Rightmost (top) wins on conflict.
    binding_stack: List["task_templating.IterationBindings"] = field(default_factory=list)
    # Run-scoped read-only variables declared by State blocks.  Flat
    # namespace, last-write-wins.  Read by tasks via {{var.NAME}}
    # templating; never written back by a task (sandbox invariant).
    # A State block inside a loop body re-applies its literals each
    # iteration — placement is the reset policy.
    variables: Dict[str, Any] = field(default_factory=dict)
    # Launch-time variable overrides (from TaskCardRun.parameter_overrides).
    # These WIN over State-block authored values at read time: merged on
    # top of ``variables`` whenever bindings are built, so an override
    # survives a loop body re-applying its baseline literals each cycle.
    # Read-only like ``variables``; never written by a task.
    overrides: Dict[str, Any] = field(default_factory=dict)
    # State prose context, keyed by the State block's id.  Each State
    # block with a ``state_context`` writes its prose here; keying by
    # block id means a State block re-executing inside a loop overwrites
    # its own entry rather than duplicating (idempotent re-application,
    # matching the variables reset policy).  Surfaced to every in-scope
    # task as a standing-context preamble.  Insertion order preserved.
    context_notes: Dict[str, str] = field(default_factory=dict)

    # Roster truncations, keyed by the repeat block's id:
    # {"roster": N, "dispatched": M, "dropped": [ids]} whenever
    # ``repeat_max`` clipped a for_each source.  Recorded at planning time
    # and read back when the loop returns, so the block's artifact can
    # state its own reduced scope.  Without it a clipped fan-out is
    # indistinguishable from a complete one after the fact — measured as a
    # 112-item queue reported as a finished pass having dispatched 60,
    # nothing naming the other 52.  ``dropped`` carries the IDENTITIES,
    # not just the count: a count tells you scope was lost, whereas the
    # identities let a follow-up pass run precisely the items that were
    # missed instead of re-running the whole roster.
    roster_truncations: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Roster shortfalls, keyed by the repeat block's id:
    # {"roster": N, "produced": M, "missing": [keys]} when a for_each
    # loop with repeat_require_complete exited with members lacking a
    # passed iteration.  The structured counterpart of the block's
    # failed artifact, mirroring roster_truncations: the artifact names
    # a bounded sample, this carries the full list a gap-fill would
    # re-dispatch from.
    roster_shortfalls: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Sibling-result stack, one slot per active sequence depth.  Each
    # _execute_sequence pushes a slot on entry and writes the most-recent
    # completed sibling's artifact into it after each child runs; the
    # next sibling reads the top slot so a task can see the prior
    # sibling's result (prose auto-context + {{previous_sibling}}).
    # A stack (not a scalar) so a nested sequence's siblings don't
    # clobber the outer sequence's slot — the top is always the current
    # depth.  None until the first sibling at a depth completes.
    sibling_stack: List[Optional[Artifact]] = field(default_factory=list)

    # Run-scoped registry of completed block artifacts, keyed by block.id.
    # Populated in execute_block as each block returns (so it captures
    # containers and their children, anywhere in the tree).  Backs the
    # {{sibling("block-id")}} by-id lookup — an explicit reference to any
    # block that has completed, unlike the positional previous_sibling.
    # Last-write-wins: a block re-executed inside a loop body overwrites
    # its own entry, so the lookup sees that block's most recent result.
    artifact_registry: Dict[str, Artifact] = field(default_factory=dict)

    # Canonical keys of the Call targets currently on the stack, outermost
    # first.  Two jobs: bound nesting at MAX_CALL_DEPTH, and reject a cycle
    # (a target that is already executing).  Keyed by card id rather than
    # by the name the caller used, so A→A is caught even when the two
    # references spell the target differently.
    call_stack: List[str] = field(default_factory=list)

    # Human-readable labels for the same stack, outermost first.
    # Maintained in PARALLEL with ``call_stack`` rather than derived from
    # it, because that stack holds ``resolved.key`` — ``card:<uuid>``.
    # Keying by id is correct there and load-bearing (it is what catches
    # A→A under two different names), and useless as a breadcrumb: a user
    # shown ``card:8f3a1c04-…`` learns nothing about which phase of their
    # study broke.  ``resolved.label`` is the card's name, so this carries
    # that instead and the hold surface reads "CL0 → CL1 → …".
    call_labels: List[str] = field(default_factory=list)

    # Name of the card that owns this run — the OUTERMOST hop of the
    # breadcrumb.  Nothing is pushed onto ``call_labels`` for it (it was
    # never "called"), so without this a hold inside CL1 under CL0
    # produced a path that started at CL1 and omitted the one card the
    # reader already has on screen and uses to orient.
    root_card_label: Optional[str] = None

    # Infrastructure faults observed anywhere in this run, in the order
    # they occurred.  Accumulated IN MEMORY, deliberately: a hold is not
    # the first fault, it is the terminal state of a progressive
    # collapse, and its breadth is the actionable part — but
    # TaskRunStorage does unguarded read-modify-write (get -> mutate ->
    # _write_json, no lock, no atomic replace), so N concurrent siblings
    # incrementing a counter on the run file would lose writes and
    # under-report the very number that matters.  One aggregate write
    # happens at the end instead, in the run's own handler.
    #
    # Read by _infra_gate_open() to decide whether to keep admitting
    # work; the kind-dependent policy lives in app.utils.infra_gate
    # because the enforcement site (here), the surfacing layer and the
    # tests must not each carry their own copy of it.
    infra_faults: List["InfraFault"] = field(default_factory=list)
    # Width of the fan-out currently executing, i.e. the denominator for
    # the proportional gate.  Set by _execute_repeat before dispatch and
    # restored after, so a nested loop's width does not leak to the
    # enclosing one.  Zero outside a fan-out, where only session-level
    # kinds can gate.
    infra_fanout_width: int = 0
    # Widest fan-out this run has entered, latched and never restored.
    # ``infra_fanout_width`` is restored on exit so a nested loop cannot
    # corrupt its parent's denominator — but the hold surface reads its
    # denominator LATER, from the top-level handler, by which time every
    # loop has exited and the live width is back to 0.  Reading the live
    # value there would report fanout_width=0 and silently destroy the
    # ``fleet_wide`` signal, which is the whole point of the aggregate.
    infra_widest_fanout: int = 0
    # Set once the gate has fired, so the decision is made exactly once
    # and every subsequent boundary reads the same answer rather than
    # re-evaluating a policy whose inputs are still growing.
    infra_gated_reason: Optional[str] = None

    # ---- Resume-from-block support ----
    # Set when this run is a resume of an earlier run (see the resume
    # endpoint).  ``resume_from_block_id`` is the structural block to
    # re-enter at; execution proceeds normally from there to the end of
    # the deck.  Only structural blocks are valid targets, because only
    # those have persisted state — ``_mark_block_status`` deliberately
    # skips blocks inside an active loop iteration (non-empty
    # ``binding_stack``), so there is no per-iteration inner record to
    # resume from.  Targeting inside a loop resumes the enclosing loop.
    resume_from_block_id: Optional[str] = None
    # True while walking the tree BEFORE the resume target is reached.
    # Blocks encountered in this state are not executed; their persisted
    # artifacts are replayed into ``artifact_registry`` /
    # ``sibling_stack`` instead, so that {{sibling("id")}} and
    # {{previous_sibling}} still resolve for the resumed blocks — this is
    # what preserves the prior deck state rather than recomputing it.
    # Cleared the moment the target block is reached.
    #
    # State blocks are the deliberate exception: they are re-executed
    # even while skipping.  ``_execute_state`` only writes authored
    # literals into ``ctx.variables`` / ``ctx.context_notes`` and emits
    # an event — it is pure and has no side effects — and those two
    # stores are the run-scoped state that is NOT persisted anywhere.
    # Re-running them is how a resumed run rebuilds {{var.NAME}} without
    # needing a variables snapshot on disk.
    resume_skipping: bool = False
    # Persisted block artifacts from the run being resumed, keyed by
    # block id, read out of the source run's ``block_states``.  Consulted
    # only while ``resume_skipping`` is True.  A missing entry means that
    # block never completed (queued/failed), in which case there is
    # nothing to replay and the slot is simply left unset.
    resume_artifacts: Dict[str, Artifact] = field(default_factory=dict)
    # ---- Mid-loop resume ----
    # When set, the loop block named by ``resume_from_block_id`` starts
    # executing at this iteration index; earlier iterations replay their
    # recorded artifacts (from ``resume_iteration_artifacts``) instead of
    # running.  Distinct from the block-level gate above because a loop's
    # iterations are not blocks — they share one ``block_states`` entry
    # and are recorded only as ``iteration_summaries``, which is exactly
    # why the loop was previously resumable only at index 0.
    #
    # None means start at 0, i.e. every pre-existing resume behaves as
    # before.
    resume_from_iteration: Optional[int] = None
    # Recorded iteration artifacts keyed by index, for the loop above.
    # A replayed iteration returns its entry so the NEXT iteration's
    # {{previous}} resolves to the same value it saw in the source run —
    # without which a mid-loop resume would run the first executed
    # iteration against an empty input while reporting success.
    resume_iteration_artifacts: Dict[int, Artifact] = field(
        default_factory=dict,
    )
    # ---- Resume THROUGH a Call ----
    # Call block ids the resume walk must DESCEND INTO rather than replay,
    # outermost first.  A Call has an empty ``body``, so
    # ``_subtree_contains`` reports a callee target as absent and the gate
    # replays the whole call — which is why a run held on iteration 19 of a
    # fan-out inside a called card had no resume path except re-entering
    # that callee from its own start and re-running every banked iteration.
    # Measured on one study: 14 hours of completed work discarded by a
    # control labelled "resume".
    #
    # Supplied by the resume endpoint from the SOURCE run's
    # ``call_snapshots`` (app.utils.resume_targets.locate_block), the only
    # record of a callee's tree — the callee is named, not inlined, so it is
    # in neither the card nor ``card_snapshot``.  Empty for a normal launch
    # and for a resume targeting the caller's own tree, so this is inert on
    # every pre-existing path.
    resume_call_chain: List[str] = field(default_factory=list)

    def resume_descend_ids(self) -> List[str]:
        """Ids whose enclosing CONTAINERS must be descended into.

        The resume target plus every Call on the way to it.  The chain
        members matter independently of the target: the caller's root
        container encloses the outermost Call but NOT the target, so
        testing the target alone made the root itself replay and nothing
        ran at all.
        """
        out = [self.resume_from_block_id] if self.resume_from_block_id else []
        return out + list(self.resume_call_chain)

    def effective_scope(self, leaf_scope: Optional[TaskScope] = None) -> Optional[TaskScope]:
        """Merge deck + card + every active ancestor's scope + an
        optional leaf scope, root→leaf order (outermost first, so a
        more specific layer only ever ADDS grants — see merge_scopes).
        """
        layers: List[Optional[TaskScope]] = [self.deck_scope, self.card_scope]
        layers.extend(self.scope_stack)
        if leaf_scope is not None:
            layers.append(leaf_scope)
        return merge_scopes(*layers)

    def cancel_requested(self) -> bool:
        if self.storage is None:
            return False
        run = self.storage.get(self.run_id)
        return bool(run and run.cancel_requested)

    def record_infra_fault(self, exc: BaseException, index: Optional[int] = None) -> None:
        """Note an infrastructure fault against this run.

        Called from the fan-out's per-iteration except BEFORE the
        exception propagates, so the aggregate is complete even though
        only one exception survives to the top-level handler.
        """
        from app.utils.infra_gate import InfraFault
        kind = getattr(exc, "infra_kind", "")
        if not kind:
            return
        # Labels, not keys — and rooted at the card that owns the run, so
        # the breadcrumb names every hop instead of starting mid-chain.
        # Falls back to the key stack only when no labels were captured at
        # all, so a path built by an older code path still yields
        # something rather than nothing.
        _hops: List[str] = []
        if self.root_card_label:
            _hops.append(self.root_card_label)
        _hops.extend(self.call_labels)
        _path = tuple(_hops) if _hops else tuple(self.call_stack)
        self.infra_faults.append(InfraFault(
            kind=kind,
            block_id=getattr(exc, "block_id", "") or "",
            call_path=_path,
            index=index,
            at=time.time(),
        ))

    def infra_gate_closed(self) -> bool:
        """True once observed faults justify refusing to admit new work.

        Evaluated at the same boundaries as ``cancel_requested``.  The
        decision is latched in ``infra_gated_reason``: re-deciding on
        every boundary against a still-growing fault list would let a
        gate that fired at a third of the fan-out silently un-fire as
        later siblings completed successfully.
        """
        if self.infra_gated_reason:
            return True
        if not self.infra_faults:
            return False
        from app.utils.infra_gate import gate_reason
        reason = gate_reason(self.infra_faults, self.infra_fanout_width)
        if reason:
            self.infra_gated_reason = reason
            logger.warning(f"⏸️ INFRA_GATE closed: {reason}")
            return True
        return False

    def infra_summary(self) -> Dict[str, Any]:
        """Aggregate fault record for the hold surface."""
        from app.utils.infra_gate import summarize
        return summarize(
            self.infra_faults,
            self.infra_widest_fanout or self.infra_fanout_width,
        )

    def pause_requested(self) -> bool:
        if self.storage is None:
            return False
        run = self.storage.get(self.run_id)
        return bool(run and run.pause_requested)


# Stores that answer "where in the tree am I", as distinct from "which
# run is this".  Only these are private to a concurrent iteration; every
# other field stays shared, because the run is genuinely one run.
_ITERATION_PRIVATE = (
    "binding_stack", "sibling_stack", "artifact_registry", "scope_stack",
)


class _IterationScope:
    """A per-iteration view of an ``ExecutionContext``.

    A parallel Repeat runs its iterations as concurrent asyncio Tasks that
    all shared ONE context, while templating resolves ``{{item}}`` from
    ``ctx.binding_stack[-1]`` and ``{{previous_sibling}}`` from
    ``ctx.sibling_stack[-1]``.  Both are plain lists, so the top element
    belonged to whichever iteration pushed most recently rather than to
    the one doing the read.  Iterations suspend at every await -- the
    model call, and the ``_emit`` on each block transition -- so the
    interleaving is the normal case, not a race needing bad luck.

    Measured on an 8-wide two-stage fan-out: all eight iterations resolved
    ``{{item}}`` to the EIGHTH item, the first seven items were never
    processed, and the loop still reported eight passed iterations.
    Nothing raised, so the wrong answers were indistinguishable from
    right ones.

    ``scope_stack`` is private for a related reason rather than a measured
    one: it feeds ``effective_scope()``, whose merge unions ``tools`` /
    ``skills`` / ``shell_commands``, so a concurrent sibling's entry can
    only ever WIDEN a leaf task's grants.  A uniform fan-out pushes
    identical scopes and so cannot show it, but a body whose blocks carry
    different tool lists would leak one into the other.

    Everything else -- ``storage``, ``variables``, the infra-fault state,
    the cancel and pause flags -- delegates to the parent, which is what
    keeps a fault recorded inside an iteration reaching the run and the
    infra gate closing for the whole fan-out.  Copying rather than
    starting empty preserves nesting: an inner loop still sees the
    enclosing iteration's bindings and ancestor scopes beneath its own.
    """

    __slots__ = _ITERATION_PRIVATE + ("_parent",)

    def __init__(self, parent: "ExecutionContext") -> None:
        object.__setattr__(self, "_parent", parent)
        for _name in _ITERATION_PRIVATE:
            _got = getattr(parent, _name)
            object.__setattr__(
                self, _name,
                dict(_got) if isinstance(_got, dict) else list(_got),
            )

    def __getattr__(self, name: str):
        # Reached only for names absent from __slots__ -- i.e. everything
        # that is genuinely run-scoped rather than tree-position state.
        parent = object.__getattribute__(self, "_parent")
        got = getattr(parent, name)
        # A METHOD must be re-bound to THIS view before it is returned.
        # Forwarded as the parent's bound method, "self" inside it is the
        # PARENT, so a method reading a private store reads the shared
        # copy rather than this iteration's own.  Measured:
        # effective_scope() reads self.scope_stack and returned None for
        # a view whose own stack held the leaf's scope -- and task_executor
        # reads every scope field behind "if scope else", so the leaf
        # silently lost its writable paths, fell to the tool floor, and
        # ran a declared model_tier of "large" on the default model.
        # Invisible, and worse than the bug this class exists to fix.
        #
        # Safe for the writers too: record_infra_fault and
        # infra_gate_closed touch only shared fields, which __getattr__
        # and __setattr__ route to the parent whatever self is bound to.
        if getattr(got, "__self__", None) is parent:
            return got.__func__.__get__(self)
        return got

    def __setattr__(self, name: str, value) -> None:
        if name in _ITERATION_PRIVATE:
            object.__setattr__(self, name, value)
            return
        # Scalar run state (infra_gated_reason, infra_fanout_width, ...)
        # must land on the shared parent, or the gate would close on a
        # copy discarded the moment the iteration ends.
        setattr(object.__getattribute__(self, "_parent"), name, value)


class BlockExecutionCancelled(Exception):
    """Raised internally when cancel is observed at a boundary."""


# Default ceiling on concurrent in-flight children of a parallel
# container (a Parallel block, or a Repeat with repeat_parallel).
#
# Matches delegate_manager.DEFAULT_MAX_CONCURRENCY deliberately: both
# fan work out to the same provider under the same account rate limit,
# so two different caps would just be two different ways to get
# throttled.  Overridable per-block via ``repeat_max_concurrency`` and
# globally via ZIYA_TASK_MAX_CONCURRENCY.
DEFAULT_REPEAT_CONCURRENCY = 8


def _resolve_concurrency(block_value: Optional[int], planned: int) -> int:
    """Resolve the effective concurrency limit for a parallel container.

    Precedence: explicit per-block value, then ZIYA_TASK_MAX_CONCURRENCY,
    then DEFAULT_REPEAT_CONCURRENCY.  A value <= 0 at either the block or
    env layer means unbounded and is returned as 0 — an explicit opt-out
    for a fan-out of cheap non-model work, where the cap is pure latency.

    Never raises: an unparseable env value is ignored rather than
    failing a launch over a typo in the environment.
    """
    if block_value is not None:
        return max(0, int(block_value))
    raw = (os.environ.get("ZIYA_TASK_MAX_CONCURRENCY") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            logger.warning(
                "ZIYA_TASK_MAX_CONCURRENCY=%r is not an integer - ignoring", raw
            )
    return DEFAULT_REPEAT_CONCURRENCY


def _concurrency_gate(limit: int, planned: int):
    """An async context manager bounding concurrent entries, or a no-op.

    Returns a null gate when the limit is 0 (unbounded) or already at or
    above ``planned``, so the common small fan-out adds no semaphore
    bookkeeping and the emitted log line stays truthful about whether
    anything was actually throttled.
    """
    if limit <= 0 or planned <= limit:
        return contextlib.nullcontext()
    return asyncio.Semaphore(limit)


async def _emit(ctx: "ExecutionContext", event: Dict[str, Any]) -> None:
    """Best-effort push to the live-observation relay.  Never raises."""
    if not ctx.run_id:
        return
    await _relay.safe_push(ctx.run_id, event)


def _is_step_boundary(block: Block) -> bool:
    """True if holding before ``block`` should cost a step credit.

    A credit must buy observable work, not tree descent.  Entering a
    container (group/repeat/until/parallel) crosses a boundary but
    executes nothing by itself, and each nesting level adds another such
    boundary — so charging for them made one step cost N credits for a
    task N levels deep (measured: 3 credits to reach the body of a
    group[repeat[task]]).

    Containers therefore hold when paused but pass for free when
    stepping.  Leaf blocks (task, state, schedule) are what a credit
    actually buys.

    The Repeat/until ITERATION boundaries are likewise NOT charged (they
    pass ``chargeable=False`` explicitly at their call sites): each
    iteration executes its body as a sequence, and that sequence's own
    first-child boundary is what charges for the iteration's work.
    Charging the iteration boundary too was measured at four chargeable
    holds for two iterations of work, so one credit could never advance
    exactly one iteration.
    """
    return block.block_type not in ("group", "repeat", "until", "parallel")


async def _wait_if_paused(ctx: "ExecutionContext", chargeable: bool = True) -> None:
    """Hold at a boundary while the run's pause flag is set.

    Called at the SAME boundaries as ``cancel_requested`` (between
    Repeat iterations, between sequence siblings, between until loops).
    No-op when not paused.  While held, the run's status is flipped to
    ``paused`` and a ``run_paused`` event is emitted once; on resume the
    status is restored to ``running`` and ``run_resumed`` is emitted.

    Step-debug rides on this same hold.  If the run has an unspent step
    credit (``step_budget``), one credit is spent and this returns
    immediately even though ``pause_requested`` is still set — so the
    executor crosses exactly this one boundary and then holds again at
    the next.  That is the whole of stepping: no new hold points, and
    the granularity is therefore a block/iteration boundary, never
    mid-Task.  A step is checked BEFORE the paused-status flip so a
    single step out of a running deck does not flicker the run through
    ``paused`` and back.

    Cancel wins over pause: if cancel is requested while paused, this
    raises ``BlockExecutionCancelled`` so a paused run can still be
    stopped.  The coroutine stays registered in the live-run set while
    sleeping, so the cancel endpoint's soft-cancel path reaches it.
    """
    if ctx.storage is None:
        return
    notified = False
    while ctx.pause_requested():
        if ctx.cancel_requested():
            raise BlockExecutionCancelled()
        # ``chargeable=False`` boundaries (container descent) let a
        # stepping run through WITHOUT spending a credit, so one credit
        # buys one unit of real work regardless of nesting depth.  A
        # non-stepping pause still holds here — the budget check is what
        # differentiates the two.
        if not chargeable:
            _run = ctx.storage.get(ctx.run_id)
            if _run and (_run.step_budget or 0) > 0:
                break
            # No credit outstanding: fall through and hold as an
            # ordinary pause.
        if chargeable and ctx.storage.consume_step(ctx.run_id):
            # Credit spent: cross this boundary.  pause_requested stays
            # set, so the next boundary holds again.  Emit a distinct
            # event rather than run_resumed — the run is still held, and
            # the frontend needs to tell "advanced one block" apart from
            # "released to completion".
            await _emit(ctx, {
                "type": "run_stepped", "run_id": ctx.run_id,
                "at": time.time(),
            })
            break
        if not notified:
            ctx.storage.update_status(ctx.run_id, "paused")
            await _emit(ctx, {
                "type": "run_paused", "run_id": ctx.run_id, "at": time.time(),
            })
            notified = True
        await asyncio.sleep(0.4)
    if notified:
        ctx.storage.update_status(ctx.run_id, "running")
        await _emit(ctx, {
            "type": "run_resumed", "run_id": ctx.run_id, "at": time.time(),
        })


async def _mark_block_status(
    ctx: "ExecutionContext", block: Block, status: str,
    error: Optional[str] = None,
    artifact: Optional[Artifact] = None,
) -> None:
    """Record a block's lifecycle transition for the run map.

    Always emits a ``block_status`` event (cheap; drives the live run
    map).  Persists to ``run.block_states`` only for structural blocks
    — those NOT inside an active loop iteration (``binding_stack``
    empty) — so a 10,000-iteration loop doesn't rewrite the run file
    twice per inner block per iteration.  Inner-block state is
    live-only; the loop block itself plus its iteration summaries
    carry the durable record.
    """
    if not block.id:
        return
    event: Dict[str, Any] = {
        "type": "block_status",
        "block_id": block.id,
        "block_type": block.block_type,
        "status": status,
        "at": time.time(),
    }
    if error:
        event["error"] = error[:500]
    await _emit(ctx, event)
    if ctx.storage is not None and not ctx.binding_stack:
        try:
            ctx.storage.update_block_status(
                ctx.run_id, block.id, status, error=error, artifact=artifact,
            )
        except Exception as exc:
            logger.debug(f"update_block_status failed (non-fatal): {exc}")


async def execute_block(block: Block, ctx: ExecutionContext) -> Artifact:
    """Execute any block — dispatcher over block_type.

    Any block (leaf or container) may carry its own ``scope``, which
    applies additively to itself and its entire subtree.  It is pushed
    onto ``ctx.scope_stack`` for the duration of this call so a leaf
    Task anywhere beneath it sees deck scope + card scope + every
    ancestor's scope + its own, merged additively (see
    ExecutionContext.effective_scope / app.models.task_card.merge_scopes).
    """
    # ---- Resume-from-block gate ----
    # While walking the tree ahead of the resume target, blocks are not
    # executed; their persisted artifacts are replayed so that the
    # resumed blocks still see prior deck state via {{sibling("id")}}
    # and {{previous_sibling}}.  Sits ahead of the "running" status
    # write below so a replayed block never reports as running.
    if ctx.resume_skipping:
        if block.id and block.id == ctx.resume_from_block_id:
            # Target reached — everything from here on executes for real.
            ctx.resume_skipping = False
        elif block.block_type in ("state", "ask"):
            # Deliberately re-executed while skipping: _execute_state only
            # writes authored literals into ctx.variables/context_notes,
            # and those two stores are the run-scoped state that is not
            # persisted anywhere.  Re-running them is how {{var.NAME}} is
            # rebuilt without a variables snapshot on disk.
            #
            # An Ask re-executes for the same reason, and is safe to because
            # its ANSWER is persisted: re-running it re-applies the recorded
            # answer and returns without asking again.  Replaying its
            # artifact instead would leave {{var.NAME}} and its standing
            # context note unset for every block after it — exactly the
            # failure the state branch exists to prevent.
            pass
        elif block.block_type == "call" and block.id in ctx.resume_call_chain:
            # A Call on the path to the target.  Descend: _execute_call
            # resolves the callee and dispatches its REAL tree, whose ids
            # are the ids the target and the replay artifacts are keyed by
            # (task_call._resolve_card returns the callee card's own root),
            # so the gate then applies inside the callee exactly as it does
            # in the caller.  This cannot be inferred from the tree — a
            # Call's body is empty — which is why the chain is passed in.
            pass
        elif _subtree_contains_any(block, ctx.resume_descend_ids()):
            # A container on the path to the target: descend so the inner
            # sequence keeps skipping its own earlier children.  Only
            # reachable for group/root containers, because loop-body
            # blocks have no persisted state and are therefore not valid
            # targets (see resume_from_block_id).
            pass
        else:
            return await _replay_artifact(block, ctx)

    ctx.scope_stack.append(block.scope)
    await _mark_block_status(ctx, block, "running")
    try:
        if block.block_type == "task":
            effective = _apply_templating_to_task(block, ctx)
            merged_scope = ctx.effective_scope()
            if merged_scope is not effective.scope:
                effective = effective.model_copy(update={"scope": merged_scope})
            artifact = await execute_task_block(
                effective,
                project_root=ctx.project_root,
                project_id=ctx.project_id,
                run_id=ctx.run_id,
            )
        elif block.block_type == "repeat":
            artifact = await _maybe_self_improve(block, ctx, _execute_repeat)
        elif block.block_type == "parallel":
            artifact = await _maybe_self_improve(block, ctx, _execute_parallel)
        elif block.block_type == "until":
            artifact = await _maybe_self_improve(block, ctx, _execute_until)
        elif block.block_type == "schedule":
            artifact = await _execute_schedule_passthrough(block, ctx)
        elif block.block_type == "state":
            artifact = await _execute_state(block, ctx)
        elif block.block_type == "ask":
            artifact = await _execute_ask(block, ctx)
        elif block.block_type == "call":
            artifact = await _execute_call(block, ctx)
        elif block.block_type == "group":
            artifact = await _maybe_self_improve(block, ctx, _execute_group)
        else:
            raise TaskExecutorError(f"Unknown block_type: {block.block_type!r}")
    except BlockExecutionCancelled:
        await _mark_block_status(ctx, block, "cancelled")
        raise
    except Exception as exc:
        # An infra fault marks the block "held", not "failed": the two ask
        # for different responses, and writing "failed" here made the
        # faulting block indistinguishable from a genuine work failure in
        # the run map — so the only way to find where a fan-out collapsed
        # was to open every subagent.
        await _mark_block_status(
            ctx, block,
            "held" if getattr(exc, "infra_kind", "") else "failed",
            error=str(exc),
        )
        raise
    finally:
        ctx.scope_stack.pop()
    # Terminal status: an artifact that reports failure marks the block
    # failed even though no exception escaped (a failed leaf task, or a
    # stopped sequence whose failure propagated up).
    await _mark_block_status(
        ctx, block, "failed" if artifact.failed else "done", artifact=artifact,
    )
    # Register the completed artifact by block id for {{sibling("id")}}
    # lookups by later blocks.  Skip blocks with no id (shouldn't happen
    # post-_assign_block_ids, but guard so a stray empty id can't clobber
    # the registry under the "" key).  Last-write-wins for loop re-runs.
    if block.id:
        ctx.artifact_registry[block.id] = artifact
    return artifact


def _call_failure(summary: str) -> Artifact:
    """A failed artifact for a call that never ran.

    Not an exception: an unresolvable or cyclic call is an authoring
    defect, and returning a failed artifact routes it through the same
    ``on_failure`` policy as a task that failed — so ``stop`` halts the
    deck and ``continue`` records the defect and moves on, rather than
    tearing down the whole run either way.
    """
    logger.warning("📞 CALL: %s", summary)
    return Artifact(summary=summary, failed=True, created_at=time.time())


def _sequence_child_failure(child: Block, exc: Exception) -> Artifact:
    """A failed artifact standing in for a sibling that raised.

    ``on_failure`` is documented in terms of "the first child whose
    artifact is failed" (design/task-cards.md §Failure policy), so a
    child that RAISES has no artifact for the policy to inspect and
    formerly bypassed it entirely: the exception unwound every
    enclosing sequence to the run boundary, making ``stop`` and
    ``continue`` behave identically -- the run died either way -- while
    remaining siblings went unmarked and the outputs already
    accumulated from earlier siblings were discarded along with the
    artifact that was never returned.

    Converting here restores that contract and matches the two
    containers that already do this: ``_execute_parallel`` records a
    failed child and ``_execute_repeat`` a failed iteration, each
    re-raising ONLY infra faults.

    Deliberately NOT done inside ``execute_block``: the root callers
    (api.task_cards, cli_card_runner, task_scheduler) read an escaping
    TaskExecutorError to populate the run's ``error`` field, so
    swallowing it there would blank that field for every root-level
    task.  ``execute_block``'s own handler has already written this
    child's block_state as "failed" with this error before re-raising,
    so what this adds is the sequence-level view, not the record.
    """
    err = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    label = child.name or child.id or child.block_type
    logger.warning(
        "⛔ SEQUENCE: child %s raised %s -- converted to a failed "
        "artifact so on_failure governs: %s",
        label, type(exc).__name__, err,
    )
    return Artifact(
        summary=f"{label} raised {type(exc).__name__}: {err}",
        decisions=[f"child {label!r} raised, not returned: {err}"],
        failed=True,
        created_at=time.time(),
    )


def _seed_callee_block_states(ctx: "ExecutionContext", root: Block) -> None:
    """Register the callee subtree in the run's ``block_states``.

    ``update_block_status`` updates in place and returns early when the
    block has no existing state, and launch-time seeding only walked the
    CALLER's tree — so without this every callee block's status write is
    silently dropped and the run record shows a call that produced an
    artifact from nothing.

    Gated on an empty ``binding_stack`` for the same reason
    ``_mark_block_status`` is: inside a loop iteration this would rewrite
    the run file once per callee block per iteration.
    """
    if ctx.storage is None or ctx.binding_stack:
        return
    # State this run already holds for the callee's blocks.  Seeding blind
    # is destructive: set_block_state REPLACES the whole state object, so
    # a loop inside the callee loses the iteration_summaries a resume
    # installed for it — the records that tell the NEXT resume which
    # iterations are already banked.
    try:
        _run = ctx.storage.get(ctx.run_id)
        _existing = dict((_run.block_states or {}) if _run else {})
    except Exception:  # noqa: BLE001 — bookkeeping only
        _existing = {}
    stack = [root]
    while stack:
        node = stack.pop()
        if node.id:
            try:
                _prior = _existing.get(node.id)
                ctx.storage.set_block_state(ctx.run_id, TaskRunBlockState(
                    block_id=node.id,
                    block_type=node.block_type,
                    status="queued",
                    iteration_summaries=list(
                        getattr(_prior, "iteration_summaries", None) or []
                    ),
                ))
            except Exception as exc:  # noqa: BLE001 — bookkeeping only
                logger.debug(f"seed callee block state failed: {exc}")
        stack.extend(node.body or [])


def _record_call_audit(ctx: "ExecutionContext", block: Block, resolved) -> None:
    """Persist the resolved callee tree and its effective block scopes.

    Two consumers, one write:

    * the run map, which cannot draw the callee's rows without its tree
      (the callee is named, not inlined, so it is in neither the card nor
      ``card_snapshot``);
    * ``run_outcome.summarize_side_effects``, which answers "did this run
      change my workspace?" by intersecting ``block_states`` with
      ``permissions_snapshot.block_scopes``.  The callee's blocks were
      already in the former (``_seed_callee_block_states``) but absent
      from the latter, so a callee task holding a write grant intersected
      to nothing and the banner reported NO hazard — an actively wrong
      "nothing changed", not merely a missing row.

    Scopes are computed from the CALLEE's own hierarchy — deck scope plus
    the callee's card scope — mirroring the isolation ``_execute_call``
    enforces at run time.  Recording the caller's would describe
    permissions the callee never had.

    Best-effort: an audit-trail failure must not abort the work, matching
    how the launch-time capture is treated.
    """
    if ctx.storage is None:
        return
    via = {
        "call_block_id": block.id,
        "target": resolved.label,
        "kind": resolved.kind,
    }
    try:
        from ..utils.permissions_snapshot import (
            build_block_scopes, synthesize_grant_scope,
        )
        if resolved.shell_grants or resolved.writable_grants:
            # File-task callee: its grants are raw lists, never a
            # TaskScope, so there is no scope tree to walk.
            scopes = synthesize_grant_scope(
                resolved.root,
                shell_commands=resolved.shell_grants,
                write_patterns=resolved.writable_grants,
                via_call=via,
            )
        else:
            scopes = build_block_scopes(
                resolved.root,
                deck_scope=ctx.deck_scope,
                card_scope=resolved.card_scope,
                via_call=via,
            )
        ctx.storage.record_call(ctx.run_id, block.id, {
            **via,
            "key": resolved.key,
            "root": resolved.root.model_dump(),
        }, block_scopes=scopes)
    except Exception as exc:  # noqa: BLE001 — audit must not break a run
        logger.warning(f"📞 CALL: audit record failed (non-fatal): {exc}")


async def _execute_call(block: Block, ctx: ExecutionContext) -> Artifact:
    """Run a named card or file task inline, as this block's work.

    **Permissions do not cross the boundary.** The caller's ``card_scope``
    and ancestor ``scope_stack`` are swapped out for the callee's own for
    the duration.  This is load-bearing rather than merely tidy: a leaf
    task's escalation is authorized by hashing its FULL merged scope
    (``authorize_scope`` ← ``ExecutionContext.effective_scope``), so any
    caller grant left in the merge would change the hash away from what
    was signed for the callee and demote an approved callee to the floor.
    Isolation is therefore what makes "the callee's own approval governs"
    actually work, and it simultaneously closes the laundering path where
    a caller the agent may freely author confers its grants on a callee.

    ``deck_scope`` is deliberately NOT reset: it is the project-wide
    baseline, both cards live in the same project, and it is already part
    of every hash signed on either side.

    Run-scoped STATE does flow in — ``variables``, ``overrides``,
    ``context_notes``, ``sibling_stack`` and ``binding_stack`` are shared.
    For an unparameterized call this is the only channel by which a callee
    can learn anything about its invocation, and those stores are
    run-scoped by design.  Sharing ``binding_stack`` in particular keeps
    the loop-persistence guard in ``_mark_block_status`` honest: clearing
    it would make a call inside a 10,000-iteration loop persist every
    callee block on every pass.
    """
    from .task_call import CallResolutionError, resolve_call_target

    if len(ctx.call_stack) >= MAX_CALL_DEPTH:
        return _call_failure(
            f"call depth limit reached ({MAX_CALL_DEPTH}); refusing to call "
            f"{block.call_target!r} from {' → '.join(ctx.call_stack)}"
        )

    try:
        resolved = resolve_call_target(
            block.call_target or "",
            block.call_target_kind,
            project_id=ctx.project_id,
            project_root=ctx.project_root,
        )
    except CallResolutionError as exc:
        return _call_failure(f"call could not be resolved: {exc}")

    if resolved.key in ctx.call_stack:
        # Depth alone would not catch this: a two-node cycle recurses
        # forever only in the sense that it burns the whole depth budget
        # on the same work, which is expensive and never terminates
        # usefully.  Name the cycle so the author can see it.
        cycle = " → ".join([*ctx.call_stack, resolved.key])
        return _call_failure(f"call cycle detected: {cycle}")

    await _emit(ctx, {
        "type": "call_resolved",
        "block_id": block.id,
        "target_kind": resolved.kind,
        "target": resolved.label,
        "target_key": resolved.key,
        "depth": len(ctx.call_stack) + 1,
        "at": time.time(),
    })

    _seed_callee_block_states(ctx, resolved.root)
    _record_call_audit(ctx, block, resolved)

    saved_stack = ctx.scope_stack
    saved_card_scope = ctx.card_scope
    ctx.scope_stack = []
    ctx.card_scope = resolved.card_scope
    ctx.call_stack.append(resolved.key)
    # Display counterpart of the key stack (see ExecutionContext.call_labels).
    # Pushed and popped in lockstep so the two can never disagree about depth.
    ctx.call_labels.append(resolved.label or resolved.key)
    try:
        artifact = await _run_callee(resolved, ctx)
    finally:
        ctx.call_stack.pop()
        ctx.call_labels.pop()
        # Restore the SAME list object the enclosing execute_block pushed
        # onto, so its own ``finally: ctx.scope_stack.pop()`` still pops
        # the frame it owns.
        ctx.scope_stack = saved_stack
        ctx.card_scope = saved_card_scope

    # Provenance goes in ``decisions``, never in ``summary``: a caller may
    # be a Repeat whose ``repeat_until`` substring-matches the summary, so
    # prefixing it would silently change the caller's loop condition.
    notes = [f"called {resolved.kind} {resolved.label!r}", *resolved.notes]
    return artifact.model_copy(update={
        "decisions": [*(artifact.decisions or []), *notes],
    })


async def _run_callee(resolved, ctx: ExecutionContext) -> Artifact:
    """Execute a resolved callee, activating any pre-authorized grants.

    Card callees carry no pre-authorized grants (their blocks authorize
    individually downstream), so this is a plain dispatch.  A file task's
    ``allow`` was authorized against the CLI ledger during resolution and
    is handed to ``execute_task_block`` explicitly.
    """
    if not resolved.shell_grants and not resolved.writable_grants:
        return await execute_block(resolved.root, ctx)
    return await execute_task_block(
        resolved.root,
        project_root=ctx.project_root,
        project_id=ctx.project_id,
        run_id=ctx.run_id,
        pre_authorized_shell_commands=resolved.shell_grants,
        pre_authorized_writable=resolved.writable_grants,
    )


def _subtree_contains(block: Block, target_id: Optional[str]) -> bool:
    """True if ``target_id`` names ``block`` or any block beneath it.

    Used by the resume gate to tell a container that must be descended
    into (it encloses the target) from one that can be replayed whole.
    """
    if not target_id:
        return False
    if block.id == target_id:
        return True
    for child in (block.body or []):
        if _subtree_contains(child, target_id):
            return True
    return False


def _subtree_contains_any(
    block: Block, target_ids: List[str],
) -> bool:
    """True if ``block``'s subtree holds ANY of ``target_ids``.

    The resume gate must descend into a container that encloses the target
    OR the next Call on the way to it.  Those are different ids living at
    different depths — the caller's root encloses ``call-p1`` but not the
    callee block inside it — so a single-id test made the root replay and
    the resume execute nothing at all.
    """
    for target_id in target_ids:
        if _subtree_contains(block, target_id):
            return True
    return False


async def _replay_artifact(block: Block, ctx: ExecutionContext) -> Artifact:
    """Return a block's artifact from the resumed run instead of running it.

    Registers it under the block id so later {{sibling("id")}} lookups
    resolve exactly as they did in the original run.  The caller
    (_execute_sequence) threads the return value into sibling_stack, so
    {{previous_sibling}} works too.

    The replayed artifact is force-cleared of ``failed`` before being
    returned.  This is load-bearing, not cosmetic: _execute_sequence's
    on_failure="stop" policy halts at the first child whose artifact is
    failed, so replaying a genuinely-failed artifact from the source run
    would break the sequence BEFORE the resume target was ever reached
    and resume would silently do nothing — the exact deck shape most
    likely to be resumed (one that stopped on a failure) was the one it
    could not handle.  The original summary is preserved so the prior
    failure is still visible to the operator and to later blocks; only
    the control-flow flag is dropped, because "this failed earlier" must
    not re-trigger a stop the operator is explicitly retrying past.

    A block with no persisted artifact never completed in the source run
    (queued/failed/cancelled).  There is nothing to replay, so a marker
    artifact stands in, likewise not failed.
    """
    replayed = ctx.resume_artifacts.get(block.id or "")
    if replayed is None:
        artifact = Artifact(
            summary=f"(skipped on resume: no recorded result for {block.id})",
            created_at=time.time(),
        )
    elif replayed.failed:
        artifact = replayed.model_copy(update={"failed": False})
    else:
        artifact = replayed
    if block.id:
        ctx.artifact_registry[block.id] = artifact
    # Record the replay in the new run's map.  Without this the block
    # stays at the "queued" value seeded at launch, so a resumed run
    # renders as though its earlier blocks never happened — losing the
    # prior deck state the resume exists to preserve.  "skipped" is the
    # honest status: this run did not execute the block.
    await _mark_block_status(ctx, block, "skipped", artifact=artifact)
    return artifact


def _until_condition_met(
    block: Block, artifact: Artifact,
) -> bool:
    """Decide whether a Repeat-until loop should terminate after
    producing this artifact.

    Two modes:

    - **Declarative** — when ``block.repeat_until`` is a non-empty
      string, the loop terminates when that substring appears
      (case-insensitive) in ``artifact.summary`` AND the artifact did
      not fail.  This covers "retry until the model says DONE" flows.

    - **Implicit** — when ``block.repeat_until`` is empty/None, the
      loop terminates on the first non-failed iteration.  This is
      the original behaviour before declarative conditions landed and
      remains useful for plain retry-until-success loops.

    In both modes, ``repeat_max`` upper-bounds iteration count (see
    ``_plan_iterations``), so a never-matching condition won't hang.
    """
    if artifact.failed:
        return False
    cond = (block.repeat_until or "").strip()
    if not cond:
        # Implicit: stop on first non-failed iteration.
        return True
    # Declarative: substring match against the summary.
    return cond.lower() in (artifact.summary or "").lower()


def _build_iteration_context(bindings: "task_templating.IterationBindings") -> str:
    """Build a plain-language context block describing prior iteration
    state for the model.  Prepended to a Task's instructions inside
    Repeat/Until so users can write "use the last result" in plain
    English without knowing about Mustache templating.

    Returns the empty string when there's nothing useful to surface
    (e.g. parallel count-mode iterations, where bindings carry only
    'index' -- not informative on its own).
    """
    has_previous = bindings.previous is not None
    has_item = bindings.item is not None
    has_history = bool(bindings.all_summaries)
    if not (has_previous or has_item or has_history):
        return ""
    lines = ["[Iteration context -- automatically provided to help your task]"]
    lines.append(f"- Iteration number: {bindings.index}")
    if has_item:
        lines.append(f"- Current item: {bindings.item}")
    if has_previous:
        prev = (bindings.previous.summary or "").strip()
        if prev:
            lines.append(f"- Previous iteration produced: {prev}")
    if has_history:
        prior = [s.strip() for s in bindings.all_summaries if s.strip()]
        if prior:
            # Cap at 10 to keep the context block bounded on long Repeats.
            shown = prior[-10:]
            ellipsis = " ..." if len(prior) > 10 else ""
            lines.append(
                f"- All prior results (oldest->newest):{ellipsis} "
                + " | ".join(shown)
            )
    return "\n".join(lines)


def _build_state_context(
    ctx: "ExecutionContext",
    bindings: Optional[task_templating.IterationBindings] = None,
) -> str:
    """Build the standing-context preamble from State-block prose.

    This is the conversational baseline: freeform givens authored in a
    State block's ``state_context`` flow into the task here, without the
    author needing any {{var}} templating.  Multiple State blocks'
    notes are joined in insertion order.  Returns empty string when no
    prose givens are active.

    Prose is rendered against ``bindings`` when supplied.  Templating is
    not REQUIRED in prose, but it must WORK there: authors reasonably
    write "DEPLOY_COMMAND: {{var.DEPLOY_COMMAND}}" as a given, and the
    previous verbatim-only handling passed the braces through to the
    agent unresolved.  Because unknown placeholders are preserved by
    ``render``, prose with no resolvable placeholder is byte-identical
    to the old behaviour.
    """
    notes = [n.strip() for n in ctx.context_notes.values() if n and n.strip()]
    if not notes:
        return ""
    body = "\n\n".join(notes)
    if bindings is not None:
        body = task_templating.render(body, bindings)
    return f"[Assumptions and context for this task -- treat these as given]\n{body}"


def _build_sibling_context(ctx: "ExecutionContext") -> str:
    """Build a standing-context preamble from the prior sibling's result.

    Mirrors the iteration-context and State-prose auto-injection: a task
    that follows another block in a sequence (e.g. "print the final
    count" after an Until loop) sees the prior sibling's summary without
    needing any {{previous_sibling}} templating.  Reads the top of the
    sibling stack (the current sequence depth); empty when this is the
    first sibling or there is no enclosing sequence.
    """
    if not ctx.sibling_stack:
        return ""
    prev = ctx.sibling_stack[-1]
    if prev is None:
        return ""
    summary = (prev.summary or "").strip()
    if not summary:
        return ""
    return (
        "[Result of the previous step -- automatically provided]\n"
        f"{summary}"
    )


# Placeholders whose value is a PRIOR BLOCK'S RESULT.  Their honest
# rendering when no such result exists is the empty string, so they must
# be rendered even on a run where nothing has completed yet — unlike
# {{index}}, which resolves to "0" against default bindings and would
# assert an iteration that never happened.
#
# That asymmetry is why _apply_templating_to_task cannot simply always
# render: the early-return below is what keeps loop-scoped placeholders
# literal outside a loop.  Narrowing it rather than removing it
# preserves that, while fixing the first-block case where
# {{sibling("x")}} and {{previous_sibling}} were handed to the model as
# raw template text — the registry is empty and no sibling has completed
# when the FIRST block of a run renders, which is precisely the state the
# guard treated as "nothing to substitute".
_SEQUENCE_PLACEHOLDER_RE = re.compile(
    r"\{\{\s*(?:sibling\(|previous_sibling\b)"
)


def _references_sequence_placeholder(instructions: Optional[str]) -> bool:
    """True if ``instructions`` reference a prior block's result."""
    if not instructions or "{{" not in instructions:
        return False
    return bool(_SEQUENCE_PLACEHOLDER_RE.search(instructions))


def _apply_templating_to_task(block: Block, ctx: ExecutionContext) -> Block:
    """Return a shallow copy of the task block with instructions rendered
    against the innermost active iteration bindings, then prepended with
    an auto-generated iteration-context block so prior results are
    surfaced to the model without requiring explicit templating.
    Renders when either a Repeat/Until is active (iteration bindings) or
    run-scoped State variables exist — a top-level task with no loop can
    still reference {{var.NAME}}.  Returns the block unchanged when
    neither applies or nothing changed."""
    if not block.instructions:
        return block
    sibling_prev = ctx.sibling_stack[-1] if ctx.sibling_stack else None
    if (not ctx.binding_stack and not ctx.variables and not ctx.overrides
            and not ctx.context_notes and sibling_prev is None
            and not ctx.artifact_registry
            and not _references_sequence_placeholder(block.instructions)):
        return block
    base = ctx.binding_stack[-1] if ctx.binding_stack else task_templating.IterationBindings()
    # Merge run-scoped variables with launch-time overrides (overrides
    # win) and attach without mutating the stacked binding.  Empty merge
    # leaves the binding untouched.
    merged = {**ctx.variables, **ctx.overrides}
    # Attach merged vars and the prior-sibling artifact for templating.
    _updates = {}
    if merged:
        _updates["variables"] = merged
    if sibling_prev is not None:
        _updates["previous_sibling"] = sibling_prev
    if ctx.artifact_registry:
        _updates["sibling_artifacts"] = ctx.artifact_registry
    bindings = replace(base, **_updates) if _updates else base
    rendered = task_templating.render(block.instructions, bindings)
    # Assemble preambles, prose givens first (the conversational
    # baseline), then the auto iteration-context (loop-only).  Both are
    # standing context the task receives without needing templating —
    # but prose is rendered against the same bindings as the
    # instructions, so a {{var.X}} given resolves rather than reaching
    # the agent as literal braces.
    preambles: List[str] = []
    state_ctx = _build_state_context(ctx, bindings)
    if state_ctx:
        preambles.append(state_ctx)
    sibling_ctx = _build_sibling_context(ctx)
    if sibling_ctx:
        preambles.append(sibling_ctx)
    iter_ctx = _build_iteration_context(bindings) if ctx.binding_stack else ""
    if iter_ctx:
        preambles.append(iter_ctx)
    if not preambles and rendered == block.instructions:
        return block
    final = "\n\n".join(preambles + [rendered]) if preambles else rendered
    return block.model_copy(update={"instructions": final})


async def _execute_sequence(
    blocks: List[Block], ctx: ExecutionContext,
    on_failure: str = "continue",
) -> Artifact:
    """Implicit sequence: run top-to-bottom.  Cancel is checked between
    siblings.

    The returned artifact is the LAST block's artifact (per
    design/task-cards.md §Runtime semantics) with one deliberate
    deviation: ``outputs`` and ``decisions`` accumulate across every
    sibling instead of being discarded with the earlier artifacts.

    Rationale: an artifact's ``summary`` is a return value — last-wins
    is right for it.  But ``outputs`` are declared durable deliverables
    (emit_artifact parts: frozen renders, files, findings), and
    ``decisions`` are an audit trail.  Returning only the last
    sibling's silently dropped every earlier step's work: a Group root
    whose stages each emitted artifacts reported ``outputs=[]`` at the
    run level while the per-iteration records held them correctly.

    Threads each completed sibling's artifact into ctx.sibling_stack so
    the next sibling can see it (prose auto-context + {{previous_sibling}}).
    Pushes a fresh slot for this depth and pops it on exit so a nested
    sequence never leaks its last sibling to the enclosing one.

    ``on_failure`` is the enclosing container's failure policy:
    - "continue" (default, legacy) — every sibling runs regardless of
      prior failures; a failed artifact flows onward as
      {{previous_sibling}}.
    - "stop" — halt at the first child whose artifact is failed.  That
      artifact (annotated with a skip note) becomes the sequence's
      result, so the failure propagates upward instead of silently
      feeding failed input into later stages.
    """
    if not blocks:
        return Artifact(summary="", created_at=time.time())
    last: Optional[Artifact] = None
    # Accumulated across siblings; folded into the returned artifact.
    # Kept separate from ``last`` so the stop-path model_copy below
    # cannot clobber them.
    acc_outputs: List[ArtifactPart] = []
    acc_decisions: List[str] = []
    ctx.sibling_stack.append(None)
    try:
        for i, child in enumerate(blocks):
            # Gate every child, including the first.  The former ``i > 0``
            # guard assumed the caller had just checked, which holds for a
            # pause arriving mid-sequence but not for step-debug: a step
            # granted while the executor sits at a boundary would cross
            # that boundary AND run the first child of the sequence it
            # then entered, advancing two blocks per credit.  Gating i==0
            # costs one extra flag read per sequence when not paused
            # (``pause_requested`` short-circuits before any sleep), and
            # makes one credit mean exactly one block.
            # Container children pass free while stepping (see
            # _is_step_boundary) so descending into a loop or group does
            # not consume the credit meant for the work inside it.
            #
            # A block that is about to be REPLAYED rather than executed
            # (resume-from-block, see the gate at the top of
            # execute_block) is likewise free.  That gate lives inside
            # execute_block, which runs after this hold, so without this
            # check a stepped resume spends its credits replaying
            # already-finished blocks and appears to do nothing —
            # measured as 2 credits buying 0 units of work on a
            # resume@b3 deck.
            _free = ctx.resume_skipping and child.id != ctx.resume_from_block_id
            await _wait_if_paused(
                ctx, chargeable=_is_step_boundary(child) and not _free)
            if ctx.cancel_requested():
                raise BlockExecutionCancelled()
            try:
                last = await execute_block(child, ctx)
            except BlockExecutionCancelled:
                # A stop request is not a verdict on the work: it must
                # keep unwinding so the run records "cancelled".
                raise
            except Exception as exc:
                # An infra fault likewise keeps unwinding:
                # api.task_cards reads ``infra_kind`` off the live
                # exception to mark the run "held" -- which preserves
                # the resume position -- instead of "failed", so
                # converting one here would cost the run exactly what
                # a hold exists to protect.  Only a failure OF THE
                # WORK is convertible.  BaseException (the infra
                # gate's CancelledError, KeyboardInterrupt) is not
                # caught at all.
                if getattr(exc, "infra_kind", ""):
                    raise
                last = _sequence_child_failure(child, exc)
            acc_outputs.extend(last.outputs or [])
            acc_decisions.extend(last.decisions or [])
            # Make this sibling's result visible to the next sibling.
            ctx.sibling_stack[-1] = last
            if on_failure == "stop" and last.failed and i < len(blocks) - 1:
                skipped = len(blocks) - 1 - i
                label = child.name or child.id or child.block_type
                acc_decisions.append(
                    f"sequence stopped: step {i + 1}/{len(blocks)} "
                    f"({label}) failed; {skipped} remaining step(s) "
                    f"skipped (on_failure=stop)"
                )
                last = last.model_copy(update={"decisions": list(acc_decisions)})
                ctx.sibling_stack[-1] = last
                # Mark never-run siblings as skipped so the run map can
                # distinguish them from queued/failed blocks.
                for rest in blocks[i + 1:]:
                    await _mark_block_status(ctx, rest, "skipped")
                break
    finally:
        ctx.sibling_stack.pop()
    assert last is not None
    # Fold the accumulated deliverables onto the last sibling's
    # artifact.  ``summary``/``failed``/``signature`` stay last-wins.
    return last.model_copy(update={
        "outputs": acc_outputs,
        "decisions": acc_decisions,
    })


async def _execute_parallel(
    block: Block, ctx: ExecutionContext,
) -> Artifact:
    """Run all body blocks concurrently.  Returns a composite Artifact
    whose outputs are the children's outputs concatenated in order."""
    if not block.body:
        return Artifact(summary="(empty parallel block)", created_at=time.time())
    start = time.time()
    # Bound concurrency: a Parallel block with many model-invoking
    # children hits the same provider rate limit as a parallel Repeat.
    limit = _resolve_concurrency(
        getattr(block, "repeat_max_concurrency", None), len(block.body),
    )
    gate = _concurrency_gate(limit, len(block.body))
    if limit and len(block.body) > limit:
        logger.info(
            f"⛓️ PARALLEL: {block.id} running {len(block.body)} children "
            f"at concurrency {limit}"
        )

    async def _gated(child: Block) -> Artifact:
        async with gate:
            return await execute_block(child, ctx)

    children = await asyncio.gather(
        *[_gated(c) for c in block.body],
        return_exceptions=True,
    )
    # Infra faults propagate rather than becoming a failed child: see the
    # matching guard in _execute_repeat.  A held run keeps its resume
    # position; a failed one does not, and the fault is not a verdict on
    # any child's work.
    for result in children:
        if isinstance(result, BaseException) and getattr(result, "infra_kind", ""):
            logger.warning(
                f"⏸️ PARALLEL: {block.id} aborting — infra fault "
                f"({getattr(result, 'infra_kind', '')}) in one child"
            )
            raise result
    outputs: List[ArtifactPart] = []
    decisions: List[str] = []
    any_failed = False
    for idx, result in enumerate(children):
        if isinstance(result, BaseException):
            any_failed = True
            decisions.append(f"child[{idx}] failed: {result}")
            continue
        if result.failed:
            any_failed = True
        outputs.extend(result.outputs)
        decisions.extend(result.decisions)
    elapsed_ms = int((time.time() - start) * 1000)
    summary = f"Parallel of {len(block.body)} child block(s)"
    return Artifact(
        summary=summary,
        decisions=decisions,
        outputs=outputs,
        duration_ms=elapsed_ms,
        created_at=time.time(),
        failed=any_failed,
    )


async def _execute_repeat(
    block: Block, ctx: ExecutionContext,
) -> Artifact:
    """Execute a Repeat block in its declared mode.  One iteration is
    one top-to-bottom pass of the body."""
    try:
        iterations = _plan_iterations(block, ctx)
    except ForEachSourceError as e:
        # An unresolvable templated fan-out source is an authoring or
        # upstream-output defect, not a crash: return a failed artifact
        # so the enclosing container's on_failure policy decides whether
        # the run stops, exactly as an unresolvable Call target does.
        summary = f"for_each source did not resolve - 0 iterations run. {e}"
        logger.warning("🔁 REPEAT: %s", summary)
        return Artifact(
            summary=summary,
            decisions=[f"for_each source unresolved for block {block.id!r}"],
            failed=True,
            created_at=time.time(),
        )
    except RosterAssertionError as e:
        # The roster cannot satisfy its own completeness assertion — a
        # finite cap contradicting repeat_require_complete, or members
        # that cannot be uniquely keyed.  Refused before ANY spend, as a
        # failed block rather than a crash, for the same reason as
        # ForEachSourceError above.
        summary = f"for_each roster refused - 0 iterations run. {e}"
        logger.warning("🔁 REPEAT: %s", summary)
        return Artifact(
            summary=summary,
            decisions=[
                f"repeat_require_complete refused the roster for "
                f"block {block.id!r}"
            ],
            failed=True,
            created_at=time.time(),
        )
    if not iterations:
        # An EMPTY resolved list is legitimate (a planner that found
        # nothing to do), unlike an unresolvable source above.
        return Artifact(summary="(repeat with 0 iterations)", created_at=time.time())

    start = time.time()
    propagate = block.repeat_propagate or "none"
    prior_summaries: List[str] = []
    last_artifact: Optional[Artifact] = None
    outputs: List[ArtifactPart] = []
    # Terminal outcome per iteration index ("passed" / "failed" /
    # "cancelled"), fed by every path that concludes an iteration —
    # executed, replayed, and synthesized-failure alike.  The roster
    # completeness assertion at loop exit diffs the planned keys against
    # this rather than re-reading storage.
    iter_outcomes: Dict[int, str] = {}

    # Persist the roster size for for_each loops before announcing the
    # block: the run map renders loop progress as "n/m", and for_each is
    # the one mode whose denominator exists only at run time (count is
    # readable from the card; until's max is a ceiling, not a target, so
    # "3/10" there would misread a legitimate early stop as incomplete).
    # Written before the block_started emit so the refetch that event
    # triggers on the frontend already sees the value.
    if (
        ctx.storage is not None and block.id
        and (block.repeat_mode or "count") == "for_each"
    ):
        try:
            ctx.storage.set_block_planned_iterations(
                ctx.run_id, block.id, len(iterations),
            )
        except Exception as exc:
            logger.debug(
                f"set_block_planned_iterations failed (non-fatal): {exc}"
            )

    await _emit(ctx, {
        "type": "block_started",
        "block_id": block.id,
        "block_type": "repeat",
        "planned": len(iterations),
        "at": time.time(),
    })

    # Mid-loop resume: which iteration executes first.  Clamped to the
    # planned range so a stale index (a card edited to run fewer
    # iterations since the source run) cannot skip the loop entirely and
    # report it complete.
    resume_at = 0
    if (
        ctx.resume_from_iteration is not None
        and block.id
        and block.id == ctx.resume_from_block_id
    ):
        resume_at = max(0, min(int(ctx.resume_from_iteration), len(iterations)))
        if resume_at:
            logger.info(
                f"repeat {block.id} resuming at iteration {resume_at} "
                f"of {len(iterations)} ({resume_at} replayed)"
            )

    def _replay_iteration(index: int) -> Optional[Artifact]:
        """The recorded artifact for a skipped iteration, if retained.

        Cleared of ``failed`` for the same reason ``_replay_artifact``
        does it: on_failure="stop" would otherwise halt the loop at a
        replayed failure before reaching the iteration being retried.
        """
        got = ctx.resume_iteration_artifacts.get(index)
        if got is None:
            return None
        return got.model_copy(update={"failed": False}) if got.failed else got

    async def _run_one(index: int, item: Any = None,
                        previous: Optional[Artifact] = None,
                        all_prior: Optional[List[str]] = None,
                        item_key: Optional[str] = None) -> Artifact:
        await _emit(ctx, {
            "type": "iteration_started",
            "block_id": block.id, "index": index,
        })
        iter_start = time.time()
        bindings = task_templating.IterationBindings(
            index=index,
            item=item,
            previous=previous,
            all_summaries=list(all_prior or []),
        )
        # Concurrent iterations must not share the stores that describe
        # tree position.  A parallel fan-out interleaves at every await,
        # so pushing these bindings onto the shared context leaves every
        # iteration reading whichever sibling pushed last -- see
        # _IterationScope for the measured consequence.
        #
        # Serial loops keep the shared context deliberately: they cannot
        # interleave, so there is nothing to isolate, and copying would
        # change what a later iteration sees of an earlier one's inner
        # blocks -- a behaviour change unrelated to this defect.
        iter_scope = _IterationScope(ctx) if block.repeat_parallel else ctx
        iter_scope.binding_stack.append(bindings)
        # Stamp the iteration context so nested task_executor emissions
        # tag streaming deltas with the *iteration owner*'s block_id
        # (this repeat block) rather than the inner task block.  The
        # frontend reducer routes deltas by block_id; without this they
        # would land in a never-sealed phantom bucket keyed to the task
        # block id and every iteration's output would collapse into a
        # single "Iteration 0" in the Live and Tools tabs.
        iter_ctx_token = set_task_iteration_context(block.id, index)
        try:
            artifact = await _execute_sequence(
                block.body, iter_scope,
                on_failure=(block.on_failure or "continue"),
            )
        except BaseException as exc:
            # Record before propagating.  gather() surfaces only ONE
            # exception, so an aggregate assembled after it has already
            # lost the other N-1 — and the hold would report the first
            # subagent's fault as though it were the whole event.
            # Recording HERE is also what lets the concurrent watcher see
            # the fault while siblings are still running or still queued
            # behind the concurrency gate, which is the only point at
            # which cancelling them can still save work.
            ctx.record_infra_fault(exc, index=index)
            raise
        finally:
            if iter_scope is ctx:
                # Shared stack: undo the push.  A private scope is
                # discarded whole, so popping it would only mutate a
                # list nobody reads again.
                ctx.binding_stack.pop()
            reset_task_iteration_context(iter_ctx_token)
        # Seal timing if the body didn't.
        if not artifact.duration_ms:
            artifact.duration_ms = int((time.time() - iter_start) * 1000)
        await _record_iteration(block, ctx, index, artifact, item_key=item_key)
        iter_outcomes[index] = "failed" if artifact.failed else "passed"
        await _emit(ctx, {
            "type": "iteration_completed",
            "block_id": block.id, "index": index,
            "status": ("failed" if artifact.failed else "passed"),
            "signature": artifact.signature,
            "duration_ms": artifact.duration_ms,
            "tokens": artifact.tokens,
        })
        return artifact

    if block.repeat_parallel and block.repeat_mode in (None, "count", "for_each"):
        # Selective replay.  A parallel fan-out has no ordering, so a resume
        # cannot take a PREFIX of it — but it can skip the iterations that
        # already produced an artifact and execute only the ones that never
        # finished.  That is the entire value of resuming a wide fan-out: a
        # 20-agent audit that lost one subagent to an expired credential
        # re-runs one subagent, not twenty.
        #
        # Until now this branch built its task list from
        # ``range(len(iterations))`` unconditionally while only the serial
        # branch consulted the banked prefix, so the hold surface's promise
        # of preserved progress was broken for the one block shape most
        # likely to hold.  Keyed on MEMBERSHIP rather than a start index,
        # because ``previous=None`` here means index order carries no
        # dependency: a gap in the middle is filled, not re-run wholesale.
        _banked: Dict[int, Artifact] = {}
        if block.id and block.id == ctx.resume_from_block_id:
            _banked = {
                i: a for i, a in ctx.resume_iteration_artifacts.items()
                if a is not None and 0 <= i < len(iterations)
            }
        _todo = [i for i in range(len(iterations)) if i not in _banked]
        for _i in sorted(_banked):
            _art = _banked[_i]
            iter_outcomes[_i] = "passed"
            last_artifact = _art
            outputs.extend(_art.outputs)
            await _emit(ctx, {
                "type": "iteration_completed",
                "block_id": block.id, "index": _i,
                "status": "passed", "replayed": True,
                "duration_ms": 0, "tokens": 0,
            })
        if _banked:
            logger.info(
                f"🔁 REPEAT: {block.id} replaying {len(_banked)} banked "
                f"iteration(s), executing {len(_todo)} of {len(iterations)}"
            )
        # Denominator for the proportional infra gate.  Saved/restored so a
        # nested loop cannot leave a stale width behind for its parent.
        # Sized to what is actually DISPATCHED: gating against the card's
        # declared width would under-report a second collapse, since one
        # fault out of one executing iteration is fleet-wide while one out
        # of twenty is not.
        _saved_width = ctx.infra_fanout_width
        ctx.infra_fanout_width = len(_todo)
        ctx.infra_widest_fanout = max(
            ctx.infra_widest_fanout, len(_todo),
        )
        # Parallel iterations cannot see each other's outputs — propagation
        # is last/all relative to prior iterations, which is ill-defined
        # when everything runs concurrently.  Bindings still carry index
        # and item; previous/all are left empty.  The design doc treats
        # propagation as a sequential-loop feature.
        #
        # Concurrency is bounded (see _resolve_concurrency).  The gate is
        # acquired INSIDE _run_one rather than around create_task on
        # purpose: the cancel watcher below polls every entry of
        # ``pending`` and cancels the not-yet-done ones, so each iteration
        # must own a Task from the outset.  Gating dispatch instead would
        # leave queued iterations with no task to cancel and make a
        # cancelled 60-wide fan-out wait for the queue to drain.
        _limit = _resolve_concurrency(
            block.repeat_max_concurrency, len(_todo),
        )
        _gate = _concurrency_gate(_limit, len(_todo))
        if _limit and len(_todo) > _limit:
            logger.info(
                f"⛓️ REPEAT: {block.id} running {len(_todo)} parallel "
                f"iterations at concurrency {_limit}"
            )

        async def _run_one_gated(i: int) -> Artifact:
            async with _gate:
                return await _run_one(
                    i,
                    item=iterations[i].get("item"),
                    item_key=iterations[i].get("item_key"),
                    previous=None,
                    all_prior=None,
                )

        pending = [
            asyncio.create_task(_run_one_gated(i))
            for i in _todo
        ]
        # Poll cancel_requested while iterations run.  The serial path
        # checks between iterations; the parallel path has no natural
        # checkpoint, so without this a repeat_count=1000 parallel block
        # ignores cancellation until every task finishes.
        async def _watch_cancel() -> None:
            while any(not t.done() for t in pending):
                if ctx.cancel_requested():
                    for t in pending:
                        if not t.done():
                            t.cancel()
                    return
                # Infra gate.  This is the ONLY place cancelling a
                # sibling can still save work: gather(return_exceptions=
                # True) does not short-circuit — it waits for every task —
                # so by the time it returns, all N siblings have already
                # run to completion against the same dead dependency.
                # Measured: a fast-failing task alongside three 0.6 s
                # siblings returned at 0.60 s with every task .done(), so
                # a post-gather cancel() loop is a no-op that merely reads
                # as if it bounded the damage.
                if ctx.infra_gate_closed():
                    n = sum(1 for t in pending if not t.done())
                    if n:
                        logger.warning(
                            f"⏸️ INFRA_GATE cancelling {n} in-flight "
                            f"sibling(s) in block {block.id}"
                        )
                    for t in pending:
                        if not t.done():
                            t.cancel()
                    return
                await asyncio.sleep(0.25)
        watcher = asyncio.create_task(_watch_cancel())
        try:
            results = await asyncio.gather(*pending, return_exceptions=True)
        finally:
            watcher.cancel()
            # Restore on every exit, including the infra re-raise below:
            # a stale width would corrupt an enclosing fan-out's
            # proportional gate.  Safe for the hold surface because
            # ctx.infra_widest_fanout latches the width independently.
            ctx.infra_fanout_width = _saved_width
        # An infrastructure fault is not an iteration result.  Materialising
        # it as a failed Artifact below discards the ``infra_kind`` attribute
        # that api.task_cards reads off the live exception to decide between
        # mark_held and update_status("failed") — so a whole fan-out dying on
        # one expired credential was recorded as N failures of the work, the
        # loop advanced to the next iteration into the same dead dependency,
        # and the run lost the resume position a hold preserves.
        #
        # Materialise any exceptional iteration as a failed Artifact so
        # the persistence contract in design/task-cards.md ("every failing
        # iteration is always persisted") holds for both execution paths.
        #
        # This runs BEFORE the infra re-raise below, deliberately: raising
        # first skipped this loop entirely, so a gate-cancelled fan-out
        # persisted NO iteration records -- the run map had nothing to draw
        # for the very event that stopped the run, and the contract above
        # was silently false on exactly the path that most needs it.
        for _pos, r in enumerate(results):
            # Position in ``results`` maps to an ITERATION INDEX through
            # ``_todo``.  With banked iterations skipped the two differ, so
            # using the position directly would record iteration 19's
            # failure against index 0 — corrupting the very record the NEXT
            # resume reads to decide what is already banked.
            idx = _todo[_pos]
            if isinstance(r, Artifact):
                last_artifact = r
                outputs.extend(r.outputs)
                continue
            if isinstance(r, BaseException):
                err_text = "".join(traceback.format_exception_only(type(r), r)).strip()
                # A sibling the infra gate cancelled is not a failure of
                # the work: the harness killed it deliberately because a
                # peer hit dead infrastructure.  asyncio.CancelledError is
                # NOT a subclass of Exception, so it never reaches
                # execute_block's ``except Exception`` and would otherwise
                # be recorded here as a generic failure — making a fan-out
                # look N-wide broken when only the faulting subset was,
                # and blaming the card for the environment's fault.
                _cancelled = isinstance(r, asyncio.CancelledError)
                _iter_status = "cancelled" if _cancelled else "failed"
                iter_outcomes[idx] = _iter_status
                synth = Artifact(
                    summary=(
                        f"Iteration {idx} cancelled — a sibling hit an "
                        f"infrastructure fault"
                        if _cancelled
                        else f"Iteration {idx} raised {type(r).__name__}"
                    ),
                    decisions=[err_text],
                    duration_ms=0,
                    created_at=time.time(),
                    failed=not _cancelled,
                )
                synth.signature = _derive_signature(synth)
                await _record_iteration(
                    block, ctx, idx, synth,
                    item_key=iterations[idx].get("item_key"),
                )
                await _emit(ctx, {
                    "type": "iteration_completed",
                    "block_id": block.id, "index": idx,
                    "status": _iter_status,
                    "signature": synth.signature,
                    "duration_ms": 0,
                    "tokens": 0,
                })
                last_artifact = synth
        # Now that every iteration is on record, re-raise so the run holds
        # rather than being reported as a failure of the work.  Order
        # matters: raising before the loop above left the run map with no
        # iteration records for the collapse that stopped the run.
        for r in results:
            if isinstance(r, BaseException) and getattr(r, "infra_kind", ""):
                logger.warning(
                    f"🔁 REPEAT: {block.id} aborting fan-out — infra fault "
                    f"({getattr(r, 'infra_kind', '')}) in one iteration"
                )
                raise r
        # If cancellation fired, surface it the same way the serial path does.
        if ctx.cancel_requested():
            raise BlockExecutionCancelled()
    else:
        for i in range(len(iterations)):
            # Replayed prefix on a mid-loop resume.  Threaded through the
            # same ``last_artifact`` / ``prior_summaries`` variables the
            # executed path uses, so the first REAL iteration sees exactly
            # the {{previous}} / {{all}} bindings it saw in the source run.
            # ``continue`` before the pause gate is deliberate: replaying a
            # record is not work, so it must not consume a step credit.
            if i < resume_at:
                iter_outcomes[i] = "passed"
                replayed = _replay_iteration(i)
                if replayed is not None:
                    last_artifact = replayed
                    outputs.extend(replayed.outputs)
                    if propagate == "all":
                        prior_summaries.append(replayed.summary or "")
                await _emit(ctx, {
                    "type": "iteration_completed",
                    "block_id": block.id, "index": i,
                    "status": "passed", "replayed": True,
                    "duration_ms": 0, "tokens": 0,
                })
                continue
            # Non-chargeable: the iteration's body is a sequence, whose
            # own first-child boundary charges the credit for this
            # iteration's work.  Charging here too made one iteration
            # cost two credits (traced: repeat_count=2 crossed four
            # chargeable holds for two units of work), so a single step
            # could never advance exactly one iteration.
            await _wait_if_paused(ctx, chargeable=False)
            if ctx.cancel_requested():
                raise BlockExecutionCancelled()
            # Serial fan-out: the gate stops admitting further iterations.
            # Cheaper than the parallel case (no work is in flight to
            # cancel) and strictly more effective — nothing after the
            # gating fault runs at all.
            if ctx.infra_gate_closed():
                break
            # Honour propagate mode.  "none" isolates iterations entirely
            # (no prior info reaches templating or auto-injection).
            # Anything else surfaces the previous artifact; "all" also
            # surfaces the full history.
            isolate = propagate == "none"
            prev_for_binding = None if isolate else last_artifact
            prior_for_binding = prior_summaries if propagate == "all" else None
            artifact = await _run_one(
                i,
                item=iterations[i].get("item"),
                item_key=iterations[i].get("item_key"),
                previous=prev_for_binding,
                all_prior=prior_for_binding,
            )
            last_artifact = artifact
            outputs.extend(artifact.outputs)
            if propagate == "all":
                prior_summaries.append(artifact.summary or "")
            if block.repeat_mode == "until" and _until_condition_met(block, artifact):
                break

    elapsed_ms = int((time.time() - start) * 1000)
    await _emit(ctx, {
        "type": "block_completed",
        "block_id": block.id,
        "at": time.time(),
    })
    # Roster completeness assertion.  Diff the planned member keys
    # against iterations whose terminal outcome is "passed"; on
    # shortfall the block FAILS naming the missing members, so the
    # enclosing container's on_failure governs.  Deliberately a failure
    # rather than a decision line — unlike a repeat_max clip below, the
    # author has declared that partial is not success.  Coverage is
    # status-shaped, not output-shaped: an iteration that passed while
    # writing nothing still counts as covered (see
    # design/task-card-roster-assertion.md §4).
    if (
        (block.repeat_mode or "count") == "for_each"
        and getattr(block, "repeat_require_complete", False)
    ):
        missing = [
            str(iterations[i].get("item_key") or f"#{i}")
            for i in range(len(iterations))
            if iter_outcomes.get(i) != "passed"
        ]
        if missing:
            produced = len(iterations) - len(missing)
            ctx.roster_shortfalls[block.id or ""] = {
                "roster": len(iterations),
                "produced": produced,
                "missing": list(missing),
            }
            await _emit(ctx, {
                "type": "roster_shortfall",
                "block_id": block.id,
                "roster": len(iterations),
                "produced": produced,
                "missing": missing[:50],
                "at": time.time(),
            })
            shown = ", ".join(missing[:10])
            more = f" (+{len(missing) - 10} more)" if len(missing) > 10 else ""
            return Artifact(
                summary=(
                    f"roster incomplete: {produced}/{len(iterations)} "
                    f"members passed - missing: {shown}{more}"
                ),
                decisions=[
                    f"repeat_require_complete: {len(missing)} roster "
                    f"member(s) with no passed iteration: "
                    + ", ".join(missing[:50])
                    + (f" (+{len(missing) - 50} more)"
                       if len(missing) > 50 else "")
                ],
                outputs=outputs,
                duration_ms=elapsed_ms,
                created_at=time.time(),
                failed=True,
            )
    # Scope reduction, surfaced on the block's own artifact.  The loop
    # completed every iteration it PLANNED, so this is not a failure —
    # but "ran 60 of 60 planned" and "ran 60 of the 112 asked for" are
    # different results, and only the second is honest when repeat_max
    # clipped the roster.  Recorded as a decision rather than a failure
    # so an enclosing on_failure policy is unaffected.
    _decisions = list(last_artifact.decisions if last_artifact else [])
    _trunc = ctx.roster_truncations.get(block.id or "")
    if _trunc:
        _skipped = _trunc["roster"] - _trunc["dispatched"]
        _decisions.append(
            f"scope reduced by repeat_max: {_trunc['roster']} items "
            f"resolved, {_trunc['dispatched']} dispatched, {_skipped} "
            f"never run — this block's result covers "
            f"{_trunc['dispatched']}/{_trunc['roster']} of its roster"
        )
        # Name the missed items, not just how many.  A count tells a
        # reader that coverage is short; the identities are what let them
        # re-run exactly the remainder.  Sampled in the decision line to
        # keep it readable — the full list stays in roster_truncations.
        _dropped = _trunc.get("dropped") or []
        if _dropped:
            _shown = ", ".join(str(d) for d in _dropped[:10])
            _more = (
                f" (+{len(_dropped) - 10} more)"
                if len(_dropped) > 10 else ""
            )
            _decisions.append(f"never run: {_shown}{_more}")
    return Artifact(
        summary=(last_artifact.summary if last_artifact else "(no iterations completed)"),
        decisions=_decisions,
        outputs=outputs,
        duration_ms=elapsed_ms,
        created_at=time.time(),
        failed=bool(last_artifact and last_artifact.failed),
    )


class ForEachSourceError(Exception):
    """A templated ``for_each`` source resolved to no usable item list.

    Its own type because the remedy is specific and the alternative is
    catastrophic: falling back to count-based iteration runs the body
    ``repeat_max`` times with ``item=None``, so a 60-wide fan-out whose
    roster failed to resolve becomes 60 agents each told to process the
    empty string — hours of spend producing a run record that looks
    populated.  "I cannot determine the item list" is never legitimately
    "iterate blindly", so this surfaces as a failed block instead.
    """


class RosterAssertionError(Exception):
    """A for_each roster cannot satisfy ``repeat_require_complete``.

    Raised at plan time, before any spend.  Two causes: a finite
    ``repeat_max`` (a cost ceiling and a completeness requirement
    cannot both hold), or roster members that cannot be uniquely keyed
    (see app.utils.roster_keys) — an ambiguous roster can never be
    diffed against what was produced.  Surfaces as a failed block, so
    the enclosing container's on_failure policy governs.
    """


# A for_each source that is EXACTLY one artifact-part reference and
# nothing else, e.g. '{{sibling("recon").outputs.roster.slugs}}'.
# Anchored and whole-string because mixing a precise reference into
# surrounding prose means the author wants the lenient path.  The
# trailing dotted path is required in practice: a data part must be a
# JSON object (task_artifacts.build_part enforces it), so a fan-out
# list always lives under a key.
#
# ``outputs_all`` is admitted alongside ``outputs``, and its trailing
# path is OPTIONAL: the plural form already renders a JSON array, so
# '{{sibling("fan").outputs_all.audit}}' is a complete fan-out source
# with no projection needed.  Both paths parse an identical whole-string
# array identically (verified across nested, bracketed-text and
# whitespace-padded cases), so this changes no parse result — what it
# changes is the FAILURE message: an unresolvable gathered source now
# gets the precise-reference remedy ("have the upstream task
# emit_artifact a data part under that key") instead of the prose-scrape
# advice, which is misleading for a reference that never involved prose.
_PRECISE_SOURCE_RE = re.compile(
    r"^\s*\{\{\s*(?:"
    r"sibling\(\s*['\"][^'\"]+['\"]\s*\)"
    r"|previous_sibling|previous"
    r")\.outputs(?:_all)?\.[a-zA-Z_][a-zA-Z0-9_]*"
    r"(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*\s*\}\}\s*$"
)


def _resolve_for_each_items(
    block: Block, ctx: Optional["ExecutionContext"],
) -> Optional[List[Any]]:
    """Resolve a for_each source to its item list, or raise.

    Three cases, deliberately distinguished:

    1. **Untemplated literal** (no ``{{``) — parsed leniently, and an
       unparseable literal returns None so the historical count
       fallback still applies.  A static source is an authoring-time
       value the author can see; degrading it costs nothing at runtime.
    2. **Precise reference** (whole source is one ``outputs.…``
       reference) — parsed STRICTLY.  The author named an exact
       structured part, so scanning its rendering for an incidental
       array would substitute a different value than the one requested.
    3. **Templated prose** — parsed leniently (the planner-summary
       shape this feature was built for).

    Cases 2 and 3 RAISE ``ForEachSourceError`` when no list is found,
    rather than falling back to count.
    """
    raw = block.repeat_for_each_source
    templated = bool(raw and "{{" in raw)
    source = (
        _render_for_each_source(block, ctx)
        if (ctx is not None and templated) else raw
    )
    precise = bool(raw and _PRECISE_SOURCE_RE.match(raw))
    items = task_templating.parse_for_each_source(source, strict=precise)
    if items is not None:
        return items
    if not templated:
        return None      # static literal: legacy count fallback
    # Templated and unresolved — fail loudly.  The rendered text is
    # included (bounded) because the usual causes are visible in it: an
    # empty render (the referenced part was never emitted) or prose with
    # no array (the agent ignored the output-format instruction).
    shown = (source or "").strip()
    detail = (
        "resolved to empty text" if not shown
        else f"resolved to {len(shown)} chars containing no JSON array: "
             f"{shown[:280]!r}"
    )
    hint = (
        " The source names an exact artifact part, so only a whole-string "
        "JSON array is accepted - have the upstream task emit_artifact a "
        "data part holding the list under that key."
        if precise else
        " Have the upstream task emit_artifact a data part and reference "
        "it precisely, or ensure its summary contains exactly one JSON array."
    )
    raise ForEachSourceError(f"for_each source {raw!r} {detail}.{hint}")


def _render_for_each_source(
    block: Block, ctx: "ExecutionContext",
) -> Optional[str]:
    """Render templating in a Repeat's for_each source at dispatch time.

    Enables the canonical decomposition shape — Task("plan") followed by
    Repeat(for_each over the plan's output): the source may reference
    {{sibling("plan-id")}} / {{previous_sibling}} / {{var.X}}, resolved
    against the artifacts completed so far in this run.  A source with
    no placeholders passes through unchanged (the JSON-literal path).
    """
    raw = block.repeat_for_each_source
    if not raw or "{{" not in raw:
        return raw
    base = ctx.binding_stack[-1] if ctx.binding_stack else task_templating.IterationBindings()
    updates: Dict[str, Any] = {}
    merged = {**ctx.variables, **ctx.overrides}
    if merged:
        updates["variables"] = merged
    sibling_prev = ctx.sibling_stack[-1] if ctx.sibling_stack else None
    if sibling_prev is not None:
        updates["previous_sibling"] = sibling_prev
    if ctx.artifact_registry:
        updates["sibling_artifacts"] = ctx.artifact_registry
    bindings = replace(base, **updates) if updates else base
    rendered = task_templating.render(raw, bindings)
    if rendered != raw:
        logger.info(
            f"for_each source for block {block.id!r} rendered at "
            f"dispatch time ({len(raw)} -> {len(rendered)} chars)"
        )
    return rendered


def _plan_iterations(
    block: Block, ctx: Optional["ExecutionContext"] = None,
) -> List[Dict[str, Any]]:
    """Produce the list of iteration descriptors for a Repeat block.

    When ``ctx`` is provided, a for_each source containing {{...}}
    placeholders is rendered against the run's completed artifacts and
    variables first, so a planner Task's output can drive the fan-out.

    Raises ``ForEachSourceError`` when a TEMPLATED source yields no
    item list — see ``_resolve_for_each_items``.
    """
    mode = block.repeat_mode or "count"
    if mode == "count":
        n = int(block.repeat_count or 1)
        return [{"index": i, "item": None} for i in range(max(0, n))]
    if mode == "until":
        n_max = int(block.repeat_max or 1)
        return [{"index": i, "item": None} for i in range(max(0, n_max))]
    if mode == "for_each":
        items = _resolve_for_each_items(block, ctx)
        if items is not None:
            require = bool(getattr(block, "repeat_require_complete", False))
            key_path = getattr(block, "repeat_item_key", None)
            if require:
                # Refused, never guessed — the plan-time contradictions
                # the assertion cannot survive (see
                # design/task-card-roster-assertion.md §3.1-3.2).  Also
                # refused at validation time; repeated here because
                # validation is advisory for programmatic launches and a
                # TEMPLATED roster only exists at this point.
                if block.repeat_max and block.repeat_max > 0:
                    raise RosterAssertionError(
                        "repeat_require_complete and a finite repeat_max "
                        "contradict - a completeness requirement and a "
                        "cost ceiling cannot both hold. Remove one; "
                        "bound cost with repeat_max_concurrency instead"
                    )
                problems = roster_key_problems(items, key_path)
                if problems:
                    raise RosterAssertionError("; ".join(problems))
            # Respect repeat_max as an upper bound when provided.
            if block.repeat_max and block.repeat_max > 0:
                if len(items) > block.repeat_max:
                    # Clipping is legitimate — repeat_max is a cost
                    # ceiling — but clipping SILENTLY is not: the run then
                    # reports a complete pass over a reduced scope, and a
                    # downstream stage that reads the output directory has
                    # no way to notice.
                    logger.warning(
                        "🔁 REPEAT: %s roster truncated by repeat_max: "
                        "%d resolved, %d dispatched, %d never run",
                        block.id, len(items), block.repeat_max,
                        len(items) - block.repeat_max,
                    )
                    # ctx is optional on unit paths, so this must not be
                    # the thing that turns a working call into an error.
                    if ctx is not None:
                        ctx.roster_truncations[block.id or ""] = {
                            "roster": len(items),
                            "dispatched": block.repeat_max,
                            # The identities, so the loss is recoverable.
                            # No size guard: ``items`` is already fully
                            # materialized above, so retaining the tail's
                            # labels costs nothing a large roster has not
                            # already spent.
                            "dropped": [
                                str(it) for it in items[block.repeat_max:]
                            ],
                        }
                items = items[: block.repeat_max]
            # item_key is recorded UNCONDITIONALLY, not only under the
            # assertion: it is what makes an iteration nameable after
            # the fact — run-map dots, shortfall diffs, and any future
            # member-level re-dispatch all key on it.  None when not
            # derivable (a non-scalar with no key path) and the
            # assertion is off.
            return [
                {"index": i, "item": it,
                 "item_key": derive_item_key(it, key_path)}
                for i, it in enumerate(items)
            ]
        # Fallback: no parseable source → treat like count.
        if getattr(block, "repeat_require_complete", False):
            # No roster resolved, so there is nothing to assert over —
            # falling through to anonymous count iterations would make
            # the assertion silently vacuous.
            raise RosterAssertionError(
                "repeat_require_complete is set but the for_each source "
                "did not resolve to an item list - there is no roster "
                "to assert completeness over"
            )
        n = int(block.repeat_max or block.repeat_count or 1)
        return [{"index": i, "item": None} for i in range(max(0, n))]
    return []


async def _record_iteration(
    block: Block, ctx: ExecutionContext, index: int, artifact: Artifact,
    item_key: Optional[str] = None,
) -> None:
    """Persist summary + (optionally) full artifact for one iteration."""
    if ctx.storage is None or not block.id:
        return
    status: IterationStatus = "failed" if artifact.failed else "passed"
    signature = _derive_signature(artifact) if artifact.failed else None
    # Retention: always persist failures; cap passes per block.
    keep_full = True
    if status == "passed":
        prev = ctx.pass_counts.get(block.id, 0)
        keep_full = prev < PASS_ARTIFACT_RETENTION_CAP
        ctx.pass_counts[block.id] = prev + 1
    if keep_full:
        ctx.storage.write_iteration_artifact(ctx.run_id, block.id, index, artifact)
    summary = IterationSummary(
        index=index,
        status=status,
        signature=signature,
        duration_ms=artifact.duration_ms,
        tokens=artifact.tokens,
        has_artifact=keep_full,
        item_key=item_key,
    )
    ctx.storage.append_iteration_summary(ctx.run_id, block.id, summary)


def _derive_signature(artifact: Artifact) -> str:
    """Hash of (error_type, error_location) for failure clustering.
    Extracted from the artifact's decisions/summary as a best-effort."""
    probe = "\n".join(artifact.decisions[:3]) or artifact.summary[:300]
    return hashlib.sha256(probe.encode("utf-8", errors="replace")).hexdigest()[:12]


def _iteration_signature(a: Artifact) -> str:
    """Cheap signature for convergence detection: SHA-16 of normalized
    summary text.  Two iterations producing the same normalized
    summary are treated as a stop signal — the agent has converged
    on a stable conclusion and further iterations would be redundant.
    """
    body = " ".join((a.summary or "").lower().split())
    return hashlib.sha256(body.encode()).hexdigest()[:16]


# Consecutive non-progressing iterations that trip an Until loop's stall
# breaker.  Three rather than two: layer 2's convergence check runs only when
# there is no explicit condition, so it can afford to be eager.  The stall
# breaker overrides an explicit condition, so it must be certain the loop is
# stuck rather than merely slow.
_UNTIL_STALL_LIMIT = 3


async def _execute_until(block: Block, ctx: ExecutionContext) -> Artifact:
    """Repeat the body until a model-evaluated condition is true.

    On each iteration:
      1. Run the body sequence (top-to-bottom).
      2. Ask the evaluator model: given this artifact, is
         <condition> true?  Reply yes or no.
      3. If yes → terminate; if no and max not hit → continue.

    Hard upper bound is `until_max` (defaults to 5 if unset) so a
    never-satisfied condition cannot hang the run.
    """
    n_max = max(1, int(block.until_max or 5))
    condition = (block.until_condition or "").strip()
    mode = (block.until_mode or "model").lower()
    start = time.time()
    last_artifact: Optional[Artifact] = None
    outputs: List[ArtifactPart] = []
    decisions: List[str] = []
    signatures: List[str] = []  # for convergence backstop
    # Stall-breaker state.  Unlike ``signatures`` these are maintained even
    # when an explicit condition is set, which is the case the breaker exists
    # for.
    stall_streak = 0
    prev_sig: Optional[str] = None

    await _emit(ctx, {
        "type": "block_started",
        "block_id": block.id, "block_type": "until",
        "planned": n_max, "at": time.time(),
    })

    # Mid-loop resume — see the equivalent block in _execute_repeat.
    resume_at = 0
    if (
        ctx.resume_from_iteration is not None
        and block.id
        and block.id == ctx.resume_from_block_id
    ):
        resume_at = max(0, min(int(ctx.resume_from_iteration), n_max))
        if resume_at:
            logger.info(
                f"until {block.id} resuming at iteration {resume_at} "
                f"of max {n_max}"
            )

    for i in range(n_max):
        if i < resume_at:
            # Replay, and critically SKIP the three exit-condition layers
            # below.  A replayed iteration's self_assessment would break
            # the loop immediately (layer 1 fires on objective_met=true),
            # so a resume-at-4 would exit at iteration 0 having executed
            # nothing while reporting the goal met — a false success, the
            # worst available failure mode.  Convergence (layer 2) would
            # likewise trip on two identical replayed summaries.
            got = ctx.resume_iteration_artifacts.get(i)
            if got is not None:
                last_artifact = (
                    got.model_copy(update={"failed": False})
                    if got.failed else got
                )
                outputs.extend(last_artifact.outputs)
                if not condition:
                    # Keep the signature history aligned with the replayed
                    # prefix so convergence detection compares executed
                    # iterations against the right predecessor.
                    signatures.append(_iteration_signature(last_artifact))
            await _emit(ctx, {
                "type": "iteration_completed",
                "block_id": block.id, "index": i,
                "status": "passed", "replayed": True,
                "duration_ms": 0, "tokens": 0,
            })
            continue
        # Non-chargeable for the same reason as the repeat path above:
        # the body sequence's first-child boundary is what charges.
        await _wait_if_paused(ctx, chargeable=False)
        if ctx.cancel_requested():
            raise BlockExecutionCancelled()
        await _emit(ctx, {
            "type": "iteration_started",
            "block_id": block.id, "index": i,
        })
        bindings = task_templating.IterationBindings(
            index=i, item=None, previous=last_artifact, all_summaries=[],
        )
        ctx.binding_stack.append(bindings)
        # See _execute_repeat._run_one — stamp iteration context so
        # nested task_executor emissions are tagged with this until
        # block's id, not the inner task block's id.
        iter_ctx_token = set_task_iteration_context(block.id, i)
        try:
            artifact = await _execute_sequence(
                block.body, ctx,
                on_failure=(block.on_failure or "continue"),
            )
        finally:
            ctx.binding_stack.pop()
            reset_task_iteration_context(iter_ctx_token)
        await _record_iteration(block, ctx, i, artifact)
        await _emit(ctx, {
            "type": "iteration_completed",
            "block_id": block.id, "index": i,
            "status": ("failed" if artifact.failed else "passed"),
            "signature": artifact.signature,
            "duration_ms": artifact.duration_ms, "tokens": artifact.tokens,
        })
        last_artifact = artifact
        outputs.extend(artifact.outputs)

        # ---------- Exit-condition layer 1: agent self-assessment ----------
        # The task executor parses <self_assessment objective_met="..."
        # rationale="..." /> at end of response into artifact.self_assessment.
        # For goal cards (no until_condition), this is the primary signal.
        #
        # GUARDED on `not condition` — same guard as layer 2 below.  When
        # the user wrote an explicit until_condition ("counter is above
        # 300"), the model-evaluated condition (layer 3) is the source of
        # truth.  The inner task's self_assessment describes whether *its
        # own atomic task* succeeded ("did I add 20? yes") — which is
        # unrelated to the loop's exit — and would otherwise break the
        # loop after iteration 0.  This was the "Until ran once, count=1"
        # bug: a per-iteration task that always reports success collapsed
        # an N-iteration loop into a single pass.
        sa = {} if condition else (getattr(artifact, "self_assessment", None) or {})
        objective_met = (sa.get("objective_met") or "").strip().lower()
        rationale = (sa.get("rationale") or "").strip()
        if objective_met == "true":
            decisions.append(
                f"self_assessment: objective_met=true"
                + (f" ({rationale})" if rationale else "")
            )
            break
        if objective_met == "partial":
            # Partial = stopped making progress on a real obstacle.
            # Don't keep iterating; surface to user.
            decisions.append(
                f"self_assessment: objective_met=partial — stopping"
                + (f" ({rationale})" if rationale else "")
            )
            break

        # ---------- Exit-condition layer 2: convergence backstop ----------
        # Only fires when there's no explicit condition.  When a real
        # until_condition is set, the model evaluator is the source of
        # truth and we don't second-guess it via summary similarity.
        if not condition:
            sig = _iteration_signature(artifact)
            signatures.append(sig)
            if len(signatures) >= 2 and signatures[-1] == signatures[-2]:
                decisions.append(
                    "converged: 2 consecutive identical iteration summaries"
                )
                break

        # ---------- Exit-condition layer 2b: stall breaker ----------------
        # Active even when an explicit condition IS set — the case where
        # layers 1 and 2 are both disabled and until_max becomes the only
        # terminator.  Run 2e1fbe76 burned 35 consecutive iterations that
        # way: its condition demanded visual verification, the deploy step
        # that made verification possible was broken, so the condition was
        # unsatisfiable by construction while each iteration still reported
        # new work.
        #
        # Non-progress needs UNAMBIGUOUS evidence, because each signal on
        # its own has a legitimate reading.  A failed iteration is the
        # NORMAL intermediate state of a fix-until-green loop — the
        # canonical Until use case — so counting bare failure made
        # until_max > 3 unreachable for it.  A repeated summary is a terse
        # agent as often as a stuck one, and when an explicit condition is
        # set the layer-3 contract is that only the evaluator decides.  So
        # require either the agent's own explicit obstacle report, or
        # failure TOGETHER WITH no new information:
        #   - objective_met="partial" — the agent reporting a real obstacle
        #     rather than a verdict on the goal; deliberate, and the honest
        #     form of the run-2e1fbe76 failure this breaker exists for; or
        #   - the iteration failed AND its summary is unchanged, i.e. it
        #     went wrong and told us nothing we did not already know.
        # THREE in a row are still required, so a loop that is genuinely
        # advancing is untouched — the breaker must not degenerate into a
        # lower until_max.
        raw_sa = getattr(artifact, "self_assessment", None) or {}
        sig_now = _iteration_signature(artifact)
        repeated = prev_sig is not None and sig_now == prev_sig
        stalled = (
            (raw_sa.get("objective_met") or "").strip().lower() == "partial"
            or (bool(artifact.failed) and repeated)
        )
        prev_sig = sig_now
        stall_streak = stall_streak + 1 if stalled else 0
        if stall_streak >= _UNTIL_STALL_LIMIT:
            # Checked before layer 3 so a condition that never evaluates
            # true cannot starve the breaker.  If the condition would have
            # been satisfied on this same iteration the loop exits either
            # way; only the recorded reason differs.
            decisions.append(
                f"stall breaker: {stall_streak} consecutive non-progressing "
                f"iterations (failed / objective_met=partial / repeated "
                f"summary); stopped at iter {i} of max {n_max}"
            )
            break

        # ---------- Exit-condition layer 3: model-evaluated condition ----
        if not condition:
            # No condition → rely on layer 1 (self_assessment) and layer 2
            # (convergence) to terminate.  If neither fires, run to
            # until_max — the cap is the safety net, not the primary stop.
            continue
        if mode == "expression":
            # Reserved for a future expression evaluator.  Until then,
            # treat as never-satisfied so the loop runs to until_max.
            decisions.append("until_mode='expression' not yet implemented; running to max")
            continue
        # mode == "model"
        try:
            satisfied = await _evaluate_until_condition_with_model(condition, artifact)
        except Exception as e:
            logger.warning(f"until condition eval failed (continuing): {e}")
            satisfied = False
        if satisfied:
            decisions.append(f"until condition satisfied at iter {i}")
            break

    elapsed_ms = int((time.time() - start) * 1000)
    await _emit(ctx, {
        "type": "block_completed", "block_id": block.id, "at": time.time(),
    })
    return Artifact(
        summary=(last_artifact.summary if last_artifact else "(until ran 0 iterations)"),
        decisions=(last_artifact.decisions if last_artifact else []) + decisions,
        outputs=outputs, duration_ms=elapsed_ms,
        created_at=time.time(),
        failed=bool(last_artifact and last_artifact.failed),
    )


async def _execute_schedule_passthrough(
    block: Block, ctx: ExecutionContext,
) -> Artifact:
    """A schedule block executed directly (rather than fired by the
    scheduler) runs its body once.  This makes "Run now" on a
    scheduled card behave intuitively and keeps tests simple.
    """
    if not block.body:
        return Artifact(summary="(empty schedule block)", created_at=time.time())
    logger.info(f"schedule block {block.id} executed directly (passthrough)")
    return await _execute_sequence(
        block.body, ctx, on_failure=(block.on_failure or "continue"),
    )


async def _execute_state(block: Block, ctx: ExecutionContext) -> Artifact:
    """Apply a State block's read-only variable declarations to the run.

    State is a leaf: it declares run-scoped named variables (name ->
    literal) that tasks read via {{var.NAME}} templating.  It writes
    those literals into ``ctx.variables`` and returns a trivial artifact.

    Placement is the reset policy.  A State block in a body that runs
    once (card root wrapper, Repeat count=1, or before an inner loop)
    sets its variables once per run.  The same block inside a Repeat /
    Until body re-executes at the start of every iteration, re-applying
    its authored literals — i.e. resetting those variables to baseline
    each cycle.  Read-only: no task writes back, so the sandbox
    invariant (only artifacts cross task boundaries) is preserved.

    Note: variables set inside a loop body remain in ``ctx.variables``
    after the loop ends (flat scope, last-write-wins).  This is benign
    for read-only givens — downstream blocks simply see the final
    applied value — and avoids a scoped-shadowing mechanism the
    placement-as-policy model does not need.
    """
    declared = block.state_variables or {}
    if declared:
        ctx.variables.update(declared)
    # Prose givens — the conversational baseline.  Keyed by block id so
    # a State block re-executing in a loop overwrites its own note
    # rather than duplicating (idempotent, matching the variables reset
    # policy).  Empty/blank prose clears any prior note for this block.
    prose = (block.state_context or "").strip()
    if block.id:
        if prose:
            ctx.context_notes[block.id] = prose
        else:
            ctx.context_notes.pop(block.id, None)
    names = ", ".join(sorted(declared.keys())) if declared else "(none)"
    # Resolved values surfaced live to the running card: each declared
    # var's effective value AFTER launch-time overrides win, so the
    # panel shows what the run is actually operating under (not just the
    # authored baseline).  Names-only ``variables`` kept for back-compat.
    resolved = {k: ctx.overrides.get(k, declared[k]) for k in declared}
    await _emit(ctx, {
        "type": "state_applied",
        "block_id": block.id,
        "variables": sorted(declared.keys()),
        "values": resolved,
        "has_context": bool(prose),
        "at": time.time(),
    })
    return Artifact(
        summary=f"Initialized state: {names}",
        created_at=time.time(),
    )

# How often the wait-loop re-reads the record.  Coarser than the pause
# loop's 0.4s on purpose: a human answer arrives on a scale of minutes, so
# polling faster only rewrites nothing more often.
_ASK_POLL_SECONDS = 0.5


def _recorded_ask_answer(
    ctx: "ExecutionContext", block_id: str,
) -> Optional[Dict[str, Any]]:
    """The human answer already on record for this Ask block, if any."""
    if ctx.storage is None or not block_id:
        return None
    run = ctx.storage.get(ctx.run_id)
    if run is None:
        return None
    return (getattr(run, "ask_answers", None) or {}).get(block_id)


async def _execute_ask(block: Block, ctx: ExecutionContext) -> Artifact:
    """Hold the run at this boundary until a human answers.

    The whole block is "answer already on record? apply it; else ask", and
    that ordering is what makes an Ask idempotent in the two places it has
    to be: a resume walk re-executes it (see the resume-skip gate, which
    treats ask like state), and a run reconciled to "held" by a server
    restart can be answered and then resumed.  In both cases the settled
    answer is re-applied without the operator being asked twice.

    Introduces no new hold point: an Ask sits at an ordinary block boundary,
    which a sequence has already passed _wait_if_paused to reach.
    """
    recorded = _recorded_ask_answer(ctx, block.id)
    if recorded is None:
        if ctx.storage is None:
            raise TaskExecutorError(
                f"ask block {block.id!r} cannot hold for a human: this run "
                f"has no storage, so the question could not be persisted "
                f"and an answer could never be recorded"
            )
        question = (block.ask_question or "").strip()
        choices = [str(c) for c in (block.ask_choices or [])]
        ctx.storage.open_ask(ctx.run_id, block.id, question, choices)
        await _emit(ctx, {
            "type": "ask_opened", "block_id": block.id,
            "question": question, "choices": choices, "at": time.time(),
        })
        try:
            while recorded is None:
                if ctx.cancel_requested():
                    raise BlockExecutionCancelled()
                await asyncio.sleep(_ASK_POLL_SECONDS)
                recorded = _recorded_ask_answer(ctx, block.id)
        finally:
            # Clears the open question either way.  close_ask deliberately
            # does not walk the status back to running unless it is still
            # awaiting_input, so a cancelled run is never briefly reported
            # as live on its way out.
            ctx.storage.close_ask(ctx.run_id)
        await _emit(ctx, {
            "type": "ask_answered", "block_id": block.id,
            "decision": recorded.get("decision"), "at": time.time(),
        })

    decision = str(recorded.get("decision") or "approve").strip().lower()
    answer = str(recorded.get("answer") or "").strip()
    who = str(recorded.get("answered_by") or "").strip() or "the operator"
    label = block.name or block.id or "ask"

    if decision == "reject":
        # A failed artifact rather than a branch.  The block grammar has no
        # conditional, and the enclosing container's on_failure already
        # expresses both readings — "stop" halts the sequence, "continue"
        # carries on with the rejection recorded — so inventing a branch for
        # this one case would be a second control-flow mechanism to keep in
        # agreement with the first.
        return Artifact(
            summary=f"{label}: rejected by {who}"
                    + (f" - {answer}" if answer else ""),
            decisions=[
                f"human rejected at {label}: {answer or 'no reason given'}"
            ],
            failed=True,
            created_at=time.time(),
        )

    if block.ask_variable:
        ctx.variables[block.ask_variable] = answer
    if block.id:
        note = f"Human checkpoint '{label}' was approved by {who}."
        if answer:
            note += f" They said: {answer}"
        ctx.context_notes[block.id] = note
    return Artifact(
        summary=f"{label}: approved by {who}"
                + (f" - {answer}" if answer else ""),
        decisions=[f"human approved at {label}"],
        created_at=time.time(),
    )


async def _execute_group(block: Block, ctx: ExecutionContext) -> Artifact:
    """Group dispatch as a named coroutine so the self-improvement
    wrapper can re-invoke it uniformly with the loop executors."""
    return await _execute_sequence(
        block.body, ctx, on_failure=(block.on_failure or "continue"),
    )


async def _maybe_self_improve(block: Block, ctx: ExecutionContext,
                              inner) -> Artifact:
    """Execute a container block; when it carries ``self_improve``,
    judge the outcome and — only when a tangible, outcome-affecting
    text improvement exists — patch the card's TEXT (never privilege)
    and restart this level with the revised text.

    Every guard lives in app/utils/self_improve.py: the field
    whitelist (instructions/state_context only), existing-id keying
    (so signed scope approvals are never orphaned), the structure
    fingerprint (text changed and ONLY text), the cross-run
    oscillation guard, and the per-block + run-wide budgets.  The
    verdict call is app/agents/improve_evaluator.py, which resolves
    every failure to "accept" so a flaky judge cannot spin edits.

    Durability: an applied patch is persisted to the LIVE card
    (best-effort — ids that drifted since launch simply don't apply)
    and a lesson record is appended to the project's ledger either
    way, so run N+1's judge refines rather than re-derives.
    """
    if not getattr(block, "self_improve", False):
        return await inner(block, ctx)

    from app.utils import self_improve as si
    from .improve_evaluator import evaluate_improvement

    improve_max = si.resolve_improve_max(getattr(block, "improve_max", None))
    drift = getattr(block, "improve_drift", None) or "conservative"
    criterion = (getattr(block, "improve_criterion", None) or "").strip()

    card_id = None
    if ctx.storage is not None:
        run = ctx.storage.get(ctx.run_id)
        card_id = getattr(run, "card_id", None) if run else None
    ledger = None
    if ctx.project_id:
        try:
            from app.utils.paths import get_project_dir
            ledger = si.LessonLedger(get_project_dir(ctx.project_id))
        except Exception as e:  # noqa: BLE001 — ledger is best-effort
            logger.debug(f"self_improve: ledger unavailable: {e}")

    current = block
    artifact: Optional[Artifact] = None
    # Revision breadcrumbs carried ACROSS restarts.  Each restart gets a
    # fresh artifact from ``inner``, so a note appended to the discarded
    # pass's artifact dies with it — the FINAL artifact must carry the
    # full trail or a mid-run revision is invisible in the durable
    # record (improve_revision events are transient by design).
    trail: List[str] = []
    for revision in range(improve_max + 1):
        artifact = await inner(current, ctx)
        if trail:
            artifact.decisions = list(trail) + list(artifact.decisions)
        if ctx.cancel_requested():
            return artifact
        lessons = (
            ledger.for_block(card_id, block.id)
            if (ledger and card_id) else []
        )
        verdict = await evaluate_improvement(
            current, artifact, criterion=criterion, drift=drift,
            lessons=lessons, revision=revision,
        )
        v = verdict.get("verdict") or "accept"
        patch = verdict.get("patch") or {}
        rec: Dict[str, Any] = {
            "run_id": ctx.run_id, "card_id": card_id,
            "block_id": block.id, "revision": revision, "verdict": v,
            "rationale": verdict.get("rationale", ""),
            "lesson": verdict.get("lesson", ""),
            "drift": drift, "applied": False, "persisted": False,
        }
        stop_reason: Optional[str] = None
        if v != "revise" or not patch:
            stop_reason = v if v in ("accept", "stop") else "accept"
        elif revision >= improve_max:
            stop_reason = "budget_exhausted"
        elif ctx.improve_edits_used >= si.run_improve_ceiling():
            stop_reason = "run_ceiling"
        else:
            subtree = current.model_dump()
            errors = si.validate_improve_patch(patch, subtree)
            if errors:
                stop_reason = "invalid_patch"
                rec["errors"] = errors[:5]
            else:
                p_hash = si.patch_hash(patch)
                if ledger and card_id and ledger.seen_patch_hash(
                        card_id, block.id, p_hash):
                    stop_reason = "oscillation"
                else:
                    before = si.structure_fingerprint(subtree)
                    # Pre-image MUST be extracted before application —
                    # it is what makes the revision revertable (the
                    # revert endpoint replays it through the same
                    # guarded patch path).  See si.extract_pre_image.
                    pre_image = si.extract_pre_image(patch, subtree)
                    si.apply_improve_patch(subtree, patch)
                    if si.structure_fingerprint(subtree) != before:
                        # Impossible by construction; belt-and-braces.
                        stop_reason = "structure_changed"
                    else:
                        rec.update({
                            "patch": patch, "patch_hash": p_hash,
                            "pre_image": pre_image,
                            "applied": True,
                            "persisted": si.persist_patch_to_card(
                                ctx.project_id, card_id, patch),
                        })
                        ctx.improve_edits_used += 1
                        current = Block(**subtree)
        if ledger:
            ledger.record(rec)
        await _emit(ctx, {
            "type": "improve_revision",
            "block_id": block.id, "revision": revision, "verdict": v,
            "rationale": (verdict.get("rationale") or "")[:500],
            "applied": rec["applied"], "persisted": rec["persisted"],
            "stop": stop_reason, "at": time.time(),
        })
        if stop_reason:
            if stop_reason != "accept":
                artifact.decisions = list(artifact.decisions) + [
                    f"self-improve: {stop_reason}",
                ]
            return artifact
        trail.append(
            f"self-improve: revision {revision + 1} applied — "
            f"restarting this level"
        )
    return artifact