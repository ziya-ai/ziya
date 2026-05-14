"""
Task run API endpoints.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional, Dict, Any

from ..models.task_card import Artifact
from ..models.task_run import TaskRun, IterationSummary, IterationStatus
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
    use POST /cancel to stop a running run first."""
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status == "running":
        raise HTTPException(status_code=409, detail="Cannot delete a running run")
    storage.delete(run_id)


@router.post("/{run_id}/cancel", response_model=TaskRun)
async def cancel_task_run(project_id: str, run_id: str):
    """Soft-cancel a running task run.  Sets the cancel flag; the block
    executor picks it up at the next iteration or sibling boundary.
    In-flight Task invocations complete normally.  See
    design/task-cards.md §Cancellation.
    """
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status in ("done", "failed", "cancelled"):
        # Idempotent: already terminal, return unchanged.
        return run
    return storage.request_cancel(run_id)


@router.get("/{run_id}/iterations")
async def list_iterations(
    project_id: str, run_id: str,
    block_id: Optional[str] = Query(None),
    status: Optional[IterationStatus] = Query(None),
    signature: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    include: str = Query("", description="comma-list; 'artifact' hydrates full Artifacts"),
) -> Dict[str, Any]:
    """Filter iteration summaries across one or all Repeat blocks in a run.

    Defaults return summaries only; pass include=artifact to also load
    each match's full Artifact from disk (expensive at scale — use a
    tight filter with include=artifact).
    """
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")

    includes = {tok.strip() for tok in include.split(",") if tok.strip()}
    hydrate = "artifact" in includes

    matches: List[Dict[str, Any]] = []
    for bid, state in run.block_states.items():
        if block_id and bid != block_id:
            continue
        for summary in state.iteration_summaries:
            if status and summary.status != status:
                continue
            if signature and summary.signature != signature:
                continue
            row: Dict[str, Any] = {
                "block_id": bid,
                "summary": summary.model_dump(),
            }
            if hydrate and summary.has_artifact:
                artifact = storage.read_iteration_artifact(
                    run_id, bid, summary.index,
                )
                row["artifact"] = artifact.model_dump() if artifact else None
            matches.append(row)

    matches.sort(key=lambda r: (r["block_id"], r["summary"]["index"]))
    return {
        "total": len(matches),
        "limit": limit,
        "offset": offset,
        "items": matches[offset:offset + limit],
    }


@router.get("/{run_id}/iterations/{block_id}/{index}", response_model=Artifact)
async def get_iteration_artifact(
    project_id: str, run_id: str, block_id: str, index: int,
):
    """Fetch the full Artifact for one iteration.  Returns 404 if the
    iteration was retained as a summary-only record (beyond the
    pass-retention cap) or if it never existed."""
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    artifact = storage.read_iteration_artifact(run_id, block_id, index)
    if not artifact:
        raise HTTPException(status_code=404, detail="Iteration artifact not found")
    return artifact
