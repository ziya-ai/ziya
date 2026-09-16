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
import os
import socket
import time
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
            # Output begins with the newline that ended the echoed command
            # line; strip it so a block does not open with a blank line.
            # Tabs are expanded before the 5-column prefix shifts the tab
            # stops, or ``ls``/``ps`` columns collapse into each other.
            text = r.get("text", "").strip("\n")
            if not text:
                continue
            first, *rest = (ln.expandtabs(8) for ln in text.split("\n"))
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
    rows = [("ID", "LABEL", "SEG", "EXEC", "CHAT", "LAST ACTIVITY", "COMMAND")]
    for s in sessions:
        flags = ("exec" if s.get("allow_exec") else "-") + ("/ask" if s.get("pending_ask") else "")
        conv = (s.get("attached") or {}).get("conversation_id")
        rows.append((s["session_id"], s["label"][:28], s.get("segmentation", "?"), flags,
                     str(conv)[:8] if conv else "-",
                     s.get("last_activity") or s.get("started_at", ""),
                     " ".join(s.get("argv") or [])[:40]))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)


# -- control leases (§6.1a line control) --------------------------------------

def control_acquire(ref: str, conversation_id: str, *, restriction: str = "gated",
                    policy: str = "builtin", mode: str = "line",
                    provenance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    entry = resolve_one(ref)
    return request(entry, "control_acquire", conversation_id=conversation_id,
                   restriction=restriction, policy=policy, mode=mode,
                   provenance=provenance or {})


def control_heartbeat(ref, lease_id: str, conversation_id: str) -> Dict[str, Any]:
    entry = ref if hasattr(ref, "socket") else resolve_one(ref)
    return request(entry, "control_heartbeat", lease_id=lease_id,
                   conversation_id=conversation_id, timeout=2.0)


def control_release(ref: str, lease_id: str,
                    provenance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    entry = resolve_one(ref)
    return request(entry, "control_release", lease_id=lease_id,
                   provenance=provenance or {})


def control_status(ref: str) -> Dict[str, Any]:
    return request(resolve_one(ref), "control_status")


def send_line(ref: str, lease_id: str, text: str,
              provenance: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    entry = resolve_one(ref)
    # A gated command may sit at the terminal's confirm banner for up to
    # sock_server.CONFIRM_TIMEOUT_S; the socket wait must outlast that.
    # ... plus the yield-to-human wait (pty_host.SEND_WAIT_S).
    return request(entry, "send_line", lease_id=lease_id, text=text,
                   provenance=provenance or {}, timeout=85.0)


def wait_idle(ref: str, quiet_ms: int = 800, timeout_ms: int = 15000) -> Dict[str, Any]:
    entry = resolve_one(ref)
    # Socket-level timeout must outlast the server-side wait.
    return request(entry, "wait_idle", quiet_ms=quiet_ms, timeout_ms=timeout_ms,
                   timeout=(timeout_ms / 1000.0) + 5.0)


class LeaseKeepalive:
    """Background heartbeat for a control lease held by a chat conversation.

    A lease has a short (~10 s) dead-man timeout so a crashed controller
    releases it promptly.  A chat conversation is not a tight loop — there
    can be minutes of model/human think-time between control ops — so the
    heartbeat cannot ride on tool calls alone.  This runs a daemon thread
    in the *chat/server process* that pings until stopped or the session
    dies.  Because it lives in that process, the dead-man property holds:
    if the process dies the pings stop and the lease expires (§6.1).

    Keyed and registry-managed by the tool layer so a conversation has at
    most one keepalive per session, and it is torn down on release.
    """

    def __init__(self, entry, lease_id: str, conversation_id: str,
                 interval_s: Optional[float] = None):
        import threading
        self._entry = entry
        self._lease_id = lease_id
        self._conv = conversation_id
        self._interval = interval_s or 3.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="shadow-lease-hb",
                                        daemon=True)

    def start(self) -> "LeaseKeepalive":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                resp = control_heartbeat(self._entry, self._lease_id, self._conv)
            except (OSError, ShadowError):
                return  # session gone — nothing left to keep alive
            if isinstance(resp, dict) and resp.get("error"):
                return


# -- headless sessions (§6.2) ---------------------------------------------------

SPAWN_ANNOUNCE_TIMEOUT_S = 10.0


def spawn_headless(argv: List[str], *, label: Optional[str] = None,
                   ceiling: str = "gated", spawned_by: Optional[Dict[str, Any]] = None,
                   idle_timeout_s: Optional[float] = None) -> str:
    """Fork a detached headless shadow host running ``argv``; return its id.

    The child is started in its own session (``start_new_session``) with
    stdio on /dev/null so it survives the calling tool/turn/process.  It
    announces its session id back over an inherited pipe; anything else it
    writes there (``error: ...``) is raised as ``ShadowError``.
    """
    import subprocess, sys, json as _json, select as _select
    if not argv:
        raise ShadowError("bad_request", "argv is required")
    # Local exec with no allowlist in front of it and no human keystroke:
    # the head must be on the spawn allowlist and, for ssh, must not run
    # anything at spawn time (app.shadow.policy.check_spawn_argv).
    from app.shadow.policy import check_spawn_argv
    ok, why = check_spawn_argv(argv)
    if not ok:
        raise ShadowError("spawn_denied", why)
    if ceiling not in ("gated", "unrestricted"):
        raise ShadowError("bad_request", f"invalid ceiling: {ceiling!r}")
    r, w = os.pipe()
    cmd = [sys.executable, "-m", "app.shadow.headless",
           "--ceiling", ceiling, "--spawned-by", _json.dumps(spawned_by or {}),
           "--announce-fd", str(w)]
    if label:
        cmd += ["--label", label]
    if idle_timeout_s is not None:
        cmd += ["--idle-timeout", str(float(idle_timeout_s))]
    cmd += ["--"] + list(argv)
    env = dict(os.environ)
    # The child must import ``app`` the way this process does: pin the
    # directory that contains the ``app`` package (site-packages or a
    # checkout), not the whole of sys.path — that carries cwd-derived
    # entries a detached daemon has no reason to inherit.  Third-party
    # imports resolve through sys.executable's own site.
    import app as _app_pkg
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(_app_pkg.__file__)))
    env["PYTHONPATH"] = os.pathsep.join(
        [pkg_root] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True,
                                pass_fds=(w,), env=env, close_fds=True)
    finally:
        os.close(w)
    buf = b""
    deadline = time.monotonic() + SPAWN_ANNOUNCE_TIMEOUT_S
    try:
        while b"\n" not in buf and time.monotonic() < deadline:
            ready, _, _ = _select.select([r], [], [], 0.2)
            if ready:
                chunk = os.read(r, 4096)
                if not chunk:
                    break
                buf += chunk
    finally:
        os.close(r)
    # The Popen'd process is only the daemonizing intermediate (see
    # app.shadow.headless); it exits immediately.  Reap it so it never
    # lingers as a zombie of this process.
    try:
        proc.wait(timeout=5.0)
    except Exception:  # noqa: BLE001
        pass
    line = buf.split(b"\n", 1)[0].decode("utf-8", errors="replace").strip()
    if not line:
        raise ShadowError("spawn_failed", "headless host did not announce a session id")
    if line.startswith("error:"):
        raise ShadowError("spawn_failed", line[6:].strip())
    return line


def kill_session(ref: str, *, conversation_id: Optional[str] = None) -> Dict[str, Any]:
    """Terminate a headless session (SIGTERM → host hangs up its child).

    Only the spawning conversation may kill it; interactive sessions are
    never killable from a chat (the human at the terminal ends those).
    """
    import signal as _signal
    entry = resolve_one(ref)
    if not entry.headless or not entry.spawned_by:
        raise ShadowError("not_headless", "only agent-spawned headless sessions can be killed from chat")
    owner = (entry.spawned_by or {}).get("conversation_id")
    if not conversation_id or owner != conversation_id:
        raise ShadowError("not_owner", "this conversation did not spawn that session")
    # The registry pid is plaintext and pids are reused: before signalling,
    # make the host prove over its socket that it is still this session.
    try:
        pong = request(entry, "ping", timeout=2.0)
    except (OSError, ShadowError) as e:
        raise ShadowError("unreachable", f"session host did not answer on its socket: {e}")
    if pong.get("session_id") != entry.session_id:
        raise ShadowError("not_session", "socket peer is not this session; refusing to signal its pid")
    try:
        os.kill(entry.pid, _signal.SIGTERM)
    except ProcessLookupError:
        return {"killed": False, "note": "host already gone"}
    return {"killed": True, "session_id": entry.session_id, "pid": entry.pid}