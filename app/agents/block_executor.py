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

from ..models.task_card import Artifact, ArtifactPart, Block
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

    def cancel_requested(self) -> bool:
        if self.storage is None:
            return False
        run = self.storage.get(self.run_id)
        return bool(run and run.cancel_requested)


class BlockExecutionCancelled(Exception):
    """Raised internally when cancel is observed at a boundary."""


async def _emit(ctx: "ExecutionContext", event: Dict[str, Any]) -> None:
    """Best-effort push to the live-observation relay.  Never raises."""
    if not ctx.run_id:
        return
    await _relay.safe_push(ctx.run_id, event)


async def execute_block(block: Block, ctx: ExecutionContext) -> Artifact:
    """Execute any block — dispatcher over block_type."""
    if block.block_type == "task":
        effective = _apply_templating_to_task(block, ctx)
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
    elif block.block_type == "group":
        artifact = await _execute_sequence(block.body, ctx)
    else:
        raise TaskExecutorError(f"Unknown block_type: {block.block_type!r}")
    # Register the completed artifact by block id for {{sibling("id")}}
    # lookups by later blocks.  Skip blocks with no id (shouldn't happen
    # post-_assign_block_ids, but guard so a stray empty id can't clobber
    # the registry under the "" key).  Last-write-wins for loop re-runs.
    if block.id:
        ctx.artifact_registry[block.id] = artifact
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
) -> Artifact:
    """Implicit sequence: run top-to-bottom, return the last block's
    artifact.  Cancel is checked between siblings.

    Threads each completed sibling's artifact into ctx.sibling_stack so
    the next sibling can see it (prose auto-context + {{previous_sibling}}).
    Pushes a fresh slot for this depth and pops it on exit so a nested
    sequence never leaks its last sibling to the enclosing one.
    """
    if not blocks:
        return Artifact(summary="", created_at=time.time())
    last: Optional[Artifact] = None
    ctx.sibling_stack.append(None)
    try:
        for i, child in enumerate(blocks):
            if i > 0 and ctx.cancel_requested():
                raise BlockExecutionCancelled()
            last = await execute_block(child, ctx)
            # Make this sibling's result visible to the next sibling.
            ctx.sibling_stack[-1] = last
    finally:
        ctx.sibling_stack.pop()
    assert last is not None
    return last


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
    iterations = _plan_iterations(block)
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
            artifact = await _execute_sequence(block.body, ctx)
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


def _plan_iterations(block: Block) -> List[Dict[str, Any]]:
    """Produce the list of iteration descriptors for a Repeat block."""
    mode = block.repeat_mode or "count"
    if mode == "count":
        n = int(block.repeat_count or 1)
        return [{"index": i, "item": None} for i in range(max(0, n))]
    if mode == "until":
        n_max = int(block.repeat_max or 1)
        return [{"index": i, "item": None} for i in range(max(0, n_max))]
    if mode == "for_each":
        items = task_templating.parse_for_each_source(block.repeat_for_each_source)
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

    for i in range(n_max):
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
            artifact = await _execute_sequence(block.body, ctx)
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
    return await _execute_sequence(block.body, ctx)


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
