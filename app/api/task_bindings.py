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
from ..storage.task_runs import TaskRunStorage
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
    for a deleted chat can still be listed (and would be empty).

    Each binding with a run_id is enriched with ``run_status``,
    ``root_run_id`` and ``attempt`` extra fields (TaskBinding has
    extra="allow") so the client can show running-task affordances AND
    collapse an attempt lineage to one tile without a per-binding round
    trip.  All three come from a single run read, so the lineage fields
    are free: the ``run_status`` lookup was already loading the record.
    A client-side per-binding fetch would also have targeted the WRONG
    project for a cross-project global chat — see the fallback below.
    """
    storage = _bindings_storage(project_id)
    bindings = storage.list_for_chat(chat_id)
    source_project_id = project_id
    run_dir = get_project_dir(project_id)

    # Cross-project fallback for global conversations.  A global chat is
    # visible from every project, but its binding file
    # (chats/{chat_id}.bindings.json) lives only under the project the card
    # was launched in — the chat's HOME project.  Viewed from any other
    # project the URL's project_id has no binding file, so already-run cards
    # vanish from the inline chat view.  Resolve the chat's owning project
    # via the shared chat_index (chat_id -> owning project_id, O(1),
    # self-healing) and read the bindings from there instead.  Cards are
    # launched from their home project (confirmed workflow), so the chat
    # owner is always the correct binding source; the mixed case (a card
    # launched against a global chat from a *third* project) is out of scope.
    if not bindings and chat_id:
        try:
            from app.storage import chat_index
            owner = chat_index.lookup(get_ziya_home(), chat_id)
            if owner and owner[0] != project_id:
                owner_pid = owner[0]
                owner_dir = get_project_dir(owner_pid)
                bindings = TaskBindingStorage(owner_dir).list_for_chat(chat_id)
                if bindings:
                    source_project_id = owner_pid
                    run_dir = owner_dir
        except Exception as e:
            logger.debug(
                f"cross-project binding resolution failed for "
                f"chat {chat_id[:8]}: {e}"
            )

    if bindings:
        run_storage = TaskRunStorage(run_dir)
        for b in bindings:
            # Stamp the project the binding actually lives in (TaskBinding
            # has extra="allow", same channel as run_status) so the client
            # targets its card / run / iteration / cancel / rerun calls at
            # the OWNING project rather than the possibly-different viewing
            # project — otherwise those follow-up reads 404.
            b.project_id = source_project_id
            if b.run_id:
                try:
                    run = run_storage.get(b.run_id)
                    if run:
                        b.run_status = run.status
                        # Lineage key + ordinal for the client-side
                        # collapse.  ``or run.id`` mirrors the storage
                        # default so a pre-lineage record reads as its
                        # own single-attempt lineage rather than null.
                        b.root_run_id = run.root_run_id or run.id
                        b.attempt = run.attempt or 1
                except Exception as e:
                    logger.debug(f"Binding {b.id[:8]}: run status lookup failed: {e}")
    return bindings


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


@router.post("/{binding_id}/launch", response_model=TaskRun)
async def launch_staged_binding(
    project_id: str, chat_id: str, binding_id: str,
) -> TaskRun:
    """Launch the run for a staged binding (one whose ``run_id`` is
    None because it was created by the ``/goal`` slash command and
    is awaiting explicit user confirmation).

    409 if the binding has already been launched.  404 if the binding
    does not exist for this chat.
    """
    _ensure_project(project_id)
    project_dir = get_project_dir(project_id)
    binding_storage = TaskBindingStorage(project_dir)
    binding = binding_storage.get(chat_id, binding_id)
    if not binding:
        raise HTTPException(status_code=404, detail="Binding not found")
    if binding.run_id:
        raise HTTPException(status_code=409, detail="Binding already launched")

    run = await _launch_run_for_card(
        project_id=project_id,
        card_id=binding.card_id,
        source_conversation_id=chat_id,
    )
    binding_storage.update_run_id(chat_id, binding.id, run.id)
    logger.info(f"🚀 Staged binding {binding_id[:8]} launched → run {run.id[:8]}")
    return run