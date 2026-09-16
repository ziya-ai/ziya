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
import secrets
import socket
import stat
import struct
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

from app.shadow import SHADOW_PROTOCOL_VERSION
from app.shadow.journal import JournalReader, JournalWriter
from app.shadow.lease import LeaseSlot, HEARTBEAT_TIMEOUT_S as _HEARTBEAT_TIMEOUT_S

MAX_REQUEST_BYTES = 64 * 1024
MAX_COMMENT_CHARS = 2000
MAX_LABEL_CHARS = 120
# A gated command waits this long for the human's keystroke, then is denied.
CONFIRM_TIMEOUT_S = 60.0


def _err(code: str, msg: str) -> Dict[str, Any]:
    return {"error": {"code": code, "msg": msg}}


def _now() -> float:
    return time.monotonic()


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
                 on_attach: Optional[Callable[[], None]] = None,
                 on_control: Optional[Callable[[object], None]] = None,
                 on_send_line: Optional[Callable[[str], Dict[str, Any]]] = None,
                 on_confirm: Optional[Callable[[str, str, str], None]] = None,
                 on_confirm_timeout: Optional[Callable[[str], None]] = None):
        self.entry = entry
        self.journal = journal
        self.reader = JournalReader(entry.journal)
        self.on_comment = on_comment
        self.on_meta_change = on_meta_change
        self.on_attach = on_attach
        self.on_control = on_control
        self.on_send_line = on_send_line
        self.on_confirm = on_confirm
        self.on_confirm_timeout = on_confirm_timeout
        # Gated-lease per-command confirm (§6.3): at most one outstanding.
        # {"text", "reason", "lease_id", "event", "decision"}
        self._confirm: Optional[Dict[str, Any]] = None
        self._confirm_lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._subscribers = 0
        self._sub_lock = threading.Lock()
        # Single control lease per session (§6.1).  Guarded by _lease_lock;
        # the state machine itself is pure (app/shadow/lease.py).
        self._leases = LeaseSlot()
        self._lease_lock = threading.Lock()

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
        # A comment is the chat speaking to the terminal, which is exactly
        # what a shadow-initiated ask (§8) was waiting for.  Clearing the
        # flag here is the phase-2 answer to "answered asks stay pending":
        # once the chat has replied on the canvas, the human is no longer
        # waiting.  Journaled + reflected into the registry so shadow_list
        # and the context tag stop reporting a stale question.
        cleared_ask = False
        if self.entry.pending_ask:
            self.entry.pending_ask = False
            self.entry.save()
            cleared_ask = True
            if self.on_meta_change is not None:
                try:
                    self.on_meta_change()
                except Exception:  # noqa: BLE001
                    pass
        return {"seq": seq, "rendered": rendered, "cleared_ask": cleared_ask}

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
        """Clear the attachment.  A conversation may only detach itself.

        There is no anonymous force path over the socket: the shadow side
        clears ``entry.attached`` directly when it needs to.  Detaching a
        session nobody is attached to is a no-op success.
        """
        cur = self.entry.attached or {}
        conv = req.get("conversation_id")
        if not isinstance(conv, str) or not conv.strip():
            return _err("bad_request", "conversation_id is required")
        conv = conv.strip()[:64]
        if cur and cur.get("conversation_id") != conv:
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
        """Request a control lease (§6.1).  Line mode only in this build.

        The lease is granted immediately for a headless session (the
        spawning conversation is the authority, §6.2); for an interactive
        session it is created *pending* and the human grants it with a
        keystroke at the terminal (a later phase wires that key).  Screen
        mode is refused until phase 4.
        """
        if self.entry.control_ceiling == "none":
            return _err("control_disabled", "session was not started with --allow-control")
        conv = req.get("conversation_id")
        if not isinstance(conv, str) or not conv.strip():
            return _err("bad_request", "conversation_id is required")
        conv = conv.strip()[:64]
        mode = req.get("mode", "line")
        if mode == "screen":
            return _err("not_implemented", "screen-mode control is not available in this build")
        if mode != "line":
            return _err("bad_request", f"invalid mode: {mode!r}")
        restriction = req.get("restriction", "gated")
        if restriction not in ("supervised", "strict", "gated", "unrestricted"):
            return _err("bad_request", f"invalid restriction: {restriction!r}")
        # Fail closed: an absent policy axis is the shipped read-only set,
        # never "everything".
        policy_set = req.get("policy") or "builtin"
        if not isinstance(policy_set, str):
            return _err("bad_request", "policy must be a string")
        # An agent-spawned session has no human at a banner, so a lease on
        # it may only ever be strict (§6.3): the policy is the whole control.
        if self.entry.spawned_by:
            restriction = "strict"
        # Resolve the policy now, not on the first send: the grant banner
        # must name something that exists, and a typo must not fail closed
        # only after the human has already granted.
        try:
            from app.shadow.policy import resolve_policy, clamp_restriction_for
            effective = clamp_restriction_for(restriction, self.entry.control_ceiling)
            resolve_policy(policy_set, effective or restriction)
        except (ValueError, NotImplementedError, FileNotFoundError, OSError) as e:
            return _err("bad_request", f"policy {policy_set!r}: {e}")
        # ``none`` disables the policy axis entirely, which is only honest
        # when the mode axis is already "run everything".  Under strict or
        # gated it would silently switch the mode table off while the
        # banner still says strict/gated — and on a spawned session
        # (clamped strict, implicit grant) it would be total control with
        # no human anywhere.
        if policy_set == "none" and effective != "unrestricted":
            return _err("policy_requires_unrestricted",
                        "policy 'none' is only available with an effective "
                        "restriction of 'unrestricted'")
        with self._lease_lock:
            lease, err = self._leases.acquire(
                conv, "line", restriction, policy_set, self.entry.control_ceiling,
                implicit_grant=bool(self.entry.headless))
            if err is not None:
                return _err(err, {
                    "lease_held": "another conversation holds the control lease",
                    "control_disabled": "session ceiling permits no control lease",
                }.get(err, err))
        self.journal.meta("control_acquire", {
            "lease_id": lease.lease_id, "conversation_id": conv, "mode": "line",
            "restriction": lease.restriction, "policy": policy_set,
            "granted": lease.granted, "provenance": req.get("provenance") or {}})
        if self.on_control is not None:
            try:
                self.on_control(lease)
            except Exception:  # noqa: BLE001 — a display error must not fail the op
                pass
        return {"lease_id": lease.lease_id, "restriction": lease.restriction,
                "mode": "line", "policy": policy_set, "granted": lease.granted,
                "heartbeat_timeout_s": _HEARTBEAT_TIMEOUT_S,
                "pending_grant": (not lease.granted)}

    def _op_control_heartbeat(self, req: Dict[str, Any]) -> Dict[str, Any]:
        lease_id = req.get("lease_id")
        conv = str(req.get("conversation_id") or "").strip()[:64]  # as in acquire
        with self._lease_lock:
            ok = self._leases.heartbeat(str(lease_id or ""), conv)
            ls = self._leases.current(_now())
        if not ok:
            return _err("no_lease", "no live lease matches this id/conversation")
        return {"ok": True, "granted": ls.granted if ls else False,
                "paused": ls.paused if ls else False}

    def _op_control_release(self, req: Dict[str, Any]) -> Dict[str, Any]:
        lease_id = str(req.get("lease_id") or "")
        with self._lease_lock:
            ok = self._leases.release(lease_id)
        if ok:
            self.journal.meta("control_release", {
                "lease_id": lease_id, "provenance": req.get("provenance") or {}})
            if self.on_control is not None:
                try:
                    self.on_control(None)
                except Exception:  # noqa: BLE001
                    pass
        return {"ok": ok}

    def _op_control_status(self, req: Dict[str, Any]) -> Dict[str, Any]:
        with self._lease_lock:
            ls = self._leases.current(_now())
        if ls is None:
            return {"lease": None}
        # The lease_id is the bearer token for send_line/release; status is
        # readable by any socket peer, so it is not echoed here.
        return {"lease": {"conversation_id": ls.conversation_id,
                          "mode": ls.mode, "restriction": ls.restriction,
                          "policy": ls.policy_set, "granted": ls.granted,
                          "paused": ls.paused}}

    def grant_active_lease(self, expected_lease_id: Optional[str] = None) -> Optional[str]:
        """Human grant at the terminal (called by the frontend keystroke).

        Returns the granted lease_id, or None if there is nothing pending.
        Kept as a method (not a socket op) so a lease can only be granted
        from the physical terminal, never over the socket (§6.1 authority).

        ``expected_lease_id`` is the lease the banner named; a re-acquire
        (possibly looser) that superseded it between banner and keystroke
        is not what the human granted, so it stays pending.
        """
        with self._lease_lock:
            ls = self._leases.current(_now())
            if ls is None:
                return None
            if expected_lease_id is not None and ls.lease_id != expected_lease_id:
                return None
            self._leases.grant(ls.lease_id)
        self.journal.meta("control_grant", {"lease_id": ls.lease_id,
                                             "conversation_id": ls.conversation_id})
        return ls.lease_id

    def revoke_lease(self) -> bool:
        """Shadow-side revoke (menu key)."""
        with self._lease_lock:
            ok = self._leases.revoke()
        if ok:
            self.journal.meta("control_revoke", {})
            if self.on_control is not None:
                try:
                    self.on_control(None)
                except Exception:  # noqa: BLE001
                    pass
        return ok

    def current_lease(self):
        """The live lease (pending or active) or None — for the frontend."""
        with self._lease_lock:
            return self._leases.current(_now())

    def pending_confirm(self) -> Optional[Dict[str, Any]]:
        with self._confirm_lock:
            c = self._confirm
            return ({"text": c["text"], "reason": c["reason"],
                     "confirm_id": c["confirm_id"]} if c else None)

    def resolve_confirm(self, approve: bool, confirm_id: Optional[str] = None) -> bool:
        """Human keystroke at the terminal decides the outstanding gated
        command.  Never reachable over the socket (§6.1 authority).

        ``confirm_id`` names the banner the keystroke answers.  If the
        command it was shown for has since timed out and another took the
        slot, the stale keystroke must not approve the newcomer.
        """
        with self._confirm_lock:
            c = self._confirm
            if c is None or c["decision"] is not None:
                return False
            if confirm_id is not None and c["confirm_id"] != confirm_id:
                return False
            c["decision"] = bool(approve)
            c["event"].set()
        return True

    def _op_wait_idle(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve when journal output quiesces for ``quiet_ms`` (§6.1a).

        Journal-quiescence based (no screen state): the control loop is
        send_line -> wait_idle -> tail.  Bounded by ``timeout_ms`` so a
        chatty stream cannot hang the caller.
        """
        try:
            quiet_ms = int(req.get("quiet_ms", 800))
        except (TypeError, ValueError):
            return _err("bad_request", "quiet_ms must be an integer")
        quiet_ms = max(50, min(quiet_ms, 5000))
        try:
            timeout_ms = int(req.get("timeout_ms", 15000))
        except (TypeError, ValueError):
            timeout_ms = 15000
        timeout_ms = max(quiet_ms, min(timeout_ms, 120000))
        deadline = _now() + timeout_ms / 1000.0
        quiet_s = quiet_ms / 1000.0
        last_seq = self.reader.bounds().get("tail_seq")
        last_change = _now()
        while _now() < deadline:
            time.sleep(min(0.05, quiet_s / 2))
            seq = self.reader.bounds().get("tail_seq")
            now = _now()
            if seq != last_seq:
                last_seq, last_change = seq, now
                continue
            if (now - last_change) >= quiet_s:
                return {"idle": True, "tail_seq": seq}
        return {"idle": False, "tail_seq": last_seq, "timed_out": True}

    def _op_send_line(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Run one command under an active line-control lease (§6.1a, §6.3).

        Enforcement is here (any same-UID process can speak the socket):
        validate the lease, classify the command against the lease's
        policy, apply the mode table, and only then hand the write to the
        core.  Guards that need live PTY state (altscreen / echo-off) are
        checked by the core callback.
        """
        if self.entry.control_ceiling == "none":
            return _err("control_disabled", "session was not started with --allow-control")
        text = req.get("text")
        if not isinstance(text, str) or not text.strip():
            return _err("bad_request", "text is required")
        # Exactly one printable line, whatever the policy says: an embedded
        # CR/LF is a second command, ESC drives the line editor, ^C/^D
        # interrupt or hang up the child, TAB completes.  Checked before the
        # policy so even unrestricted+none cannot smuggle a second line.
        from app.shadow.policy import has_control_chars
        if has_control_chars(text.rstrip("\r\n")):
            return _err("bad_request",
                        "text must be a single line without control characters")
        lease_id = str(req.get("lease_id") or "")
        with self._lease_lock:
            ls = self._leases.current(_now())
            if ls is None or ls.lease_id != lease_id:
                return _err("no_lease", "no live lease matches this id")
            if not ls.granted:
                return _err("lease_pending", "lease has not been granted at the terminal")
            if ls.paused:
                return _err("control_paused", "control is paused; the human has the terminal")
            restriction, policy_set = ls.restriction, ls.policy_set

        # Policy verdict (§6.3).  A verdict error must never fall open.
        try:
            from app.shadow.policy import resolve_policy
            verdict, reason = resolve_policy(policy_set, restriction).decide(text)
        except Exception as e:  # noqa: BLE001
            self.journal.meta("control_denied", {"lease_id": lease_id,
                              "text": text[:200], "reason": f"policy error: {e}"})
            return _err("policy_error", str(e))
        from app.shadow.policy import RUN, CONFIRM, DENY
        if verdict == DENY:
            self.journal.meta("control_denied", {"lease_id": lease_id,
                              "text": text[:200], "reason": reason})
            return _err("command_denied", reason)
        if verdict == CONFIRM:
            # gated: one keystroke at the shadow terminal decides (§6.3).
            # With no terminal to ask (headless), refuse — never type a
            # not-allowed command without a human's confirm.
            if self.on_confirm is None or self.entry.headless:
                self.journal.meta("control_confirm_required", {"lease_id": lease_id,
                                  "text": text[:200], "reason": reason})
                return _err("confirm_required", reason)
            with self._confirm_lock:
                if self._confirm is not None:
                    return _err("confirm_pending",
                                "another command is awaiting confirmation at the terminal")
                slot = {"text": text, "reason": reason, "lease_id": lease_id,
                        "confirm_id": secrets.token_hex(4),
                        "event": threading.Event(), "decision": None}
                self._confirm = slot
            self.journal.meta("control_confirm_request", {"lease_id": lease_id,
                              "conversation_id": ls.conversation_id,
                              "text": text[:200], "reason": reason})
            try:
                try:
                    self.on_confirm(text, reason, ls.conversation_id, slot["confirm_id"])
                except Exception:  # noqa: BLE001 — banner failure = deny, below
                    pass
                slot["event"].wait(CONFIRM_TIMEOUT_S)
                approved = slot["decision"] is True
                outcome = ("approved" if approved else
                           "denied" if slot["decision"] is False else "timeout")
            finally:
                with self._confirm_lock:
                    self._confirm = None
            self.journal.meta("control_confirm", {"lease_id": lease_id, "text": text[:200],
                              "outcome": outcome})
            if outcome == "timeout" and self.on_confirm_timeout is not None:
                # The banner is still on screen and the frontend is still in
                # confirm mode; tell it so the human's next key is not eaten.
                try:
                    self.on_confirm_timeout(slot["confirm_id"])
                except Exception:  # noqa: BLE001
                    pass
            if not approved:
                return _err("command_denied",
                            f"{'denied' if outcome == 'denied' else 'no confirmation'} "
                            f"at the terminal: {reason}")
            # The lease may have expired, been released or been superseded
            # while the command sat at the banner; a "y" that arrives after
            # that must not type under a dead lease.
            with self._lease_lock:
                ls2 = self._leases.current(_now())
                if ls2 is None or ls2.lease_id != lease_id or not ls2.granted:
                    self.journal.meta("control_denied", {"lease_id": lease_id,
                                      "text": text[:200],
                                      "reason": "lease ended during confirmation"})
                    return _err("no_lease", "lease ended while awaiting confirmation")

        if self.on_send_line is None:
            return _err("no_child", "session has no writable child (headless core not wired)")
        result = self.on_send_line(text)
        if "error" in result:
            self.journal.meta("control_send_blocked", {"lease_id": lease_id,
                              "reason": result["error"]})
            return _err(result["error"], {
                "altscreen_active": "child is in a full-screen program; input refused",
                "echo_off": "child has echo off (password prompt); input refused",
                "no_child": "session has no live child",
                "terminal_busy": f"the human is using the terminal ({result.get('detail', 'busy')}); "
                                 "try again shortly",
            }.get(result["error"], result["error"]))
        self.journal.meta("control_send", {"lease_id": lease_id,
                          "conversation_id": ls.conversation_id, "text": text[:500],
                          "restriction": restriction, "policy": policy_set})
        return {"ok": True, "sent_at_seq": result.get("sent_at_seq"),
                "cmd_seq": result.get("cmd_seq")}

    # send_keys / screen (screen-mode control) land in phase 4.
    def _op_send_keys(self, req: Dict[str, Any]) -> Dict[str, Any]:
        if self.entry.control_ceiling == "none":
            return _err("control_disabled", "session was not started with --allow-control")
        return _err("not_implemented", "screen-mode send_keys lands in phase 4")

    _op_screen = _op_send_keys

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
