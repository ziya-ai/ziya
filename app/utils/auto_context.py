"""
Automatic context for files a conversation writes (phase 1).

See design/auto-context-tiers.md.  When a conversation writes a project file
-- the apply button, the ``file_write`` tool, or any other write path that
knows its conversation -- the file is recorded on that conversation's chat
record:

  * **included**: the file is at or under the project's
    ``contextManagement.auto_add_token_limit``.  It is appended to
    ``additionalFiles`` and tagged in ``_autoAddedFiles`` (owner ``auto``), so
    it reaches the next prompt through the existing model-pinned merge in
    ``build_messages_for_streaming``.
  * **tracked**: the file is over the limit.  It is NOT added to context.  An
    entry in ``_autoTrackedFiles`` records the pre-write and post-write hashes
    and the size, and ``format_tracked_files_notice`` tells the model each turn
    that the file exists and whether it changed on disk since this
    conversation last wrote it.  No content is stored or sent.

Tracked-file state is kept on the chat record rather than in
``FileStateManager``: FileStateManager is rebuilt from the files that ARE in
context and its authority message walks every file it holds, so registering a
not-in-context file there could put it in the prompt, which is exactly what the
tracked tier exists to avoid.

Rules
-----
* Files the user pinned, or the model added with ``context_add_file``, keep
  their owner; a write never changes ownership.
* Only project files count.  Paths outside the project root and scratch paths
  under ``.ziya/`` are ignored (they change constantly and would flood context).
* Phase 1 has no decay: an ``auto`` file stays included until removed with
  ``context_remove_file``, and a file already included that grows past the limit
  stays included.  A tracked file that shrinks under the limit is promoted.
* Every entry point swallows its own errors.  Recording context is a
  side effect of a write; it must never make a successful write look failed.
"""

import hashlib
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger

AUTO_OWNER_FIELD = "_autoAddedFiles"
TRACKED_FIELD = "_autoTrackedFiles"
MODEL_OWNER_FIELD = "_modelAddedFiles"   # mirrors app.mcp.tools.context_management

_SCRATCH_PREFIXES = (".ziya/", ".ziya" + os.sep)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sha256_of(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def sha256_text(text: Optional[str]) -> Optional[str]:
    """Hash of text as it would be written with utf-8; None for None."""
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def project_relative(abs_path: str, project_root: str) -> Optional[str]:
    """Project-relative path, or None if outside the project or scratch."""
    try:
        root = os.path.realpath(project_root)
        target = os.path.realpath(abs_path)
    except (OSError, TypeError, ValueError):
        return None
    if target != root and not target.startswith(root + os.sep):
        return None
    rel = os.path.relpath(target, root).replace(os.sep, "/")
    if rel == ".ziya" or rel.startswith(_SCRATCH_PREFIXES):
        return None
    return rel


def _open_chat(conversation_id: str, project_root: str):
    """(storage, chat_file, chat_data) for an existing chat, else None.

    Never registers a project or creates a chat record: a write that cannot be
    attributed to a persisted conversation is simply not recorded.
    """
    from app.storage.chats import ChatStorage
    from app.storage.projects import ProjectStorage
    from app.utils.paths import get_project_dir, get_ziya_home

    project = ProjectStorage(get_ziya_home()).get_by_path(project_root)
    if not project:
        return None
    storage = ChatStorage(get_project_dir(project.id))
    chat_file = storage._chat_file(conversation_id)
    chat_data = storage._read_json(chat_file)
    if not chat_data:
        return None
    return storage, chat_file, chat_data


def _tokens_and_limit(abs_path: str, project_root: str) -> Tuple[int, int]:
    # Resolved through the module so tests (and callers) can patch the limit.
    from app.utils import chat_context_files as ccf
    limit = ccf.resolve_auto_add_token_limit(project_root)
    tokens = ccf.estimate_file_tokens(abs_path)
    return tokens, limit


def _fmt_time(ms: Optional[int]) -> str:
    if not ms:
        return "unknown time"
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ms / 1000))


# ---------------------------------------------------------------------------
# write side
# ---------------------------------------------------------------------------

def record_conversation_write(
    conversation_id: Optional[str],
    project_root: Optional[str],
    abs_path: str,
    pre_content: Optional[str],
    source: str,
) -> Optional[Dict[str, Any]]:
    """Record that ``conversation_id`` wrote ``abs_path``.

    ``pre_content`` is the file's text before the write (None for a new
    file); only its hash is kept.  ``source`` names the write path
    ("apply", "file_write", ...).

    Returns ``{"path", "tier", "tokens"}`` where tier is ``included``,
    ``tracked`` or ``already_in_context``; or None when nothing was recorded
    (no conversation, not a project file, no chat record, or an error).
    """
    if not conversation_id or not project_root or not abs_path:
        return None
    try:
        rel = project_relative(abs_path, project_root)
        if rel is None or not os.path.isfile(abs_path):
            return None
        opened = _open_chat(conversation_id, project_root)
        if opened is None:
            logger.debug(f"auto_context: no chat record for {conversation_id[:8]}; "
                         f"write to {rel} not recorded")
            return None
        storage, chat_file, chat = opened

        additional: List[str] = list(chat.get("additionalFiles") or [])
        auto: List[str] = list(chat.get(AUTO_OWNER_FIELD) or [])
        tracked: Dict[str, Any] = dict(chat.get(TRACKED_FIELD) or {})
        tokens, limit = _tokens_and_limit(abs_path, project_root)
        now = int(time.time() * 1000)

        if rel in additional:
            # Already in context under some owner; the existing file-state
            # machinery tracks its changes.  Never change ownership here.
            if rel not in tracked:
                return {"path": rel, "tier": "already_in_context", "tokens": tokens}
            tracked.pop(rel, None)   # inconsistent record; context wins
            tier = "already_in_context"
        elif limit <= 0 or tokens <= limit:
            # tokens == 0 means "unknown size": never block on it, matching
            # exceeds_auto_add_limit and the frontend filter.
            additional.append(rel)
            if rel not in auto:
                auto.append(rel)
            tracked.pop(rel, None)   # promoted from tracked, if it was
            tier = "included"
        else:
            prev = tracked.get(rel) or {}
            tracked[rel] = {
                "path": rel,
                "source": source,
                "tokens": tokens,
                "token_limit": limit,
                # the baseline is the content before this conversation FIRST
                # wrote the file; later writes keep it
                "baseline_hash": (prev["baseline_hash"] if "baseline_hash" in prev
                                  else sha256_text(pre_content)),
                "written_hash": _sha256_of(abs_path),
                "first_written_at": prev.get("first_written_at", now),
                "last_written_at": now,
                "write_count": int(prev.get("write_count", 0)) + 1,
            }
            tier = "tracked"

        chat["additionalFiles"] = additional
        chat[AUTO_OWNER_FIELD] = auto
        chat[TRACKED_FIELD] = tracked
        # Same stale-copy guard as context_add_file.
        chat["lastActiveAt"] = now
        chat["_version"] = now
        storage._write_json(chat_file, chat)
        logger.info(f"📎 AUTO_CONTEXT: {source} wrote {rel} (~{tokens} tokens, "
                    f"limit {limit}) -> {tier} for {conversation_id[:8]}")
        return {"path": rel, "tier": tier, "tokens": tokens}
    except Exception as e:
        logger.warning(f"auto_context: recording write to {abs_path} failed (non-fatal): {e}")
        return None


def read_text_or_none(abs_path: str) -> Optional[str]:
    """Pre-write snapshot helper for callers: text, or None if absent/unreadable."""
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# read side
# ---------------------------------------------------------------------------

def get_tracked_files(chat_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    tracked = (chat_data or {}).get(TRACKED_FIELD)
    return tracked if isinstance(tracked, dict) else {}


def is_auto_owned(chat_data: Optional[Dict[str, Any]], path: str) -> bool:
    return path in ((chat_data or {}).get(AUTO_OWNER_FIELD) or [])


def format_tracked_files_notice(conversation_id: Optional[str],
                                project_root: Optional[str] = None) -> str:
    """One line per tracked file, for the per-turn (non-cached) prompt part.

    Empty string when there is nothing to report or anything goes wrong.
    """
    if not conversation_id:
        return ""
    try:
        if not project_root:
            from app.context import get_project_root_or_none
            project_root = (get_project_root_or_none()
                            or os.environ.get("ZIYA_USER_CODEBASE_DIR"))
        if not project_root:
            return ""
        opened = _open_chat(conversation_id, project_root)
        if opened is None:
            return ""
        tracked = get_tracked_files(opened[2])
        if not tracked:
            return ""

        lines = []
        for rel in sorted(tracked):
            entry = tracked[rel] or {}
            abs_path = os.path.join(project_root, rel)
            when = _fmt_time(entry.get("last_written_at"))
            src = entry.get("source") or "a write"
            head = f"- {rel}: last written by this conversation {when} via {src}"
            if not os.path.isfile(abs_path):
                lines.append(f"{head}; the file no longer exists.")
                continue
            size = f"~{int(entry.get('tokens') or 0):,} tokens"
            if _sha256_of(abs_path) == entry.get("written_hash"):
                state = "unchanged since"
            else:
                state = "CHANGED on disk since this conversation last wrote it"
            lines.append(f"{head}, {size}; {state}.")

        return (
            "Files this conversation wrote that are too large to keep in context "
            "(tracked, not included). Their content is NOT shown here; use file_read "
            "for the part you need, or ask the user to pin the file.\n"
            + "\n".join(lines)
        )
    except Exception as e:
        logger.debug(f"format_tracked_files_notice({conversation_id}): {e}")
        return ""
