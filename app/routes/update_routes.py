"""
Header update chip: is a newer Ziya installed, and restart into it.

State lives in app/utils/update_watch.py.  Every handler is in-memory work
(the prefs write is one small file), so they stay on the event loop, which
is also where update_watch's wake event lives.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.utils import update_watch

router = APIRouter(tags=["update"])


class RestartRequest(BaseModel):
    cancel: bool = False


class UpdatePrefs(BaseModel):
    auto_restart: bool


@router.get("/api/update/status")
async def get_update_status():
    return update_watch.status()


@router.post("/api/update/restart")
async def post_update_restart(req: RestartRequest):
    """Restart into the installed version as soon as nothing is running
    (immediately when nothing is), or cancel a queued restart."""
    if req.cancel:
        update_watch.cancel_restart()
    else:
        update_watch.request_restart()
    return update_watch.status()


@router.put("/api/update/prefs")
async def put_update_prefs(prefs: UpdatePrefs):
    update_watch.set_prefs(prefs.auto_restart)
    return update_watch.status()
