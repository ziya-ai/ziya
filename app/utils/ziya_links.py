"""In-app links the model can write and the browser can follow.

A ``ziya://`` href addresses something inside this Ziya install — a
project, a conversation, or one message of a conversation — so the model
can cite a past turn as a clickable link instead of a bare id::

    ziya://project/<projectId>
    ziya://chat/<conversationId>
    ziya://chat/<conversationId>/<messageIndex>      0-based, as chat_search reports

Written in ordinary markdown, ``[what it says](ziya://chat/…/12)``, it renders
as a link; the frontend (``frontend/src/utils/ziyaLinks.ts`` is the
mirror of this module) intercepts the click, resolves the conversation's
project through ``GET /api/v1/chats/{id}/locate`` when it is not the
current one, switches project, opens the conversation and scrolls to the
message.  The project is deliberately NOT part of a chat link: a
conversation can be moved between projects, and the id alone still
resolves through the chat index.

The chat-history tools (``app/mcp/tools/chat_history_tools.py``) attach
these links to every conversation and match they return, so the model has
a ready-made href for each fact it cites.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

SCHEME = "ziya://"

# Conversation ids are uuid4 strings; project ids are the directory names
# under ~/.ziya/projects.  Neither ever contains a slash, so a single
# permissive token class is enough to reject anything path-like.
_ID = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_CHAT_RE = re.compile(rf"^{re.escape(SCHEME)}chat/({_ID})(?:/(\d+))?/?$")
_PROJECT_RE = re.compile(rf"^{re.escape(SCHEME)}project/({_ID})/?$")


def chat_link(conversation_id: str, message_index: Optional[int] = None) -> str:
    """The href for a conversation, or for one of its messages."""
    href = f"{SCHEME}chat/{conversation_id}"
    if message_index is not None and int(message_index) >= 0:
        href += f"/{int(message_index)}"
    return href


def project_link(project_id: str) -> str:
    return f"{SCHEME}project/{project_id}"


def parse_ziya_link(href: str) -> Optional[Dict[str, Any]]:
    """Decode a ``ziya://`` href.

    Returns ``{"kind": "chat", "conversationId": …, "messageIndex": int|None}``
    or ``{"kind": "project", "projectId": …}``; None for anything else,
    including a malformed ``ziya://`` href.
    """
    if not isinstance(href, str):
        return None
    s = href.strip()
    m = _CHAT_RE.match(s)
    if m:
        return {
            "kind": "chat",
            "conversationId": m.group(1),
            "messageIndex": int(m.group(2)) if m.group(2) is not None else None,
        }
    m = _PROJECT_RE.match(s)
    if m:
        return {"kind": "project", "projectId": m.group(1)}
    return None


# One sentence, shared by the chat-history tool descriptions so the model
# learns the format exactly where it receives the ids.
LINK_HINT = (
    "To give the user a clickable reference, write a markdown link whose "
    "href is the `link` field of a result — `ziya://chat/<conversationId>` "
    "for a conversation, `ziya://chat/<conversationId>/<messageIndex>` for "
    "one message, `ziya://project/<projectId>` for a project."
)
