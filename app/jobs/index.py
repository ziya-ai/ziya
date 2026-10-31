"""Per-conversation job summaries for the sidebar's run-status index.

A job started with ``job_start`` lived only in the tool results of the turn
that started it, which scroll away in a long conversation.  The sidebar
already polls ``/task-runs/status-index`` for task runs; this adds the
chat-started jobs of a project to that same payload, so a job running in a
conversation the user is not looking at still shows on its row.

Only jobs with NO ``run_id`` owner are included.  A job started from inside
a Task Card run is already represented by that run's gear; counting it
again would indicate the same work twice.

Summaries carry state and times, not the command or owner: the sidebar
renders a glyph and a tooltip, and the open conversation's rail reads the
full records from the jobs API instead.
"""
from __future__ import annotations

from typing import Any, Dict, List

SUMMARY_FIELDS = ("job_id", "label", "state", "exit_code", "signal", "reason",
                  "started_at", "ended_at", "runtime_s", "duration_s")


def is_chat_started(state: Dict[str, Any]) -> bool:
    """True for a job the chat started directly (not a Task Card block)."""
    return not (state.get("owner") or {}).get("run_id")


def summarize_job(state: Dict[str, Any]) -> Dict[str, Any]:
    return {k: state[k] for k in SUMMARY_FIELDS if state.get(k) is not None}


def conversation_job_index(project_root: str) -> Dict[str, List[Dict[str, Any]]]:
    """conversation_id -> chat-started job summaries, newest first."""
    from app.jobs.manager import get_job_manager
    out: Dict[str, List[Dict[str, Any]]] = {}
    for st in get_job_manager().list(project_root):
        if not is_chat_started(st):
            continue
        conv = st.get("conversation_id")
        if not conv:
            continue
        out.setdefault(str(conv), []).append(summarize_job(st))
    for jobs in out.values():
        jobs.sort(key=lambda j: float(j.get("started_at") or 0), reverse=True)
    return out


def has_running_jobs(index: Dict[str, List[Dict[str, Any]]]) -> bool:
    """True while any listed job can still change on its own."""
    return any(j.get("state") == "running" for jobs in index.values() for j in jobs)
