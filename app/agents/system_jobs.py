"""
Internal periodic system jobs — interval-gated background work that rides
the task-scheduler's single-writer loop (app/agents/task_scheduler.py).

This is the minimal first step toward a general user-task/workflow cron
kernel.  The registry interface is deliberately a superset target: a job
is just (name, interval, gate, run).  Today the only registered job is
memory-organize; later, user-authored scheduled workflows become
additional registered jobs without changing the loop integration.

Why a separate layer from Task Cards:
  - Task Cards fire user-defined TaskRuns and live per-project.
  - System jobs are process-internal maintenance (organize memory, etc.),
    gated on runtime config, with their own state file.

Single-writer guarantee: tick_system_jobs() is only ever called from the
scheduler loop, which holds ~/.ziya/scheduler.lock.  So across multiple
Ziya servers sharing a home, exactly one fires these jobs.

State lives at ~/.ziya/system_jobs.json:
    {
      "<job_name>": {
        "last_check_ms": 17000...,   # last time the (maybe-expensive) gate ran
        "last_run_ms": 17000...,     # last time the job actually executed
        "runs_so_far": 12
      }, ...
    }

The interval gates the *check* cadence (the gate predicate is evaluated at
most once per interval), so a per-tick (15s) loop doesn't run an O(N) store
scan every tick.  The gate then decides whether work actually happens.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..utils.logging_utils import logger
from ..utils.paths import get_ziya_home


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class SystemJob:
    """A registered internal periodic job.

    name:       stable key for state persistence and logging.
    interval_s: minimum seconds between gate evaluations (check cadence).
    gate:       sync predicate — return True when the job should run NOW.
                Evaluated at most once per interval.  Keep store scans here,
                not in run(), so they don't run every loop tick.
    run:        async work.  Exceptions are caught and logged, never fatal.
    """
    name: str
    interval_s: int
    gate: Callable[[], bool]
    run: Callable[[], Awaitable[None]]


_JOBS: List[SystemJob] = []
_registered = False


def register_system_job(job: SystemJob) -> None:
    """Register a job.  Idempotent by name — re-registering replaces."""
    global _JOBS
    _JOBS = [j for j in _JOBS if j.name != job.name]
    _JOBS.append(job)


def _state_path() -> Path:
    return get_ziya_home() / "system_jobs.json"


def _read_state() -> Dict[str, Dict[str, Any]]:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"system_jobs state read failed: {e}")
        return {}


def _write_state(state: Dict[str, Dict[str, Any]]) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(p)
    except OSError as e:
        logger.warning(f"system_jobs state write failed: {e}")


async def tick_system_jobs(now_ms: Optional[int] = None) -> List[str]:
    """Evaluate all registered jobs.  For each job whose check-interval has
    elapsed, run its gate; if the gate passes, execute it.  Returns the list
    of job names that actually ran this tick (for tests / observability).

    Per-job isolation: one job's failure never blocks another.
    """
    _ensure_registered()
    if now_ms is None:
        now_ms = _now_ms()
    if not _JOBS:
        return []

    state = _read_state()
    ran: List[str] = []
    dirty = False

    for job in _JOBS:
        rec = state.setdefault(job.name, {})
        # Interval gates the check cadence (avoid per-tick gate scans).  A
        # never-checked job (key absent) always checks on first tick; using
        # key-presence rather than truthiness so a legitimate first check at
        # now_ms==0 (low clock / tests) isn't mistaken for "never checked".
        last_check_raw = rec.get("last_check_ms")
        if last_check_raw is not None and now_ms - int(last_check_raw) < job.interval_s * 1000:
            continue
        rec["last_check_ms"] = now_ms
        dirty = True
        try:
            should = bool(job.gate())
        except Exception as e:
            logger.warning(f"⏰ system-job {job.name!r} gate failed (skip): {e}")
            continue
        if not should:
            logger.debug(f"⏰ system-job {job.name!r}: gate declined")
            continue
        try:
            logger.info(f"⏰ system-job {job.name!r}: firing")
            await job.run()
            rec["last_run_ms"] = now_ms
            rec["runs_so_far"] = int(rec.get("runs_so_far", 0)) + 1
            ran.append(job.name)
            logger.info(f"⏰ system-job {job.name!r}: completed")
        except Exception as e:
            logger.error(f"⏰ system-job {job.name!r} run failed: {e}", exc_info=True)

    if dirty:
        _write_state(state)
    return ran


# ── Built-in jobs ─────────────────────────────────────────────────────────

# Memory organize: every 6h, but only when there's something to do —
# orphans pending OR no organize in the last 24h (a freshness pass on a
# stable corpus).  Organize calls the LLM, so the gate prevents burning
# model spend clustering nothing on every idle install.
MEMORY_ORGANIZE_INTERVAL_S = 6 * 60 * 60
MEMORY_ORGANIZE_STALE_MS = 24 * 60 * 60 * 1000


def _memory_organize_gate() -> bool:
    from app.config.env_registry import ziya_env
    if not ziya_env("ZIYA_ENABLE_MEMORY"):
        return False
    try:
        from app.storage.memory import get_memory_storage
        from app.memory import get_review_summary, load_organize_history
        store = get_memory_storage()
        review = get_review_summary(store)
        # Nothing in the store at all → nothing to organize.
        if (review.get("total_memories", 0) or 0) <= 0:
            return False
        orphans = int(review.get("orphan_count", 0) or 0)
        if orphans > 0:
            return True
        # No orphans — only run if the last organize is stale (>24h).
        history = load_organize_history()  # newest-first
        if not history:
            return True
        last_ts = int(history[0].get("timestamp", 0) or 0)
        return (_now_ms() - last_ts) > MEMORY_ORGANIZE_STALE_MS
    except Exception as e:
        logger.debug(f"memory-organize gate check failed: {e}")
        return False


async def _memory_organize_run() -> None:
    from app.memory import reorganize
    result = await reorganize()
    bootstrap = result.get("bootstrap", {}) if isinstance(result, dict) else {}
    cross = len(result.get("cross_links", []) or []) if isinstance(result, dict) else 0
    logger.info(
        f"⏰ system-job memory_organize: "
        f"{bootstrap.get('domains_created', 0)} domains created, "
        f"{bootstrap.get('memories_placed', 0)} placed, {cross} cross-links"
    )


def _ensure_registered() -> None:
    """Register built-in jobs once.  Lazy so import order can't break it."""
    global _registered
    if _registered:
        return
    _registered = True
    register_system_job(SystemJob(
        name="memory_organize",
        interval_s=MEMORY_ORGANIZE_INTERVAL_S,
        gate=_memory_organize_gate,
        run=_memory_organize_run,
    ))


def _reset_for_tests() -> None:
    """Test hook: clear the registry and re-arm lazy registration."""
    global _JOBS, _registered
    _JOBS = []
    _registered = False
