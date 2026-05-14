"""
Task binding API endpoints.

Bindings attach launched task cards to a chat.  See
design/task-cards.md §UX shape.

Routes:
  - GET    /chats/{chat_id}/task-bindings
  - POST   /chats/{chat_id}/task-bindings       (launches + binds atomically)
  - DELETE /chats/{chat_id}/task-bindings/{binding_id}
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

from ..models.task_binding import TaskBinding
from ..models.task_run import TaskRun
from ..storage.projects import ProjectStorage
from ..storage.chats import ChatStorage
from ..storage.task_bindings import TaskBindingStorage
from ..storage.task_cards import TaskCardStorage
from ..utils.paths import get_ziya_home, get_project_dir
from ..utils.logging_utils import logger
from .task_cards import _launch_run_for_card

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/chats/{chat_id}/task-bindings",
    tags=["task-bindings"],
)


def _ensure_project(project_id: str) -> None:
    ziya_home = get_ziya_home()
    project_storage = ProjectStorage(ziya_home)
    if not project_storage.get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")


def _bindings_storage(project_id: str) -> TaskBindingStorage:
    _ensure_project(project_id)
    return TaskBindingStorage(get_project_dir(project_id))


class TaskBindingCreateRequest(BaseModel):
    """What the client sends to POST /task-bindings.

    card_id is required.  anchor_message_id is the message id the
    binding is anchored after; null means unanchored (appears at
    top of chat).  The server creates the run internally.
    """
    card_id: str
    anchor_message_id: Optional[str] = None


class TaskBindingCreateResponse(BaseModel):
    """Atomic create returns both the binding and the freshly-created
    run so the client can start polling immediately without a second
    round trip."""
    binding: TaskBinding
    run: TaskRun


@router.get("", response_model=List[TaskBinding])
async def list_task_bindings(project_id: str, chat_id: str) -> List[TaskBinding]:
    """List all bindings attached to a chat.  Returns [] if chat has
    no bindings.  Does not validate that the chat exists — bindings
    for a deleted chat can still be listed (and would be empty)."""
    storage = _bindings_storage(project_id)
    return storage.list_for_chat(chat_id)


@router.post("", response_model=TaskBindingCreateResponse, status_code=201)
async def create_task_binding(
    project_id: str, chat_id: str, body: TaskBindingCreateRequest,
) -> TaskBindingCreateResponse:
    """Launch a card and bind it to a chat in one transaction.

    The chat_id is treated as opaque: the frontend may create a
    conversation locally and launch a task against it before the
    dual-write debounce pushes the chat to the server.  The binding
    file coexists with chat files by naming convention
    (chats/{chat_id}.bindings.json), not by foreign-key relationship,
    so validating chat existence here would introduce a race.
    Garbage bindings against truly nonexistent chats are harmless —
    they'll be invisible to the UI (which only looks them up by active
    chat) and the storage is cheap to clean up.
    """
    _ensure_project(project_id)

    # Also validate the card before creating the run.  _launch_run_for_card
    # would 404 on its own, but we want a clean failure path before we
    # touch any storage.
    card_storage = TaskCardStorage(get_project_dir(project_id))
    if not card_storage.get(body.card_id):
        raise HTTPException(status_code=404, detail="Task card not found")

    run = await _launch_run_for_card(
        project_id=project_id, card_id=body.card_id,
        source_conversation_id=chat_id,
    )

    bindings = TaskBindingStorage(get_project_dir(project_id))
    binding = bindings.create(
        chat_id=chat_id, card_id=body.card_id, run_id=run.id,
        anchor_message_id=body.anchor_message_id,
    )
    logger.info(f"🔗 Binding {binding.id[:8]} attached card {body.card_id[:8]} → chat {chat_id[:8]}")
    return TaskBindingCreateResponse(binding=binding, run=run)


@router.delete("/{binding_id}", status_code=204)
async def delete_task_binding(
    project_id: str, chat_id: str, binding_id: str,
) -> None:
    """Remove a binding.  Does NOT delete the underlying run or card —
    those remain accessible via their own endpoints."""
    storage = _bindings_storage(project_id)
    if not storage.delete(chat_id, binding_id):
        raise HTTPException(status_code=404, detail="Task binding not found")
