"""
Provenance register: which prior conversations the current conversation
has read, so a memory proposal made afterwards can be credited with the
independent conversation the fact came from.

The gap this closes (2026-10-20/21): the model could not find the user's
terminal id in memory, recovered it by ``chat_read`` of an earlier
conversation, answered from that, and re-proposed the fact.  No
``response_match`` landed (the answer was composed from the chat text,
not from the proposal), and the re-proposal did not count as
corroboration (different wording, and the earlier proposal had already
decayed).  Yet the fact demonstrably existed in a second conversation,
which is exactly what a corroboration asserts.

Mechanics:

* ``chat_read`` calls ``note_history_read`` with the calling conversation,
  the conversation it read, and the text it returned.
* ``memory_propose`` calls ``provenance_for`` with its content.  A read
  conversation supports the proposal when the proposal's identifiers
  appear in the read text, or when at least ``_MIN_OVERLAP`` of the
  proposal's tokens do (and at least ``_MIN_TOKENS`` tokens overlap).
  Reading an unrelated conversation earns nothing.
* The caller records one corroboration per supporting conversation; the
  ProposalsStore projection already ignores the proposing conversation
  itself and duplicates.

Entries expire after ``_TTL_SECONDS`` so a read early in a long session
does not vouch for proposals hours later.  In-process only: provenance
is a within-turn relationship and does not need to survive a restart.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, List, Set, Tuple

_TTL_SECONDS = 3600.0
_MIN_OVERLAP = 0.5
_MIN_TOKENS = 3
_MAX_CONVERSATIONS = 200
_MAX_READS_PER_CONVERSATION = 50

# conversation_id -> [(monotonic ts, read_chat_id, token set)]
_reads: Dict[str, List[Tuple[float, str, Set[str]]]] = defaultdict(list)
_lock = threading.Lock()


def _tokens(text: str) -> Set[str]:
    from app.storage.memory import _tokenize
    from app.utils.memory_identifiers import extract_identifiers
    toks = set(_tokenize(text))
    toks.update(extract_identifiers(text))
    return toks


def _prune_locked(now: float) -> None:
    stale = []
    for cid, rows in _reads.items():
        rows[:] = [r for r in rows if now - r[0] <= _TTL_SECONDS]
        if not rows:
            stale.append(cid)
    for cid in stale:
        _reads.pop(cid, None)
    while len(_reads) > _MAX_CONVERSATIONS:
        _reads.pop(next(iter(_reads)), None)


def note_history_read(conversation_id: str | None, read_chat_id: str | None,
                      text: str) -> None:
    """Record that ``conversation_id`` read ``read_chat_id`` and saw ``text``."""
    if not conversation_id or not read_chat_id or read_chat_id == conversation_id:
        return
    toks = _tokens(text or "")
    if not toks:
        return
    now = time.monotonic()
    with _lock:
        _prune_locked(now)
        rows = _reads[conversation_id]
        # Merge repeated reads of the same chat (paging) into one token set.
        for i, (_ts, rid, existing) in enumerate(rows):
            if rid == read_chat_id:
                rows[i] = (now, rid, existing | toks)
                return
        rows.append((now, read_chat_id, toks))
        if len(rows) > _MAX_READS_PER_CONVERSATION:
            del rows[0]


def provenance_for(conversation_id: str | None, content: str) -> List[str]:
    """Conversation ids read by ``conversation_id`` whose text supports
    ``content``.  Empty when nothing was read or nothing matches."""
    if not conversation_id or not content:
        return []
    from app.utils.memory_identifiers import extract_identifiers
    idents = set(extract_identifiers(content))
    toks = _tokens(content)
    now = time.monotonic()
    out: List[str] = []
    with _lock:
        for ts, rid, read_toks in _reads.get(conversation_id, []):
            if now - ts > _TTL_SECONDS:
                continue
            if idents and idents & read_toks:
                out.append(rid)
                continue
            if toks:
                overlap = len(toks & read_toks)
                if overlap >= _MIN_TOKENS and overlap / len(toks) >= _MIN_OVERLAP:
                    out.append(rid)
    return out


def clear(conversation_id: str | None = None) -> None:
    """Forget reads for one conversation, or all (tests)."""
    with _lock:
        if conversation_id is None:
            _reads.clear()
        else:
            _reads.pop(conversation_id, None)
