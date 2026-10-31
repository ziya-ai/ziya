"""
Last use of each bench capability, for ordering the bench's "within reach"
and "shelved" lanes by recency.

Keys are bench item keys (``skill:<Skill.name>``, ``mcp:<server>``; see
``app.models.activation.make_key``).  A use is:

  * a skill: a successful ``get_skill_details`` load (the tool names the same
    ``Skill.name`` the bench keys on);
  * an MCP server: a call to one of its tools that passed the quarantine and
    permission checks in ``MCPManager.call_tool``.

Stored per user (``~/.ziya/state/capability_usage.json``) because MCP servers
are configured per user and a skill used in one project is still the one you
last reached for.  Writes go through ``JsonStateStore.update`` (an ``fcntl``
lock), so concurrent Ziya processes do not lose each other's stamps, and a
stamp only ever moves forward.  Each process writes a key at most once per
``MIN_INTERVAL_MS``: a shell-heavy turn would otherwise rewrite the file on
every command for no change in ordering.

Recording never raises: a failed write must not fail the tool call.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

STATE_NAME = "capability_usage"
MIN_INTERVAL_MS = 60_000

_recent: Dict[str, int] = {}
_recent_lock = threading.Lock()


def _store():
    from app.storage.json_state import JsonStateStore
    from app.utils.paths import user_state_file
    return JsonStateStore(user_state_file(STATE_NAME))


def record_use(key: str, at_ms: Optional[int] = None) -> None:
    """Stamp ``key`` as used now (or at ``at_ms``)."""
    if not isinstance(key, str) or ":" not in key:
        return
    now = int(time.time() * 1000) if at_ms is None else int(at_ms)
    with _recent_lock:
        prev = _recent.get(key)
        if prev is not None and 0 <= now - prev < MIN_INTERVAL_MS:
            return
        _recent[key] = now

    def _apply(doc):
        used = doc.get("last_used")
        if not isinstance(used, dict):
            used = {}
        old = used.get(key)
        if not isinstance(old, (int, float)) or old < now:
            used[key] = now
        doc["last_used"] = used

    try:
        _store().update(_apply)
    except Exception as e:  # noqa: BLE001 - usage is advisory
        logger.debug(f"capability usage not recorded for {key}: {e}")


def record_skill_use(name: str) -> None:
    if name:
        record_use(f"skill:{name}")


def record_mcp_use(server: str) -> None:
    if server:
        record_use(f"mcp:{server}")


def last_used_map() -> Dict[str, int]:
    """``{key: epoch_ms}`` for every capability ever used; ``{}`` if unreadable."""
    try:
        used = _store().read().get("last_used")
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(used, dict):
        return {}
    return {k: int(v) for k, v in used.items()
            if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)}
