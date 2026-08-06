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
import hashlib
import logging
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

    def pause_requested(self) -> bool:
        if self.storage is None:
            return False
        run = self.storage.get(self.run_id)
        return bool(run and run.pause_requested)


class BlockExecutionCancelled(Exception):
    """Raised internally when cancel is observed at a boundary."""


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
        elif block.block_type == "state":
            # Deliberately re-executed while skipping: _execute_state only
            # writes authored literals into ctx.variables/context_notes,
            # and those two stores are the run-scoped state that is not
            # persisted anywhere.  Re-running them is how {{var.NAME}} is
            # rebuilt without a variables snapshot on disk.
            pass
        elif _subtree_contains(block, ctx.resume_from_block_id):
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
            artifact = await _execute_repeat(block, ctx)
        elif block.block_type == "parallel":
            artifact = await _execute_parallel(block, ctx)
        elif block.block_type == "until":
            artifact = await _execute_until(block, ctx)
        elif block.block_type == "schedule":
            artifact = await _execute_schedule_passthrough(block, ctx)
        elif block.block_type == "state":
            artifact = await _execute_state(block, ctx)
        elif block.block_type == "call":
            artifact = await _execute_call(block, ctx)
        elif block.block_type == "group":
            artifact = await _execute_sequence(
                block.body, ctx,
                on_failure=(block.on_failure or "continue"),
            )
        else:
            raise TaskExecutorError(f"Unknown block_type: {block.block_type!r}")
    except BlockExecutionCancelled:
        await _mark_block_status(ctx, block, "cancelled")
        raise
    except Exception as exc:
        await _mark_block_status(ctx, block, "failed", error=str(exc))
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
    stack = [root]
    while stack:
        node = stack.pop()
        if node.id:
            try:
                ctx.storage.set_block_state(ctx.run_id, TaskRunBlockState(
                    block_id=node.id,
                    block_type=node.block_type,
                    status="queued",
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
    try:
        artifact = await _run_callee(resolved, ctx)
    finally:
        ctx.call_stack.pop()
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


def _build_state_context(ctx: "ExecutionContext") -> str:
    """Build the standing-context preamble from State-block prose.

    This is the conversational baseline: freeform givens authored in a
    State block's ``state_context`` flow into the task here, without the
    author needing any {{var}} templating.  Multiple State blocks'
    notes are joined in insertion order.  Returns empty string when no
    prose givens are active.
    """
    notes = [n.strip() for n in ctx.context_notes.values() if n and n.strip()]
    if not notes:
        return ""
    body = "\n\n".join(notes)
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
            and not ctx.artifact_registry):
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
    # standing context the task receives without templating.
    preambles: List[str] = []
    state_ctx = _build_state_context(ctx)
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
            last = await execute_block(child, ctx)
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
    children = await asyncio.gather(
        *[execute_block(c, ctx) for c in block.body],
        return_exceptions=True,
    )
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
    iterations = _plan_iterations(block, ctx)
    if not iterations:
        return Artifact(summary="(repeat with 0 iterations)", created_at=time.time())

    start = time.time()
    propagate = block.repeat_propagate or "none"
    prior_summaries: List[str] = []
    last_artifact: Optional[Artifact] = None
    outputs: List[ArtifactPart] = []

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
                        all_prior: Optional[List[str]] = None) -> Artifact:
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
        ctx.binding_stack.append(bindings)
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
                block.body, ctx,
                on_failure=(block.on_failure or "continue"),
            )
        finally:
            ctx.binding_stack.pop()
            reset_task_iteration_context(iter_ctx_token)
        # Seal timing if the body didn't.
        if not artifact.duration_ms:
            artifact.duration_ms = int((time.time() - iter_start) * 1000)
        await _record_iteration(block, ctx, index, artifact)
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
        # Parallel iterations cannot see each other's outputs — propagation
        # is last/all relative to prior iterations, which is ill-defined
        # when everything runs concurrently.  Bindings still carry index
        # and item; previous/all are left empty.  The design doc treats
        # propagation as a sequential-loop feature.
        pending = [
            asyncio.create_task(_run_one(
                i,
                item=iterations[i].get("item"),
                previous=None,
                all_prior=None,
            ))
            for i in range(len(iterations))
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
                await asyncio.sleep(0.25)
        watcher = asyncio.create_task(_watch_cancel())
        results = await asyncio.gather(*pending, return_exceptions=True)
        watcher.cancel()
        # Materialise any exceptional iteration as a failed Artifact so
        # the persistence contract in design/task-cards.md ("every failing
        # iteration is always persisted") holds for both execution paths.
        for idx, r in enumerate(results):
            if isinstance(r, Artifact):
                last_artifact = r
                outputs.extend(r.outputs)
                continue
            if isinstance(r, BaseException):
                err_text = "".join(traceback.format_exception_only(type(r), r)).strip()
                synth = Artifact(
                    summary=f"Iteration {idx} raised {type(r).__name__}",
                    decisions=[err_text],
                    duration_ms=0,
                    created_at=time.time(),
                    failed=True,
                )
                synth.signature = _derive_signature(synth)
                await _record_iteration(block, ctx, idx, synth)
                await _emit(ctx, {
                    "type": "iteration_completed",
                    "block_id": block.id, "index": idx,
                    "status": "failed",
                    "signature": synth.signature,
                    "duration_ms": 0,
                    "tokens": 0,
                })
                last_artifact = synth
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
    return Artifact(
        summary=(last_artifact.summary if last_artifact else "(no iterations completed)"),
        decisions=(last_artifact.decisions if last_artifact else []),
        outputs=outputs,
        duration_ms=elapsed_ms,
        created_at=time.time(),
        failed=bool(last_artifact and last_artifact.failed),
    )


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
    """
    mode = block.repeat_mode or "count"
    if mode == "count":
        n = int(block.repeat_count or 1)
        return [{"index": i, "item": None} for i in range(max(0, n))]
    if mode == "until":
        n_max = int(block.repeat_max or 1)
        return [{"index": i, "item": None} for i in range(max(0, n_max))]
    if mode == "for_each":
        source = (
            _render_for_each_source(block, ctx)
            if ctx is not None else block.repeat_for_each_source
        )
        items = task_templating.parse_for_each_source(source)
        if items is not None:
            # Respect repeat_max as an upper bound when provided.
            if block.repeat_max and block.repeat_max > 0:
                items = items[: block.repeat_max]
            return [{"index": i, "item": it} for i, it in enumerate(items)]
        # Fallback: no parseable source → treat like count.
        n = int(block.repeat_max or block.repeat_count or 1)
        return [{"index": i, "item": None} for i in range(max(0, n))]
    return []


async def _record_iteration(
    block: Block, ctx: ExecutionContext, index: int, artifact: Artifact,
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
