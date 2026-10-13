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
        "run_ids": ["...", ...],  # capped at most-recent 50
        # Present once the concurrency gate has acted (see _concurrency_gate):
        "skips_so_far": 1, "last_skip_at": 17000...,
        "skips": [{"at": ..., "live_run_id": "...", "reason": "..."}],
        "deferred_since": 17000..., "deferred_behind_run_id": "..."
      }, ...
    }

Concurrency
-----------
A due slot whose card already has a LIVE run (any run of the card —
scheduled or hand-launched) is governed by the schedule block's
`schedule_concurrency`: skip (default; the slot is dropped and recorded),
queue (held until the live run ends), or parallel (fire anyway).  The
gate runs before the catch-up gate, so a catch-up fire is subject to it.
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


# The in-flight system-job pass, if any.  The scheduler loop spawns the
# pass instead of awaiting it: the loop body is the ONLY place the lock
# heartbeat is written, so awaiting a multi-minute memory_organize inline
# meant no heartbeat for its whole duration.  After _LOCK_STALE_THRESHOLD_S
# a peer server declared the lock stale, took it over and started its own
# organize; the two then fought over the memories filelock, each poll-sleep
# parking its own event loop (two servers, ~10 minutes of stalled users,
# 2026-10-09).  The single-writer guarantee depended on a loop the job was
# blocking.
_system_jobs_task: Optional["asyncio.Task[Any]"] = None


def _spawn_system_jobs_tick(now_ms: int) -> bool:
    """Start a system-job pass as a background task unless one is still
    running.  Returns True when a pass was started."""
    global _system_jobs_task
    if _system_jobs_task is not None and not _system_jobs_task.done():
        return False

    async def _pass() -> None:
        try:
            from app.agents.system_jobs import tick_system_jobs
            await tick_system_jobs(now_ms)
        except Exception as e:  # noqa: BLE001 -- never affects card firing
            logger.warning(f"⏰ System-job tick error (continuing): {e}")

    _system_jobs_task = asyncio.get_running_loop().create_task(
        _pass(), name="ziya-system-jobs")
    return True


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
    card = card_storage.get(target.card_id)
    card_scope = getattr(card, "scope", None) if card is not None else None

    run = run_storage.create(TaskRunCreate(
        card_id=target.card_id, source_conversation_id=None,
        # A scheduler fire has no attached session: nobody is watching to
        # sign an escalation mid-run.  Marking it headless makes the
        # scope-authorization seam fail an unsigned escalation loudly
        # rather than shipping a silently-clamped run nobody asked for.
        launch_context="headless",
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

    # Record what this fire executes, the same way the interactive launch
    # path does (app/api/task_cards.py).  The scheduler re-reads the card
    # on every tick, so a fire always runs the CURRENT definition — but
    # without a snapshot nothing on the run says which definition that
    # was, and an operator diagnosing a bad night cannot tell whether the
    # card they are reading is the one that ran.  The snapshot is also
    # what resume-from-block walks (TaskRunStorage reads
    # ``card_snapshot.root``), so its absence made every scheduled run
    # un-resumable.  ``body_root``, not ``card.root``: block_states were
    # seeded from the resolved body above, and the snapshot must describe
    # the same tree those ids belong to.
    try:
        run_storage.set_card_snapshot(run.id, {
            "name": card.name if card is not None else target.card_name,
            "description": getattr(card, "description", "") or "",
            "version": getattr(card, "version", None) if card is not None else None,
            "root": target.body_root.model_dump(),
            # Marks the tree as the schedule's resolved body so a reader
            # comparing it against the stored card knows why the schedule
            # block itself is absent.
            "schedule_block_id": target.block.id,
        })
    except Exception as e:  # noqa: BLE001 — audit record must never block a fire
        logger.warning(f"⏰ Scheduler: card_snapshot capture failed: {e}")
    try:
        from ..utils.permissions_snapshot import build_permissions_snapshot
        from ..storage.projects import ProjectStorage as _PS
        _proj = _PS(get_ziya_home()).get(target.project_id)
        _deck = getattr(_proj.settings, "taskScope", None) if _proj else None
        from ..models.task_card import merge_scopes
        run_storage.set_permissions_snapshot(run.id, build_permissions_snapshot(
            # project_root=None mirrors the ExecutionContext built in _go()
            # below; the snapshot records what the run was actually granted.
            root_block=target.body_root, project_root=None,
            deck_scope=_deck,
            # The schedule block is not in body_root, but _go() seeds its
            # scope onto the stack, so it IS an ancestor layer at run time.
            # Fold it into the card layer so the snapshot matches the grant.
            card_scope=merge_scopes(card_scope, target.block.scope),
        ))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"⏰ Scheduler: permissions_snapshot capture failed: {e}")

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
            from ..storage.projects import ProjectStorage
            proj = ProjectStorage(get_ziya_home()).get(target.project_id)
            deck_scope = getattr(proj.settings, "taskScope", None) if proj else None
            ctx = ExecutionContext(
                run_id=run.id, project_root=None,
                project_id=target.project_id, storage=run_storage,
                deck_scope=deck_scope, card_scope=card_scope,
                # _resolve_body_root promotes the schedule's body to a
                # synthetic new root (or unwraps to a single child), so
                # the schedule block itself is never re-visited by
                # execute_block.  Seed its own scope onto the stack so
                # it still contributes to the effective-scope merge for
                # everything the fire executes.
                scope_stack=[target.block.scope],
            )
            artifact = await execute_block(target.body_root, ctx)
            run_storage.set_artifact(run.id, artifact)
            _final = "failed" if artifact.failed else "done"
            run_storage.update_status(run.id, _final)
            # Feed what the run established into probationary memory
            # (app/agents/run_memory_harvest.py).  Fire-and-forget.
            from .run_memory_harvest import schedule_harvest
            schedule_harvest(run_id=run.id, card=card, artifact=artifact,
                             status=_final,
                             project_path=getattr(proj, "path", None))
        except BlockExecutionCancelled:
            run_storage.update_status(run.id, "cancelled")
        except TaskExecutorError as e:
            run_storage.update_status(run.id, "failed", error=str(e))
        except Exception as e:
            run_storage.update_status(run.id, "failed", error=str(e))
            logger.error(f"Scheduled run crashed: {run.id[:8]}: {e}", exc_info=True)
    asyncio.create_task(_go())


def _live_runs_for_card(project_id: str, card_id: str) -> List[Any]:
    """Runs of this card whose executor is still alive, newest first.

    Every run of the card counts — a hand-launched one as much as an
    earlier fire — because the overlap the concurrency gate exists to
    prevent is "two copies of this body working the same workspace",
    and the body does not care which button started the other copy.
    """
    from ..models.task_run import LIVE_RUN_STATUSES
    from ..storage.task_runs import TaskRunStorage
    try:
        runs = TaskRunStorage(get_project_dir(project_id)).list(card_id=card_id)
    except Exception as e:  # noqa: BLE001 — an unreadable run dir must not stop the tick
        logger.warning(f"⏰ Scheduler: run listing failed for {card_id[:8]}: {e}")
        return []
    live = [r for r in runs if r.status in LIVE_RUN_STATUSES]
    live.sort(key=lambda r: getattr(r, "created_at", 0) or 0, reverse=True)
    return live


def _concurrency_gate(
    target: _ScheduledCard, rec: Dict[str, Any], now_ms: int,
) -> bool:
    """Decide whether a due slot may fire while a run of the card is live.

    Returns True to fire.  Mutates ``rec`` (the card's schedule_state
    record) when the slot is not fired, so the outcome is on disk and
    readable through GET …/schedule-state:

      * ``skip`` (the default) — drops the slot, appends to ``skips`` and
        advances ``next_fire_at`` from now.  A skipped night is a
        visible event, not silence: "skipped: run <id> still live".
      * ``queue`` — holds the slot (``next_fire_at`` unchanged) and
        marks ``deferred_since`` so the next tick after the live run
        ends fires it, bypassing the catch-up gate — a deferral is a
        decision to run late, not a missed slot.
      * ``parallel`` — fires regardless; the pre-field behaviour.

    Checked BEFORE the catch-up gate so a catch-up fire (laptop wakes at
    09:00 with the 03:00 slot due) is gated like any other — the case
    where a hand-launched run is most likely to still be live.
    """
    policy = (target.block.schedule_concurrency or "skip").lower()
    if policy == "parallel":
        return True
    live = _live_runs_for_card(target.project_id, target.card_id)
    if not live:
        return True
    live_id = live[0].id
    if policy == "queue":
        rec.setdefault("deferred_since", now_ms)
        rec["deferred_behind_run_id"] = live_id
        logger.info(
            f"⏰ Scheduler: {target.card_name!r} slot deferred — run "
            f"{live_id[:8]} still live (schedule_concurrency=queue)"
        )
        return False
    # skip
    skips = list(rec.get("skips", []))
    skips.insert(0, {
        "at": now_ms, "live_run_id": live_id,
        "reason": f"skipped: run {live_id[:8]} of this card still live "
                  f"(status={live[0].status}, schedule_concurrency=skip)",
    })
    rec["skips"] = skips[:_RUN_HISTORY_CAP]
    rec["skips_so_far"] = int(rec.get("skips_so_far", 0)) + 1
    rec["last_skip_at"] = now_ms
    rec["next_fire_at"] = compute_next_fire(target.block, now_ms)
    logger.warning(f"⏰ Scheduler: {target.card_name!r} {skips[0]['reason']}")
    return False


async def _tick_card(target: _ScheduledCard, now_ms: int) -> None:
    """One scheduler tick for one card: fire if due and permitted.

    Extracted from the loop so the fire/skip/defer decision is testable
    without the lock or the sleep.  Writes schedule_state whenever it
    changes the record and does not fire.
    """
    state = _read_state(target.project_id)
    rec = state.get(target.card_id, {})
    next_fire = rec.get("next_fire_at")
    fires_so_far = int(rec.get("fires_so_far", 0))
    if (target.block.schedule_max_runs is not None
            and fires_so_far >= int(target.block.schedule_max_runs)):
        return
    if next_fire is None:
        rec["block_id"] = target.block.id
        rec["next_fire_at"] = compute_next_fire(target.block, now_ms)
        rec.setdefault("fires_so_far", 0)
        rec.setdefault("run_ids", [])
        state[target.card_id] = rec
        _write_state(target.project_id, state)
        return
    if next_fire > now_ms:
        return
    if not _concurrency_gate(target, rec, now_ms):
        state[target.card_id] = rec
        _write_state(target.project_id, state)
        return
    deferred = rec.pop("deferred_since", None) is not None
    rec.pop("deferred_behind_run_id", None)
    if not deferred and not target.block.schedule_catch_up and fires_so_far > 0:
        rec["next_fire_at"] = compute_next_fire(target.block, now_ms)
        state[target.card_id] = rec
        _write_state(target.project_id, state)
        return
    if deferred:
        # _fire_one re-reads state; persist the cleared deferral first so
        # the fire's own write does not resurrect it.
        state[target.card_id] = rec
        _write_state(target.project_id, state)
    await _fire_one(target)


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
                    await _tick_card(target, now_ms)

                # System-job pass — internal periodic jobs (memory organize,
                # etc.) that ride this same single-writer loop.  Each job is
                # interval-gated and isolated; a failure here never affects
                # card firing.  Spawned, not awaited, so the heartbeat above
                # keeps the lock while a long job runs (see _spawn_system_jobs_tick).
                _spawn_system_jobs_tick(now_ms)
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