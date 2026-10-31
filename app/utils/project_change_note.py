"""Transcript note recording a change in a conversation's project scope.

When a conversation is moved to another project, or its global flag is
toggled, the directory the model works in changes without anything in the
transcript saying so: every relative path, file-context line and shell
command before that point was resolved against the old project root, and
everything after it against the new one.  The server appends one ``system``
message to the record at the moment of the change.

The message has two audiences:

* ``content`` is what the model sees — a ``[Ziya: ...]`` note naming both
  projects and both working directories.  It is NOT muted, so every send
  path (which filters muted messages) includes it.  The frontend forwards
  system-role history as-is; ``precision_prompt_system`` replays it as an
  assistant-side annotation, the same convention the tributary fold and
  show_document notes use.
* ``projectChange`` is what the user sees — ``Conversation.tsx`` renders a
  compact "Moved from A to B" row from it and never shows ``content``, so
  the working-directory change is not displayed, only recorded.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Literal, Optional

Kind = Literal["moved", "global_on", "global_off"]


def _proj(project: Any) -> Dict[str, Optional[str]]:
    """Reduce a Project (or a dict) to the three fields the note carries."""
    if project is None:
        return {"id": None, "name": None, "path": None}
    get = (lambda k: project.get(k)) if isinstance(project, dict) else (lambda k: getattr(project, k, None))
    return {"id": get("id"), "name": get("name"), "path": get("path")}


def _label(p: Dict[str, Optional[str]]) -> str:
    return f"\u201c{p['name']}\u201d" if p.get("name") else (p.get("id") or "unknown project")


def build_project_change_message(
    kind: Kind,
    from_project: Any,
    to_project: Any = None,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Return the message dict to append to ``chat["messages"]``.

    ``from_project`` is the owning project before the change; ``to_project``
    the owner after a move (ignored for the global toggles, whose owner does
    not change).  Accepts ``Project`` models or plain dicts with
    ``id``/``name``/``path``.
    """
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    # Same local-with-offset form as the <CurrentDateTime>/<MessageTime>
    # tags, so the model is not asked to reconcile a UTC note against
    # naive local tags around it.
    from app.utils.session_context_prompt import format_prompt_timestamp
    iso = format_prompt_timestamp(now_ms)
    src = _proj(from_project)
    dst = _proj(to_project) if kind == "moved" else src

    if kind == "moved":
        content = (
            f"[Ziya: this conversation was moved from project {_label(src)} to project "
            f"{_label(dst)} at {iso}. Working directory rerooted: previously "
            f"{src.get('path') or 'unknown'}, now {dst.get('path') or 'unknown'}. "
            f"File context, relative paths and shell commands in the turns above were "
            f"resolved against the previous directory; from here on they resolve against "
            f"the new one. Files discussed above may not exist in the new project.]"
        )
    elif kind == "global_on":
        content = (
            f"[Ziya: this conversation was made global at {iso}. It remains owned by "
            f"project {_label(src)} (working directory {src.get('path') or 'unknown'}) but "
            f"can be opened from any project; a turn sent from another project runs with "
            f"that project's root as the working directory, not {src.get('path') or 'the owner root'}.]"
        )
    else:  # global_off
        content = (
            f"[Ziya: this conversation is no longer global as of {iso}. It is scoped to "
            f"project {_label(src)} and its working directory {src.get('path') or 'unknown'}.]"
        )

    return {
        "id": str(uuid.uuid4()),
        "role": "system",
        "content": content,
        "timestamp": now_ms,
        "projectChange": {"kind": kind, "from": src, "to": dst},
    }
