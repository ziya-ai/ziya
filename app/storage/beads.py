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
import uuid
from typing import Any, Dict, List, Optional

from app.models.bead import Bead, BeadTree
from app.utils.logging_utils import logger


# Field name on the Chat JSON record
_BEADS_FIELD = "_beads"

# Field name carrying the fork-lineage root (design/bead-branching.md "b2").
# A forked conversation stamps this with the id of its lineage's ROOT; beads
# live on the root's record and every conversation in the lineage resolves to
# that one shared, state-synced tree.  Absent → the conversation is its own
# root and owns its beads directly (the non-fork path, unchanged).
_LINEAGE_ROOT_FIELD = "lineageRootId"


def _read_lineage_root(chat) -> Optional[str]:
    """Extract lineageRootId from a chat record (extra field or attr)."""
    if chat is None:
        return None
    if hasattr(chat, "__pydantic_extra__") and chat.__pydantic_extra__:
        rid = chat.__pydantic_extra__.get(_LINEAGE_ROOT_FIELD)
        if rid:
            return rid
    return getattr(chat, _LINEAGE_ROOT_FIELD, None)


def _resolve_bead_record(chat_storage, conversation_id: str):
    """Return (bead_conversation_id, chat_record_or_None): the record that
    holds this conversation's shared bead tree.

    Reads the conversation's own record once.  If it carries a
    ``lineageRootId`` whose root record exists, returns the ROOT's id+record
    so the whole lineage shares one tree (state sync).  Self-root (no
    lineageRootId) and missing-root (root deleted/never-synced) both return
    the conversation's own id+record — the latter guard prevents a dangling
    root from stranding the fork's bead writes.
    """
    if chat_storage is None:
        return conversation_id, None
    try:
        own = chat_storage.get(conversation_id)
    except Exception:
        return conversation_id, None
    if not own:
        return conversation_id, None
    root_id = _read_lineage_root(own)
    if not root_id or root_id == conversation_id:
        return conversation_id, own
    try:
        root = chat_storage.get(root_id)
    except Exception:
        root = None
    if root:
        return root_id, root
    return conversation_id, own


def _get_conversation_id(conversation_id: Optional[str] = None) -> Optional[str]:
    """Resolve a conversation id: explicit arg first, then request ContextVar."""
    if conversation_id:
        return conversation_id
    try:
        from app.context import get_conversation_id_or_none
        return get_conversation_id_or_none()
    except ImportError:
        return None


# Bead statuses that count as "open" for the sidebar indicator: a thread the
# user could still return to.  active + parked (per request); completed /
# abandoned are done.  An active bead is just "the current thread", so any
# conversation with a live tree shows at least 1.
_OPEN_BEAD_STATUSES = frozenset({"active", "parked"})


def count_open_beads(raw_beads) -> int:
    """Count beads in an open (active or parked) state.

    Accepts the raw ``_beads`` list off a chat record (list of dicts) or a
    list of Bead objects; tolerant of None / non-list (-> 0) so the summary
    builders can call it on every chat unconditionally.  This is the cheap
    derived signal the sidebar renders — it never loads or constructs the
    full BeadTree.
    """
    if not isinstance(raw_beads, list):
        return 0
    n = 0
    for b in raw_beads:
        status = b.get("status") if isinstance(b, dict) else getattr(b, "status", None)
        if status in _OPEN_BEAD_STATUSES:
            n += 1
    return n


def count_open_beads_for_conversation(raw_record, conversation_id) -> int:
    """Open-bead count for the sidebar indicator, mirroring load_bead_tree's
    SOURCES (record first, then the standalone fallback store) without ever
    constructing a BeadTree.

    The plain count_open_beads(data['_beads']) reads only the chat record.
    But beads written when the chat record wasn't resolvable (CLI sessions,
    not-yet-synced web conversations) live in ~/.ziya/beads/<id>.json until a
    later save_bead_tree migrates them onto the record.  load_bead_tree reads
    that fallback, so the bead CHIP shows those beads — but a record-only
    summary count showed 0, the exact chip-vs-summary divergence this fixes.

    Record-first ordering is correct across the migration window:
    save_bead_tree writes the record then removes the fallback, so whenever
    the record carries beads we trust it; only a record with no _beads
    consults the fallback (one stat for the common no-fallback case).

    Known gap (deferred): a lineage fork's beads live on the lineage ROOT
    record, not this conversation's record or its fallback — resolving that
    needs a cross-record read in the hot path and is tracked separately.
    """
    beads = raw_record.get("_beads") if isinstance(raw_record, dict) else None
    if beads:
        return count_open_beads(beads)
    if conversation_id:
        return count_open_beads(_load_fallback(conversation_id))
    return 0


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


def get_conversation_message_count(conversation_id: Optional[str] = None) -> Optional[int]:
    """Count of persisted user-visible messages for a conversation.

    This is the seam basis for ``Bead.message_index``: the point in the
    user-visible conversation where a bead was spawned, which is exactly
    what a future branch-from-bead operation truncates at.  The chat
    record's ``messages`` list is the correct source — it is the same
    user-visible history the frontend renders and a branch would slice,
    unlike the streaming executor's internal turn array (whose length
    counts system/tool_result turns and does not map to frontend indices).

    Returns None when the chat record isn't resolvable (CLI sessions, or a
    brand-new web conversation not yet synced to disk).  In that case the
    bead simply records no seam and branch-from-bead is unavailable for it
    — a clean graceful degradation, never an error.

    Note: read during streaming, this reflects the last frontend sync, so
    the seam is accurate to within ~1 message of live frontend state.  The
    branch UI anchors on the bead's content ("where you raised X"), never
    the raw index, so approximate is acceptable.  See design/bead-branching.md.
    """
    conversation_id = _get_conversation_id(conversation_id)
    if not conversation_id:
        return None
    try:
        chat_storage, conversation_id = _resolve_chat_storage(conversation_id)
    except ValueError as e:
        logger.debug(f"📿 message-count: chat unresolvable: {e}")
        return None
    chat = chat_storage.get(conversation_id)
    if not chat:
        return None
    msgs = getattr(chat, "messages", None)
    if not isinstance(msgs, list):
        return None
    return len(msgs)


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

    # Resolve to the lineage-root record so a fork shares the root's tree
    # (b2).  Self-root conversations resolve to themselves (no behavior
    # change).  bead_id is the id whose record holds the beads.
    bead_id, chat = (
        _resolve_bead_record(chat_storage, conversation_id)
        if chat_storage else (conversation_id, None)
    )
    if not chat:
        return BeadTree(beads=_parse_beads(_load_fallback(bead_id)))

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
        return BeadTree(beads=_parse_beads(_load_fallback(bead_id)))

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

    # Resolve to the lineage-root record so a fork's writes land on the
    # shared root tree (b2).  Self-root resolves to itself.
    bead_id, chat = _resolve_bead_record(chat_storage, conversation_id)
    if not chat:
        # Chat not on disk yet (new conversation pre-sync, or CLI session).
        _save_fallback(bead_id, bead_dicts)
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
    chat_storage._write_json(chat_storage._chat_file(bead_id), d)
    logger.debug(f"📿 Saved {len(tree.beads)} beads for conv {bead_id[:8]}"
                 + (f" (lineage root of {conversation_id[:8]})" if bead_id != conversation_id else ""))

    # Beads now live on the chat record — retire any fallback file.
    _remove_fallback(bead_id)


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


def resolve_origin_bead(origin_conversation_id: str, origin_bead_id: str) -> Optional[str]:
    """Mark a forked bead's origin as completed, walking the lineage edge.

    When a bead inherited via ``inherit_beads_for_seam`` is completed, the
    origin's parked note for that same thread is now a stale lie — the thread
    *was* followed, just in the branch.  This resolves it: load the origin
    conversation's tree, find the origin bead, and mark it completed.

    Constraints (mechanical, not policy):
      - **Only non-terminal origins are touched.**  An origin already
        ``completed`` is left alone (idempotent); an ``abandoned`` one is not
        resurrected.  Only ``active`` / ``parked`` resolve.
      - **Cascades for free, one hop at a time.**  A resolved origin bead may
        itself carry an ``origin_*`` edge (fork of a fork); this recurses along
        that edge so the whole lineage chain resolves.  Cycle-guarded by a
        visited set keyed on (conversation_id, bead_id).
      - **Best-effort.**  A missing origin conversation/bead is not an error —
        the branch may have been deleted; we simply stop walking.

    Returns the origin bead id that was resolved (the first hop), or None when
    nothing was resolved (origin gone, terminal, or no edge).
    """
    return _resolve_origin_walk(origin_conversation_id, origin_bead_id, set())


def _resolve_origin_walk(conv_id: str, bead_id: str, visited: set) -> Optional[str]:
    if not conv_id or not bead_id:
        return None
    key = (conv_id, bead_id)
    if key in visited:
        return None
    visited.add(key)

    try:
        chat_storage, conv_id = _resolve_chat_storage(conv_id)
    except ValueError as e:
        logger.debug(f"📿 resolve-origin: origin chat unresolvable: {e}")
        return None

    tree = load_bead_tree(chat_storage=chat_storage, conversation_id=conv_id)
    target = next((b for b in tree.beads if b.id == bead_id), None)
    if target is None:
        logger.debug(f"📿 resolve-origin: bead {bead_id[:8]} gone from {conv_id[:8]}")
        return None
    if target.status in ("completed", "abandoned"):
        return None  # terminal — leave it; don't resurrect or double-resolve

    target.status = "completed"
    # Resume the origin's parent if it was parked (mirror bead_complete).
    if target.parent_id:
        parent = next((b for b in tree.beads if b.id == target.parent_id), None)
        if parent and parent.status == "parked":
            parent.status = "active"
    save_bead_tree(tree, chat_storage=chat_storage, conversation_id=conv_id)
    logger.info(f"📿 resolve-origin: completed {bead_id[:8]} in {conv_id[:8]} via lineage")

    # Cascade one hop further if this origin was itself a fork.
    if target.origin_conversation_id and target.origin_bead_id:
        _resolve_origin_walk(target.origin_conversation_id, target.origin_bead_id, visited)
    return bead_id


def inherit_beads_for_seam(
    tree: BeadTree, bead_id: str, source_conversation_id: Optional[str] = None
):
    """Compute the bead set a fork inherits when splitting at a bead's seam.

    A bead is an un-taken branch point recorded with its message_index seam
    (design/bead-branching.md).  Splitting on it produces a new conversation
    truncated to that seam.  This computes which beads come along, by the
    timeline rule:

      - inherit beads with ``message_index is not None and message_index <= seam``
        (ancestors always qualify — a parent is created no later than its
        child, so its message_index <= the child's, which keeps the
        parent_id chain intact)
      - the chosen bead is promoted to ``active``
      - any OTHER bead that was ``active`` becomes ``parked`` (one active at a
        time)
      - beads born after the seam (``message_index > seam``) are dropped —
        those threads hadn't happened yet on this branch
      - beads with ``message_index is None`` are dropped — they predate the
        seam-recording feature (or were created when the chat wasn't
        resolvable) and can't be placed on the timeline

    Returns ``(seam_index, inherited_beads, bead_label)``.  Inherited beads are
    deep copies — the source tree is never mutated.  Raises ``ValueError`` if
    the bead is absent or has no message_index seam (branch is unavailable for
    those, per the step-1 graceful-degradation contract).
    """
    chosen = next((b for b in tree.beads if b.id == bead_id), None)
    if chosen is None:
        raise ValueError(f"bead not found: {bead_id}")
    seam = chosen.message_index
    if seam is None:
        raise ValueError(
            f"bead {bead_id} has no message_index seam — cannot branch from it"
        )

    selected = [
        b for b in tree.beads
        if b.message_index is not None and b.message_index <= seam
    ]
    # Fresh-id map; remap parent_id within the inherited set.  A parent's
    # message_index <= its child's, so every selected bead's parent is also
    # selected (or None) — get() with a None fallback roots any stray.
    id_map = {b.id: f"bead_{uuid.uuid4().hex[:12]}" for b in selected}

    inherited = []
    for b in selected:
        nb = b.model_copy(deep=True)
        # Record origin against the ORIGINAL ids before reassigning.
        nb.origin_conversation_id = source_conversation_id
        nb.origin_bead_id = b.id
        nb.id = id_map[b.id]
        nb.parent_id = id_map.get(b.parent_id) if b.parent_id else None
        if b.id == bead_id:
            nb.status = "active"
        elif nb.status == "active":
            nb.status = "parked"
        inherited.append(nb)
    return seam, inherited, chosen.content