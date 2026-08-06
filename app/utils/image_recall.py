"""
Recall store for elided image tool results.

Compaction exists to reclaim CONTEXT space, not storage.  Those are not
the same resource: an image costs its base64 length in every subsequent
request once it is in the conversation, but costs that once in RAM.  So
dropping an image out of context does not require forgetting it.

This store keeps the bytes after the conversation copy is replaced by a
text summary, and hands back a short handle the model can use to pull the
image into view again via the ``recall_image`` tool.  Elision becomes
paging rather than amnesia: the model can consult prior visual state
deliberately instead of either carrying every render forever or
re-rendering blind.

Bounds
------
Limits are PER SCOPE (one conversation or task run), not merely global.
The resource being protected is fairness as much as total memory: a long
fuzz run producing hundreds of renders must not evict the one diagram a
different conversation is still consulting.  Each scope therefore gets its
own byte/entry budget and its own LRU order.

A global ceiling still applies on top, because per-scope budgets are
unbounded in aggregate — two hundred conversations at 16 MB each is not a
budget.  When the global ceiling binds, eviction targets the HEAVIEST
scope, so pressure created by one busy conversation is paid for by that
conversation rather than by a quiet one holding a single render.

Scoping is also an isolation boundary: ``retrieve`` requires a matching
scope, so one conversation cannot pull another's renders into its context.
"""

import logging
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Per-scope ceilings.  Generous relative to the context budget they
# substitute for — a few MB of process memory is cheap next to re-sending
# the same base64 on every one of thirty iterations.
MAX_BYTES_PER_SCOPE = 16 * 1024 * 1024
MAX_ENTRIES_PER_SCOPE = 64

# Global backstop across all scopes.
MAX_TOTAL_BYTES = 96 * 1024 * 1024

# Entries older than this are dropped on the next touch.  A stale handle
# is not an error — ``retrieve`` returning None is a normal outcome the
# tool reports honestly rather than treating as a fault.
MAX_AGE_SECONDS = 6 * 60 * 60

# Bucket for entries stashed without a scope (CLI one-shots).  A sentinel
# rather than None so they share one budget instead of each becoming its
# own unbounded scope.
_UNSCOPED = "\x00unscoped"

_lock = threading.Lock()
# handle -> {content, scope, bytes, ts, label}, in global insertion order.
# A single OrderedDict rather than a scope->dict tree: filtering by scope
# preserves per-scope LRU order for free, and at these entry counts the
# O(n) scans are irrelevant next to keeping two structures consistent.
_store: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def _payload_bytes(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    total = 0
    for b in content:
        if isinstance(b, dict) and b.get("type") == "image":
            data = (b.get("source") or {}).get("data")
            if isinstance(data, str):
                total += len(data)
    return total


def _scope_of(entry: Dict[str, Any]) -> str:
    return entry.get("scope") or _UNSCOPED


def _drop_expired_locked() -> None:
    now = time.time()
    for handle in [h for h, e in _store.items()
                   if now - e.get("ts", now) > MAX_AGE_SECONDS]:
        _store.pop(handle, None)


def _evict_scope_locked(scope_key: str) -> None:
    """Enforce one scope's budget, dropping its oldest entries first."""
    while True:
        entries = [(h, e) for h, e in _store.items()
                   if _scope_of(e) == scope_key]
        if not entries:
            return
        total = sum(e.get("bytes", 0) for _, e in entries)
        if (len(entries) <= MAX_ENTRIES_PER_SCOPE
                and total <= MAX_BYTES_PER_SCOPE):
            return
        _store.pop(entries[0][0], None)


def _evict_global_locked() -> None:
    """Enforce the global ceiling, charging the heaviest scope."""
    while _store:
        if sum(e.get("bytes", 0) for e in _store.values()) <= MAX_TOTAL_BYTES:
            return
        by_scope: Dict[str, int] = {}
        for entry in _store.values():
            key = _scope_of(entry)
            by_scope[key] = by_scope.get(key, 0) + entry.get("bytes", 0)
        heaviest = max(by_scope, key=lambda k: by_scope[k])
        victim = next((h for h, e in _store.items()
                       if _scope_of(e) == heaviest), None)
        if victim is None:
            return
        _store.pop(victim, None)


def stash(
    content: List[Any], scope: Optional[str] = None,
    label: Optional[str] = None,
) -> Optional[str]:
    """Retain an image content-block list and return its recall handle.

    Returns None when there is nothing worth keeping (no image payload) or
    when the entry could never fit its own scope budget — in that case the
    image is simply gone, and the caller's placeholder must not advertise a
    handle that ``retrieve`` would refuse.
    """
    size = _payload_bytes(content)
    if size <= 0 or size > MAX_BYTES_PER_SCOPE:
        return None
    handle = f"img-{secrets.token_hex(4)}"
    scope_key = scope or _UNSCOPED
    try:
        with _lock:
            _store[handle] = {
                "content": content,
                "scope": scope,
                "bytes": size,
                "ts": time.time(),
                "label": label or "",
            }
            _drop_expired_locked()
            _evict_scope_locked(scope_key)
            _evict_global_locked()
            if handle not in _store:
                # Evicted by its own admission (ceiling already saturated) —
                # don't hand back a dead handle.
                return None
    except Exception as exc:  # noqa: BLE001 — recall must never break a sweep
        logger.debug(f"🖼️ IMAGE_RECALL: stash failed: {exc}")
        return None
    logger.debug(
        f"🖼️ IMAGE_RECALL: stashed {handle} ({size} b64 chars, scope={scope!r})"
    )
    return handle


def retrieve(handle: str, scope: Optional[str] = None) -> Optional[List[Any]]:
    """Return a stashed content-block list, or None if unavailable.

    A scope mismatch is reported as absence rather than as an error: the
    caller has no legitimate need to distinguish "never existed" from
    "belongs to another conversation", and conflating them avoids leaking
    the existence of other conversations' renders.

    A caller with NO scope (a CLI one-shot) is not treated as hostile —
    only a genuine mismatch between two known scopes is refused.
    """
    if not handle:
        return None
    try:
        with _lock:
            entry = _store.get(handle.strip())
            if entry is None:
                return None
            if entry.get("scope") and scope and entry["scope"] != scope:
                logger.debug(
                    f"🖼️ IMAGE_RECALL: scope mismatch for {handle} — refusing"
                )
                return None
            if time.time() - entry.get("ts", 0) > MAX_AGE_SECONDS:
                _store.pop(handle.strip(), None)
                return None
            # Refresh recency: an image being consulted is an image worth
            # keeping, so LRU should protect it.  Without this, recall
            # degrades precisely when it is being used.
            entry["ts"] = time.time()
            _store.move_to_end(handle.strip())
            return entry.get("content")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"🖼️ IMAGE_RECALL: retrieve failed: {exc}")
        return None


def describe(handle: str) -> Optional[str]:
    """Human/model-readable label for a handle, if known."""
    with _lock:
        entry = _store.get((handle or "").strip())
        return entry.get("label") if entry else None


def stats(scope: Optional[str] = None) -> Dict[str, int]:
    """Occupancy, globally or for one scope.  For logging and tests."""
    with _lock:
        if scope is None:
            values = list(_store.values())
        else:
            values = [e for e in _store.values() if _scope_of(e) == scope]
        return {
            "entries": len(values),
            "bytes": sum(e.get("bytes", 0) for e in values),
        }


def clear() -> None:
    """Drop everything.  Test hook; also usable on session teardown."""
    with _lock:
        _store.clear()
