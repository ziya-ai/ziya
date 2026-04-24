"""
Task run API endpoints.

Read-only for Slice C: list, get.  Cancel, stream, and other
write operations come in later slices.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional

from ..models.task_run import TaskRun
from ..storage.projects import ProjectStorage
from ..storage.task_runs import TaskRunStorage
from ..utils.paths import get_ziya_home, get_project_dir

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/task-runs",
    tags=["task-runs"],
)


def _get_storage(project_id: str) -> TaskRunStorage:
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    project = project_storage.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return TaskRunStorage(get_project_dir(project_id))


@router.get("", response_model=List[TaskRun])
async def list_task_runs(
    project_id: str,
    card_id: Optional[str] = Query(None),
):
    """List runs for a project.  Filter by card_id if supplied."""
    return _get_storage(project_id).list(card_id=card_id)


@router.get("/{run_id}", response_model=TaskRun)
async def get_task_run(project_id: str, run_id: str):
    run = _get_storage(project_id).get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    return run


@router.delete("/{run_id}", status_code=204)
async def delete_task_run(project_id: str, run_id: str):
    """Delete a completed run record.  Does not cancel running runs —
    cancel support lands in a later slice."""
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status == "running":
        raise HTTPException(status_code=409, detail="Cannot delete a running run")
    storage.delete(run_id)
