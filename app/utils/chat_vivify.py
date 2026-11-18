"""
Create a minimal chat record at a client-chosen id.

A brand-new web conversation exists only in the browser until it has a
message: bulkSync does not push empty shells.  A server-side write that needs
the record (turning an agent on, a context tool) would otherwise refuse a
conversation the user is looking at.  The record is created at the exact id
the client already uses, never a fresh uuid, so the client's first push lands
on it and the server-owned fields written here survive (app/api/chats.py
carries ``activeAgent``, ``placements`` and ``loadedMcp`` forward).

Same shape as the context tools' auto-vivify
(app/mcp/tools/context_management.py).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Must match the frontend placeholder EXACTLY (PLACEHOLDER_TITLES, isEmptyShell).
PLACEHOLDER_TITLE = "New Conversation"


def vivify_chat(storage: Any, project_id: str, chat_id: str) -> Optional[Any]:
    """Return the chat at ``chat_id``, creating an empty record when none exists.

    Returns None when the conversation is stored in a different project (a
    global conversation shown here): writing a same-id record here would
    create an invisible shadow of it.
    """
    existing = storage.get(chat_id)
    if existing is not None:
        return existing
    try:
        from app.utils.chat_home import resolve_chat_home
        home = resolve_chat_home(project_id, storage.chats_dir.parent, chat_id)
    except Exception as e:  # noqa: BLE001 -- the probe is advisory
        logger.debug("vivify_chat: chat home probe failed: %s", e)
        home = None
    if home is not None and home[0] != project_id:
        return None
    now = int(time.time() * 1000)
    storage._write_json(storage._chat_file(chat_id), {
        "id": chat_id,
        "title": PLACEHOLDER_TITLE,
        "groupId": None,
        "contextIds": [],
        "skillIds": [],
        "additionalFiles": [],
        "additionalPrompt": None,
        "messages": [],
        "createdAt": now,
        "lastActiveAt": now,
        "_version": now,
    })
    try:
        from app.storage import chat_index
        chat_index.on_chat_written(chat_id, project_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("vivify_chat: chat_index update skipped: %s", e)
    logger.info("vivified chat record %s in project %s", chat_id[:8], project_id)
    return storage.get(chat_id)
