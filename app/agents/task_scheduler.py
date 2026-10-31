"""
Task-card scheduler — fires scheduled cards on interval / at /
daily_at / cron.  In-process, asyncio-driven.

What is scheduled
-----------------
Registrations (schedule_registry.py), not cards.  A card's Schedule block
is the author's request for a cadence; the user registers the card to put
it on the scheduler and owns the cadence from then on.  Each tick resolves
every enabled registration against its card: the registration supplies a
synthetic Schedule block (cadence, concurrency, catch-up, max runs), the
card supplies the body.

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


# Defaults for fields the author left unset.  These are the values the
# Schedule block editor and the register dialog DISPLAY for an unset field
# (ScheduleBlockEditor.tsx, scheduleCadence.normalizeCadence), so a card
# saved without touching them fires on the cadence the author saw.  They
# used to be minutes / 00:00 here, so "every 2 (hours shown)" fired every
# two minutes.
DEFAULT_INTERVAL_UNIT = "hours"
DEFAULT_DAILY_AT = "09:00"


def schedule_tz(block: Block):
    """The tzinfo a schedule's wall-clock fields are read in: the block's
    ``schedule_timezone`` (an IANA name) or None for the server's local
    time.  An unknown name is reported and falls back to local, so a typo
    shifts the fire rather than silencing it."""
    name = (getattr(block, "schedule_timezone", None) or "").strip()
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception as e:  # noqa: BLE001 — ZoneInfoNotFoundError, bad key types
        logger.warning(f"schedule_timezone {name!r} unknown ({e}); using local time")
        return None


def compute_next_fire(block: Block, after_ms: int) -> Optional[int]:
    """Return the next fire time (epoch ms) at or after `after_ms`,
    or None if the schedule has no future fire (e.g. one-shot already past,
    or a cadence the scheduler cannot read -- see cadence_problem).

    Wall-clock fields (``daily_at``, a naive ``at``, cron) are interpreted
    in ``schedule_timezone`` when set, else local time.
    """
    mode = (block.schedule_mode or "interval").lower()
    tz = schedule_tz(block)
    after = datetime.fromtimestamp(after_ms / 1000.0, tz)
    if mode == "interval":
        n = max(1, int(block.schedule_interval_value or 1))
        unit = (block.schedule_interval_unit or DEFAULT_INTERVAL_UNIT).lower()
        delta = {
            "minutes": timedelta(minutes=n),
            "hours": timedelta(hours=n),
            "days": timedelta(days=n),
        }.get(unit, timedelta(hours=n))
        return int((after + delta).timestamp() * 1000)
    if mode == "at":
        if not block.schedule_at_iso:
            return None
        try:
            dt = datetime.fromisoformat(block.schedule_at_iso)
        except ValueError:
            logger.warning(f"schedule_at_iso unparseable: {block.schedule_at_iso!r}")
            return None
        if dt.tzinfo is None and tz is not None:
            dt = dt.replace(tzinfo=tz)
        ts = int(dt.timestamp() * 1000)
        return ts if ts > after_ms else None  # one-shot doesn't repeat
    if mode == "daily_at":
        hhmm = block.schedule_daily_at or DEFAULT_DAILY_AT
        try:
            hh, mm = hhmm.split(":")
            hh, mm = int(hh), int(mm)
            if not (0 <= hh < 24 and 0 <= mm < 60):
                raise ValueError(hhmm)
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


def cadence_problem(block: Block, now_ms: Optional[int] = None) -> Optional[str]:
    """Why ``compute_next_fire`` would return None for this cadence, in
    one sentence, or None when the cadence is readable.

    Shared by the card validator (so a bad cadence is refused at save and
    launch), the registry (so register / edit refuse it with a 422) and the
    schedules list (so an already-registered bad cadence says what is
    wrong instead of "next slot not computed yet" forever).  A one-shot
    ``at`` whose time has passed is reported too: it is not an error on
    save, but a registration for it will never fire.
    """
    mode = (block.schedule_mode or "interval").lower()
    if mode == "interval":
        n = block.schedule_interval_value
        if n is not None and (not isinstance(n, int) or n < 1):
            return f"interval must be a whole number of at least 1 (got {n!r})"
        unit = block.schedule_interval_unit
        if unit is not None and str(unit).lower() not in ("minutes", "hours", "days"):
            return f"interval unit must be minutes, hours or days (got {unit!r})"
        return None
    if mode == "at":
        iso = block.schedule_at_iso
        if not iso:
            return "no date and time given for a one-shot schedule"
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return f"date/time {iso!r} is not ISO-8601"
        tz = schedule_tz(block)
        if dt.tzinfo is None and tz is not None:
            dt = dt.replace(tzinfo=tz)
        if now_ms is not None and int(dt.timestamp() * 1000) <= now_ms:
            return f"one-shot time {iso} has already passed"
        return None
    if mode == "daily_at":
        hhmm = block.schedule_daily_at or DEFAULT_DAILY_AT
        try:
            hh, mm = hhmm.split(":")
            hh, mm = int(hh), int(mm)
            if not (0 <= hh < 24 and 0 <= mm < 60):
                raise ValueError(hhmm)
        except ValueError:
            return f"daily time {hhmm!r} is not HH:MM (24-hour)"
        return None
    if mode == "cron":
        expr = (block.schedule_cron or "").strip()
        if not expr:
            return "cron expression is empty"
        if len(expr.split()) != 5:
            return f"cron expression {expr!r} needs 5 fields (min hour day month weekday)"
        try:
            from croniter import croniter  # type: ignore
        except ImportError:
            return "cron schedules need the croniter package, which is not installed"
        try:
            croniter(expr, datetime.now())
        except (ValueError, KeyError) as e:
            return f"cron expression {expr!r} is invalid: {e}"
        return None
    return f"unknown schedule mode {mode!r}"


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
    """Walk every project's schedule REGISTRATIONS (schedule_registry.py)
    and resolve each enabled one against its card.  Cheap enough to do on
    every tick — projects and registrations are bounded; storage is local
    JSON.

    A card's own Schedule block is only the author's request; it fires
    nothing until the user registers the card.  The registration carries
    the cadence (possibly edited since), the card carries the body.  The
    card is read with ``get`` so a conversation-only (draft) card can be
    registered like any other.
    """
    from ..storage.projects import ProjectStorage
    from ..storage.task_cards import TaskCardStorage
    from . import schedule_registry as registry
    out: List[_ScheduledCard] = []
    try:
        projects = ProjectStorage(get_ziya_home()).list()
    except Exception as e:
        logger.debug(f"scheduler: projects enumeration failed: {e}")
        return out
    for proj in projects:
        try:
            card_storage = TaskCardStorage(get_project_dir(proj.id))
            regs = registry.ensure_migrated(proj.id, card_storage)
        except Exception as e:
            logger.debug(f"scheduler: registry read failed for {proj.id}: {e}")
            continue
        for card_id, reg in regs.items():
            if not reg.enabled:
                continue
            try:
                card = card_storage.get(card_id)
            except Exception as e:  # noqa: BLE001 — one corrupt card must not stop the tick
                logger.warning(f"scheduler: card {card_id[:8]} unreadable: {e}")
                continue
            if card is None:
                logger.debug(f"scheduler: registration for missing card {card_id[:8]} skipped")
                continue
            source = registry.find_source_block(card)
            out.append(_ScheduledCard(
                project_id=proj.id, card_id=card.id, card_name=card.name,
                block=reg.as_block(source),
                body_root=registry.body_root_for(card, source),
            ))
    return out


async def _fire_one(target: _ScheduledCard) -> str:
    """Launch a TaskRun for one schedule fire and update state.  Returns
    the new run's id.

    The run itself is driven by app/agents/run_driver.py, the same code
    the interactive launch endpoint uses, so a scheduled run takes the
    executor lock (Cancel works; a sibling server's startup reconcile
    leaves it alone), emits run_started / run_completed, is held rather
    than failed on an infrastructure fault, and has its jobs cleaned up.
    """
    from ..models.task_run import TaskRunCreate, TaskRunBlockState
    from ..storage.task_runs import TaskRunStorage
    from ..storage.task_cards import TaskCardStorage

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

    # Hand the run to the shared driver.  ``execute`` is resolved at call
    # time from block_executor so a test that patches
    # app.agents.block_executor.execute_block is honoured.
    from .run_driver import RunLaunch, start_run

    async def _execute(block: Block, ctx: Any) -> Any:
        from . import block_executor
        return await block_executor.execute_block(block, ctx)

    start_run(RunLaunch(
        run_id=run.id, root=target.body_root,
        project_id=target.project_id, project_root=project_root,
        card=card, run_storage=run_storage,
        deck_scope=_deck,
        project_path=getattr(_proj, "path", None),
        # _resolve_body_root promotes the schedule's body to a synthetic
        # root (or unwraps to a single child), so the schedule block itself
        # is never re-visited by execute_block.  Seed its scope onto the
        # stack so it still contributes to the effective-scope merge for
        # everything the fire executes.
        context_kwargs={"scope_stack": [target.block.scope],
                        "root_card_label": target.card_name},
        execute=_execute, label="Scheduled run",
    ))
    return run.id


class NotFireable(ValueError):
    """fire_now could not resolve a runnable target for the card."""


async def fire_now(project_id: str, card_id: str) -> str:
    """Fire a card through the scheduler path immediately, as if its slot
    were due: headless run, card snapshot marked as the schedule's body,
    fire count and run history bumped.  Returns the run id.

    Distinct from the interactive launch (which runs a Schedule root as a
    one-off passthrough and records nothing in schedule_state): this is
    "run the nightly now and have it count".  Uses the registration's
    cadence when the card is registered, else the card's Schedule block,
    else the whole card -- so an unregistered card can be test-fired
    before registering it.
    """
    from ..storage.task_cards import TaskCardStorage
    from . import schedule_registry as registry
    card = TaskCardStorage(get_project_dir(project_id)).get(card_id)
    if card is None:
        raise NotFireable(f"task card {card_id} not found")
    source = registry.find_source_block(card)
    reg = registry.get_registration(project_id, card_id)
    if reg is not None:
        block = reg.as_block(source)
    elif source is not None:
        block = source
    else:
        block = Block(block_type="schedule", id=f"adhoc:{card.id}",
                      name="(fire now)", body=[], schedule_mode="daily_at",
                      schedule_daily_at=DEFAULT_DAILY_AT)
    target = _ScheduledCard(
        project_id=project_id, card_id=card.id, card_name=card.name,
        block=block, body_root=registry.body_root_for(card, source),
    )
    return await _fire_one(target)


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
        # A cadence the scheduler cannot read projects no slot.  Record
        # the reason so the schedules list and the block's status line
        # say what is wrong; without it the card read "next slot not
        # computed yet" for ever.  Logged once per distinct reason, not
        # once per 15 s tick.
        problem = (cadence_problem(target.block, now_ms)
                   if rec["next_fire_at"] is None else None)
        if problem:
            if rec.get("cadence_error") != problem:
                logger.warning(f"⏰ Scheduler: {target.card_name!r} will not fire: {problem}")
            rec["cadence_error"] = problem
        else:
            rec.pop("cadence_error", None)
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