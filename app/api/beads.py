"""
Bead API endpoints — read/manage bead trees for the frontend.

Beads are created by the model silently via MCP tools.  These endpoints
let the frontend read the tree and let the user resume parked beads.
"""
from fastapi import APIRouter, HTTPException, Request
from typing import Optional
from pydantic import BaseModel

from app.utils.logging_utils import get_mode_aware_logger

logger = get_mode_aware_logger(__name__)
router = APIRouter(tags=["beads"])


class ResumeBeadRequest(BaseModel):
    bead_id: str


@router.get("/api/v1/projects/{project_id}/chats/{chat_id}/beads")
async def get_beads(project_id: str, chat_id: str, request: Request):
    """Return the bead tree for a conversation."""
    from app.storage.chats import ChatStorage
    from app.storage.beads import load_bead_tree
    from app.utils.paths import get_project_dir

    project_dir = get_project_dir(project_id)
    storage = ChatStorage(project_dir)
    tree = load_bead_tree(chat_storage=storage, conversation_id=chat_id)

    if not tree.beads:
        return {"beads": [], "active_id": None, "parked_count": 0}

    active = tree.active_bead
    return {
        "beads": [b.model_dump() for b in tree.beads],
        "active_id": active.id if active else None,
        "parked_count": len(tree.parked_beads),
        "completed_count": len([b for b in tree.beads if b.status == "completed"]),
    }


@router.post("/api/v1/projects/{project_id}/chats/{chat_id}/beads/resume")
async def resume_bead(project_id: str, chat_id: str, body: ResumeBeadRequest, request: Request):
    """Resume a parked bead — sets it active, parks the current active one.

    The frontend should follow this call by injecting a user message like
    "Let's go back to: <bead content>" to give the model context about
    the switch.
    """
    from app.storage.chats import ChatStorage
    from app.storage.beads import load_bead_tree, save_bead_tree
    from app.utils.paths import get_project_dir

    project_dir = get_project_dir(project_id)
    storage = ChatStorage(project_dir)
    tree = load_bead_tree(chat_storage=storage, conversation_id=chat_id)

    target = next((b for b in tree.beads if b.id == body.bead_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Bead not found")
    if target.status not in ("parked", "completed"):
        raise HTTPException(status_code=400, detail=f"Cannot resume bead with status '{target.status}'")

    # Park the current active bead
    for b in tree.beads:
        if b.status == "active" and b.id != body.bead_id:
            b.status = "parked"

    target.status = "active"
    save_bead_tree(tree, chat_storage=storage, conversation_id=chat_id)

    # Build a context summary for the frontend to inject as a user message
    path = tree.get_path_to_root(target.id)
    breadcrumb = " → ".join(reversed([b.content for b in path]))

    return {
        "ok": True,
        "resumed_bead": target.model_dump(),
        "breadcrumb": breadcrumb,
        "suggested_message": (
            f"Let's go back to: {target.content}"
            + (f"\n\nContext: {target.context_hint}" if target.context_hint else "")
        ),
    }
