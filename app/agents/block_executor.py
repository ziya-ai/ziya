"""
Block executor — the loop controller for Task Card block trees.

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
from dataclasses import dataclass, field
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
        return await execute_task_block(
            effective,
            project_root=ctx.project_root,
            project_id=ctx.project_id,
            run_id=ctx.run_id,
        )
    if block.block_type == "repeat":
        return await _execute_repeat(block, ctx)
    if block.block_type == "parallel":
        return await _execute_parallel(block, ctx)
    if block.block_type == "until":
        return await _execute_until(block, ctx)
    if block.block_type == "schedule":
        return await _execute_schedule_passthrough(block, ctx)
    raise TaskExecutorError(f"Unknown block_type: {block.block_type!r}")


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
    \`index\` -- not informative on its own).
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


def _apply_templating_to_task(block: Block, ctx: ExecutionContext) -> Block:
    """Return a shallow copy of the task block with instructions rendered
    against the innermost active iteration bindings, then prepended with
    an auto-generated iteration-context block so prior results are
    surfaced to the model without requiring explicit templating.
    Returns the block unchanged when no Repeat/Until is active."""
    if not ctx.binding_stack or not block.instructions:
        return block
    bindings = ctx.binding_stack[-1]
    rendered = task_templating.render(block.instructions, bindings)
    context = _build_iteration_context(bindings)
    if not context and rendered == block.instructions:
        return block
    final = f"{context}\n\n{rendered}" if context else rendered
    return block.model_copy(update={"instructions": final})


async def _execute_sequence(
    blocks: List[Block], ctx: ExecutionContext,
) -> Artifact:
    """Implicit sequence: run top-to-bottom, return the last block's
    artifact.  Cancel is checked between siblings."""
    if not blocks:
        return Artifact(summary="", created_at=time.time())
    last: Optional[Artifact] = None
    for i, child in enumerate(blocks):
        if i > 0 and ctx.cancel_requested():
            raise BlockExecutionCancelled()
        last = await execute_block(child, ctx)
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

        if not condition:
            # No condition → behave like Repeat-until-success.
            if not artifact.failed:
                break
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