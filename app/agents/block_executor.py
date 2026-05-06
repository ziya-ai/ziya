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
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..models.task_card import Artifact, ArtifactPart, Block
from ..models.task_run import IterationStatus, IterationSummary, TaskRunBlockState
from ..storage.task_runs import TaskRunStorage
from .task_executor import TaskExecutorError, execute_task_block
from . import task_run_stream_relay as _relay

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
    storage: Optional[TaskRunStorage] = None
    # Per-block pass-retention counters.  Keyed by block.id.
    pass_counts: Dict[str, int] = field(default_factory=dict)

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
    try:
        await _relay.push(ctx.run_id, event)
    except Exception as exc:
        # Live observation is optional — failures must not affect execution.
        logger.debug(f"task_run_stream_relay push failed (non-fatal): {exc}")


async def execute_block(block: Block, ctx: ExecutionContext) -> Artifact:
    """Execute any block — dispatcher over block_type."""
    if block.block_type == "task":
        return await execute_task_block(block, project_root=ctx.project_root)
    if block.block_type == "repeat":
        return await _execute_repeat(block, ctx)
    if block.block_type == "parallel":
        return await _execute_parallel(block, ctx)
    raise TaskExecutorError(f"Unknown block_type: {block.block_type!r}")


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
    last_artifact: Optional[Artifact] = None
    outputs: List[ArtifactPart] = []

    await _emit(ctx, {
        "type": "block_started",
        "block_id": block.id,
        "block_type": "repeat",
        "planned": len(iterations),
        "at": time.time(),
    })

    async def _run_one(index: int) -> Artifact:
        await _emit(ctx, {
            "type": "iteration_started",
            "block_id": block.id, "index": index,
        })
        iter_start = time.time()
        artifact = await _execute_sequence(block.body, ctx)
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
        tasks = [asyncio.create_task(_run_one(i)) for i in range(len(iterations))]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Artifact):
                last_artifact = r
                outputs.extend(r.outputs)
    else:
        for i in range(len(iterations)):
            if ctx.cancel_requested():
                raise BlockExecutionCancelled()
            artifact = await _run_one(i)
            last_artifact = artifact
            outputs.extend(artifact.outputs)
            if block.repeat_mode == "until" and not artifact.failed:
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
        # for_each source resolution happens via templating elsewhere;
        # for now treat it like count bounded by repeat_max or 1.
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
