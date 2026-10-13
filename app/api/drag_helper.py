"""
Native drag helper API.

One route.  The frontend POSTs here on pointerdown over a diagram or table
so the macOS helper (app/utils/drag_pasteboard_helper.py) tightens its
pasteboard poll and learns which origin's drags are Ziya's.  Fire-and-
forget on the client; the response only says whether the helper exists,
so the client can stop asking on a platform without one.

Both handlers await nothing, so they are plain ``def``: Starlette runs them
on its worker threadpool instead of the event loop.  ``is_supported()``
imports AppKit and ``arm`` may spawn the helper and writes its stdin pipe --
blocking work that must not park the loop.  Guarded by
tests/test_drag_helper_not_event_loop_blocking.py.
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..utils.drag_file_source import DragFileError, resolve_drag_file
from ..utils.drag_pasteboard_helper import get_supervisor, is_supported

router = APIRouter(prefix="/api/v1/drag", tags=["drag"])


class ArmRequest(BaseModel):
    # window.location.origin of the page starting the drag.  The helper
    # rewrites only drags Chromium stamps with this origin.
    origin: str


@router.get("/status")
def drag_status():
    sup = get_supervisor()
    return {"supported": is_supported(), "running": sup.running}


@router.post("/arm")
def arm_drag(body: ArmRequest):
    """Tell the helper a drag from ``body.origin`` is about to start."""
    origin = body.origin.strip()
    # Only an http(s) origin, and a short one: this string is written to the
    # helper's stdin as one line and compared against the pasteboard.
    if not origin.startswith(("http://", "https://")) or len(origin) > 256:
        return {"armed": False, "supported": is_supported(),
                "reason": "origin must be an http(s) origin"}
    if not is_supported():
        return {"armed": False, "supported": False,
                "reason": "native drag helper is macOS + pyobjc only "
                          "(ziya-install-extras --drag)"}
    armed = get_supervisor().arm(origin)
    return {"armed": armed, "supported": True}


@router.get("/file")
def drag_file(path: str = Query(..., description="Project-relative file path")):
    """The bytes of a project file a file-operation block names, for the
    frontend to publish on a drag.  Confined exactly like ``file_read``.
    Plain ``def``: the stat and read run on the threadpool, not the loop."""
    from ..context import get_project_root_or_none
    try:
        resolved, mime = resolve_drag_file(path, get_project_root_or_none())
    except DragFileError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    return FileResponse(
        str(resolved), media_type=mime, filename=resolved.name,
        headers={"Cache-Control": "no-store"},
    )
