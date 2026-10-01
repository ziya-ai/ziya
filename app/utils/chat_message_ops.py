"""
Message-level operations on a chat record, and the id-merge bulk-sync uses.

Why this exists (design/conversation-single-writer.md, "Turn authorship" and
the 2026-09-25 shrink-path audit):

  * The server had no way to delete, truncate or edit a message.  Every
    shrink the browser made (edit-and-resubmit, resubmit, delete) was sent as
    a whole shorter ``messages`` array through bulk-sync, whose count guard
    refused it while still accepting the newer ``_version``.  The disk record
    silently kept the pre-edit history and looked newer than the browser.
  * bulk-sync replaced ``messages`` wholesale when the count did not drop, so
    a client that never received an answer could overwrite it by adding a
    message of its own (same count, different content).

The fix has two halves that only work together:

  1. Shrinks are INTENTS with their own endpoints (``rewind``,
     ``delete_message``, ``patch_message`` here).  Removed messages are
     tombstoned so a stale copy from another tab cannot resurrect them.
  2. bulk-sync MERGES by message id (``merge_messages``): it can add messages
     and update client-owned metadata (mute, feedback flags) but can never
     remove a message or change its content.  With (1) in place there is no
     longer any legitimate reason for bulk-sync to shrink anything.

Everything here operates on raw chat dicts (the JSON on disk), not on the
pydantic ``Chat`` model, so server-owned underscore fields (``_beads``,
``_version``, ``_deletedMessages``) round-trip untouched.

Legacy messages: frontend messages carry an optional id.  Messages created
before stable ids existed may have none locally, and ``conversationToServerChat``
mints a fresh random id for them on every push.  Matching therefore falls back
to (role, content) against not-yet-matched server messages, which adopts the
server's id; the result then differs from what the client sent, the version is
bumped, and the client's next pull picks up the server ids.  The legacy case
heals itself after one round trip.
"""
from __future__ import annotations

import fcntl
import hashlib
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Fields of a message that only an explicit endpoint (or the server turn
# writer) may change.  Everything else on a message (muted, _edited,
# _isFeedback, _feedbackStatus, ...) is client-owned metadata and follows the
# client copy on bulk-sync.  ``parts`` and ``turnStatus`` are the server's
# saved facts of an assistant turn (design/conversation-single-writer.md,
# "save facts, render at display time"); a browser copy never replaces them.
CORE_FIELDS = ("id", "role", "content", "images", "timestamp", "parts", "turnStatus")

# Server-owned record field holding tombstones for removed messages.
TOMBSTONE_FIELD = "_deletedMessages"
_TOMBSTONE_CAP = 1000

# A message the server itself authored (turn assembler, delegates, future
# subthread spawns).  Its content and metadata are never taken from a client
# copy; only ``muted`` is, because muting is a user action on any message.
SERVER_AUTHORED_FIELD = "_serverAuthored"

# Legacy locator: how much leading content identifies an id-less message.
LOCATOR_HEAD_CHARS = 200


class MessageOpError(ValueError):
    """A message operation could not be applied.  ``status`` is the HTTP
    status the endpoint should return."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── locking ───────────────────────────────────────────────────────────────

@contextmanager
def chat_record_lock(storage: Any) -> Iterator[None]:
    """Exclusive lock spanning a read-modify-write of chat records.

    ``_write_json`` replaces the file by rename, so the data file's own inode
    cannot carry the lock; a sidecar file in the chats directory does.  One
    lock per project directory rather than per chat: the critical sections are
    milliseconds of JSON I/O, and a single file avoids leaving a lock file per
    conversation behind.  flock is per open file description, so this also
    serializes threads within one process (each call opens its own fd).  Two
    servers sharing ``~/.ziya`` is a supported mode, which is why this is a
    file lock and not a threading lock.  Never nest it on one thread.
    """
    lock_path = storage.chats_dir / ".messages.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# ── identity ──────────────────────────────────────────────────────────────

def message_fingerprint(msg: Dict[str, Any]) -> str:
    """Stable content identity of a message, used for legacy matching and
    tombstones.  Stores a hash, never the content, on the record."""
    role = str(msg.get("role") or "")
    content = msg.get("content")
    if not isinstance(content, str):
        content = "" if content is None else str(content)
    return hashlib.sha1(f"{role}\0{content}".encode("utf-8", "replace")).hexdigest()


def is_push_minted_id(mid: Any) -> bool:
    """True for an id that carries no identity across pushes: absent, or the
    ``msg-<ts>-<rand>`` placeholder ``conversationToServerChat`` mints for an
    id-less local message (a different one on every push).  Only such
    messages may be matched or tombstoned by content; a message with a real
    id is matched and tombstoned by id alone, so a genuinely new message that
    repeats earlier text (a second "continue") is never swallowed."""
    return not mid or (isinstance(mid, str) and mid.startswith("msg-"))


def _tombstones(record: Dict[str, Any]) -> Tuple[set, set]:
    t = record.get(TOMBSTONE_FIELD) if isinstance(record, dict) else None
    if not isinstance(t, dict):
        return set(), set()
    return set(t.get("ids") or []), set(t.get("fps") or [])


def _add_tombstones(record: Dict[str, Any], removed: List[Dict[str, Any]]) -> None:
    if not removed:
        return
    t = record.get(TOMBSTONE_FIELD)
    if not isinstance(t, dict):
        t = {"ids": [], "fps": []}
    ids = list(t.get("ids") or [])
    fps = list(t.get("fps") or [])
    for m in removed:
        if m.get("id"):
            ids.append(m["id"])
        fps.append(message_fingerprint(m))
    record[TOMBSTONE_FIELD] = {"ids": ids[-_TOMBSTONE_CAP:], "fps": fps[-_TOMBSTONE_CAP:]}


def locate_message(messages: List[Dict[str, Any]], locator: Dict[str, Any]) -> int:
    """Resolve a locator to an index in ``messages``.

    By id first.  Otherwise (a legacy message the browser holds without the
    server's id) by index, verified against role and the leading content and
    length the browser saw; if the index has shifted, by a unique match on
    those anywhere in the list.  Refuses (409) rather than guessing, because a
    wrong guess deletes or truncates the wrong part of someone's history.
    """
    mid = locator.get("messageId")
    if mid:
        for i, m in enumerate(messages):
            if m.get("id") == mid:
                return i
    head = locator.get("contentHead")
    length = locator.get("contentLength")
    role = locator.get("role")
    if head is None and length is None:
        raise MessageOpError(f"message {mid!r} not found", status=404)

    def _fits(m: Dict[str, Any]) -> bool:
        c = m.get("content") if isinstance(m.get("content"), str) else ""
        if role and m.get("role") != role:
            return False
        if length is not None and len(c) != length:
            return False
        return head is None or c[:LOCATOR_HEAD_CHARS] == head[:LOCATOR_HEAD_CHARS]

    idx = locator.get("index")
    if isinstance(idx, int) and 0 <= idx < len(messages) and _fits(messages[idx]):
        return idx
    hits = [i for i, m in enumerate(messages) if _fits(m)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise MessageOpError("message not found (no id, and no content match)", status=404)
    raise MessageOpError(
        f"message locator is ambiguous ({len(hits)} matches); refusing to guess",
        status=409,
    )


# ── the bulk-sync merge ───────────────────────────────────────────────────

def merge_messages(
    existing: List[Dict[str, Any]],
    incoming: List[Dict[str, Any]],
    record: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Merge a client's message list into the server's, by id.

    Returns ``(merged, differs_from_incoming)``.  The second value tells the
    caller the client's copy is not the truth any more, so the record's
    version must move past the client's for its next pull to converge.

    Rules:
      * No server message is ever removed (shrinks go through the explicit
        endpoints) and no server message's CORE fields are replaced.
      * A matched message takes the client's metadata (everything outside
        CORE_FIELDS) unless the server authored it, in which case only
        ``muted`` follows the client.
      * An unmatched client message is new.  It is placed after the last
        server message the client also had before it, and after any server
        messages the client never saw that follow that point: a client that
        missed answer ``a2`` and then asked ``h3`` produces ``[h2 a2 h3]``.
      * An unmatched client message whose id was tombstoned (deleted or
        rewound away) is dropped, so a stale tab cannot bring it back.  For a
        push-minted id the fingerprint is checked instead.
    """
    tomb_ids, tomb_fps = _tombstones(record or {})
    result = [dict(m) for m in existing]
    ids_to_idx: Dict[str, List[int]] = {}
    for i, m in enumerate(existing):
        if m.get("id"):
            ids_to_idx.setdefault(m["id"], []).append(i)
    consumed: set = set()
    # (anchor existing index or -1, message) in incoming order.
    additions: List[Tuple[int, Dict[str, Any]]] = []
    anchor = -1

    for inc in incoming:
        if not isinstance(inc, dict):
            continue
        idx = None
        for cand in ids_to_idx.get(inc.get("id") or "", []):
            if cand not in consumed:
                idx = cand
                break
        if idx is None and is_push_minted_id(inc.get("id")):
            fp = message_fingerprint(inc)
            # Legacy fallback: same role+content, not yet matched; prefer the
            # first one after the current anchor so repeated short messages
            # ("continue") pair up in order.
            cands = [j for j in range(len(existing))
                     if j not in consumed and message_fingerprint(existing[j]) == fp]
            after = [j for j in cands if j > anchor]
            idx = (after or cands or [None])[0]
        if idx is not None:
            consumed.add(idx)
            base = result[idx]
            if base.get(SERVER_AUTHORED_FIELD):
                if "muted" in inc:
                    base["muted"] = inc["muted"]
            else:
                updated = {k: v for k, v in inc.items() if k not in CORE_FIELDS}
                for k in CORE_FIELDS:
                    if k in base:
                        updated[k] = base[k]
                result[idx] = updated
            anchor = max(anchor, idx)
            continue
        if inc.get("id") in tomb_ids:
            continue
        if is_push_minted_id(inc.get("id")) and message_fingerprint(inc) in tomb_fps:
            continue
        additions.append((anchor, inc))

    # Place each addition before the next CONSUMED server message after its
    # anchor, i.e. after any run of server messages the client never had.
    consumed_sorted = sorted(consumed)
    by_position: Dict[int, List[Dict[str, Any]]] = {}
    for a, msg in additions:
        pos = next((c for c in consumed_sorted if c > a), len(existing))
        by_position.setdefault(pos, []).append(dict(msg))
    merged: List[Dict[str, Any]] = []
    for i, m in enumerate(result):
        merged.extend(by_position.get(i, []))
        merged.append(m)
    merged.extend(by_position.get(len(existing), []))

    def _shape(ms):
        return [(m.get("id"), m.get("content")) for m in ms if isinstance(m, dict)]
    return merged, _shape(merged) != _shape(incoming)


# ── explicit operations (endpoints) ───────────────────────────────────────

def _bump(record: Dict[str, Any]) -> int:
    now = int(time.time() * 1000)
    prev = record.get("_version") or 0
    version = now if now > prev else prev + 1
    record["_version"] = version
    record["lastActiveAt"] = now
    return version


_UNSET = object()


def rewind(record: Dict[str, Any], locator: Dict[str, Any],
           text: Optional[str] = None, images: Any = _UNSET) -> Dict[str, Any]:
    """Drop every message after the located HUMAN message, optionally
    replacing its text/images.  This is the resubmit / edit-and-resubmit
    intent; the turn endpoint (Turn authorship, step 3) will run it as its
    first step, atomically with appending and starting the turn."""
    msgs = list(record.get("messages") or [])
    idx = locate_message(msgs, locator)
    target = dict(msgs[idx])
    if target.get("role") != "human":
        raise MessageOpError("rewind target must be a human message", status=400)
    removed = msgs[idx + 1:]
    if text is not None and text != target.get("content"):
        # The pre-edit text is gone from the record; tombstone it so a stale
        # copy of the old question cannot come back as a "new" message.
        _add_tombstones(record, [target])
        target["content"] = text
        target["_edited"] = True
    if images is not _UNSET:
        target["images"] = images
    msgs = msgs[:idx] + [target]
    _add_tombstones(record, removed)
    record["messages"] = msgs
    version = _bump(record)
    return {"messageId": target.get("id"), "removed": len(removed),
            "messageCount": len(msgs), "_version": version}


def delete_message(record: Dict[str, Any], locator: Dict[str, Any]) -> Dict[str, Any]:
    msgs = list(record.get("messages") or [])
    idx = locate_message(msgs, locator)
    removed = msgs.pop(idx)
    _add_tombstones(record, [removed])
    record["messages"] = msgs
    version = _bump(record)
    return {"messageId": removed.get("id"), "messageCount": len(msgs), "_version": version}


def patch_message(record: Dict[str, Any], locator: Dict[str, Any],
                  content: Optional[str] = None, images: Any = _UNSET) -> Dict[str, Any]:
    """Edit a message in place (no truncation)."""
    msgs = list(record.get("messages") or [])
    idx = locate_message(msgs, locator)
    target = dict(msgs[idx])
    if target.get(SERVER_AUTHORED_FIELD):
        raise MessageOpError("server-authored messages cannot be edited", status=403)
    if content is not None and content != target.get("content"):
        _add_tombstones(record, [target])
        target["content"] = content
        target["_edited"] = True
    if images is not _UNSET:
        target["images"] = images
    msgs[idx] = target
    record["messages"] = msgs
    version = _bump(record)
    return {"messageId": target.get("id"), "messageCount": len(msgs), "_version": version}
