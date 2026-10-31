"""
/api/extras — optional-feature status and in-server install.

Thin HTTP face of app/utils/extras_registry.py.  GET lists every extra with
its installed / stale / new-in-this-version state; POST starts an install for
an extra the server may install itself (no sudo, not platform-blocked) and
GET .../install reports its progress.  An extra that needs a terminal is
refused with the command to paste.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.utils import extras_registry as er

router = APIRouter(prefix="/api/extras", tags=["extras"])


@router.get("")
def list_extras():
    return er.all_status()


@router.post("/{key}/install", status_code=202)
def install_extra(key: str):
    try:
        run = er.start_install(key)
    except ValueError as e:
        x = er.BY_KEY.get(key)
        raise HTTPException(
            status_code=404 if x is None else 409,
            detail={"message": str(e), "command": x.command() if x else None},
        )
    return run.as_dict()


@router.get("/{key}/install")
def install_progress(key: str):
    if key not in er.BY_KEY:
        raise HTTPException(status_code=404, detail=f"unknown extra {key!r}")
    st = er.install_status(key)
    if st is None:
        raise HTTPException(status_code=404, detail="no install started for this extra")
    return st


@router.post("/reconcile")
def reconcile_now():
    """Re-run the startup reconcile (adopt installed extras, refresh stale
    opted-in ones).  The Settings page offers this after a terminal install."""
    rep = er.reconcile()
    return {"adopted": rep.adopted, "refreshed": rep.refreshed,
            "notices": rep.notices, "new": rep.new}


@router.get("/state")
def extras_state():
    """The raw ~/.ziya/extras_state.json, for diagnosis."""
    return er.read_state()
