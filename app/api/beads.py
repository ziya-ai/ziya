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


class ForkBeadRequest(BaseModel):
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


@router.post("/api/v1/projects/{project_id}/chats/{chat_id}/beads/fork")
async def fork_from_bead(project_id: str, chat_id: str, body: ForkBeadRequest, request: Request):
    """Split a conversation at a bead's seam into a new branched conversation.

    Mode-1 non-destructive fork (design/bead-branching.md): the source
    conversation is left fully intact; a new conversation is created holding
    only the messages up to the chosen bead's message_index seam, with that
    bead's thread promoted active, the inherited beads carried along (timeline
    rule: message_index <= seam), and lineage metadata stamped so the UI can
    render the branch relationship.  This is the backend mechanism; the
    "split from here" UI action that calls it is step 3.
    """
    import uuid
    import time as _time
    from app.storage.chats import ChatStorage
    from app.storage.beads import _parse_beads, load_bead_tree, inherit_beads_for_seam
    from app.models.bead import BeadTree
    from app.utils.paths import get_project_dir

    project_dir = get_project_dir(project_id)
    storage = ChatStorage(project_dir)

    raw = storage._read_json(storage._chat_file(chat_id))
    if not raw:
        raise HTTPException(status_code=404, detail="Source conversation not found")

    # Prefer the bead snapshot on the chat record (the synced common case, and
    # consistent with the same raw snapshot the messages are truncated from);
    # fall back to load_bead_tree for beads that live only in the standalone
    # fallback store (a brand-new conversation not yet synced to disk).
    raw_beads = raw.get("_beads")
    tree = (BeadTree(beads=_parse_beads(raw_beads)) if raw_beads
            else load_bead_tree(chat_storage=storage, conversation_id=chat_id))

    try:
        seam, inherited, label = inherit_beads_for_seam(tree, body.bead_id, chat_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # message_index is the user-visible message count at bead creation, so
    # messages[:seam] keeps exactly the prefix through the message that raised
    # the thread.  seam > len (source later shortened) is safe — slice clamps.
    truncated = (raw.get("messages") or [])[:seam]

    new_id = str(uuid.uuid4())
    now = int(_time.time() * 1000)
    new_chat = {
        "id": new_id,
        "title": (label or "Branch")[:60],
        "messages": truncated,
        "createdAt": now,
        "lastActiveAt": now,
        "lastAccessedAt": now,
        "_version": now,
        "projectId": project_id,
        "folderId": raw.get("folderId"),
        "isActive": True,
        "branchedFrom": chat_id,
        "branchedAtMessageIndex": seam,
        "branchedFromLabel": label,
        "_beads": [b.model_dump() for b in inherited],
    }
    storage._write_json(storage._chat_file(new_id), new_chat)
    logger.info(
        f"🌿 fork_from_bead: {chat_id[:8]} @bead {body.bead_id[:8]} "
        f"(seam={seam}, {len(truncated)} msgs, {len(inherited)} beads) → {new_id[:8]}"
    )
    return {
        "ok": True,
        "new_chat_id": new_id,
        "branchedFrom": chat_id,
        "branchedAtMessageIndex": seam,
        "branchedFromLabel": label,
        "message_count": len(truncated),
        "inherited_bead_count": len(inherited),
    }
