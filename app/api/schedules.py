"""
Schedule registrations API.

A card's Schedule block is only the author's request; a registration
(app/agents/schedule_registry.py) is what puts the card on the scheduler.
These routes are how the user registers a card, owns its cadence
(enable/disable, time and frequency) and unregisters it, without editing
the card.

    GET    /api/v1/projects/{pid}/schedules            registrations + unregistered requests + lock holder
    POST   /api/v1/projects/{pid}/schedules/{card_id}  register (body: {cadence?, enabled?})
    PATCH  /api/v1/projects/{pid}/schedules/{card_id}  edit (body: {enabled?, cadence?})
    DELETE /api/v1/projects/{pid}/schedules/{card_id}  unregister
    POST   /api/v1/projects/{pid}/schedules/{card_id}/fire  fire now through the scheduler path
    GET    /api/v1/scheduler/status                    which process holds the fire lock
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from ..agents import schedule_registry as registry
from ..agents.schedule_registry import ScheduleCadence
from ..storage.projects import ProjectStorage
from ..storage.task_cards import TaskCardStorage
from ..storage.task_runs import TaskRunStorage
from ..utils.paths import get_project_dir, get_ziya_home

router = APIRouter(prefix="/api/v1/projects/{project_id}/schedules", tags=["schedules"])
status_router = APIRouter(prefix="/api/v1/scheduler", tags=["schedules"])


class RegisterBody(BaseModel):
    cadence: Optional[ScheduleCadence] = None
    enabled: bool = True


class PatchBody(BaseModel):
    enabled: Optional[bool] = None
    cadence: Optional[ScheduleCadence] = None


def _require_project(project_id: str) -> None:
    if not ProjectStorage(get_ziya_home()).get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")


def _row(project_id: str, card_id: str) -> Dict[str, Any]:
    from ..agents.task_scheduler import _read_state
    reg = registry.get_registration(project_id, card_id)
    if reg is None:
        raise HTTPException(status_code=404, detail="Card is not registered")
    pdir = get_project_dir(project_id)
    return registry.registration_row(
        project_id, reg, TaskCardStorage(pdir).get(card_id),
        _read_state(project_id).get(card_id) or {}, TaskRunStorage(pdir),
    )


@router.get("")
def list_project_schedules(project_id: str) -> Dict[str, Any]:
    _require_project(project_id)
    return registry.list_schedules(project_id)


@router.post("/{card_id}", status_code=201)
def register_card(project_id: str, card_id: str,
                  body: Optional[RegisterBody] = None) -> Dict[str, Any]:
    _require_project(project_id)
    body = body or RegisterBody()
    card = TaskCardStorage(get_project_dir(project_id)).get(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Task card not found")
    overrides = body.cadence.model_dump(exclude_none=True) if body.cadence else None
    try:
        registry.register(project_id, card, overrides=overrides, enabled=body.enabled)
    except registry.AlreadyRegistered:
        raise HTTPException(status_code=409, detail="Card is already registered")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _row(project_id, card_id)


@router.patch("/{card_id}")
def patch_registration(project_id: str, card_id: str, body: PatchBody) -> Dict[str, Any]:
    _require_project(project_id)
    patch: Dict[str, Any] = {}
    if body.enabled is not None:
        patch["enabled"] = body.enabled
    if body.cadence is not None:
        # exclude_unset: only fields the caller sent change; an explicit
        # null (e.g. clearing max_runs) is honoured.
        patch.update(body.cadence.model_dump(exclude_unset=True))
    try:
        registry.update_registration(project_id, card_id, patch)
    except registry.NotRegistered:
        raise HTTPException(status_code=404, detail="Card is not registered")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _row(project_id, card_id)


@router.delete("/{card_id}", status_code=204)
def unregister_card(project_id: str, card_id: str) -> Response:
    _require_project(project_id)
    if not registry.unregister(project_id, card_id):
        raise HTTPException(status_code=404, detail="Card is not registered")
    return Response(status_code=204)


@router.post("/{card_id}/fire", status_code=202)
async def fire_card_now(project_id: str, card_id: str) -> Dict[str, Any]:
    """Fire the card now as the scheduler would: a headless run of the
    Schedule block's body (or the whole card), counted in the fire
    history and snapshotted as the schedule's body.  The interactive
    Launch runs a Schedule root as a one-off passthrough and records
    nothing here; this is "run the nightly now and have it count".
    Works for an unregistered card, so a cadence can be test-fired
    before it is registered.
    """
    _require_project(project_id)
    from ..agents.task_scheduler import NotFireable, fire_now
    try:
        run_id = await fire_now(project_id, card_id)
    except NotFireable as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"run_id": run_id, "card_id": card_id}


@status_router.get("/status")
def scheduler_status() -> Dict[str, Any]:
    return registry.lock_status()
