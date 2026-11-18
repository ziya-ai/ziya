"""Post a message from one conversation into another
(design/conversation-post.md).

A post is one message appended to the TARGET conversation's record by the
server, with role ``peer``.  Like the project-change note
(app/utils/project_change_note.py) it has two audiences:

* ``content`` is what the model is sent: a ``[Ziya: posted from ...]`` header
  naming the source conversation, then the posted text.  The chat request
  carries history as ``[role, content, ...]`` tuples, so the header is the
  only provenance that reaches the server on a later turn.
* ``peerPost`` is what the transcript renders: the source link, the mode and
  the bare text.

Modes:

* ``context``  append only.  The target model sees it on its next turn.
* ``submit``   append, then a turn.  If the target has a turn running in this
  process the post is delivered into it through the feedback queue (the model
  sees it at the next tool boundary).  If the target is idle, the server
  starts a turn there from the record and saves its answer
  (app/utils/server_turn.py).  If that fails the post stays as context and
  the result says why.

Loop guard, depth 1: a turn that a ``submit`` post is driving may not itself
``submit`` to another conversation.  Tracked in memory by relay turn id.

The peer role is not one a provider accepts; ``fold_peer_posts`` turns peer
entries of request history into human-role text before the role filter in
``/api/chat``.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from app.utils.logging_utils import logger

PEER_ROLE = "peer"
POST_PREFIX = "[Ziya: posted from conversation "
MODES = ("context", "submit")
MAX_POST_CHARS = 100_000

_HUMAN_ROLES = ("human", "user")

# target conversation id -> relay turn id that a submit post is driving.
_peer_driven: Dict[str, str] = {}


class ChatPostError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── message ───────────────────────────────────────────────────────────────

def build_post_message(text: str, source: Dict[str, Any], mode: str,
                       now_ms: Optional[int] = None) -> Dict[str, Any]:
    """The message dict appended to the target's ``messages``.

    ``source``: ``{chatId, title?, projectId?, messageIndex?}``.
    """
    from app.utils.session_context_prompt import format_prompt_timestamp
    from app.utils.ziya_links import chat_link
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    title = (source.get("title") or "").strip() or "untitled"
    link = chat_link(source["chatId"], source.get("messageIndex"))
    header = (
        f"{POST_PREFIX}\u201c{title}\u201d ({link}) at {format_prompt_timestamp(now_ms)}. "
        "The text below was written in that conversation and posted here; "
        "the user did not type it in this one.]"
    )
    return {
        "id": str(uuid.uuid4()),
        "role": PEER_ROLE,
        "content": f"{header}\n\n{text}",
        "timestamp": now_ms,
        "_serverAuthored": True,
        "peerPost": {
            "fromChatId": source["chatId"],
            "fromTitle": title,
            "fromProjectId": source.get("projectId"),
            "fromMessageIndex": source.get("messageIndex"),
            "link": link,
            "mode": mode,
            "text": text,
        },
    }


def is_peer_post(content: Any) -> bool:
    return isinstance(content, str) and content.startswith(POST_PREFIX)


# ── request history ───────────────────────────────────────────────────────

def _role_content(msg: Any):
    if isinstance(msg, (list, tuple)) and len(msg) >= 2:
        return msg[0], msg[1]
    if isinstance(msg, dict):
        return msg.get("role", msg.get("type")), msg.get("content")
    return None, None


def _replace(msg: Any, role: str, content: str) -> Any:
    if isinstance(msg, dict):
        out = {**msg, "content": content}
        out["role" if "role" in msg or "type" not in msg else "type"] = role
        return out
    out = list(msg)
    out[0], out[1] = role, content
    return out


def fold_peer_posts(messages: Any) -> Any:
    """Make peer posts in request history into human-role text.

    The chat endpoint keeps only human and assistant entries.  A peer post
    directly after a human entry is appended to it; one directly before a
    human entry is prepended to it (two human turns in a row are avoided);
    otherwise it becomes a human entry of its own.  Returns a new list.
    """
    if not isinstance(messages, list):
        return messages
    out: list = []
    pending: Any = None           # a peer entry already converted to human
    for msg in messages:
        role, content = _role_content(msg)
        if role == PEER_ROLE and isinstance(content, str):
            if pending is not None:
                _r, pc = _role_content(pending)
                pending = _replace(pending, "human", pc + "\n\n" + content)
                continue
            if out:
                r, c = _role_content(out[-1])
                if r in _HUMAN_ROLES and isinstance(c, str):
                    out[-1] = _replace(out[-1], r, (c + "\n\n" if c else "") + content)
                    continue
            pending = _replace(msg, "human", content)
            continue
        if pending is not None:
            if role in _HUMAN_ROLES and isinstance(content, str):
                _r, pc = _role_content(pending)
                msg = _replace(msg, role, pc + ("\n\n" + content if content else ""))
            else:
                out.append(pending)
            pending = None
        out.append(msg)
    if pending is not None:
        out.append(pending)
    return out


# ── loop guard ────────────────────────────────────────────────────────────

def _active_turn_id(conversation_id: str) -> Optional[str]:
    """Id of the turn running for the conversation in THIS process."""
    from app.agents import chat_turn_relay
    if conversation_id not in chat_turn_relay.active_conversation_ids():
        return None
    st = chat_turn_relay.status(conversation_id) or {}
    return st.get("turn_id") if st.get("active") else None


def mark_peer_driven(conversation_id: str, turn_id: Optional[str]) -> None:
    if conversation_id and turn_id:
        _peer_driven[conversation_id] = turn_id


def is_peer_driven(conversation_id: Optional[str],
                   active_turn_id: Callable[[str], Optional[str]] = _active_turn_id) -> bool:
    """Whether the conversation's CURRENT turn received a submit post."""
    if not conversation_id:
        return False
    marked = _peer_driven.get(conversation_id)
    if not marked:
        return False
    if active_turn_id(conversation_id) == marked:
        return True
    _peer_driven.pop(conversation_id, None)
    return False


def _reset_for_tests() -> None:
    _peer_driven.clear()


# ── the post ──────────────────────────────────────────────────────────────

def _storage(project_id: str):
    from app.storage.chats import ChatStorage
    from app.utils.paths import get_project_dir
    return ChatStorage(get_project_dir(project_id))


def _load(chat_id: str):
    from app.mcp.tools.chat_history_tools import _load_chat
    from app.utils.paths import get_ziya_home
    return _load_chat(get_ziya_home(), chat_id)


def _write_post(source_id: str, target_id: str, text: str, mode: str) -> Dict[str, Any]:
    """Disk half (sync).  Returns ``{message, target: {...}}``."""
    from app.utils.chat_message_ops import chat_record_lock
    target = _load(target_id)
    if target is None:
        raise ChatPostError(f"No conversation found with id {target_id!r}.", 404)
    target_pid, _raw = target
    source: Dict[str, Any] = {"chatId": source_id}
    loaded = _load(source_id)
    if loaded is not None:
        spid, sraw = loaded
        n = len(sraw.get("messages") or [])
        source.update(title=sraw.get("title"), projectId=sraw.get("projectId") or spid,
                      messageIndex=(n - 1) if n else None)
    message = build_post_message(text, source, mode)

    storage = _storage(target_pid)
    with chat_record_lock(storage):
        path = storage._chat_file(target_id)
        raw = storage._read_json(path)
        if not raw:
            raise ChatPostError(f"Conversation {target_id!r} vanished mid-post.", 404)
        raw.setdefault("messages", []).append(message)
        now = int(time.time() * 1000)
        raw["lastActiveAt"] = now
        raw["_version"] = max(now, int(raw.get("_version") or 0) + 1)
        raw["hasUnreadResponse"] = True
        storage._write_json(path, raw)
    return {
        "message": message,
        "target": {"conversationId": target_id, "projectId": target_pid,
                   "title": raw.get("title") or "",
                   "messageIndex": len(raw["messages"]) - 1,
                   "_version": raw["_version"]},
    }


def _default_deliver(conversation_id: str, item: Dict[str, Any]) -> None:
    from app.server import _enqueue_feedback   # lazy: app.server imports widely
    _enqueue_feedback(conversation_id, item)


async def _default_start_turn(conversation_id: str, message: Dict[str, Any]) -> str:
    from app.utils.server_turn import start_server_turn
    return await start_server_turn(
        conversation_id, {"kind": "peer_post", "messageId": message["id"]})


def submit_policy(source_id: str, target_id: str) -> Optional[str]:
    """Why a ``submit`` from ``source_id`` to ``target_id`` is refused, or
    None to allow it.

    The one place rules about who may start work where belong (a project
    that does not accept submitted turns, a per-source rate limit).  It
    allows everything today.  Asking the USER before a submit needs no code
    here: the tool declares ``consent_op_key = "mode"``, so setting
    ``consent.tools.chat_post.ops.submit`` to ``ask`` in permissions.json
    puts the call behind the consent gate.
    """
    return None


_DEFAULT_STARTER: Any = object()


async def post_to_chat(
    source_id: Optional[str], target_id: str, text: str, mode: str = "context", *,
    deliver: Callable[[str, Dict[str, Any]], None] = _default_deliver,
    active_turn_id: Callable[[str], Optional[str]] = _active_turn_id,
    start_turn: Any = _DEFAULT_STARTER,
) -> Dict[str, Any]:
    """Append a peer post to ``target_id`` and, for ``submit``, get a turn
    to follow.  Raises ``ChatPostError``.  Must run on the event loop (the
    feedback queue is an ``asyncio.Queue``); disk work is done off it.
    """
    source_id = (source_id or "").strip()
    target_id = (target_id or "").strip()
    if mode not in MODES:
        raise ChatPostError(f"mode must be one of {', '.join(MODES)}.")
    if not source_id:
        raise ChatPostError("No source conversation: a post names where it came from.")
    if not target_id:
        raise ChatPostError("target chat_id is required.")
    if target_id == source_id:
        raise ChatPostError("A conversation cannot post to itself.")
    if not isinstance(text, str) or not text.strip():
        raise ChatPostError("text must not be empty.")
    if len(text) > MAX_POST_CHARS:
        raise ChatPostError(f"text is {len(text)} chars; max is {MAX_POST_CHARS}.")
    if mode == "submit" and is_peer_driven(source_id, active_turn_id):
        raise ChatPostError(
            "This turn was itself started by a post from another conversation, "
            "so it cannot submit a turn elsewhere (depth limit 1). "
            "mode='context' is still allowed.", 409)
    if mode == "submit":
        refused = submit_policy(source_id, target_id)
        if refused:
            raise ChatPostError(refused, 403)
    if start_turn is _DEFAULT_STARTER:
        start_turn = _default_start_turn

    written = await asyncio.to_thread(_write_post, source_id, target_id, text, mode)
    message, target = written["message"], written["target"]
    out: Dict[str, Any] = {"success": True, "mode": mode, "messageId": message["id"],
                           "target": target, "delivery": "appended"}
    if mode != "submit":
        out["message"] = f"Posted to '{target['title']}' as context. No turn was started."
        return out

    turn_id = active_turn_id(target_id)
    if turn_id:
        deliver(target_id, {"type": "tool_feedback", "message": message["content"],
                            "feedback_id": message["id"]})
        mark_peer_driven(target_id, turn_id)
        out.update(delivery="live_feedback", turnId=turn_id,
                   message=f"Posted to '{target['title']}' and delivered into its running turn.")
    elif start_turn is not None:
        try:
            started = start_turn(target_id, message)
            if asyncio.iscoroutine(started):
                started = await started
        except Exception as exc:  # noqa: BLE001 -- the post is already saved; report, don't raise
            logger.warning(f"📮 CHAT_POST: could not start a turn in {target_id[:8]}: {exc!r}")
            out.update(delivery="appended", turnStarted=False, turnError=str(exc),
                       message=(f"Posted to '{target['title']}', but no turn was started "
                                f"there ({exc}). The post will be read on that "
                                "conversation's next turn."))
        else:
            mark_peer_driven(target_id, started if isinstance(started, str) else None)
            out.update(delivery="turn_started", turnId=started,
                       message=(f"Posted to '{target['title']}' and started a turn there. "
                                "Its answer is saved to that conversation; read it with "
                                "chat_read once the turn has finished."))
    else:
        out.update(delivery="appended", turnStarted=False,
                   message=(f"Posted to '{target['title']}'. It has no turn running and "
                            "no turn was started: the post will be read on that "
                            "conversation's next turn."))
    logger.info(f"📮 CHAT_POST: {source_id[:8]} -> {target_id[:8]} mode={mode} delivery={out['delivery']}")
    return out
