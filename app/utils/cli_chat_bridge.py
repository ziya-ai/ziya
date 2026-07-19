"""Bridge between the CLI's session store and the GUI's chat store.

The CLI persists conversations as ``~/.ziya/sessions/<id>.json`` with a flat
``history: [{type: 'human'|'ai', content}]`` list, keyed on a ``cli_<id>``
conversation id.  The GUI persists them as ``Chat`` records under
``~/.ziya/projects/<project_id>/chats/<chat_id>.json`` (encrypted at rest),
where the bare ``chat_id`` *is* the conversation id the rest of the backend
keys on (beads, task-result injection, feedback).

This module is the seam that lets a CLI session *join* — and continue
writing into — a live GUI conversation ("Option B, live attach").  While
attached, both surfaces operate on the same underlying chat file: turns
added on either side are picked up by the other.  It keeps two concerns
cleanly separated:

  * Pure conversion (:func:`gui_messages_to_cli_history`,
    :func:`cli_history_to_gui_messages`) — no I/O, no encryption, no model
    import — so the round-trip is unit-testable in isolation.
  * Thin storage helpers (:func:`resolve_project`, :func:`list_joinable_chats`,
    :func:`load_chat_as_history`, :func:`write_back`, :func:`chat_signature`)
    that reuse the GUI's own ``ProjectStorage`` / ``ChatStorage`` so the CLI
    never re-implements the chat file format or its encryption policy.

Role vocab is bidirectional to match the server's own normalization
(app/server.py): GUI ``human``/``user`` <-> CLI ``human``; GUI ``assistant``
<-> CLI ``ai``; ``system`` is preserved verbatim in both directions.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Pure conversion (no I/O) — unit-testable without disk or encryption.
# ---------------------------------------------------------------------------

def _get(obj: Any, key: str, default=None):
    """Read ``key`` from a dict or a pydantic ``Message`` uniformly."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _cli_type_for_role(role: str) -> str:
    """Map a GUI message role to a CLI history ``type``."""
    if role in ("assistant", "ai"):
        return "ai"
    if role == "system":
        return "system"
    return "human"  # human, user, or anything unexpected


def _gui_role_for_type(t: str) -> str:
    """Map a CLI history ``type`` to a GUI message role."""
    if t == "ai":
        return "assistant"
    if t == "system":
        return "system"
    return "human"


def gui_messages_to_cli_history(messages) -> List[dict]:
    """Convert GUI ``Message`` objects (or dicts) to CLI history entries.

    Preserves ``_timestamp`` (epoch ms) and any ``images`` so the CLI's
    ``build_messages_for_streaming`` can surface elapsed-time context and
    multi-modal content exactly as the web path does.
    """
    history: List[dict] = []
    for m in messages or []:
        role = _get(m, "role", "") or ""
        entry: dict = {
            "type": _cli_type_for_role(role),
            "content": _get(m, "content", "") or "",
        }
        ts = _get(m, "timestamp")
        if ts:
            entry["_timestamp"] = ts
        images = _get(m, "images")
        if images:
            entry["images"] = images
        history.append(entry)
    return history


def cli_history_to_gui_messages(history, existing=None) -> List[dict]:
    """Convert CLI history entries to GUI message dicts, index-aligned.

    ``existing`` is the GUI chat's current message list.  Where a history
    entry lines up positionally with an existing message of the *same role*,
    that message's ``id`` (and, absent an explicit ``_timestamp``, its
    ``timestamp``) is reused so the unchanged prefix of the conversation
    keeps stable ids — the GUI sidebar doesn't churn and message-level
    references (beads, feedback) stay valid.  New turns get a fresh uuid and
    the current time.  Returned as plain dicts; ``ChatUpdate`` coerces them
    to ``Message`` on write.
    """
    existing = existing or []
    now = int(time.time() * 1000)
    out: List[dict] = []
    for i, entry in enumerate(history or []):
        role = _gui_role_for_type(_get(entry, "type", "human") or "human")
        content = _get(entry, "content", "") or ""
        prior = existing[i] if i < len(existing) else None
        prior_role = _get(prior, "role") if prior is not None else None
        aligned = prior is not None and prior_role == role
        msg: dict = {
            "id": _get(prior, "id") if aligned else str(uuid.uuid4()),
            "role": role,
            "content": content,
            "timestamp": (
                _get(entry, "_timestamp")
                or (_get(prior, "timestamp") if aligned else None)
                or now
            ),
        }
        images = _get(entry, "images")
        if images:
            msg["images"] = images
        out.append(msg)
    return out


# ---------------------------------------------------------------------------
# Storage helpers — reuse the GUI's own ProjectStorage / ChatStorage.
# Imports are deferred into each function to keep CLI startup light (the
# CLI intentionally avoids importing the storage/model stack until needed).
# ---------------------------------------------------------------------------

def resolve_project(root: str):
    """Return the GUI ``Project`` registered for ``root``, or ``None``.

    Mirrors :func:`app.cli_card_runner.resolve_card`'s project resolution:
    a root that has never been opened in the GUI has no project record and
    therefore no joinable chats.
    """
    from app.storage.projects import ProjectStorage
    from app.utils.paths import get_ziya_home
    return ProjectStorage(get_ziya_home()).get_by_path(root)


def _chat_storage(project_id: str):
    from app.storage.chats import ChatStorage
    from app.utils.paths import get_project_dir
    return ChatStorage(get_project_dir(project_id))


def list_joinable_chats(root: str) -> Tuple[Optional[str], list]:
    """Return ``(project_id, summaries)`` for the chats under ``root``.

    ``summaries`` are ``ChatSummary`` objects (message-count only, no bodies)
    sorted most-recently-active first — the cheap list the picker renders.
    ``project_id`` is ``None`` when the root has no GUI project.
    """
    project = resolve_project(root)
    if project is None:
        return None, []
    return project.id, _chat_storage(project.id).list_summaries()


def load_chat_as_history(project_id: str, chat_id: str):
    """Load a GUI chat and return ``(chat, cli_history)``.

    ``chat`` is the full ``Chat`` (or ``None`` if missing/expired);
    ``cli_history`` is its messages converted to CLI history form.
    """
    storage = _chat_storage(project_id)
    chat = storage.get(chat_id)
    if chat is None:
        return None, []
    return chat, gui_messages_to_cli_history(chat.messages)


def chat_signature(project_id: str, chat_id: str) -> Optional[Tuple[int, int]]:
    """Cheap change signature for external-edit detection.

    ``(lastActiveAt, message_count)`` — both advance on any write from
    either side (``ChatStorage.update`` bumps ``lastActiveAt``).  Recorded
    after the CLI's own write-back as a baseline; a later value beyond that
    baseline means the GUI (or another CLI) touched the chat.  ``None`` if
    the chat can't be read.
    """
    chat = _chat_storage(project_id).get(chat_id)
    if chat is None:
        return None
    return (chat.lastActiveAt, len(chat.messages))


def write_back(project_id: str, chat_id: str, cli_history) -> Optional[Tuple[int, int]]:
    """Persist CLI history into the GUI chat, returning the new signature.

    Reuses the existing message ids for the unchanged prefix (see
    :func:`cli_history_to_gui_messages`).  Returns the post-write
    :func:`chat_signature` so the caller can adopt it as the new baseline,
    or ``None`` if the chat no longer exists.
    """
    from app.models.chat import ChatUpdate
    storage = _chat_storage(project_id)
    chat = storage.get(chat_id)
    if chat is None:
        return None
    messages = cli_history_to_gui_messages(cli_history, chat.messages)
    storage.update(chat_id, ChatUpdate(messages=messages))
    return chat_signature(project_id, chat_id)


def chat_display_label(summary) -> str:
    """Short human label for a chat summary (title, falling back to id)."""
    title = (_get(summary, "title", "") or "").strip()
    if title:
        return title
    return "untitled " + (_get(summary, "id", "") or "")[:8]
