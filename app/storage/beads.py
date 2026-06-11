"""
Bead storage — read/write bead trees on Chat records.

Beads are stored as a `_beads` list on the chat JSON.  This keeps them
co-located with the conversation they belong to and ensures they survive
sync cycles without schema changes.  The underscore prefix follows the
pattern of `_modelAddedFiles` — internal metadata that round-trips
through the frontend sync but isn't rendered.

Storage operations resolve the chat via the request-scoped ContextVars
(conversation_id + project_root), same pattern as context_management.py.

When the chat record can't be resolved (CLI sessions, which persist to
~/.ziya/sessions rather than ChatStorage, or brand-new web conversations
that haven't synced to disk yet), beads fall back to a standalone file at
~/.ziya/beads/<conversation_id>.json.  A later save that finds the chat
record available migrates the fallback beads into it and removes the file.
"""
import json
import os
import time
from typing import Any, Dict, List, Optional

from app.models.bead import Bead, BeadTree
from app.utils.logging_utils import logger


# Field name on the Chat JSON record
_BEADS_FIELD = "_beads"


def _get_conversation_id(conversation_id: Optional[str] = None) -> Optional[str]:
    """Resolve a conversation id: explicit arg first, then request ContextVar."""
    if conversation_id:
        return conversation_id
    try:
        from app.context import get_conversation_id_or_none
        return get_conversation_id_or_none()
    except ImportError:
        return None


def _resolve_chat_storage(conversation_id: Optional[str] = None):
    """Resolve ChatStorage + conversation_id from request context.

    Returns (chat_storage, conversation_id) or raises ValueError.
    """
    from app.context import get_project_root_or_none
    from app.storage.projects import ProjectStorage
    from app.storage.chats import ChatStorage
    from app.utils.paths import get_ziya_home, get_project_dir

    conversation_id = _get_conversation_id(conversation_id)
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


# ── Standalone fallback store ────────────────────────────────────────────

def _fallback_beads_file(conversation_id: str):
    """Path of the standalone bead file for a conversation."""
    from app.utils.paths import get_ziya_home
    beads_dir = get_ziya_home() / "beads"
    beads_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in conversation_id)
    return beads_dir / f"{safe}.json"


def _load_fallback(conversation_id: str) -> List[dict]:
    path = _fallback_beads_file(conversation_id)
    if not path.exists():
        return []
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError) as e:
        logger.debug(f"📿 Fallback bead load failed: {e}")
        return []


def _save_fallback(conversation_id: str, bead_dicts: List[dict]) -> None:
    path = _fallback_beads_file(conversation_id)
    try:
        with open(path, "w") as f:
            json.dump(bead_dicts, f, indent=2)
        logger.debug(
            f"📿 Saved {len(bead_dicts)} beads to fallback store for "
            f"conv {conversation_id[:8]}"
        )
    except OSError as e:
        logger.warning(f"📿 Fallback bead save failed: {e}")


def _remove_fallback(conversation_id: str) -> None:
    try:
        path = _fallback_beads_file(conversation_id)
        if path.exists():
            path.unlink()
    except OSError:
        pass


def remove_fallback_beads(conversation_id: str) -> None:
    """Remove the standalone bead file for a conversation, if any.

    Called from deletion paths (chat delete, CLI session cleanup) so
    fallback files don't outlive the conversations they belong to.
    """
    _remove_fallback(conversation_id)


def cleanup_orphaned_fallbacks(max_age_days: int = 30) -> int:
    """Delete fallback bead files not modified in ``max_age_days`` days.

    Fallback files normally retire themselves: either migration onto the
    chat record removes them, or the deletion hooks do.  This sweep catches
    the leftovers — ephemeral web conversations that never synced, crashed
    CLI sessions, conversations deleted before the hooks existed.  Age is
    keyed on mtime, so any active conversation keeps refreshing its file.

    Returns the number of files removed.
    """
    from app.utils.paths import get_ziya_home
    beads_dir = get_ziya_home() / "beads"
    if not beads_dir.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        for path in beads_dir.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    except OSError as e:
        logger.debug(f"📿 Orphan bead sweep failed: {e}")
    if removed:
        logger.info(f"📿 Removed {removed} orphaned bead file(s) older than {max_age_days}d")
    return removed


def _parse_beads(raw_beads) -> List[Bead]:
    beads = []
    for entry in raw_beads or []:
        try:
            beads.append(Bead(**entry) if isinstance(entry, dict) else entry)
        except Exception as e:
            logger.debug(f"Skipping malformed bead entry: {e}")
    return beads


def load_bead_tree(chat_storage=None, conversation_id: str = None) -> BeadTree:
    """Load the bead tree for the current (or specified) conversation.

    Returns an empty BeadTree if no beads exist yet.
    """
    conversation_id = _get_conversation_id(conversation_id)
    if not conversation_id:
        return BeadTree()

    if chat_storage is None:
        try:
            chat_storage, conversation_id = _resolve_chat_storage(conversation_id)
        except ValueError as e:
            logger.debug(f"📿 Chat record unavailable, using fallback store: {e}")
            chat_storage = None

    chat = chat_storage.get(conversation_id) if chat_storage else None
    if not chat:
        return BeadTree(beads=_parse_beads(_load_fallback(conversation_id)))

    # Read raw _beads from the chat's extra fields
    raw_beads = None
    if hasattr(chat, "__pydantic_extra__") and chat.__pydantic_extra__:
        raw_beads = chat.__pydantic_extra__.get(_BEADS_FIELD)
    if not raw_beads:
        # Try dict access for raw-dict loaded chats
        raw_beads = getattr(chat, _BEADS_FIELD, None)

    if not raw_beads or not isinstance(raw_beads, list):
        # Chat exists but carries no beads yet — a fallback file may hold
        # beads written before the chat first synced to disk.
        return BeadTree(beads=_parse_beads(_load_fallback(conversation_id)))

    return BeadTree(beads=_parse_beads(raw_beads))


def save_bead_tree(tree: BeadTree, chat_storage=None, conversation_id: str = None) -> None:
    """Persist the bead tree back to the chat record."""
    conversation_id = _get_conversation_id(conversation_id)
    if not conversation_id:
        logger.warning("Cannot save beads: no conversation_id available")
        return

    bead_dicts = [b.model_dump() for b in tree.beads]

    if chat_storage is None:
        try:
            chat_storage, conversation_id = _resolve_chat_storage(conversation_id)
        except ValueError as e:
            logger.debug(f"📿 Chat record unavailable, saving to fallback: {e}")
            _save_fallback(conversation_id, bead_dicts)
            return

    chat = chat_storage.get(conversation_id)
    if not chat:
        # Chat not on disk yet (new conversation pre-sync, or CLI session).
        _save_fallback(conversation_id, bead_dicts)
        return

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

    # Beads now live on the chat record — retire any fallback file.
    _remove_fallback(conversation_id)


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
