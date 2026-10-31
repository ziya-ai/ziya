"""
Durable record of chat turns, so a turn outlives the in-memory relay.

The relay (app/agents/chat_turn_relay.py) keeps a turn's SSE frames in
memory for the turn's life plus 5 minutes.  Nothing else holds the
assistant's answer: the frontend persists it only from a live stream, and
the server never writes it.  So a turn that finished (or was cancelled)
with nobody watching -- a dropped ssh tunnel, a headless batch turn -- was
gone once that window passed, and a server restart lost even a running
turn's partial output.

This module writes each turn to ``<ZIYA_HOME>/chat_turns/`` as two files:

  ``<conversation_id>.status.json``  small: the relay's status dict plus
                                     headers and the writing process's
                                     boot id.  Read by every status query.
  ``<conversation_id>.frames.json``  the folded frame buffer.  Read only
                                     when a client actually reattaches.

Both are encrypted under the ``conversation_data`` category, the same as
chat records: the frames ARE the conversation.  One record per
conversation (the relay runs one turn per conversation); a new turn
overwrites the previous one.  Records older than the retention window are
pruned opportunistically on write.

All functions are synchronous file IO.  The relay calls ``save`` through
``asyncio.to_thread``; the read side is small (status) or happens once per
reattach (frames).
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.utils.logging_utils import logger

_CATEGORY = "conversation_data"
_STATUS_SUFFIX = ".status.json"
_FRAMES_SUFFIX = ".frames.json"
_PRUNE_INTERVAL_SECONDS = 3600.0
_last_prune = 0.0


def _retention_seconds() -> float:
    try:
        from app.config.env_registry import ziya_env
        return float(ziya_env("ZIYA_CHAT_TURN_RECORD_RETENTION_HOURS")) * 3600.0
    except Exception:  # noqa: BLE001 -- unregistered or unparsable: default
        return 7 * 24 * 3600.0


def _dir() -> Path:
    from app.utils.paths import get_ziya_home
    d = get_ziya_home() / "chat_turns"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(conversation_id: str, suffix: str) -> Path:
    from app.storage.base import contained_path
    return contained_path(_dir(), f"{conversation_id}{suffix}")


def _write(path: Path, payload: Any) -> None:
    from app.utils.encryption import get_encryptor
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8", errors="replace")
    enc = get_encryptor()
    if enc.is_enabled(_CATEGORY):
        data = enc.encrypt(data, _CATEGORY)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _read(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        from app.utils.encryption import get_encryptor, is_encrypted
        if is_encrypted(raw):
            raw = get_encryptor().decrypt(raw)
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001 -- a bad record is no record
        logger.warning(f"📡 CHAT_TURN_STORE: unreadable {path.name}: {exc}")
        return None


def save(status: Dict[str, Any], frames: List[str], dropped: int) -> None:
    """Write one turn.  Frames first, so a status never points at frames
    from a different turn: a reader that finds a status checks the turn id
    the frames file carries."""
    cid = status["conversation_id"]
    _write(_path(cid, _FRAMES_SUFFIX),
           {"turn_id": status["turn_id"], "dropped": dropped, "frames": frames})
    _write(_path(cid, _STATUS_SUFFIX), {**status, "saved_at": time.time()})
    _maybe_prune()


def load_status(conversation_id: str) -> Optional[Dict[str, Any]]:
    try:
        rec = _read(_path(conversation_id, _STATUS_SUFFIX))
    except ValueError:  # traversal-shaped id
        return None
    return rec if isinstance(rec, dict) and rec.get("turn_id") else None


def load_frames(conversation_id: str, turn_id: str) -> Optional[Dict[str, Any]]:
    """The frames of ``turn_id``, or None if the file is missing or holds a
    different turn (a write interrupted between the two files)."""
    try:
        rec = _read(_path(conversation_id, _FRAMES_SUFFIX))
    except ValueError:
        return None
    if not isinstance(rec, dict) or rec.get("turn_id") != turn_id:
        return None
    if not isinstance(rec.get("frames"), list):
        return None
    return rec


def list_statuses() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        names = [p for p in _dir().iterdir() if p.name.endswith(_STATUS_SUFFIX)]
    except OSError:
        return out
    for p in names:
        rec = _read(p)
        if isinstance(rec, dict) and rec.get("turn_id"):
            out.append(rec)
    return out


def _maybe_prune(now: Optional[float] = None) -> int:
    """Delete records whose status was saved before the retention window."""
    global _last_prune
    now = time.time() if now is None else now
    if now - _last_prune < _PRUNE_INTERVAL_SECONDS:
        return 0
    _last_prune = now
    cutoff = now - _retention_seconds()
    removed = 0
    try:
        entries = list(_dir().iterdir())
    except OSError:
        return 0
    for p in entries:
        if not p.name.endswith(_STATUS_SUFFIX):
            continue
        try:
            if p.stat().st_mtime >= cutoff:
                continue
            cid = p.name[: -len(_STATUS_SUFFIX)]
            p.unlink(missing_ok=True)
            (p.parent / f"{cid}{_FRAMES_SUFFIX}").unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    if removed:
        logger.info(f"📡 CHAT_TURN_STORE: pruned {removed} record(s) past retention")
    return removed


def _reset_prune_clock_for_tests() -> None:
    global _last_prune
    _last_prune = 0.0
