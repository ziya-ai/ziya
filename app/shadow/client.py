"""Chat-side client for shadow sessions (design doc §5, §9).

Thin wrapper over the Unix socket protocol plus the formatting that
turns journal records into a compact transcript the model can read.
Used by the ``shadow_*`` builtin tools and by ``ziya shadow --list``.

Nothing here touches the PTY; every call is a request to a running
shadow host.  Liveness is verified through the registry before
connecting, so a crashed host is reported as "not found" rather than a
connection error.
"""
import json
import socket
from typing import Any, Dict, List, Optional

from app.shadow import SHADOW_PROTOCOL_VERSION
from app.shadow import registry

DEFAULT_TIMEOUT_S = 5.0


class ShadowError(Exception):
    """A protocol-level error returned by the shadow host."""

    def __init__(self, code: str, msg: str):
        super().__init__(f"{code}: {msg}")
        self.code = code
        self.msg = msg


class SessionNotFound(ShadowError):
    def __init__(self, ref: str):
        super().__init__("not_found", f"no live shadow session matches {ref!r}")


class AmbiguousSession(ShadowError):
    def __init__(self, ref: str, candidates: List[registry.SessionEntry]):
        self.candidates = candidates
        listing = ", ".join(c.display for c in candidates)
        super().__init__("ambiguous", f"{ref!r} matches several sessions: {listing}")


def resolve_one(ref: str) -> registry.SessionEntry:
    """Resolve ``<id>`` / ``<label>`` / ``<label>:<id>`` to exactly one live session."""
    cands = registry.resolve(ref)
    if not cands:
        raise SessionNotFound(ref)
    if len(cands) > 1:
        raise AmbiguousSession(ref, cands)
    return cands[0]


def request(entry_or_socket, op: str, timeout: float = DEFAULT_TIMEOUT_S,
            **params: Any) -> Dict[str, Any]:
    """Send one request and return the decoded response.

    Raises ``ShadowError`` for protocol errors and ``OSError`` when the
    socket cannot be reached (the host died between registry check and
    connect).
    """
    path = entry_or_socket.socket if hasattr(entry_or_socket, "socket") else str(entry_or_socket)
    payload = {"v": SHADOW_PROTOCOL_VERSION, "op": op, **params}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    line = buf.split(b"\n", 1)[0]
    if not line:
        raise ShadowError("no_response", "shadow host closed the connection")
    resp = json.loads(line.decode("utf-8"))
    if isinstance(resp, dict) and "error" in resp:
        err = resp["error"] or {}
        raise ShadowError(str(err.get("code", "error")), str(err.get("msg", "")))
    return resp


# -- convenience wrappers ------------------------------------------------------

def list_sessions() -> List[Dict[str, Any]]:
    """Live sessions with the fields ``shadow_list`` displays (§9)."""
    out = []
    for e in registry.list_sessions():
        row: Dict[str, Any] = {
            "session_id": e.session_id,
            "label": e.label,
            "display": e.display,
            "argv": e.argv,
            "cwd": e.cwd,
            "started_at": e.started_at,
            "segmentation": e.segmentation,
            "allow_exec": e.allow_exec,
            "control_ceiling": e.control_ceiling,
            "headless": e.headless,
            "pending_ask": e.pending_ask,
            "attached": e.attached,
            "meta": dict(e.meta),
        }
        try:
            info = request(e, "info", timeout=1.0)
            row["tail_seq"] = info.get("tail_seq")
            row["last_activity"] = _last_ts(info)
        except (OSError, ShadowError):
            row["tail_seq"] = None
            row["last_activity"] = None
        out.append(row)
    return out


def _last_ts(info: Dict[str, Any]) -> Optional[str]:
    # `info` carries only bounds; fetch the final record for its timestamp.
    tail = info.get("tail_seq")
    if not tail:
        return None
    try:
        from app.shadow.journal import JournalReader
        recs = JournalReader(info["journal"]).read(int(tail), 1)["records"]
        return recs[-1].get("ts") if recs else None
    except Exception:  # noqa: BLE001
        return None


def tail(ref: str, last_n_commands: int = 10) -> List[Dict[str, Any]]:
    entry = resolve_one(ref)
    return request(entry, "tail", last_n_commands=last_n_commands)["records"]


def read(ref: str, from_seq: int, max_records: int = 200) -> Dict[str, Any]:
    entry = resolve_one(ref)
    return request(entry, "read", from_seq=from_seq, max_records=max_records)


def search(ref: str, pattern: str, max_hits: int = 20) -> List[Dict[str, Any]]:
    entry = resolve_one(ref)
    return request(entry, "search", pattern=pattern, max_hits=max_hits)["records"]


def comment(ref: str, text: str, provenance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    entry = resolve_one(ref)
    return request(entry, "comment", text=text, provenance=provenance or {})


def attach(ref: str, conversation_id: str, mode: str = "observe",
           provenance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    entry = resolve_one(ref)
    return request(entry, "attach", conversation_id=conversation_id, mode=mode,
                   provenance=provenance or {})


def detach(ref: str, conversation_id: Optional[str] = None,
           provenance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    entry = resolve_one(ref)
    params: Dict[str, Any] = {"provenance": provenance or {}}
    if conversation_id is not None:
        params["conversation_id"] = conversation_id
    return request(entry, "detach", **params)


def set_meta(ref: str, label: Optional[str] = None,
             data: Optional[Dict[str, Any]] = None,
             provenance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    entry = resolve_one(ref)
    params: Dict[str, Any] = {"provenance": provenance or {}}
    if label is not None:
        params["label"] = label
    if data is not None:
        params["data"] = data
    return request(entry, "set_meta", **params)


# -- model-facing formatting --------------------------------------------------

def format_records(records: List[Dict[str, Any]], max_chars: int = 12000) -> str:
    """Render journal records as a compact transcript.

    ::

        [41] $ systemctl status myservice
        [42] ● myservice.service - ...
             Active: failed (Result: exit-code)
        [43] → exit 3
        [44] ⏺ altscreen 142.0s

    Long output is truncated from the *middle* of the transcript so the
    most recent activity — the part the user is usually asking about —
    survives intact.  ``max_chars`` bounds what reaches the model.
    """
    lines: List[str] = []
    for r in records:
        t = r.get("t")
        seq = r.get("seq")
        if t == "cmd":
            lines.append(f"[{seq}] $ {r.get('text', '')}")
        elif t == "output":
            text = r.get("text", "").rstrip("\n")
            if not text:
                continue
            first, *rest = text.split("\n")
            lines.append(f"[{seq}] {first}")
            lines.extend(f"     {ln}" for ln in rest)
            if r.get("truncated"):
                lines.append("     … (continues in next record)")
        elif t == "exit":
            lines.append(f"[{seq}] → exit {r.get('code')}")
        elif t == "meta":
            ev = r.get("event")
            data = r.get("data") or {}
            if ev == "altscreen":
                lines.append(f"[{seq}] ⏺ full-screen program ran for {data.get('duration_s')}s (output not journaled)")
            elif ev == "mask":
                lines.append(f"[{seq}] ⏺ input masked ({data.get('reason')})")
            elif ev == "segmentation":
                lines.append(f"[{seq}] ⏺ segmentation {data.get('from')} → {data.get('to')}")
            elif ev == "comment":
                lines.append(f"[{seq}] ⏺ comment: {data.get('text', '')}")
            elif ev in ("start", "exit_session"):
                lines.append(f"[{seq}] ⏺ session {ev}: {json.dumps(data, ensure_ascii=False)}")
            else:
                lines.append(f"[{seq}] ⏺ {ev} {json.dumps(data, ensure_ascii=False)}")
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    head = max_chars // 4
    tail_n = max_chars - head - 40
    return text[:head] + "\n… [transcript truncated] …\n" + text[-tail_n:]


def format_session_table(sessions: List[Dict[str, Any]]) -> str:
    """Human-readable listing for ``ziya shadow --list``."""
    if not sessions:
        return "No live shadow sessions. Start one with: ziya shadow [--label NAME] [cmd...]"
    rows = [("ID", "LABEL", "SEG", "EXEC", "LAST ACTIVITY", "COMMAND")]
    for s in sessions:
        flags = ("exec" if s.get("allow_exec") else "-") + ("/ask" if s.get("pending_ask") else "")
        rows.append((s["session_id"], s["label"][:28], s.get("segmentation", "?"), flags,
                     s.get("last_activity") or s.get("started_at", ""),
                     " ".join(s.get("argv") or [])[:40]))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)
