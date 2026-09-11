"""Shadow session socket API (design doc §5).

Unix stream socket, newline-delimited JSON request/response.  Every
request carries ``"v": 1``.  Served from a daemon thread inside the
shadow host; any same-UID process may connect (socket is mode 0600).

Phase 1 requests: ``info``, ``read``, ``tail``, ``search``, ``comment``,
``subscribe``, ``set_meta``, ``ping``.  Exec and control requests are
recognised but answered with their §5 error codes (``exec_disabled`` /
``control_disabled``) until those phases land, so a chat-side client
gets a truthful, specific refusal rather than a parse error.

The server owns no PTY state.  It reads the journal file (append-only,
safe to read concurrently), appends its own meta records through the
shared ``JournalWriter``, and calls back into the host for the two
things that touch the human's terminal: rendering a comment and
updating the registry entry.
"""
import json
import os
import socket
import stat
import struct
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

from app.shadow import SHADOW_PROTOCOL_VERSION
from app.shadow.journal import JournalReader, JournalWriter

MAX_REQUEST_BYTES = 64 * 1024
MAX_COMMENT_CHARS = 2000
MAX_LABEL_CHARS = 120


def _err(code: str, msg: str) -> Dict[str, Any]:
    return {"error": {"code": code, "msg": msg}}


def _peer_uid(conn: socket.socket) -> Optional[int]:
    """UID of the process on the other end of a Unix socket, or None.

    File permissions on the socket path already restrict access to the
    owner; this verifies the same property on the connection itself, so a
    mis-permissioned parent directory can never widen access silently.
    None (undeterminable) is treated by the caller as foreign.
    """
    try:
        if hasattr(socket, "SO_PEERCRED"):                       # Linux
            data = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                   struct.calcsize("3i"))
            return struct.unpack("3i", data)[1]
        if sys.platform == "darwin":                              # LOCAL_PEERCRED
            data = conn.getsockopt(0, 0x0001, 76)                 # struct xucred
            return struct.unpack("II", data[:8])[1]
    except (OSError, struct.error):
        return None
    return None


def assert_private_dir(path: str) -> None:
    """Refuse a directory we would trust unless it is a real directory we
    own with no group/other permission bits — never a symlink.  The mode is
    normalised to exactly 0o700: too-open is a leak, but a directory missing
    its owner search bit (created under a restrictive umask) is unusable."""
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise RuntimeError(f"{path} is not a plain directory")
    if st.st_uid != os.getuid():
        raise RuntimeError(f"{path} is not owned by uid {os.getuid()}")
    if stat.S_IMODE(st.st_mode) != 0o700:
        os.chmod(path, 0o700)


def short_socket_dir() -> str:
    """Per-user 0700 directory used when the registry path overflows the
    AF_UNIX limit.  Shared with the registry so unlink-on-exit can confine
    itself to known shadow directories."""
    import tempfile
    d = os.path.join(tempfile.gettempdir(), f"ziya-shadow-{os.getuid()}")
    try:
        # mkdir's mode is masked by the process umask — and this runs inside
        # ShadowSocketServer.start()'s umask(0o177) window, which would yield
        # 0o600 (no search bit).  chmod afterwards is umask-independent.
        os.mkdir(d)
        os.chmod(d, 0o700)
    except FileExistsError:
        pass
    assert_private_dir(d)
    return d


class ShadowSocketServer:
    """Serves one shadow session over its Unix socket.

    ``entry`` is the live ``SessionEntry``; ``journal`` the session's
    ``JournalWriter``.  ``on_comment(text, provenance) -> bool`` renders on
    the canvas and reports whether anything was shown (False when
    headless); ``on_meta_change()`` is called after
    label/meta edits so the host can refresh anything it displays.
    """

    def __init__(self, entry, journal: JournalWriter, *,
                 on_comment: Optional[Callable[[str, Dict[str, Any]], None]] = None,
                 on_meta_change: Optional[Callable[[], None]] = None,
                 on_attach: Optional[Callable[[], None]] = None):
        self.entry = entry
        self.journal = journal
        self.reader = JournalReader(entry.journal)
        self.on_comment = on_comment
        self.on_meta_change = on_meta_change
        self.on_attach = on_attach
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._subscribers = 0
        self._sub_lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        path = self.entry.socket
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o177)
        try:
            try:
                self._unlink_quiet(path)
                srv.bind(path)
            except OSError as e:
                # AF_UNIX paths are capped (104 bytes on macOS, 108 on
                # Linux); a deep $HOME overflows it.  Fall back to a short
                # per-user 0700 directory and record the real path in the
                # registry so clients still find it.
                if "too long" not in str(e).lower() and e.errno != 63:
                    srv.close()
                    raise
                path = self._short_socket_path()
                self._unlink_quiet(path)
                srv.bind(path)
                self.entry.socket = path
                self.entry.save()
        finally:
            os.umask(old_umask)
        os.chmod(path, 0o600)
        srv.listen(8)
        srv.settimeout(0.5)
        self._sock = srv
        self._thread = threading.Thread(target=self._accept_loop,
                                        name="shadow-sock", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        try:
            os.unlink(self.entry.socket)
        except OSError:
            pass

    @staticmethod
    def _unlink_quiet(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass

    def _short_socket_path(self) -> str:
        return os.path.join(short_socket_dir(), f"{self.entry.session_id}.sock")

    @property
    def subscriber_count(self) -> int:
        with self._sub_lock:
            return self._subscribers

    def _accept_loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,),
                             name="shadow-conn", daemon=True).start()

    # -- per-connection -------------------------------------------------------

    def _serve(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(30.0)
            uid = _peer_uid(conn)
            if uid is None or uid != os.getuid():
                self._send(conn, _err("forbidden", "peer is not the session owner"))
                return
            buf = b""
            while not self._stop.is_set():
                while b"\n" not in buf:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    buf += chunk
                    if len(buf) > MAX_REQUEST_BYTES:
                        self._send(conn, _err("bad_request", "request too large"))
                        return
                line, buf = buf.split(b"\n", 1)
                try:
                    req = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send(conn, _err("bad_request", "invalid JSON"))
                    continue
                if not isinstance(req, dict):
                    self._send(conn, _err("bad_request", "request must be an object"))
                    continue
                if req.get("v") != SHADOW_PROTOCOL_VERSION:
                    self._send(conn, _err("version_mismatch",
                                          f"server speaks v{SHADOW_PROTOCOL_VERSION}"))
                    continue
                if req.get("op") == "subscribe":
                    self._subscribe(conn, req)
                    return  # subscribe owns the connection until it closes
                self._send(conn, self.dispatch(req))
        except (OSError, socket.timeout):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _send(conn: socket.socket, payload: Dict[str, Any]) -> None:
        conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))

    # -- dispatch (also callable in-process, e.g. from tests) -----------------

    def dispatch(self, req: Dict[str, Any]) -> Dict[str, Any]:
        op = req.get("op")
        handler = getattr(self, f"_op_{op}", None) if isinstance(op, str) else None
        if handler is None or op == "subscribe":
            return _err("bad_request", f"unknown op: {op!r}")
        try:
            return handler(req)
        except Exception as e:  # noqa: BLE001 — never let one request kill the server
            return _err("internal", str(e))

    def _op_ping(self, req: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": True, "session_id": self.entry.session_id}

    def _op_info(self, req: Dict[str, Any]) -> Dict[str, Any]:
        from dataclasses import asdict
        info = asdict(self.entry)
        info.update(self.reader.bounds())
        info["subscribers"] = self.subscriber_count
        return info

    def _op_read(self, req: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from_seq = int(req.get("from_seq", 1))
        except (TypeError, ValueError):
            return _err("bad_seq", "from_seq must be an integer")
        if from_seq < 0:
            return _err("bad_seq", "from_seq must be >= 0")
        return self.reader.read(from_seq, int(req.get("max_records", 500) or 500))

    def _op_tail(self, req: Dict[str, Any]) -> Dict[str, Any]:
        n = int(req.get("last_n_commands", 10) or 10)
        return {"records": self.reader.tail(n)}

    def _op_search(self, req: Dict[str, Any]) -> Dict[str, Any]:
        pattern = req.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return _err("bad_request", "pattern is required")
        hits = self.reader.search(pattern, int(req.get("max_hits", 20) or 20))
        return {"records": hits}

    def _op_comment(self, req: Dict[str, Any]) -> Dict[str, Any]:
        text = req.get("text")
        if not isinstance(text, str) or not text.strip():
            return _err("bad_request", "text is required")
        text = text.strip()[:MAX_COMMENT_CHARS]
        provenance = req.get("provenance") or {}
        seq = self.journal.meta("comment", {"text": text, "provenance": provenance})
        rendered = False
        if self.on_comment is not None:
            try:
                rendered = bool(self.on_comment(text, provenance))
            except Exception:  # noqa: BLE001
                rendered = False
        return {"seq": seq, "rendered": rendered}

    def _op_set_meta(self, req: Dict[str, Any]) -> Dict[str, Any]:
        changed: Dict[str, Any] = {}
        label = req.get("label")
        if label is not None:
            if not isinstance(label, str) or not label.strip():
                return _err("bad_request", "label must be a non-empty string")
            self.entry.label = label.strip()[:MAX_LABEL_CHARS]
            changed["label"] = self.entry.label
        data = req.get("data")
        if data is not None:
            if not isinstance(data, dict):
                return _err("bad_request", "data must be an object")
            for k, v in data.items():
                if v is None:
                    self.entry.meta.pop(str(k), None)
                else:
                    self.entry.meta[str(k)] = str(v)
            changed["data"] = dict(self.entry.meta)
        if not changed:
            return _err("bad_request", "nothing to set")
        self.entry.save()
        seq = self.journal.meta("meta", {**changed, "provenance": req.get("provenance") or {}})
        if self.on_meta_change is not None:
            try:
                self.on_meta_change()
            except Exception:  # noqa: BLE001
                pass
        return {"seq": seq, "label": self.entry.label, "meta": dict(self.entry.meta)}

    def _fire_attach(self) -> None:
        if self.on_attach is not None:
            try:
                self.on_attach()
            except Exception:  # noqa: BLE001 — a display error must not fail the op
                pass

    def _op_attach(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Bind a chat conversation to this session for observation (§6.1).

        Observation is shareable, so this is intentionally last-writer-wins
        on the single display slot; the takeover is journaled with the
        conversation it replaced.  Exec/control attachment modes arrive in
        later phases and are refused here.
        """
        conv = req.get("conversation_id")
        if not isinstance(conv, str) or not conv.strip():
            return _err("bad_request", "conversation_id is required")
        conv = conv.strip()[:64]
        mode = req.get("mode", "observe")
        if mode not in ("observe",):
            return _err("bad_request", f"unsupported attach mode: {mode!r}")
        prev = (self.entry.attached or {}).get("conversation_id")
        self.entry.attached = {"conversation_id": conv, "mode": mode,
                               "attached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        self.entry.save()
        seq = self.journal.meta("attach", {"conversation_id": conv, "mode": mode,
                                           "replaced": prev,
                                           "provenance": req.get("provenance") or {}})
        self._fire_attach()
        return {"seq": seq, "attached": dict(self.entry.attached), "replaced": prev}

    def _op_detach(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Clear the attachment.  A conversation may only detach itself
        (or force with no conversation_id, e.g. shadow-side revoke)."""
        cur = self.entry.attached or {}
        conv = req.get("conversation_id")
        if cur and conv and cur.get("conversation_id") != conv:
            return _err("not_attached", "session is attached to a different conversation")
        self.entry.attached = None
        self.entry.save()
        seq = self.journal.meta("detach", {"conversation_id": cur.get("conversation_id"),
                                           "provenance": req.get("provenance") or {}})
        self._fire_attach()
        return {"seq": seq, "attached": None}

    # Phase 2/3 surface: truthful refusals with the §5 error codes.

    def _op_exec(self, req: Dict[str, Any]) -> Dict[str, Any]:
        if not self.entry.allow_exec:
            return _err("exec_disabled", "session was not started with --allow-exec")
        return _err("not_implemented", "exec handshake is not available in this build")

    _op_exec_status = _op_exec

    def _op_control_acquire(self, req: Dict[str, Any]) -> Dict[str, Any]:
        if self.entry.control_ceiling == "none":
            return _err("control_disabled", "session was not started with --allow-control")
        return _err("not_implemented", "control leases are not available in this build")

    _op_control_release = _op_control_acquire
    _op_send_line = _op_control_acquire
    _op_send_keys = _op_control_acquire
    _op_screen = _op_control_acquire
    _op_wait_idle = _op_control_acquire

    # -- subscribe --------------------------------------------------------------

    def _subscribe(self, conn: socket.socket, req: Dict[str, Any]) -> None:
        """Push journal records to this connection as they append.

        Replays from ``from_seq`` first so a subscriber never misses the
        window between its last read and the subscription taking effect.
        """
        try:
            from_seq = int(req.get("from_seq", 0) or 0)
        except (TypeError, ValueError):
            self._send(conn, _err("bad_seq", "from_seq must be an integer"))
            return
        send_lock = threading.Lock()
        closed = threading.Event()

        def push(record: Dict[str, Any]) -> None:
            if closed.is_set():
                return
            try:
                with send_lock:
                    self._send(conn, {"record": record})
            except OSError:
                closed.set()

        # Register first, then replay: anything appended during the replay
        # is delivered by the listener (possibly duplicated — clients
        # dedupe by seq).
        self.journal.add_listener(push)
        with self._sub_lock:
            self._subscribers += 1
        try:
            if from_seq > 0:
                for rec in self.reader.read(from_seq, 500)["records"]:
                    push(rec)
            with send_lock:
                self._send(conn, {"subscribed": True, "session_id": self.entry.session_id})
            conn.settimeout(1.0)
            while not self._stop.is_set() and not closed.is_set():
                try:
                    data = conn.recv(1024)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
        finally:
            closed.set()
            self.journal.remove_listener(push)
            with self._sub_lock:
                self._subscribers -= 1
