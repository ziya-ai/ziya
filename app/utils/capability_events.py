"""
✦ pickup events (design/capabilities-hub.md, slice 5).

When the model loads an on-demand skill through ``get_skill_details``, the
stream carries ``{"type": "capability_used", "key": "skill:<name>"}`` so the
client can mark that skill as picked up for the rest of the turn.

Derived from the ``tool_display`` chunk rather than emitted by the tool:
every executor path (native tool loop, batch runner, direct wrappers)
already produces a ``tool_display`` with ``tool_name`` and ``result``, and
``stream_chunks`` is the one place they all pass through.  An event emitted
inside the tool would need a side channel through each of those paths.

The key is built from the ``[Skill: <name>]`` header ``GetSkillDetailsTool``
puts on every successful result (built-in and file-discovered alike).  That
name is ``Skill.name``, which is also what the bench keys on, so the event
names the same item ``GET /bench`` returns.  Error results carry no header
and produce no event.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

SKILL_TOOL = "get_skill_details"
MCP_LOADER_TOOL = "load_server_tools"
_SKILL_HEADER = re.compile(r"\[Skill: ([^\]\n]+)\]")


def capability_used_event(chunk: Any) -> Optional[Dict[str, Any]]:
    """The ``capability_used`` event a ``tool_display`` chunk implies, or None."""
    if not isinstance(chunk, dict) or chunk.get("type") != "tool_display":
        return None
    if chunk.get("is_error"):
        return None
    name = str(chunk.get("tool_name") or "")
    # Builtins may arrive prefixed (``mcp_get_skill_details``).
    is_skill = name == SKILL_TOOL or name.endswith("_" + SKILL_TOOL)
    is_mcp = name == MCP_LOADER_TOOL or name.endswith("_" + MCP_LOADER_TOOL)
    if not (is_skill or is_mcp):
        return None
    text = chunk.get("result")
    if not isinstance(text, str):
        text = chunk.get("content")
    if not isinstance(text, str):
        return None
    if is_mcp:
        # ``load_server_tools`` (design/mcp-on-demand.md): the success marker
        # names the server; a refusal carries none.
        from app.utils.mcp_ondemand import MARKER_RE
        mm = MARKER_RE.search(text)
        return {"type": "capability_used", "key": f"mcp:{mm.group(1).strip()}"} if mm else None
    m = _SKILL_HEADER.search(text)
    if not m:
        return None
    return {"type": "capability_used", "key": f"skill:{m.group(1).strip()}"}
