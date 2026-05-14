"""
Task card API endpoints.

CRUD endpoints plus a launch endpoint.  Launch creates a TaskRun,
schedules execution in a background task, and returns immediately
with the run_id.  Clients poll GET /task-runs/{run_id} for status.
"""

import asyncio
import time
from fastapi import APIRouter, HTTPException, Query
from typing import List

from ..models.task_card import (
    Block, TaskCard, TaskCardCreate, TaskCardUpdate, TaskCardRun,
)
from ..models.task_run import TaskRun, TaskRunCreate, TaskRunBlockState
from ..storage.projects import ProjectStorage
from ..storage.task_cards import TaskCardStorage
from ..storage.task_runs import TaskRunStorage
from ..agents.task_executor import TaskExecutorError
from ..agents.block_executor import (
    execute_block, ExecutionContext, BlockExecutionCancelled,
)
from ..agents import task_run_stream_relay as _relay
from ..utils.paths import get_ziya_home, get_project_dir
from ..utils.logging_utils import logger

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/task-cards",
    tags=["task-cards"],
)


def _get_storage(project_id: str) -> TaskCardStorage:
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    project = project_storage.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return TaskCardStorage(get_project_dir(project_id))


@router.get("", response_model=List[TaskCard])
async def list_task_cards(
    project_id: str,
    templates_only: bool = Query(False),
):
    """List all task cards in a project, optionally templates only."""
    return _get_storage(project_id).list(templates_only=templates_only)


@router.get("/{card_id}", response_model=TaskCard)
async def get_task_card(project_id: str, card_id: str):
    card = _get_storage(project_id).get(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")
    return card


@router.post("", response_model=TaskCard, status_code=201)
async def create_task_card(project_id: str, body: TaskCardCreate):
    return _get_storage(project_id).create(body)


@router.put("/{card_id}", response_model=TaskCard)
async def update_task_card(project_id: str, card_id: str, body: TaskCardUpdate):
    card = _get_storage(project_id).update(card_id, body)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")
    return card


@router.delete("/{card_id}", status_code=204)
async def delete_task_card(project_id: str, card_id: str):
    if not _get_storage(project_id).delete(card_id):
        raise HTTPException(status_code=404, detail="Task card not found")


@router.post("/{card_id}/duplicate", response_model=TaskCard, status_code=201)
async def duplicate_task_card(
    project_id: str, card_id: str,
    as_template: bool = Query(False),
):
    card = _get_storage(project_id).duplicate(card_id, as_template=as_template)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")
    return card


def _seed_block_states(run_storage: TaskRunStorage, run_id: str, block: Block) -> None:
    """Pre-populate TaskRun.block_states so append_iteration_summary
    has a place to write.  Walks the tree depth-first."""
    if block.id:
        run_storage.set_block_state(run_id, TaskRunBlockState(
            block_id=block.id, block_type=block.block_type, status="queued",
        ))
    for child in block.body or []:
        _seed_block_states(run_storage, run_id, child)

async def _launch_run_for_card(
    project_id: str,
    card_id: str,
    source_conversation_id=None,
) -> TaskRun:
    """Shared helper: validates the card, creates a TaskRun, seeds
    block_states, and schedules the background executor task.
    Returns the run immediately.

    Used by the plain /launch endpoint and by the binding-creation
    endpoint, which needs the run_id before recording the binding.
    """
    storage = _get_storage(project_id)
    card = storage.get(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")

    run_storage = TaskRunStorage(get_project_dir(project_id))
    run = run_storage.create(TaskRunCreate(
        card_id=card_id,
        source_conversation_id=source_conversation_id,
    ))
    storage.record_run(card_id)
    _seed_block_states(run_storage, run.id, card.root)

    from ..context import get_project_root_or_none
    project_root = get_project_root_or_none()

    async def _run(run_id: str, block, project_root):
        logger.info(f"🚀 TASK_RUN: _run coroutine entered for {run_id[:8]}")
        # Lifecycle event emitter — kept local so the run_id/project_id
        # are captured in closure and callers don't have to thread them.
        async def _emit_run(status: str, **extra):
            await _relay.safe_push(run_id, {
                "type": "run_completed" if status != "started" else "run_started",
                "run_id": run_id,
                "status": status,
                "at": time.time(),
                **extra,
            })
        try:
            logger.info(f"🚀 TASK_RUN: {run_id[:8]} → marking running")
            run_storage.update_status(run_id, "running")
            await _emit_run("started")
            ctx = ExecutionContext(
                run_id=run_id,
                project_root=project_root,
                project_id=project_id,
                storage=run_storage,
            )
            logger.info(f"🚀 TASK_RUN: {run_id[:8]} → execute_block start (type={block.block_type})")
            artifact = await execute_block(block, ctx)
            logger.info(
                f"🚀 TASK_RUN: {run_id[:8]} → execute_block returned "
                f"(summary_len={len(artifact.summary)}, failed={artifact.failed})"
            )
            run_storage.set_artifact(run_id, artifact)
            final_status = "failed" if artifact.failed else "done"
            run_storage.update_status(run_id, final_status)
            await _emit_run(final_status)
            logger.info(f"✅ Task run complete: {run_id[:8]}")
        except BlockExecutionCancelled:
            run_storage.update_status(run_id, "cancelled")
            await _emit_run("cancelled")
            logger.info(f"🛑 Task run cancelled: {run_id[:8]}")
        except TaskExecutorError as e:
            run_storage.update_status(run_id, "failed", error=str(e))
            await _emit_run("failed", error=str(e))
            logger.warning(f"❌ Task run failed: {run_id[:8]}: {e}")
        except Exception as e:  # Broad: background task must not bubble
            run_storage.update_status(run_id, "failed", error=str(e))
            await _emit_run("failed", error=str(e))
            logger.error(f"❌ Task run crashed: {run_id[:8]}: {e}", exc_info=True)

    asyncio.create_task(_run(run.id, card.root, project_root))
    logger.info(f"🚀 Task card launched: {card.name} → run {run.id[:8]} (task scheduled)")
    return run


@router.post("/{card_id}/launch")
async def launch_task_card(
    project_id: str, card_id: str, body: TaskCardRun,
) -> TaskRun:
    """Launch a task card — create a TaskRun and start executing in
    the background.  Returns the run immediately; clients poll the
    task-runs endpoints for status and the final artifact.
    """
    return await _launch_run_for_card(
        project_id=project_id, card_id=card_id,
        source_conversation_id=body.source_conversation_id,
    )