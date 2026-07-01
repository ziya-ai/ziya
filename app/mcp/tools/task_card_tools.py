"""
Task Card read/write MCP tools.

Lets an agent read and edit task-card *definitions* as part of a turn —
the capability gap that forced screenshot-driven debugging of cards
(the run record on disk is encrypted and needs the live server's DEK,
but card definitions are now plaintext, see
EncryptionPolicy.never_encrypted_categories).

These route through TaskCardStorage, which lives under the project the
request resolves to (request-scoped project_root ContextVar →
ProjectStorage.get_by_path → TaskCardStorage(project_dir)).  Reads/lists
are unrestricted; a write replaces a card's definition fields and bumps
updated_at via the same path the editor and API use.

Scope decision: cards are project-scoped, not conversation-scoped (many
cards per project, many runs per card), so these tools take an explicit
``card_id`` rather than resolving one from the conversation.  A
``task_card_list`` tool surfaces the ids/names so the agent can discover
the card to operate on; an optional ``bound_to_current_chat`` filter
narrows the list to cards launched in this conversation (via the
TaskBinding records) for the "fix the card above" flow.
"""
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.utils.logging_utils import logger


# ── Shared project resolution ───────────────────────────────────

def _resolve_card_storage() -> Dict[str, Any]:
    """Resolve TaskCardStorage for the current request's project.

    Returns {ok: True, storage, project, project_id} or
    {ok: False, error: <message>}.  Mirrors context_management's
    _resolve_chat_for_request but resolves to the project (cards are
    project-scoped), not a specific chat.
    """
    import os
    from app.context import get_project_root_or_none
    from app.storage.projects import ProjectStorage
    from app.storage.task_cards import TaskCardStorage
    from app.utils.paths import get_ziya_home, get_project_dir

    project_root = get_project_root_or_none() or os.environ.get("ZIYA_USER_CODEBASE_DIR")
    if not project_root:
        return {"ok": False,
                "error": "Cannot determine project root for the current request."}

    project = ProjectStorage(get_ziya_home()).get_by_path(project_root)
    if not project:
        return {"ok": False,
                "error": f"Project not registered for path: {project_root}"}

    return {
        "ok": True,
        "project": project,
        "project_id": project.id,
        "storage": TaskCardStorage(get_project_dir(project.id)),
    }


# ── Tool: task_card_list ────────────────────────────────────────

class TaskCardListInput(BaseModel):
    """Input schema for task_card_list."""
    bound_to_current_chat: bool = Field(
        False,
        description=("If true, list only cards launched in the current "
                     "conversation (via task bindings) — the 'fix the card "
                     "above' case.  If false, list all cards in the project."),
    )


class TaskCardListTool(BaseMCPTool):
    """List task cards in the current project (id, name, root block type)."""

    name: str = "task_card_list"
    description: str = (
        "List Task Cards in the current project so you can find the card_id "
        "to read or edit.  Returns id, name, description, and root block "
        "type for each.  Set bound_to_current_chat=true to list only cards "
        "launched in this conversation (the 'fix the task card above' case)."
    )
    InputSchema = TaskCardListInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        res = _resolve_card_storage()
        if not res["ok"]:
            return {"error": True, "message": res["error"]}
        storage = res["storage"]

        try:
            cards = storage.list()
        except Exception as e:
            logger.warning(f"task_card_list failed: {e}")
            return {"error": True, "message": str(e)}

        bound_ids: Optional[set] = None
        if kwargs.get("bound_to_current_chat"):
            bound_ids = self._bound_card_ids(res["project_id"])
            if bound_ids is None:
                return {"error": True, "message": (
                    "bound_to_current_chat requested but no conversation_id "
                    "is set in the current request context.")}

        out = []
        for c in cards:
            if bound_ids is not None and c.id not in bound_ids:
                continue
            out.append({
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "root_block_type": getattr(c.root, "block_type", None),
            })
        return {
            "success": True,
            "count": len(out),
            "cards": out,
            "message": f"{len(out)} task card(s) in project.",
        }

    @staticmethod
    def _bound_card_ids(project_id: str) -> Optional[set]:
        """Card ids bound to the current conversation, or None when no
        conversation_id is resolvable."""
        from app.context import get_conversation_id_or_none
        from app.storage.task_bindings import TaskBindingStorage
        from app.utils.paths import get_project_dir

        conv_id = get_conversation_id_or_none()
        if not conv_id:
            return None
        try:
            bindings = TaskBindingStorage(get_project_dir(project_id)).list_for_chat(conv_id)
            return {b.card_id for b in bindings}
        except Exception as e:
            logger.debug(f"task_card_list: binding lookup failed: {e}")
            return set()


# ── Tool: task_card_read ────────────────────────────────────────

class TaskCardReadInput(BaseModel):
    """Input schema for task_card_read."""
    card_id: str = Field(..., description="The id of the task card to read (from task_card_list).")


class TaskCardReadTool(BaseMCPTool):
    """Read a task card's full definition as JSON."""

    name: str = "task_card_read"
    description: str = (
        "Read a Task Card's full definition (the block tree: task/repeat/"
        "until/parallel/state/group blocks, instructions, counts, "
        "conditions) as JSON.  Use task_card_list first to find the "
        "card_id.  This is the definition, not a run's results."
    )
    InputSchema = TaskCardReadInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        card_id = (kwargs.get("card_id") or "").strip()
        if not card_id:
            return {"error": True, "message": "card_id is required"}
        res = _resolve_card_storage()
        if not res["ok"]:
            return {"error": True, "message": res["error"]}

        try:
            card = res["storage"].get(card_id)
        except Exception as e:
            logger.warning(f"task_card_read failed: {e}")
            return {"error": True, "message": str(e)}
        if not card:
            return {"error": True,
                    "message": f"No task card with id '{card_id}' in this project."}

        return {
            "success": True,
            "card": card.model_dump(),
            "message": f"Task card '{card.name}' ({card_id[:8]}).",
        }


# ── Tool: task_card_write ───────────────────────────────────────

class TaskCardWriteInput(BaseModel):
    """Input schema for task_card_write."""
    card_id: str = Field(..., description="The id of the task card to update.")
    root: Optional[Dict[str, Any]] = Field(
        None,
        description=("Replacement root block tree (a JSON object with "
                     "block_type and, for non-leaf blocks, a body list). "
                     "Omit to leave the structure unchanged."),
    )
    name: Optional[str] = Field(None, description="New card name (optional).")
    description: Optional[str] = Field(None, description="New description (optional).")


class TaskCardWriteTool(BaseMCPTool):
    """Update a task card's definition (root block tree / name / description)."""

    name: str = "task_card_write"
    description: str = (
        "Update a Task Card's definition in place — replace its root block "
        "tree (to fix loop structure, instructions, counts, conditions), "
        "and/or its name/description.  Read the card first with "
        "task_card_read, edit the block tree, then write it back.  Block "
        "ids are reassigned on write.  Does NOT launch the card; it edits "
        "the saved definition only."
    )
    InputSchema = TaskCardWriteInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        card_id = (kwargs.get("card_id") or "").strip()
        if not card_id:
            return {"error": True, "message": "card_id is required"}
        root = kwargs.get("root")
        name = kwargs.get("name")
        description = kwargs.get("description")
        if root is None and name is None and description is None:
            return {"error": True, "message": (
                "Nothing to update — provide at least one of root, name, "
                "or description.")}

        res = _resolve_card_storage()
        if not res["ok"]:
            return {"error": True, "message": res["error"]}
        storage = res["storage"]

        # Build the partial update.  TaskCardUpdate.model_dump(exclude_unset)
        # in storage.update means only the fields we set are applied; the
        # root (if given) is validated into a Block and gets fresh ids.
        from app.models.task_card import TaskCardUpdate
        update_fields: Dict[str, Any] = {}
        if name is not None:
            update_fields["name"] = name
        if description is not None:
            update_fields["description"] = description
        if root is not None:
            update_fields["root"] = root
        try:
            update = TaskCardUpdate(**update_fields)
        except Exception as e:
            return {"error": True,
                    "message": f"Invalid task card update: {e}"}

        try:
            card = storage.update(card_id, update)
        except Exception as e:
            logger.warning(f"task_card_write failed: {e}")
            return {"error": True, "message": str(e)}
        if not card:
            return {"error": True,
                    "message": f"No task card with id '{card_id}' in this project."}

        return {
            "success": True,
            "card_id": card_id,
            "name": card.name,
            "message": (f"Task card '{card.name}' ({card_id[:8]}) updated. "
                        f"Re-launch it to run the new definition."),
        }
