"""Managed-job API for the frontend (design/job-runner.md).

Jobs started with ``job_start`` were visible only through the model's own
``job_status`` / ``job_wait`` results, which scroll away with the turn.
This exposes the JobManager's view so the open conversation's TaskRail can
show its jobs, open a tail, and stop one.  Read endpoints are plain ``def``
(file reads and pid probes, nothing awaited), as in task_runs.py.

Kill is the one write.  It is a human-initiated stop of a process the model
may be ``job_wait``-ing on; the model then sees ``reason=exit`` with a
signal, the same as a ``max_runtime`` stop.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from ..jobs.manager import JobError, get_job_manager
from ..storage.projects import ProjectStorage
from ..utils.paths import get_ziya_home

router = APIRouter(prefix="/api/v1/projects/{project_id}/jobs", tags=["jobs"])


def _project_root(project_id: str) -> str:
    project = ProjectStorage(get_ziya_home()).get(project_id)
    if not project or not getattr(project, "path", None):
        raise HTTPException(status_code=404, detail="Project not found")
    return project.path


def _job(project_id: str, job_id: str):
    root = _project_root(project_id)
    try:
        return get_job_manager().get(job_id, root), root
    except JobError:
        raise HTTPException(status_code=404, detail="Job not found")


def _tail(journal_path, tail_lines: int) -> str:
    """Last ``tail_lines`` output lines of the journal, bounded.

    Reads from near the end rather than paging from seq 1: a long job's
    journal is thousands of records and this is called on every popover
    open.  A record may hold several lines, so over-fetch and trim.
    """
    from app.shadow.journal import JournalReader
    reader = JournalReader(str(journal_path))
    last = reader.bounds().get("tail_seq") or 0
    if not last:
        return ""
    page = reader.read(max(1, last - tail_lines * 2), max_records=500)
    lines: List[str] = []
    for rec in page.get("records", []):
        if rec.get("t") == "output":
            lines.extend((rec.get("text") or "").splitlines())
        elif rec.get("t") == "exit":
            lines.append(f"→ exit {rec.get('code')}"
                         + (f" (signal {rec['signal']})" if rec.get("signal") else ""))
    return "\n".join(lines[-tail_lines:])


@router.get("")
def list_jobs(
    project_id: str,
    conversation_id: Optional[str] = Query(None),
    state: Optional[str] = Query(None, description="running | exited | lost"),
) -> List[Dict[str, Any]]:
    """Every job of the project (newest first), optionally one conversation's
    or one state's.  Includes task-run-owned jobs; the sidebar index does not."""
    root = _project_root(project_id)
    jobs = get_job_manager().list(root)
    if conversation_id:
        jobs = [j for j in jobs if j.get("conversation_id") == conversation_id]
    if state:
        jobs = [j for j in jobs if j.get("state") == state]
    jobs.sort(key=lambda j: float(j.get("started_at") or 0), reverse=True)
    return jobs


@router.get("/{job_id}")
def get_job(
    project_id: str, job_id: str,
    tail_lines: int = Query(20, ge=1, le=500),
) -> Dict[str, Any]:
    job, _ = _job(project_id, job_id)
    meta = job.meta
    return {
        **job.state(),
        "tail": _tail(job.journal_path, tail_lines),
        "declared_outputs": meta.get("declared_outputs") or [],
        "outputs": meta.get("outputs") or [],
        "outputs_missing": meta.get("outputs_missing"),
        "max_runtime_s": meta.get("max_runtime_s"),
        "keep_awake": meta.get("keep_awake"),
        "obs_id": meta.get("obs_id"),
    }


@router.post("/{job_id}/kill")
def kill_job(project_id: str, job_id: str) -> Dict[str, Any]:
    """TERM the job's process group, KILL after the grace period."""
    _, root = _job(project_id, job_id)
    return get_job_manager().kill(job_id, project_root=root, reason="killed_from_ui")
