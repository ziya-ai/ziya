"""
Task-card scheduler — fires scheduled cards on interval / at /
daily_at / cron.  In-process, asyncio-driven.

Single-writer guarantee
-----------------------
If two Ziya servers are running against the same ~/.ziya home, only
one of them holds the schedule lock and fires.  The other runs an
observer loop that polls the lock for liveness and takes over if
the holder's heartbeat goes stale.

The lock is a JSON file at ~/.ziya/scheduler.lock with shape:
    {"pid": 1234, "host": "x.local", "heartbeat_ms": 1700000000000}

Liveness threshold: 90 seconds since last heartbeat counts as dead.
The holder rewrites the heartbeat every 30 seconds.

Catch-up
--------
On startup (and on lock takeover), each scheduled card whose
`next_fire_at` is in the past is fired exactly once.  Multiple
missed slots collapse to a single fire — this matches cron's
coalesce-on-recovery behaviour.

State persistence
-----------------
Per-card state lives in a single file per project at
<project_dir>/schedule_state.json:
    {
      "<card_id>": {
        "block_id": "...",        # the schedule block in the card
        "next_fire_at": 17000...,
        "last_fire_at": 17000...,
        "fires_so_far": 12,
        "run_ids": ["...", ...]   # capped at most-recent 50
      }, ...
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models.task_card import Block, TaskCard
from ..utils.paths import get_ziya_home, get_project_dir

logger = logging.getLogger(__name__)


_LOCK_HEARTBEAT_INTERVAL_S = 30
_LOCK_STALE_THRESHOLD_S = 90
_TICK_INTERVAL_S = 15  # how often we check for due fires
_RUN_HISTORY_CAP = 50


def _now_ms() -> int:
    return int(time.time() * 1000)


def _state_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "schedule_state.json"


def _read_state(project_id: str) -> Dict[str, Dict[str, Any]]:
    p = _state_path(project_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"schedule_state read failed for {project_id}: {e}")
        return {}


def _write_state(project_id: str, state: Dict[str, Dict[str, Any]]) -> None:
    p = _state_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(p)
    except OSError as e:
        logger.warning(f"schedule_state write failed for {project_id}: {e}")


def _find_schedule_block(root: Block) -> Optional[Block]:
    """Return the topmost schedule block in the tree, or None."""
    if root.block_type == "schedule":
        return root
    for child in root.body or []:
        found = _find_schedule_block(child)
        if found:
            return found
    return None


def compute_next_fire(block: Block, after_ms: int) -> Optional[int]:
    """Return the next fire time (epoch ms) at or after `after_ms`,
    or None if the schedule has no future fire (e.g. one-shot already past).
    """
    mode = (block.schedule_mode or "interval").lower()
    after = datetime.fromtimestamp(after_ms / 1000.0)
    if mode == "interval":
        n = max(1, int(block.schedule_interval_value or 1))
        unit = (block.schedule_interval_unit or "minutes").lower()
        delta = {
            "minutes": timedelta(minutes=n),
            "hours": timedelta(hours=n),
            "days": timedelta(days=n),
        }.get(unit, timedelta(minutes=n))
        return int((after + delta).timestamp() * 1000)
    if mode == "at":
        if not block.schedule_at_iso:
            return None
        try:
            dt = datetime.fromisoformat(block.schedule_at_iso)
        except ValueError:
            logger.warning(f"schedule_at_iso unparseable: {block.schedule_at_iso!r}")
            return None
        ts = int(dt.timestamp() * 1000)
        return ts if ts > after_ms else None  # one-shot doesn't repeat
    if mode == "daily_at":
        hhmm = block.schedule_daily_at or "00:00"
        try:
            hh, mm = hhmm.split(":")
            hh, mm = int(hh), int(mm)
        except ValueError:
            logger.warning(f"schedule_daily_at unparseable: {hhmm!r}")
            return None
        candidate = after.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= after:
            candidate += timedelta(days=1)
        return int(candidate.timestamp() * 1000)
    if mode == "cron":
        # Cron parsing requires a dependency (croniter).  Guard the
        # import so missing-dep environments still load this module;
        # cron-mode schedules silently no-op until the dep is added.
        try:
            from croniter import croniter  # type: ignore
        except ImportError:
            logger.warning("cron schedule requires `croniter`; skipping fire")
            return None
        expr = (block.schedule_cron or "").strip()
        if not expr:
            return None
        try:
            it = croniter(expr, after)
            return int(it.get_next(datetime).timestamp() * 1000)
        except (ValueError, KeyError) as e:
            logger.warning(f"cron expr {expr!r} unparseable: {e}")
            return None
    return None


@dataclass
class _LockHandle:
    path: Path
    pid: int
    host: str

    def write(self) -> None:
        self.path.write_text(json.dumps({
            "pid": self.pid, "host": self.host, "heartbeat_ms": _now_ms(),
        }))

    def is_mine(self) -> bool:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        return data.get("pid") == self.pid and data.get("host") == self.host

    @staticmethod
    def is_stale(path: Path) -> bool:
        if not path.exists():
            return True
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return True
        hb = data.get("heartbeat_ms", 0)
        return (_now_ms() - hb) / 1000.0 > _LOCK_STALE_THRESHOLD_S


def _acquire_lock() -> Optional[_LockHandle]:
    path = get_ziya_home() / "scheduler.lock"
    handle = _LockHandle(path=path, pid=os.getpid(), host=socket.gethostname())
    if path.exists() and not _LockHandle.is_stale(path):
        return None
    handle.write()
    # Re-read to guard against the case where two servers wrote at the
    # same instant; whichever pid the file shows wins.
    if not handle.is_mine():
        return None
    return handle


# ── Fire loop ───────────────────────────────────────────────

@dataclass
class _ScheduledCard:
    """Resolved scheduling target for one card."""
    project_id: str
    card_id: str
    card_name: str
    block: Block        # the schedule block
    body_root: Block    # what to actually execute (the schedule's body
                        # promoted to a synthetic root for one fire)


def _resolve_body_root(schedule_block: Block) -> Block:
    """Return what should be executed when the schedule fires.

    A schedule block with one body element executes that element directly;
    multiple bodies are wrapped in an implicit sequence — represented as
    a synthetic 'parallel'-shaped passthrough is wrong (it would run them
    concurrently); we use a Repeat-count-1 wrapper to preserve sequence
    semantics without inventing a new sequence-block type.
    """
    body = schedule_block.body or []
    if not body:
        # Empty body — nothing to fire; return the schedule itself so
        # the block executor's passthrough returns a well-formed empty
        # artifact rather than raising.
        return schedule_block
    if len(body) == 1:
        return body[0]
    return Block(
        block_type="repeat", id=f"{schedule_block.id}-seq",
        name="(scheduled sequence)", repeat_mode="count", repeat_count=1,
        body=list(body),
    )


def _enumerate_scheduled_cards() -> List[_ScheduledCard]:
    """Walk every project's task_cards and collect cards with a
    schedule block.  Cheap enough to do on every tick — projects and
    cards are bounded; storage is local JSON."""
    from ..storage.projects import ProjectStorage
    from ..storage.task_cards import TaskCardStorage
    out: List[_ScheduledCard] = []
    try:
        projects = ProjectStorage(get_ziya_home()).list()
    except Exception as e:
        logger.debug(f"scheduler: projects enumeration failed: {e}")
        return out
    for proj in projects:
        try:
            cards = TaskCardStorage(get_project_dir(proj.id)).list()
        except Exception as e:
            logger.debug(f"scheduler: cards enum failed for {proj.id}: {e}")
            continue
        for card in cards:
            sched = _find_schedule_block(card.root)
            if sched is None:
                continue
            if not sched.schedule_enabled:
                continue
            out.append(_ScheduledCard(
                project_id=proj.id, card_id=card.id, card_name=card.name,
                block=sched, body_root=_resolve_body_root(sched),
            ))
    return out


async def _fire_one(target: _ScheduledCard) -> None:
    """Launch a TaskRun for one schedule fire and update state."""
    from ..models.task_run import TaskRunCreate, TaskRunBlockState
    from ..storage.task_runs import TaskRunStorage
    from ..storage.task_cards import TaskCardStorage
    from .block_executor import (
        execute_block, ExecutionContext, BlockExecutionCancelled,
    )
    from .task_executor import TaskExecutorError

    project_dir = get_project_dir(target.project_id)
    run_storage = TaskRunStorage(project_dir)
    card_storage = TaskCardStorage(project_dir)

    run = run_storage.create(TaskRunCreate(
        card_id=target.card_id, source_conversation_id=None,
    ))
    card_storage.record_run(target.card_id)
    # Seed block_states for the body so iteration_summaries can write.
    def _seed(b: Block) -> None:
        if b.id:
            run_storage.set_block_state(run.id, TaskRunBlockState(
                block_id=b.id, block_type=b.block_type, status="queued",
            ))
        for child in b.body or []:
            _seed(child)
    _seed(target.body_root)

    # Update schedule_state.json: bump fires_so_far, append run_id,
    # write the next_fire_at projected from "now".
    state = _read_state(target.project_id)
    rec = state.setdefault(target.card_id, {
        "block_id": target.block.id, "fires_so_far": 0,
        "last_fire_at": None, "next_fire_at": None, "run_ids": [],
    })
    rec["block_id"] = target.block.id  # in case it changed
    rec["fires_so_far"] = int(rec.get("fires_so_far", 0)) + 1
    rec["last_fire_at"] = _now_ms()
    rec["next_fire_at"] = compute_next_fire(target.block, _now_ms())
    rec["run_ids"] = ([run.id] + list(rec.get("run_ids", [])))[:_RUN_HISTORY_CAP]
    _write_state(target.project_id, state)

    logger.info(
        f"⏰ Scheduler fired card {target.card_name!r} "
        f"(project={target.project_id}, run={run.id[:8]})"
    )

    # Background-run the body, mirroring the launch endpoint's pattern.
    async def _go() -> None:
        try:
            run_storage.update_status(run.id, "running")
            ctx = ExecutionContext(
                run_id=run.id, project_root=None,
                project_id=target.project_id, storage=run_storage,
            )
            artifact = await execute_block(target.body_root, ctx)
            run_storage.set_artifact(run.id, artifact)
            run_storage.update_status(
                run.id, "failed" if artifact.failed else "done",
            )
        except BlockExecutionCancelled:
            run_storage.update_status(run.id, "cancelled")
        except TaskExecutorError as e:
            run_storage.update_status(run.id, "failed", error=str(e))
        except Exception as e:
            run_storage.update_status(run.id, "failed", error=str(e))
            logger.error(f"Scheduled run crashed: {run.id[:8]}: {e}", exc_info=True)
    asyncio.create_task(_go())


async def _scheduler_loop() -> None:
    """The single-writer fire loop.  Acquires the lock, then ticks
    every _TICK_INTERVAL_S until shutdown.  If the lock can't be
    acquired or goes stale on a peer, polls until takeover is possible.
    """
    handle: Optional[_LockHandle] = None
    last_heartbeat = 0.0
    logger.info("⏰ Task scheduler loop starting")
    try:
        while True:
            try:
                if handle is None:
                    handle = _acquire_lock()
                    if handle is None:
                        await asyncio.sleep(_LOCK_HEARTBEAT_INTERVAL_S)
                        continue
                    logger.info(f"⏰ Scheduler lock acquired by pid={handle.pid}")
                    last_heartbeat = time.time()

                # Heartbeat
                if time.time() - last_heartbeat > _LOCK_HEARTBEAT_INTERVAL_S:
                    if handle.is_mine():
                        handle.write()
                        last_heartbeat = time.time()
                    else:
                        logger.warning("⏰ Scheduler lost lock; will retry")
                        handle = None
                        continue

                # Fire pass
                now_ms = _now_ms()
                for target in _enumerate_scheduled_cards():
                    state = _read_state(target.project_id)
                    rec = state.get(target.card_id, {})
                    next_fire = rec.get("next_fire_at")
                    fires_so_far = int(rec.get("fires_so_far", 0))
                    if (target.block.schedule_max_runs is not None
                            and fires_so_far >= int(target.block.schedule_max_runs)):
                        continue
                    if next_fire is None:
                        rec["block_id"] = target.block.id
                        rec["next_fire_at"] = compute_next_fire(target.block, now_ms)
                        rec.setdefault("fires_so_far", 0)
                        rec.setdefault("run_ids", [])
                        state[target.card_id] = rec
                        _write_state(target.project_id, state)
                        continue
                    if next_fire <= now_ms:
                        if not target.block.schedule_catch_up and fires_so_far > 0:
                            rec["next_fire_at"] = compute_next_fire(target.block, now_ms)
                            state[target.card_id] = rec
                            _write_state(target.project_id, state)
                            continue
                        await _fire_one(target)

                # System-job pass — internal periodic jobs (memory organize,
                # etc.) that ride this same single-writer loop.  Each job is
                # interval-gated and isolated; a failure here never affects
                # card firing.  See app/agents/system_jobs.py.
                try:
                    from app.agents.system_jobs import tick_system_jobs
                    await tick_system_jobs(now_ms)
                except Exception as e:
                    logger.warning(f"⏰ System-job tick error (continuing): {e}")
            except Exception as e:
                logger.warning(f"⏰ Scheduler tick error (continuing): {e}", exc_info=True)
            await asyncio.sleep(_TICK_INTERVAL_S)
    finally:
        # Release the lock so a peer process can take over without
        # waiting for the heartbeat-staleness threshold.  This runs
        # whether the loop exits via CancelledError, a fatal exception,
        # or normal return — all of which leave the lock orphaned
        # otherwise.
        if handle is not None and handle.is_mine():
            try:
                handle.path.unlink(missing_ok=True)
                logger.info("⏰ Scheduler loop exiting; lock released")
            except OSError as e:
                logger.debug(f"scheduler: lock unlink on exit failed: {e}")


def start_scheduler() -> "asyncio.Task[None]":
    """Spawn the scheduler as a background task.  Idempotent within
    a single process; concurrent processes coordinate via the lock."""
    return asyncio.create_task(_scheduler_loop(), name="task_scheduler")