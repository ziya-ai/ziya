"""
In-memory candidate filter for chat search.

Search (app/storage/chat_search.py) used to open, decrypt and parse every
chat file in scope on every query: ~9 s for an all-projects search over
2,350 chats / 1.5 GB, and several overlapping searches (one per debounced
keystroke) pushed each past the browser's 25 s deadline, after which the
client silently fell back to a partial IndexedDB scan.

This module keeps, per chat file, a bitmap of the character trigrams its
searchable text contains.  A query is reduced to the trigrams it must
contain; only chats whose bitmap has all of them are opened and run through
the unchanged matcher.  So the filter decides *which files to read*, never
*what matches*: results are identical to a full scan, provided the filter
never drops a chat that would match.  That guarantee rests on:

  * Trigrams are taken over the alphabet ``[a-z0-9_]`` after case folding.
    A query trigram comes from a run of those characters in the query; the
    whitespace-tolerant pattern (compile_query) matches each query token as
    contiguous text, so the same run, and so the same trigram, is in the
    document.  Characters outside the alphabet only ever drop trigrams from
    the query, which widens the candidate set.
  * Folding matches ``re.IGNORECASE`` for every non-ASCII character it
    equates with an ASCII one (KELVIN SIGN, LONG S, dotted/dotless I).
  * Case-sensitive searches use the same folded filter: a superset.
  * A query with no usable trigram (only punctuation, or tokens shorter
    than three characters) returns None and the caller does a full scan.

What is held: per chat, the file's (mtime_ns, size), project id, active
flag and a 6,332-byte bitmap.  No message text is kept.  Each search
re-stats the files in scope and re-indexes the ones that changed, so writes
by other processes sharing the Ziya home are picked up without hooks.  The
index is built on first use, under a lock, so concurrent searches wait for
one build instead of each scanning everything.

Kill switch: ``ZIYA_DISABLE_CHAT_SEARCH_INDEX=1``.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from app.utils.logging_utils import logger

_ALPHA = "abcdefghijklmnopqrstuvwxyz0123456789_"
_A = len(_ALPHA)
N_TRIGRAMS = _A ** 3
BITMAP_BYTES = (N_TRIGRAMS + 7) // 8
_CH = {c: i for i, c in enumerate(_ALPHA)}

# Non-ASCII characters re.IGNORECASE treats as equal to an ASCII letter.
_FOLD = str.maketrans({"\u212a": "k", "\u017f": "s", "\u0130": "i", "\u0131": "i"})
_TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")


def _fold(text: str) -> str:
    return text.translate(_FOLD).lower()


def _trigram_codes(tokens) -> Set[int]:
    tris = {t[i:i + 3] for t in tokens for i in range(len(t) - 2)}
    ch = _CH
    return {(ch[t[0]] * _A + ch[t[1]]) * _A + ch[t[2]] for t in tris}


def query_codes(query: str) -> List[int]:
    """Trigram codes every matching chat must contain (empty: no filter)."""
    return sorted(_trigram_codes(_TOKEN_RE.findall(_fold(query or ""))))


def text_bitmap(text: str) -> bytes:
    bm = bytearray(BITMAP_BYTES)
    for c in _trigram_codes(set(_TOKEN_RE.findall(_fold(text)))):
        bm[c >> 3] |= 1 << (c & 7)
    return bytes(bm)


def chat_text(data: dict) -> str:
    """The text _search_one_chat matches against: title plus message bodies."""
    from app.storage.chat_search import _to_searchable_text
    parts = [data.get("title") if isinstance(data.get("title"), str) else ""]
    messages = data.get("messages")
    if isinstance(messages, list):
        for m in messages:
            if isinstance(m, dict):
                parts.append(_to_searchable_text(m.get("content")))
    return "\n".join(parts)


@dataclass
class _Entry:
    project_id: str
    stamp: Tuple[int, int]
    active: bool
    bitmap: Optional[bytes]     # None: the file could not be read
    failed_at: float = 0.0


# An unreadable file (no decryption key, a write in progress) is retried
# after this long, or at once if it changes.  Not retrying at all would hide
# it for the server's lifetime if the key becomes reachable later; retrying
# on every search re-read 2,283 encrypted files per query (~0.6 s) on a
# server without the key.
UNREADABLE_RETRY_S = 60.0


def _is_chat_file(name: str) -> bool:
    return (name.endswith(".json") and not name.startswith("_")
            and not name.endswith(".bindings.json"))


class ChatSearchIndex:
    def __init__(self) -> None:
        self._entries: Dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._entries)

    def memory_bytes(self) -> int:
        return sum(BITMAP_BYTES for e in self._entries.values() if e.bitmap)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _refresh(self, projects_dir: Path, project_id: str,
                 all_projects: bool) -> List[str]:
        """Bring the entries in scope up to date; return their keys."""
        from app.storage.chat_search import _read_chat_data
        if all_projects:
            dirs = [d for d in projects_dir.iterdir() if d.is_dir()]
        else:
            d = projects_dir / project_id
            dirs = [d] if d.is_dir() else []
        in_scope: List[str] = []
        prefixes = []
        n_read = 0
        now = time.monotonic()
        for proj_dir in dirs:
            chats = proj_dir / "chats"
            prefixes.append(str(chats) + os.sep)
            if not chats.is_dir():
                continue
            with os.scandir(chats) as it:
                for de in it:
                    if not _is_chat_file(de.name):
                        continue
                    try:
                        st = de.stat()
                    except OSError:
                        continue
                    key = de.path
                    stamp = (st.st_mtime_ns, st.st_size)
                    cur = self._entries.get(key)
                    stale = cur is None or cur.stamp != stamp or (
                        cur.bitmap is None
                        and now - cur.failed_at > UNREADABLE_RETRY_S)
                    if stale:
                        n_read += 1
                        data = _read_chat_data(Path(key))
                        if data:
                            cur = _Entry(proj_dir.name, stamp,
                                         data.get("isActive") is not False,
                                         text_bitmap(chat_text(data)))
                        else:
                            # Excluded, as the full scan skips it too.
                            cur = _Entry(proj_dir.name, stamp, False, None, now)
                        self._entries[key] = cur
                    in_scope.append(key)
        # Drop entries for files that no longer exist in the scanned dirs.
        live = set(in_scope)
        for key in [k for k in self._entries
                    if k not in live and any(k.startswith(p) for p in prefixes)]:
            del self._entries[key]
        if n_read:
            logger.debug(f"chat_search_index: (re)indexed {n_read} file(s); "
                         f"{len(self._entries)} entries, "
                         f"{self.memory_bytes() / 1e6:.1f} MB")
        return in_scope

    def candidates(self, ziya_home: Path, project_id: str, all_projects: bool,
                   query: str) -> Optional[List[Tuple[str, Path]]]:
        """(project_id, path) of chats that may match, or None for no filter."""
        codes = query_codes(query)
        if not codes:
            return None
        projects_dir = ziya_home / "projects"
        if not projects_dir.exists():
            return []
        t0 = time.perf_counter()
        probes = [(c >> 3, 1 << (c & 7)) for c in codes]
        out: List[Tuple[str, Path]] = []
        with self._lock:
            keys = self._refresh(projects_dir, project_id, all_projects)
            for key in keys:
                e = self._entries[key]
                if not e.active or e.bitmap is None:
                    continue
                bm = e.bitmap
                if all(bm[i] & b for i, b in probes):
                    out.append((e.project_id, Path(key)))
        logger.debug(f"chat_search_index: {len(out)}/{len(keys)} candidates for "
                     f"{query!r} in {(time.perf_counter() - t0) * 1000:.0f}ms")
        return out


_INDEX = ChatSearchIndex()


def is_enabled() -> bool:
    return os.environ.get("ZIYA_DISABLE_CHAT_SEARCH_INDEX", "").strip().lower() \
        not in ("1", "true", "yes")


def candidate_files(ziya_home: Path, project_id: str, all_projects: bool,
                    query: str) -> Optional[List[Tuple[str, Path]]]:
    """Module entry point; None means "no filter, scan everything"."""
    if not is_enabled():
        return None
    try:
        return _INDEX.candidates(ziya_home, project_id, all_projects, query)
    except Exception as e:
        logger.warning(f"chat_search_index unavailable, full scan: {e}")
        return None


def get_index() -> ChatSearchIndex:
    return _INDEX
