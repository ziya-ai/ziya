"""
Bead storage — read/write bead trees on Chat records.

Beads are stored as a `_beads` list on the chat JSON.  This keeps them
co-located with the conversation they belong to and ensures they survive
sync cycles without schema changes.  The underscore prefix follows the
pattern of `_modelAddedFiles` — internal metadata that round-trips
through the frontend sync but isn't rendered.

Storage operations resolve the chat via the request-scoped ContextVars
(conversation_id + project_root), same pattern as context_management.py.
"""
import os
import time
from typing import Any, Dict, List, Optional

from app.models.bead import Bead, BeadTree
from app.utils.logging_utils import logger


# Field name on the Chat JSON record
_BEADS_FIELD = "_beads"


def _resolve_chat_storage():
    """Resolve ChatStorage + conversation_id from request context.

    Returns (chat_storage, conversation_id) or raises ValueError.
    """
    from app.context import get_conversation_id_or_none, get_project_root_or_none
    from app.storage.projects import ProjectStorage
    from app.storage.chats import ChatStorage
    from app.utils.paths import get_ziya_home, get_project_dir

    conversation_id = get_conversation_id_or_none()
    if not conversation_id:
        raise ValueError("No conversation_id in request context")

    project_root = get_project_root_or_none() or os.environ.get("ZIYA_USER_CODEBASE_DIR")
    if not project_root:
        raise ValueError("No project_root in request context")

    ps = ProjectStorage(get_ziya_home())
    project = ps.get_by_path(project_root)
    if not project:
        raise ValueError(f"No project found for path: {project_root}")

    project_dir = get_project_dir(project.id)
    return ChatStorage(project_dir), conversation_id


def load_bead_tree(chat_storage=None, conversation_id: str = None) -> BeadTree:
    """Load the bead tree for the current (or specified) conversation.

    Returns an empty BeadTree if no beads exist yet.
    """
    if chat_storage is None or conversation_id is None:
        chat_storage, conversation_id = _resolve_chat_storage()

    chat = chat_storage.get(conversation_id)
    if not chat:
        return BeadTree()

    # Read raw _beads from the chat's extra fields
    raw_beads = None
    if hasattr(chat, "__pydantic_extra__") and chat.__pydantic_extra__:
        raw_beads = chat.__pydantic_extra__.get(_BEADS_FIELD)
    if not raw_beads:
        # Try dict access for raw-dict loaded chats
        raw_beads = getattr(chat, _BEADS_FIELD, None)

    if not raw_beads or not isinstance(raw_beads, list):
        return BeadTree()

    beads = []
    for entry in raw_beads:
        try:
            beads.append(Bead(**entry) if isinstance(entry, dict) else entry)
        except Exception as e:
            logger.debug(f"Skipping malformed bead entry: {e}")
    return BeadTree(beads=beads)


def save_bead_tree(tree: BeadTree, chat_storage=None, conversation_id: str = None) -> None:
    """Persist the bead tree back to the chat record."""
    if chat_storage is None or conversation_id is None:
        chat_storage, conversation_id = _resolve_chat_storage()

    chat = chat_storage.get(conversation_id)
    if not chat:
        logger.warning(f"Cannot save beads: chat {conversation_id} not found")
        return

    # Serialize beads to dicts for JSON storage
    bead_dicts = [b.model_dump() for b in tree.beads]

    # Write to the chat's extra fields
    if hasattr(chat, "__pydantic_extra__"):
        if chat.__pydantic_extra__ is None:
            chat.__pydantic_extra__ = {}
        chat.__pydantic_extra__[_BEADS_FIELD] = bead_dicts
    else:
        setattr(chat, _BEADS_FIELD, bead_dicts)

    # Write the mutated chat directly rather than routing through
    # ChatStorage.update().  update() re-reads a fresh Chat from disk and
    # copies fields via setattr(); an underscore-prefixed key like
    # "_beads" is treated by pydantic v2 as a *private attribute* (not an
    # extra field), so it never reaches __pydantic_extra__ and model_dump()
    # silently drops it — discarding every bead write.  Dumping this object
    # (whose __pydantic_extra__ already holds _beads) and setting the key
    # explicitly on the dict guarantees it persists regardless of pydantic's
    # underscore handling.
    d = chat.model_dump()
    d[_BEADS_FIELD] = bead_dicts
    d["_version"] = int(time.time() * 1000)
    chat_storage._write_json(chat_storage._chat_file(conversation_id), d)
    logger.debug(f"📿 Saved {len(tree.beads)} beads for conv {conversation_id[:8]}")


def add_bead(bead: Bead) -> BeadTree:
    """Add a bead to the current conversation's tree and persist."""
    tree = load_bead_tree()
    tree.beads.append(bead)
    save_bead_tree(tree)
    return tree


def update_bead_status(bead_id: str, status: str) -> Optional[Bead]:
    """Update a bead's status and persist.  Returns the updated bead or None."""
    tree = load_bead_tree()
    target = next((b for b in tree.beads if b.id == bead_id), None)
    if not target:
        return None
    target.status = status
    save_bead_tree(tree)
    return target


def set_active_bead(bead_id: str) -> Optional[Bead]:
    """Set a bead as active, parking the currently-active one.

    This is the "resume from bead" operation: the current active bead
    becomes parked, and the target bead becomes active.
    """
    tree = load_bead_tree()
    target = next((b for b in tree.beads if b.id == bead_id), None)
    if not target:
        return None
    # Park the currently active bead
    for b in tree.beads:
        if b.status == "active" and b.id != bead_id:
            b.status = "parked"
    target.status = "active"
    save_bead_tree(tree)
    return target
