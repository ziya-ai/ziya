"""
Schedule registrations — the user-owned record that puts a task card on
the scheduler.

A card's Schedule block is the author's *request*: "this body wants to run
daily at 02:00".  Nothing happens to it until the user REGISTERS the card,
which copies the requested cadence into a registration the user owns from
then on: enable/disable it, change its time and frequency, or unregister
it, all without touching the card (no version bump, nothing in the card's
export).  The scheduler (task_scheduler.py) enumerates registrations, not
cards, so a conversation-only (draft) card can be registered exactly like
a deck card -- and a card that merely carries a Schedule block fires
nothing until someone registers it.

One registration per card, keyed by card id.  Storage is one file per
project at <project_dir>/schedule_registrations.json:

    {
      "<card_id>": {
        "card_id": "...", "registered_at": 17000..., "updated_at": 17000...,
        "enabled": true, "source_block_id": "<schedule block id or null>",
        "migrated": false,
        "schedule_mode": "daily_at", "schedule_daily_at": "02:00", ...
      }, ...
    }

Fire history stays in <project_dir>/schedule_state.json (also keyed by
card id; the scheduler owns it).  A cadence edit resets that record's
``next_fire_at`` so the new cadence takes effect at once rather than after
the old slot.

Migration: the first time a project is enumerated and it has no registry
file, every card that the OLD rules had armed (a schedule_state record
whose card still exists and still has a Schedule block) becomes a
registration with ``migrated: true``.  Cards with a Schedule block and no
state record were never armed and are left unregistered; the schedules
list shows them under "requested, not registered".
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel

from ..models.task_card import Block, TaskCard
from ..utils.paths import get_project_dir, get_ziya_home

logger = logging.getLogger(__name__)

REGISTRY_FILENAME = "schedule_registrations.json"

ScheduleMode = Literal["interval", "at", "daily_at", "cron"]

# Fields copied from a Schedule block into a registration, and editable
# on the registration afterwards.  Same names as the Block fields so a
# registration can be turned back into a synthetic Block for the
# scheduler's existing time-math and concurrency gate.
CADENCE_FIELDS = (
    "schedule_mode", "schedule_interval_value", "schedule_interval_unit",
    "schedule_at_iso", "schedule_daily_at", "schedule_cron",
    "schedule_timezone", "schedule_catch_up", "schedule_max_runs",
    "schedule_concurrency",
)


class NotRegistered(KeyError):
    pass


class AlreadyRegistered(ValueError):
    pass


class ScheduleCadence(BaseModel):
    """The editable part of a registration.  Every field optional so the
    same model serves as a PATCH body."""
    schedule_mode: Optional[ScheduleMode] = None
    schedule_interval_value: Optional[int] = None
    schedule_interval_unit: Optional[Literal["minutes", "hours", "days"]] = None
    schedule_at_iso: Optional[str] = None
    schedule_daily_at: Optional[str] = None
    schedule_cron: Optional[str] = None
    schedule_timezone: Optional[str] = None
    schedule_catch_up: Optional[bool] = None
    schedule_max_runs: Optional[int] = None
    schedule_concurrency: Optional[Literal["skip", "queue", "parallel"]] = None


class ScheduleRegistration(BaseModel):
    card_id: str
    registered_at: int
    updated_at: int
    enabled: bool = True
    # The card's Schedule block the cadence was copied from; None when the
    # card had none (any card can be registered -- its root is the body).
    source_block_id: Optional[str] = None
    migrated: bool = False

    schedule_mode: ScheduleMode = "interval"
    schedule_interval_value: Optional[int] = None
    schedule_interval_unit: Optional[Literal["minutes", "hours", "days"]] = None
    schedule_at_iso: Optional[str] = None
    schedule_daily_at: Optional[str] = None
    schedule_cron: Optional[str] = None
    schedule_timezone: Optional[str] = None
    schedule_catch_up: bool = True
    schedule_max_runs: Optional[int] = None
    schedule_concurrency: Optional[Literal["skip", "queue", "parallel"]] = None

    def cadence(self) -> Dict[str, Any]:
        return {f: getattr(self, f) for f in CADENCE_FIELDS}

    def as_block(self, source: Optional[Block]) -> Block:
        """A synthetic Schedule block carrying this registration's cadence,
        so compute_next_fire / the concurrency gate / _fire_one need not
        know registrations exist.  Takes the source block's id and scope
        (the scope is seeded onto the fire's scope stack)."""
        return Block(
            block_type="schedule",
            id=(source.id if source is not None else f"registration:{self.card_id}"),
            name=(source.name if source is not None else "(registration)"),
            scope=(source.scope if source is not None else None),
            schedule_enabled=self.enabled,
            body=[],
            **self.cadence(),
        )


def _now_ms() -> int:
    return int(time.time() * 1000)


# -- storage -------------------------------------------------------------------

def registry_path(project_id: str) -> Path:
    return get_project_dir(project_id) / REGISTRY_FILENAME


def read_registry(project_id: str) -> Dict[str, ScheduleRegistration]:
    p = registry_path(project_id)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"schedule registry read failed for {project_id}: {e}")
        return {}
    out: Dict[str, ScheduleRegistration] = {}
    for cid, rec in (raw or {}).items():
        try:
            out[cid] = ScheduleRegistration(**{**rec, "card_id": cid})
        except (TypeError, ValueError) as e:
            logger.warning(f"schedule registry: skipping corrupt record {cid}: {e}")
    return out


def write_registry(project_id: str, regs: Dict[str, ScheduleRegistration]) -> None:
    p = registry_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps({cid: r.model_dump() for cid, r in regs.items()}, indent=2))
        tmp.replace(p)
    except OSError as e:
        logger.warning(f"schedule registry write failed for {project_id}: {e}")


def get_registration(project_id: str, card_id: str) -> Optional[ScheduleRegistration]:
    return read_registry(project_id).get(card_id)


# -- card <-> registration -----------------------------------------------------

def find_source_block(card: TaskCard) -> Optional[Block]:
    from .task_scheduler import _find_schedule_block
    return _find_schedule_block(card.root)


def cadence_from_block(block: Optional[Block]) -> Dict[str, Any]:
    """The cadence a registration starts from: the card's Schedule block,
    or a daily 09:00 default for a card that has none.

    Fields the author left unset are filled with the value the editor
    showed for them (task_scheduler.DEFAULT_*), so the registration is
    explicit and a later change to a default cannot move a cadence that
    was already registered.
    """
    from .task_scheduler import DEFAULT_DAILY_AT, DEFAULT_INTERVAL_UNIT
    if block is None:
        return {"schedule_mode": "daily_at", "schedule_daily_at": DEFAULT_DAILY_AT,
                "schedule_catch_up": True}
    out: Dict[str, Any] = {}
    for f in CADENCE_FIELDS:
        v = getattr(block, f, None)
        if v is not None:
            out[f] = v
    out.setdefault("schedule_mode", "interval")
    if out["schedule_mode"] == "interval":
        out.setdefault("schedule_interval_value", 1)
        out.setdefault("schedule_interval_unit", DEFAULT_INTERVAL_UNIT)
    if out["schedule_mode"] == "daily_at":
        out.setdefault("schedule_daily_at", DEFAULT_DAILY_AT)
    return out


def validate_cadence(fields: Dict[str, Any]) -> None:
    """Raise ValueError when the scheduler could not read this cadence
    (task_scheduler.cadence_problem).  Called on register and on edit so a
    bad cron or a 25:00 is refused with a reason instead of being accepted
    and never firing."""
    from .task_scheduler import cadence_problem
    probe = Block(block_type="schedule", id="probe", name="probe", body=[],
                  **{k: v for k, v in fields.items() if k in CADENCE_FIELDS})
    problem = cadence_problem(probe, _now_ms())
    if problem:
        raise ValueError(f"schedule cannot fire: {problem}")


def body_root_for(card: TaskCard, source: Optional[Block]) -> Block:
    """What a fire executes: the Schedule block's body (the block itself
    is a trigger, not a step), or the whole card when there is none."""
    if source is None:
        return card.root
    from .task_scheduler import _resolve_body_root
    return _resolve_body_root(source)


def _reset_next_fire(project_id: str, reg: ScheduleRegistration, now_ms: int) -> None:
    """Re-project next_fire_at from now under the registration's cadence and
    drop any queued deferral, so a cadence edit is honoured at once."""
    from .task_scheduler import _read_state, _write_state, compute_next_fire
    state = _read_state(project_id)
    rec = state.get(reg.card_id)
    if not rec:
        return
    rec["next_fire_at"] = compute_next_fire(reg.as_block(None), now_ms)
    rec.pop("deferred_since", None)
    rec.pop("deferred_behind_run_id", None)
    state[reg.card_id] = rec
    _write_state(project_id, state)


def register(project_id: str, card: TaskCard,
             overrides: Optional[Dict[str, Any]] = None,
             enabled: bool = True) -> ScheduleRegistration:
    """Register ``card``: cadence from its Schedule block (or the default),
    then ``overrides`` (the user's edits in the register dialog) on top."""
    regs = read_registry(project_id)
    if card.id in regs:
        raise AlreadyRegistered(card.id)
    source = find_source_block(card)
    fields = cadence_from_block(source)
    for k, v in (overrides or {}).items():
        if k in CADENCE_FIELDS and v is not None:
            fields[k] = v
    validate_cadence(fields)
    now = _now_ms()
    reg = ScheduleRegistration(card_id=card.id, registered_at=now, updated_at=now,
                               enabled=enabled,
                               source_block_id=source.id if source is not None else None,
                               **fields)
    regs[card.id] = reg
    write_registry(project_id, regs)
    # A fresh registration starts from a clean fire record: a card that was
    # registered, unregistered and registered again does not inherit the
    # old fires_so_far (which would count against schedule_max_runs).
    from .task_scheduler import _read_state, _write_state
    state = _read_state(project_id)
    if state.pop(card.id, None) is not None:
        _write_state(project_id, state)
    return reg


def update_registration(project_id: str, card_id: str, patch: Dict[str, Any]) -> ScheduleRegistration:
    """Apply ``patch`` (``enabled`` and/or cadence fields).  A cadence
    change resets next_fire_at; an enable flip does not."""
    regs = read_registry(project_id)
    reg = regs.get(card_id)
    if reg is None:
        raise NotRegistered(card_id)
    data = reg.model_dump()
    cadence_changed = False
    for k, v in patch.items():
        if k == "enabled" and v is not None:
            data["enabled"] = bool(v)
        elif k in CADENCE_FIELDS:
            if data.get(k) != v:
                cadence_changed = True
            data[k] = v
    if cadence_changed:
        validate_cadence(data)
    now = _now_ms()
    data["updated_at"] = now
    new = ScheduleRegistration(**data)
    regs[card_id] = new
    write_registry(project_id, regs)
    if cadence_changed:
        _reset_next_fire(project_id, new, now)
    return new


def unregister(project_id: str, card_id: str) -> bool:
    """Remove the registration and its fire record.  False if absent."""
    regs = read_registry(project_id)
    if regs.pop(card_id, None) is None:
        return False
    write_registry(project_id, regs)
    from .task_scheduler import _read_state, _write_state
    state = _read_state(project_id)
    if state.pop(card_id, None) is not None:
        _write_state(project_id, state)
    return True


# -- migration -----------------------------------------------------------------

def ensure_migrated(project_id: str, card_storage: Any) -> Dict[str, ScheduleRegistration]:
    """Read the registry, creating it from schedule_state.json the first
    time.  Runs once per project: the file is written even when nothing
    migrates, so a later ``_read_state`` record never re-triggers it."""
    p = registry_path(project_id)
    if p.exists():
        return read_registry(project_id)
    from .task_scheduler import _read_state
    regs: Dict[str, ScheduleRegistration] = {}
    now = _now_ms()
    for cid, rec in _read_state(project_id).items():
        try:
            card = card_storage.get(cid)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"schedule migration: card {cid} unreadable: {e}")
            continue
        if card is None:
            continue
        source = find_source_block(card)
        if source is None:
            continue
        regs[cid] = ScheduleRegistration(
            card_id=cid, registered_at=int(rec.get("last_fire_at") or now),
            updated_at=now, enabled=bool(source.schedule_enabled),
            source_block_id=source.id, migrated=True,
            **cadence_from_block(source),
        )
        logger.info(f"⏰ Migrated armed schedule of card {card.name!r} to a registration")
    write_registry(project_id, regs)
    return regs


# -- the list ------------------------------------------------------------------

def describe_cadence(reg: ScheduleRegistration) -> str:
    from .task_scheduler import DEFAULT_INTERVAL_UNIT
    m = reg.schedule_mode
    if m == "interval":
        return (f"every {reg.schedule_interval_value or 1} "
                f"{reg.schedule_interval_unit or DEFAULT_INTERVAL_UNIT}")
    if m == "at":
        return f"once at {reg.schedule_at_iso or '?'}"
    if m == "daily_at":
        return f"daily at {reg.schedule_daily_at or '?'}"
    if m == "cron":
        return f"cron {reg.schedule_cron or '?'}"
    return m


def lock_status() -> Dict[str, Any]:
    """Who fires: the holder of ~/.ziya/scheduler.lock, and whether that is
    this process.  With several Ziya servers on one home only one fires,
    and a card that 'never fires' may be firing from another one's log."""
    from .task_scheduler import _LOCK_STALE_THRESHOLD_S, _LockHandle
    path = get_ziya_home() / "scheduler.lock"
    out: Dict[str, Any] = {"lock_path": str(path), "held": False, "stale": True,
                           "pid": None, "host": None, "heartbeat_age_s": None,
                           "this_process": False}
    if not path.exists():
        return out
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return out
    hb = data.get("heartbeat_ms")
    age = (_now_ms() - hb) / 1000.0 if isinstance(hb, (int, float)) else None
    stale = _LockHandle.is_stale(path)
    out.update(pid=data.get("pid"), host=data.get("host"),
               heartbeat_age_s=(round(age, 1) if age is not None else None),
               stale=stale, held=not stale,
               this_process=(not stale and data.get("pid") == os.getpid()
                             and data.get("host") == socket.gethostname()),
               stale_after_s=_LOCK_STALE_THRESHOLD_S)
    return out


def registration_row(project_id: str, reg: ScheduleRegistration,
                     card: Optional[TaskCard], state_rec: Dict[str, Any],
                     run_storage: Any = None) -> Dict[str, Any]:
    from .task_scheduler import cadence_problem
    fires = int(state_rec.get("fires_so_far", 0))
    exhausted = reg.schedule_max_runs is not None and fires >= int(reg.schedule_max_runs)
    # A one-shot that has fired is finished, not broken.  Anything else
    # cadence_problem reports is a registration that will never fire;
    # computed live so a bad edit shows before the scheduler's next tick,
    # with the scheduler's own recorded reason as the fallback.
    problem = cadence_problem(reg.as_block(None), _now_ms())
    if reg.schedule_mode == "at" and fires > 0:
        exhausted, problem = True, None
    problem = problem or state_rec.get("cadence_error")
    row: Dict[str, Any] = {
        "card_id": reg.card_id,
        "card_name": card.name if card is not None else None,
        "card_missing": card is None,
        "card_draft": bool(getattr(card, "draft", False)) if card is not None else False,
        "registered_at": reg.registered_at, "updated_at": reg.updated_at,
        "enabled": reg.enabled, "migrated": reg.migrated,
        "source_block_id": reg.source_block_id,
        "cadence": reg.cadence(), "summary": describe_cadence(reg),
        "next_fire_at": (None if (not reg.enabled or exhausted or state_rec.get("deferred_since") is not None)
                         else state_rec.get("next_fire_at")),
        "last_fire_at": state_rec.get("last_fire_at"),
        "fires_so_far": fires, "skips_so_far": int(state_rec.get("skips_so_far", 0)),
        "deferred_since": state_rec.get("deferred_since"),
        "exhausted": exhausted,
        "problem": problem,
        "last_run": None,
    }
    if run_storage is not None and state_rec.get("run_ids"):
        try:
            from ..utils.task_run_read import last_run_summary
            row["last_run"] = last_run_summary(run_storage, list(state_rec.get("run_ids") or []))
        except Exception as e:  # noqa: BLE001 — enrichment never breaks the list
            logger.debug(f"schedules list: last_run enrichment failed: {e}")
    return row


def list_schedules(project_id: str) -> Dict[str, Any]:
    """Everything the schedules list shows for one project."""
    from ..storage.task_cards import TaskCardStorage
    from ..storage.task_runs import TaskRunStorage
    from .task_scheduler import _read_state
    pdir = get_project_dir(project_id)
    card_storage = TaskCardStorage(pdir)
    run_storage = TaskRunStorage(pdir)
    regs = ensure_migrated(project_id, card_storage)
    state = _read_state(project_id)
    rows: List[Dict[str, Any]] = []
    for cid, reg in regs.items():
        rows.append(registration_row(project_id, reg, card_storage.get(cid),
                                     state.get(cid) or {}, run_storage))
    rows.sort(key=lambda r: (not r["enabled"], r["next_fire_at"] or float("inf")))
    # Cards whose author asked for a cadence that nobody has registered.
    unregistered: List[Dict[str, Any]] = []
    for card in card_storage.list(include_drafts=True):
        if card.id in regs:
            continue
        source = find_source_block(card)
        if source is None:
            continue
        unregistered.append({
            "card_id": card.id, "card_name": card.name,
            "card_draft": bool(getattr(card, "draft", False)),
            "source_block_id": source.id,
            "requested": cadence_from_block(source),
        })
    return {"registrations": rows, "unregistered": unregistered, "scheduler": lock_status()}
