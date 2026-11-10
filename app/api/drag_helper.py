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

import subprocess

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..utils.drag_file_source import (DragFileError, is_local_client, open_argv,
                                      resolve_drag_file)
from ..utils.drag_pasteboard_helper import get_supervisor, is_supported

router = APIRouter(prefix="/api/v1/drag", tags=["drag"])


class ArmRequest(BaseModel):
    # window.location.origin of the page starting the drag.  The helper
    # rewrites only drags Chromium stamps with this origin.
    origin: str


class OpenRequest(BaseModel):
    # Project-relative (or readable absolute) path of the file to open.
    path: str
    # True: select the file in Finder/Explorer instead of opening it.
    reveal: bool = False


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


@router.post("/open")
def open_file(body: OpenRequest, request: Request):
    """Open a project file in its default application on the machine the
    server runs on, or with ``reveal`` select it in the file browser.  The
    target of a `[name](path)` link in the chat (frontend FilePathLink).

    Confined exactly like ``file_read``; no size cap, nothing is copied.
    Only acted on for a loopback client: a browser on another machine gets
    ``supported: false`` plus the ``file://`` URI so it can copy the address
    instead.  Plain ``def``: the spawn runs on the threadpool.
    """
    from ..context import get_project_root_or_none
    try:
        resolved, _ = resolve_drag_file(body.path, get_project_root_or_none(), max_bytes=None)
    except DragFileError as e:
        raise HTTPException(status_code=e.status, detail=e.message)
    info = {"path": str(resolved), "uri": resolved.as_uri(), "reveal": body.reveal}
    host = request.client.host if request.client else None
    if not is_local_client(host):
        return {"opened": False, "supported": False,
                "reason": "the browser is not on the server's machine", **info}
    argv = open_argv(resolved, body.reveal)
    if argv is None:
        return {"opened": False, "supported": False,
                "reason": "no file opener (xdg-open) on this host", **info}
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not launch {argv[0]}: {e}")
    return {"opened": True, "supported": True, **info}
