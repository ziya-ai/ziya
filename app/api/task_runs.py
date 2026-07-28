"""
Task run API endpoints.
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
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
    """Cancel a running task run.

    Two paths:

    * **Soft-cancel (live executor).** When the run's executor is alive
      in *this* process, set ``cancel_requested`` and return.  The
      block executor honors the flag at the next iteration / sibling
      boundary and any in-flight Task invocation completes normally.
      This is the design/task-cards.md §Cancellation path.

    * **Force-cancel (zombie run).** When on-disk status is ``running``
      but no live executor exists for this run in this process, the
      run is a zombie left over from a prior server lifetime: the
      executor coroutine was killed by the restart and no flag-watcher
      will ever see ``cancel_requested``.  Mark the run ``cancelled``
      directly so the UI reflects reality.  The startup reconciler
      catches most of these; this branch handles a zombie that arrived
      *during* this server's lifetime (e.g. crash without restart).
    """
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status in ("done", "failed", "cancelled"):
        # Idempotent: already terminal, return unchanged.
        return run
    # Live executor: standard soft-cancel path.
    if storage.is_active(run_id):
        return storage.request_cancel(run_id)
    # No live executor: force the terminal state directly so the UI
    # cancel button is not silently a no-op.
    import time as _time
    run.status = "cancelled"  # type: ignore[assignment]
    run.cancel_requested = True
    if run.completed_at is None:
        run.completed_at = _time.time()
    run.updated_at = int(_time.time() * 1000)
    storage._write_json(storage._run_file(run_id), run.model_dump())
    return run


@router.post("/{run_id}/pause", response_model=TaskRun)
async def pause_task_run(project_id: str, run_id: str):
    """Request a soft pause on a running task run.

    Sets ``pause_requested`` on the run file; the executor observes it
    at the next boundary (between Repeat iterations, sequence siblings,
    or until loops — the same boundaries soft-cancel uses) and flips
    status to ``paused``.  An in-flight Task/LLM invocation is never
    interrupted — pause lands at the next boundary.

    The on-disk flag is the cross-process channel (the executor reads
    it via storage.get), exactly like soft-cancel.  We deliberately do
    NOT gate on the process-local live-run set: it is per-instance and
    a fresh storage object is built per request, so it cannot see a
    coroutine that marked itself active on a different instance.  A
    genuinely dead run (zombie) simply never observes the flag — a
    no-op that the startup reconciler cleans up.  Idempotent on
    terminal runs.
    """
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status in ("done", "failed", "cancelled"):
        return run  # terminal — nothing to pause
    return storage.request_pause(run_id)


@router.post("/{run_id}/resume", response_model=TaskRun)
async def resume_task_run(project_id: str, run_id: str):
    """Clear a run's pause flag so its executor's wait-loop resumes.

    The executor restores status to ``running`` at its next poll.
    Idempotent on terminal runs and on runs that aren't paused.
    """
    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")
    if run.status in ("done", "failed", "cancelled"):
        return run
    return storage.request_resume(run_id)


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


@router.get("/{run_id}/artifacts/{filename}")
async def get_artifact_blob(project_id: str, run_id: str, filename: str):
    """Serve one frozen artifact blob for a run.

    Reads through ``read_artifact_blob`` so at-rest-encrypted bytes are
    decrypted transparently — the browser must never receive an ALE
    envelope.

    Security: ``filename`` is attacker-influenced (artifact names come
    from model output), so path resolution goes through
    ``resolve_artifact_blob_path``, which refuses separators, ``..``,
    and any symlink resolving outside the run's own artifacts dir.
    Content type is mapped from a fixed extension table and only
    known-safe types are served ``inline``; everything else (notably
    HTML/JS/SVG, which could execute in Ziya's origin) is forced to
    ``application/octet-stream`` with ``Content-Disposition:
    attachment``.
    """
    from ..utils.task_artifacts import (
        INLINE_SAFE_MEDIA_TYPES,
        media_type_for_filename,
        read_artifact_blob,
        resolve_artifact_blob_path,
    )

    storage = _get_storage(project_id)
    run = storage.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Task run not found")

    artifacts_dir = get_project_dir(project_id) / "task_runs" / run_id / "artifacts"
    path, err = resolve_artifact_blob_path(str(artifacts_dir), filename)
    if err:
        # "not found" is a 404; every other rejection is a bad request.
        if "not found" in err:
            raise HTTPException(status_code=404, detail=err)
        raise HTTPException(status_code=400, detail=err)

    blob = read_artifact_blob(str(path))
    if blob is None:
        raise HTTPException(
            status_code=500,
            detail="Artifact exists but could not be read or decrypted",
        )

    media_type = media_type_for_filename(filename)
    if media_type in INLINE_SAFE_MEDIA_TYPES:
        disposition = f'inline; filename="{path.name}"'
    else:
        media_type = "application/octet-stream"
        disposition = f'attachment; filename="{path.name}"'

    return Response(
        content=blob,
        media_type=media_type,
        headers={
            "Content-Disposition": disposition,
            # Frozen at emit time and never rewritten — safe to cache hard.
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
