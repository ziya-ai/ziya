"""
Task card API endpoints.

CRUD endpoints plus a launch endpoint.  Launch creates a TaskRun,
schedules execution in a background task, and returns immediately
with the run_id.  Clients poll GET /task-runs/{run_id} for status.
"""

import asyncio
from fastapi import APIRouter, HTTPException, Query
from typing import List

from ..models.task_card import (
    TaskCard, TaskCardCreate, TaskCardUpdate, TaskCardRun,
)
from ..models.task_run import TaskRun, TaskRunCreate
from ..storage.projects import ProjectStorage
from ..storage.task_cards import TaskCardStorage
from ..storage.task_runs import TaskRunStorage
from ..agents.task_executor import execute_task_block, TaskExecutorError
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


@router.post("/{card_id}/launch")
async def launch_task_card(
    project_id: str, card_id: str, body: TaskCardRun,
) -> TaskRun:
    """Launch a task card — create a TaskRun and start executing in
    the background.  Returns the run immediately; clients poll the
    task-runs endpoints for status and the final artifact.

    Slice C: only supports cards whose root is a single Task block.
    Repeat / Parallel root blocks are rejected.
    """
    storage = _get_storage(project_id)
    card = storage.get(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Task card not found")

    # Validate the root is executable in Slice C before we create a run
    if card.root.block_type != "task":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cards with root block_type='{card.root.block_type}' are not "
                "yet executable; Slice C supports only Task roots."
            ),
        )

    run_storage = TaskRunStorage(get_project_dir(project_id))
    run = run_storage.create(TaskRunCreate(
        card_id=card_id,
        source_conversation_id=body.source_conversation_id,
    ))
    storage.record_run(card_id)

    # Fire-and-forget background execution.  Project-scoped ContextVar
    # is captured at dispatch time so the background task has the
    # right project root.
    from ..context import get_project_root_or_none
    project_root = get_project_root_or_none()

    async def _run(run_id: str, block, project_root):
        try:
            run_storage.update_status(run_id, "running")
            artifact = await execute_task_block(block, project_root=project_root)
            run_storage.set_artifact(run_id, artifact)
            run_storage.update_status(run_id, "done")
            logger.info(f"✅ Task run complete: {run_id[:8]}")
        except TaskExecutorError as e:
            run_storage.update_status(run_id, "failed", error=str(e))
            logger.warning(f"❌ Task run failed: {run_id[:8]}: {e}")
        except Exception as e:  # Broad: background task must not bubble
            run_storage.update_status(run_id, "failed", error=str(e))
            logger.error(f"❌ Task run crashed: {run_id[:8]}: {e}", exc_info=True)

    asyncio.create_task(_run(run.id, card.root, project_root))
    logger.info(f"🚀 Task card launched: {card.name} → run {run.id[:8]}")
    return run
