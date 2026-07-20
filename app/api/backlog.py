"""
Bead backlog API -- browse parked/abandoned beads across a project, and the
restricted parked<->abandoned status transition (design/bead-backlog-browser.md).
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.utils.logging_utils import get_mode_aware_logger

logger = get_mode_aware_logger(__name__)
router = APIRouter(tags=["backlog"])


_BACKLOG_STATUSES = ("parked", "abandoned")
_ALLOWED_FROM = {"abandoned": "parked", "parked": "abandoned"}


class SetBeadStatusRequest(BaseModel):
    status: str


def _parse_statuses(status: str):
    if not status:
        return ["parked"]
    wanted = [s.strip() for s in status.split(",") if s.strip()]
    valid = [s for s in wanted if s in _BACKLOG_STATUSES]
    return valid or ["parked"]


@router.get("/api/v1/projects/{project_id}/backlog")
async def get_backlog(project_id: str, request: Request, status: str = "parked"):
    from app.storage.backlog import get_backlog as aggregate_backlog

    statuses = _parse_statuses(status)
    return aggregate_backlog(project_id, statuses)


@router.post("/api/v1/projects/{project_id}/chats/{chat_id}/beads/{bead_id}/status")
async def set_bead_status(
    project_id: str,
    chat_id: str,
    bead_id: str,
    body: SetBeadStatusRequest,
    request: Request,
):
    from app.storage.chats import ChatStorage
    from app.storage.beads import load_bead_tree, save_bead_tree
    from app.storage.backlog import invalidate
    from app.utils.paths import get_project_dir

    new_status = body.status
    if new_status not in _ALLOWED_FROM:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot set bead status to '{new_status}' from the backlog; "
                f"only {' and '.join(_BACKLOG_STATUSES)} are allowed"
            ),
        )

    project_dir = get_project_dir(project_id)
    storage = ChatStorage(project_dir)

    if not storage._read_json(storage._chat_file(chat_id)):
        raise HTTPException(status_code=404, detail="Conversation not found")

    tree = load_bead_tree(chat_storage=storage, conversation_id=chat_id)
    target = next((b for b in tree.beads if b.id == bead_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Bead not found")

    if target.status == new_status:
        raise HTTPException(
            status_code=400,
            detail=f"Bead is already '{new_status}'",
        )
    if target.status != _ALLOWED_FROM[new_status]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot set status to '{new_status}' from '{target.status}'; "
                f"only the '{_ALLOWED_FROM[new_status]}'<->'{new_status}' "
                f"transition is allowed"
            ),
        )

    target.status = new_status
    save_bead_tree(tree, chat_storage=storage, conversation_id=chat_id)
    invalidate(chat_id)

    logger.info(
        f"backlog: bead {bead_id[:8]} in {chat_id[:8]} -> {new_status}"
    )
    return {"ok": True, "bead": target.model_dump()}
