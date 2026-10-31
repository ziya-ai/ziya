"""Per-conversation record of extraction windows already processed.

server.py dispatches ``run_post_conversation_extraction`` after EVERY
stream with the conversation's full non-muted history.  Topic windows are
prefix-stable -- a boundary depends only on the messages before it -- so
on a long conversation each turn re-ran every earlier window through the
extractor and dedup then discarded the output as duplicate.  On a 200-turn
conversation that was triage plus ~25 model calls per turn for nothing.

This module remembers which windows a conversation has already extracted,
keyed by a hash of the window's STRIPPED text rather than by position:

  * an unchanged prefix window hashes the same next turn and is skipped;
  * the trailing window grows as the conversation continues, so its hash
    changes and it is extracted again (dedup and the distinct-conversation
    corroboration guard absorb the overlap);
  * muting a turn changes the affected window's text, so it re-extracts
    rather than being skipped on stale state.

One file per conversation under ``<ziya_home>/memory/extraction_cursors/``
so the 10+ Ziya processes that share a home never race on a shared file.
Everything here is best-effort: a lost or corrupt cursor only costs a
re-extraction, never data.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Iterable, Optional, Set

from app.utils.logging_utils import logger

# Bound per-conversation state.  500 windows is ~4,000 human turns; beyond
# that the oldest keys fall off and those windows would re-extract once.
MAX_KEYS = 500


def window_key(stripped_window: str) -> str:
    """Stable key for one extraction window (its stripped transcript)."""
    return hashlib.sha1(stripped_window.encode("utf-8")).hexdigest()[:16]


def _path(conversation_id: str) -> Path:
    from app.utils.paths import get_ziya_home
    safe = "".join(c for c in conversation_id if c.isalnum() or c in "-_")[:80]
    return get_ziya_home() / "memory" / "extraction_cursors" / f"{safe or 'unknown'}.json"


def _read(conversation_id: str) -> list:
    p = _path(conversation_id)
    try:
        if p.exists():
            data = json.loads(p.read_text())
            keys = data.get("windows", [])
            return [k for k in keys if isinstance(k, str)]
    except Exception as e:  # noqa: BLE001 -- best-effort cache
        logger.debug(f"extraction cursor read failed for {conversation_id}: {e}")
    return []


def load_extracted_windows(conversation_id: Optional[str]) -> Set[str]:
    """Keys of windows this conversation has already extracted."""
    if not conversation_id:
        return set()
    return set(_read(conversation_id))


def record_extracted_windows(conversation_id: Optional[str],
                             keys: Iterable[str]) -> None:
    """Merge ``keys`` into the conversation's cursor, newest last, capped."""
    if not conversation_id:
        return
    new = [k for k in keys if k]
    if not new:
        return
    ordered = _read(conversation_id)
    present = set(ordered)
    for k in new:
        if k not in present:
            ordered.append(k)
            present.add(k)
    ordered = ordered[-MAX_KEYS:]
    p = _path(conversation_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"windows": ordered,
                                   "updated_at": int(time.time() * 1000)}))
        tmp.replace(p)
    except Exception as e:  # noqa: BLE001 -- best-effort cache
        logger.warning(f"extraction cursor write failed for {conversation_id}: {e}")
