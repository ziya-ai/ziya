"""Shadow session journal (design doc §4).

Append-only JSONL, one record per segment:

  {"t": "cmd",    "seq": 41, "ts": ..., "text": "systemctl status foo"}
  {"t": "output", "seq": 42, "ts": ..., "cmd_seq": 41, "text": "...", "truncated": false}
  {"t": "exit",   "seq": 43, "ts": ..., "cmd_seq": 41, "code": 3}
  {"t": "meta",   "seq": 44, "ts": ..., "event": "...", "data": {...}}

Rules (§4):
- seq is monotonically increasing per session; readers page by seq.
- output records are chunked at 64 KiB, multiple chunks share cmd_seq.
- rotation caps the journal at 50 MiB, dropping oldest whole
  cmd/output/exit groups.
- masked input bytes NEVER reach this file (masking happens upstream).
- the journal is unlinked on session exit (registry.remove); it is a
  live buffer, not an archive.

Encryption at rest (§10.1): when Ziya's application-level encryption is
active for the ``session_data`` category, every line is written as
``!ale1:<base64 ALE envelope>`` instead of plaintext JSON.  The
envelope is per *line* (not per file, as the other stores do) because
the journal is append-only and read concurrently while being written;
a whole-file envelope would force read-modify-write on every record.
Readers decode either form, so a journal is readable across an ALE
enable/disable boundary and a SIGKILLed host leaves only ciphertext
on disk until the next reader reaps it.
"""
import base64
import binascii
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional

from app.shadow.redaction import Redactor

OUTPUT_CHUNK_BYTES = 64 * 1024
ROTATION_CAP_BYTES = 50 * 1024 * 1024
# Rotation rewrites are amortized: check size only every N appends.
ROTATION_CHECK_EVERY = 256
ALE_CATEGORY = "session_data"
# A JSON record line always starts with "{"; this prefix cannot collide.
_ALE_LINE_PREFIX = "!ale1:"


def _now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _encryptor():
    from app.utils.encryption import get_encryptor
    return get_encryptor()


def encode_line(record: Dict) -> str:
    """Serialize one record to its on-disk line (without newline)."""
    line = json.dumps(record, ensure_ascii=False)
    enc = _encryptor()
    if not enc.is_enabled(ALE_CATEGORY):
        return line
    envelope = enc.encrypt(line.encode("utf-8"), ALE_CATEGORY)
    from app.utils.encryption import is_encrypted
    if not is_encrypted(envelope):
        # encrypt() returns plaintext when it cannot encrypt (no DEK):
        # never write a secret-bearing line in the clear in that state.
        raise RuntimeError("ALE is enabled for session_data but encryption "
                           "is unavailable; refusing to journal in plaintext")
    return _ALE_LINE_PREFIX + base64.b64encode(envelope).decode("ascii")


def decode_line(line: str) -> Optional[Dict]:
    """Parse one on-disk line (either form).  None if unreadable.

    ``ValueError`` from the encryptor (missing DEK, wrong KEK) is raised
    rather than swallowed: a reader must not silently present an
    encrypted journal as empty.
    """
    line = line.strip()
    if not line:
        return None
    if line.startswith(_ALE_LINE_PREFIX):
        try:
            envelope = base64.b64decode(line[len(_ALE_LINE_PREFIX):], validate=True)
        except (binascii.Error, ValueError):
            return None  # torn write
        plaintext = _encryptor().decrypt(envelope)
        line = plaintext.decode("utf-8", errors="replace")
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    return rec if isinstance(rec, dict) else None


class JournalWriter:
    """Single-writer append interface, owned by the shadow process.

    Thread-safe: the PTY read loop and the socket server (comment,
    set_meta, control records) may append concurrently.
    """

    def __init__(self, path: str, redactor: Optional[Redactor] = None):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._seq = 0
        self._appends_since_check = 0
        # Recover seq from an existing journal (e.g. after an exec
        # of the shadow process re-using the same session files).
        if self.path.exists():
            try:
                last = None
                with open(self.path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            last = line
                rec = decode_line(last) if last else None
                self._seq = int(rec.get("seq", 0)) if rec else 0
            except (OSError, ValueError):
                self._seq = 0
        else:
            self.path.touch()
        os.chmod(self.path, 0o600)
        # Subscribers (sock_server push channels): called with each
        # record dict after it is durably appended.
        self._listeners: List[Callable[[Dict], None]] = []
        # Every cmd/output record passes through here before disk (§7):
        # displayed credentials are the gap input-side masking cannot see.
        self._redactor = redactor if redactor is not None else Redactor()

    # -- listener plumbing (for the `subscribe` socket request) -------

    def add_listener(self, fn: Callable[[Dict], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[Dict], None]) -> None:
        with self._lock:
            try:
                self._listeners.remove(fn)
            except ValueError:
                pass

    # -- record appends ------------------------------------------------

    def cmd(self, text: str) -> int:
        """Append a command record; returns its seq (the cmd_seq)."""
        text = self._redactor.redact(text, command=True)
        return self._append({"t": "cmd", "text": text})

    def output(self, text: str, cmd_seq: Optional[int]) -> List[int]:
        """Append output, chunked at 64 KiB; returns seqs written."""
        seqs: List[int] = []
        text = self._redactor.redact(text)
        data = text.encode("utf-8", errors="replace")
        if not data:
            return seqs
        for i in range(0, len(data), OUTPUT_CHUNK_BYTES):
            chunk = data[i:i + OUTPUT_CHUNK_BYTES]
            seqs.append(self._append({
                "t": "output",
                "cmd_seq": cmd_seq,
                "text": chunk.decode("utf-8", errors="replace"),
                "truncated": (i + OUTPUT_CHUNK_BYTES) < len(data),
            }))
        return seqs

    def exit(self, cmd_seq: int, code: int) -> int:
        return self._append({"t": "exit", "cmd_seq": cmd_seq, "code": code})

    def meta(self, event: str, data: Optional[Dict] = None) -> int:
        return self._append({"t": "meta", "event": event,
                             "data": data or {}})

    # -- internals -------------------------------------------------------

    def _append(self, record: Dict) -> int:
        with self._lock:
            self._seq += 1
            record["seq"] = self._seq
            record["ts"] = _now_ts()
            line = encode_line(record)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._appends_since_check += 1
            if self._appends_since_check >= ROTATION_CHECK_EVERY:
                self._appends_since_check = 0
                self._maybe_rotate()
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(record)
            except Exception:  # noqa: BLE001 — a bad subscriber must not break the writer
                pass
        return record["seq"]

    def _maybe_rotate(self) -> None:
        """Drop oldest whole cmd/output/exit groups past the size cap.

        Called under self._lock.  Group boundary = a cmd record (or a
        meta record, which is its own group); orphan output/exit
        records that share the dropped cmd_seq go with their group.
        """
        try:
            if self.path.stat().st_size <= ROTATION_CAP_BYTES:
                return
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return
        # Drop from the front until under 80% of cap, but never split
        # a cmd group: only cut immediately before a cmd or meta record.
        target = int(ROTATION_CAP_BYTES * 0.8)
        total = sum(len(l.encode("utf-8", errors="replace")) for l in lines)
        cut = 0
        dropped = 0
        for i, line in enumerate(lines):
            if total - dropped <= target:
                break
            try:
                rec = decode_line(line)
            except ValueError:
                rec = None
            if rec is None:
                dropped += len(line.encode("utf-8", errors="replace"))
                cut = i + 1
                continue
            if rec.get("t") in ("cmd", "meta") and i > 0:
                cut = i
            dropped += len(line.encode("utf-8", errors="replace"))
            cut = max(cut, i + 1) if rec.get("t") not in ("cmd", "meta") else cut
        if cut <= 0:
            return
        tmp = self.path.with_suffix(".journal.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines[cut:])
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)


MAX_PATTERN_CHARS = 256
_QUANTIFIED_GROUP_RE = None  # compiled lazily below


def compile_search_pattern(pattern: str):
    """Compile a user/model-supplied search regex, or None if unsafe/invalid."""
    import re
    global _QUANTIFIED_GROUP_RE
    if _QUANTIFIED_GROUP_RE is None:
        _QUANTIFIED_GROUP_RE = re.compile(r"\)\s*[*+?{]|\{\d{3,}")
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_CHARS:
        return None
    if _QUANTIFIED_GROUP_RE.search(pattern):
        return None
    try:
        return re.compile(pattern)
    except re.error:
        return None


def searchable_text(rec: Dict) -> str:
    """The text a ``search`` pattern is matched against.

    cmd/output: the text.  meta: the event name plus every string value
    in its data (an ``ask`` question, a ``comment``, a control verdict's
    command and reason), so the human's questions and the chat's replies
    are findable — they were invisible to a regex before.
    """
    t = rec.get("t")
    if t in ("cmd", "output"):
        return rec.get("text", "") or ""
    if t == "exit":
        return f"exit {rec.get('code')}"
    if t == "meta":
        parts = [str(rec.get("event") or "")]
        data = rec.get("data")
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, str):
                    parts.append(v)
                elif isinstance(v, (int, float, bool)):
                    parts.append(str(v))
        return " ".join(parts)
    return ""


class JournalReader:
    """Read-side access; safe from any process (file is append-only)."""

    def __init__(self, path: str):
        self.path = Path(path)

    def _iter(self) -> Iterator[Dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    rec = decode_line(line)  # ValueError propagates: see decode_line
                    if rec is None:
                        continue  # blank, torn write at EOF, or rotation race
                    yield rec
        except FileNotFoundError:
            return

    def bounds(self) -> Dict[str, Optional[int]]:
        """Head and tail seq currently in the journal (for `info`)."""
        head = tail = None
        for rec in self._iter():
            if head is None:
                head = rec.get("seq")
            tail = rec.get("seq")
        return {"head_seq": head, "tail_seq": tail}

    def read(self, from_seq: int, max_records: int = 500) -> Dict:
        """Page records by seq (§5 `read`)."""
        max_records = max(1, min(int(max_records), 500))
        records: List[Dict] = []
        next_seq = from_seq
        for rec in self._iter():
            seq = rec.get("seq", 0)
            if seq < from_seq:
                continue
            if len(records) >= max_records:
                break
            records.append(rec)
            next_seq = seq + 1
        return {"records": records, "next_seq": next_seq}

    def tail(self, last_n_commands: int = 10) -> List[Dict]:
        """Last N cmd groups with their outputs/exits (§5 `tail`).

        In raw segmentation there may be no cmd records at all; in that
        case return the last N output/meta records instead so the call
        is still useful.
        """
        last_n_commands = max(1, min(int(last_n_commands), 50))
        all_recs = list(self._iter())
        cmd_seqs = [r["seq"] for r in all_recs if r.get("t") == "cmd"]
        if not cmd_seqs:
            return all_recs[-last_n_commands:]
        cutoff = cmd_seqs[-last_n_commands:]
        first = cutoff[0]
        return [r for r in all_recs if r.get("seq", 0) >= first]

    def search(self, pattern: str, max_hits: int = 20) -> List[Dict]:
        """Regex search over cmd/output text (§5 `search`).

        Returns matching records; the caller widens to group context
        via read() if needed.

        The pattern is model-supplied and runs inside the user's terminal
        wrapper, so it is bounded: at most MAX_PATTERN_CHARS, and any
        quantified group (``(...)+``, ``(...)*``, ``(...){n}``) is refused
        because that is the shape of every catastrophic-backtracking regex.
        """
        rx = compile_search_pattern(pattern)
        if rx is None:
            return []
        max_hits = max(1, min(int(max_hits), 100))
        hits: List[Dict] = []
        for rec in self._iter():
            if rx.search(searchable_text(rec)):
                hits.append(rec)
                if len(hits) >= max_hits:
                    break
        return hits
