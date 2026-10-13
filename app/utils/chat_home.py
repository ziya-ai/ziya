"""
Find the copy of a conversation that a request should act on.

Requests name the project the WINDOW has open, not the conversation's own.
For a global conversation shown outside its home the two differ, and the
window's project can hold a stale same-id copy with no messages (a shadow,
historically auto-created by context_management for a conversation it did
not find locally).  Acting on that copy put a handoff continuation in the
wrong project and wrote tool state onto a record nobody reads
(incident 2026-10-02, tests/test_handoff_source_resolution.py).

Rules, in order:
  - a local copy WITH messages is used as is;
  - a local copy with NO messages is replaced by the fullest sibling copy
    that has messages (an empty local copy may be a shadow);
  - with NO local copy, the fullest sibling copy is used even if it has no
    messages (a handoff continuation is empty by construction);
  - otherwise the local copy, or None when there is none anywhere.
"""
from pathlib import Path
from typing import Any, Optional, Tuple

from app.utils.logging_utils import logger


def resolve_chat_home(project_id: str, project_dir: Path,
                      chat_id: str) -> Optional[Tuple[str, Any, dict]]:
    """Return ``(project_id, storage, raw)`` for the copy to use, or None.

    ``project_dir`` is the requested project's directory; its parent is
    scanned for sibling projects.  Passed in (rather than derived) so
    callers that resolve project directories through a patched or
    alternative ``get_project_dir`` stay consistent.
    """
    from app.storage.chats import ChatStorage

    storage = ChatStorage(project_dir)
    raw = storage._read_json(storage._chat_file(chat_id))
    if raw and (raw.get("messages") or []):
        return project_id, storage, raw

    best = None
    try:
        siblings = [d for d in project_dir.parent.iterdir()
                    if d.is_dir() and d.name != project_id]
    except OSError:
        siblings = []
    for d in siblings:
        # Probe the file before constructing storage for every project.
        if not (d / "chats" / f"{chat_id}.json").exists():
            continue
        other = ChatStorage(d)
        try:
            cand = other._read_json(other._chat_file(chat_id))
        except Exception:
            continue
        if not cand:
            continue
        n = len(cand.get("messages") or [])
        if raw and not n:
            # An existing empty local copy is only displaced by real content.
            continue
        if best is None or n > best[0]:
            best = (n, d.name, other, cand)

    if best is not None:
        n, pid, other, cand = best
        logger.warning(
            f"chat_home: {chat_id[:8]} "
            f"{'has no messages' if raw else 'is not stored'} under "
            f"{project_id[:8]}; using its {n}-message copy in {pid[:8]}")
        return pid, other, cand
    if raw:
        return project_id, storage, raw
    return None
