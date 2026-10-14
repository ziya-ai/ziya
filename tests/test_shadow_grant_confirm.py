"""Line control at an interactive shadow terminal: the ceiling is the grant,
gated commands are confirmed or denied, and the menu revokes (§6.1, §6.3).

``--allow-control`` (or the [c] menu key) is set by the human at the
terminal being delegated.  That IS the grant: a chat's control_acquire is
granted on the spot and the terminal announces who took control and how to
revoke.  There is no second banner to press ``g`` at — pressing ``y`` there
used to *refuse*, and under ``gated`` the first off-list command then asked
a third time.  Per-command confirms remain exactly what ``gated`` means.

The frontend is driven in-process on pipe fds (deterministic keystrokes)
against a real ShadowCore child.  The terminal-facing overlay bytes are
captured from the stdout pipe so the banners themselves are asserted.
"""
import os
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _wait(pred, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


class _Screen:
    """Drain the frontend's stdout pipe on a thread so writes never block."""

    def __init__(self, r):
        self.buf = b""
        self._r = r
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        while True:
            try:
                chunk = os.read(self._r, 65536)
            except OSError:
                return
            if not chunk:
                return
            self.buf += chunk

    def has(self, needle: str) -> bool:
        return needle.encode() in self.buf


@pytest.fixture
def interactive(home):
    from app.shadow.pty_host import ShadowCore, InteractiveFrontend
    core = ShadowCore(["sh", "-c", "while read l; do eval \"$l\"; done"],
                      label="ui", control_ceiling="gated")
    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    fe = InteractiveFrontend(core, stdin_fd=in_r, stdout_fd=out_w)
    core.spawn()
    screen = _Screen(out_r)
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": in_r}, daemon=True)
    t.start()
    time.sleep(0.4)
    yield core, fe, screen, in_w
    core.terminate_child()
    t.join(timeout=5)
    for fd in (in_w, out_w):
        try:
            os.close(fd)
        except OSError:
            pass


def _send_in_thread(sid, lease_id, text):
    """send_line under gated blocks on the confirm banner, so callers use this."""
    from app.shadow import client
    box = {}

    def run():
        try:
            box["resp"] = client.send_line(sid, lease_id, text)
        except client.ShadowError as e:
            box["err"] = e.code
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return box, th


def test_acquire_is_granted_without_a_keystroke_and_announced(interactive):
    """The user's complaint: --allow-control was given, yet a chat's request
    still asked for [g] (and `y` refused).  Now the ceiling is the grant: the
    lease is active on acquire, the terminal says who has control and how to
    revoke, and the human's keystrokes keep going to the shell."""
    from app.shadow import client
    core, fe, screen, in_w = interactive
    sid = core.entry.session_id

    resp = client.control_acquire(sid, "conv-A", restriction="gated", policy="builtin")
    assert resp["granted"] is True
    assert "pending_grant" not in resp
    assert _wait(lambda: screen.has("chat conv-A now has control of this terminal"))
    assert screen.has("C-x C-z, r to revoke")
    assert fe._mode is None, "no grant banner may capture the keyboard"
    assert client.control_status(sid)["lease"]["granted"] is True

    # An allowed command runs at once -- nothing to press first.
    assert client.send_line(sid, resp["lease_id"], "echo granted-run")["ok"]

    # Positive half of "no keyboard capture": a line the human types reaches
    # the shell instead of being read as an answer to a banner.
    seen = []
    core.journal.add_listener(seen.append)
    os.write(in_w, b"echo typed-by-human\r")
    assert _wait(lambda: any(r.get("t") == "output" and "typed-by-human" in r.get("text", "")
                             for r in seen))
    assert not screen.has("control request denied")


def test_reacquire_supersedes_and_the_old_lease_id_is_dead(interactive):
    """A re-acquire by the same conversation (e.g. to change restriction) is
    granted the same way; the superseded bearer token cannot send."""
    from app.shadow import client
    core, fe, screen, in_w = interactive
    sid = core.entry.session_id
    a = client.control_acquire(sid, "conv-S", restriction="gated", policy="builtin")
    b = client.control_acquire(sid, "conv-S", restriction="gated", policy="builtin")
    assert a["lease_id"] != b["lease_id"] and b["granted"] is True
    with pytest.raises(client.ShadowError) as ei:
        client.send_line(sid, a["lease_id"], "echo stale")
    assert ei.value.code == "no_lease"
    assert client.send_line(sid, b["lease_id"], "echo fresh")["ok"]


def test_confirm_deny_and_revoke_flow(interactive):
    from app.shadow import client
    core, fe, screen, in_w = interactive
    sid = core.entry.session_id
    lease_id = client.control_acquire(sid, "conv-A", restriction="gated",
                                      policy="builtin")["lease_id"]
    assert _wait(lambda: screen.has("now has control"))

    # 1. A not-allowed command under gated blocks on the confirm banner; 'y' runs it.
    box, th = _send_in_thread(sid, lease_id, "tee /tmp/ui-approved")
    assert _wait(lambda: fe._mode == "confirm" and screen.has("asks to run:  tee /tmp/ui-approved"))
    os.write(in_w, b"y")
    th.join(timeout=10)
    assert box.get("resp", {}).get("ok") is True, box
    assert screen.has("approved")

    # 2. Same, but 'n' denies: command_denied, nothing written.
    box, th = _send_in_thread(sid, lease_id, "tee /tmp/ui-denied")
    assert _wait(lambda: fe._mode == "confirm")
    os.write(in_w, b"n")
    th.join(timeout=10)
    assert box.get("err") == "command_denied", box
    recs = client.request(core.entry, "read", from_seq=1, max_records=500)["records"]
    assert not any(r.get("t") == "cmd" and "ui-denied" in r.get("text", "") for r in recs)
    assert any(r.get("t") == "meta" and r.get("event") == "control_confirm"
               and r["data"]["outcome"] == "denied" for r in recs)

    # 3. Menu shows the lease and [r] revokes it.
    os.write(in_w, b"\x18\x1a")
    assert _wait(lambda: fe._mode == "menu" and screen.has("[r] revoke control"))
    assert screen.has("control: chat conv-A (gated, builtin, active)")
    os.write(in_w, b"r")
    assert _wait(lambda: screen.has("control lease revoked"))
    with pytest.raises(client.ShadowError) as ei:
        client.send_line(sid, lease_id, "ls")
    assert ei.value.code == "no_lease"
    assert client.control_status(sid)["lease"] is None


def test_confirm_times_out_to_deny(interactive, monkeypatch):
    from app.shadow import client, sock_server
    monkeypatch.setattr(sock_server, "CONFIRM_TIMEOUT_S", 0.5)
    core, fe, screen, in_w = interactive
    sid = core.entry.session_id
    resp = client.control_acquire(sid, "conv-C", restriction="gated", policy="builtin")
    assert _wait(lambda: screen.has("now has control"))
    with pytest.raises(client.ShadowError) as ei:
        client.send_line(sid, resp["lease_id"], "tee /tmp/never")   # nobody presses a key
    assert ei.value.code == "command_denied" and "no confirmation" in ei.value.msg
    # The timeout releases the banner's keystroke mode (audit C18), so a
    # late "y" is ordinary input to the shell — not an answer to anything.
    assert _wait(lambda: fe._mode is None and screen.has("no confirmation"))
    os.write(in_w, b"y")
    time.sleep(0.3)
    assert not screen.has("nothing awaiting confirmation")


def test_headless_gated_still_refuses_without_a_terminal(home):
    """No frontend → nobody to ask → confirm_required, never a blind run."""
    from app.shadow import client
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(["sh", "-c", "read x"], label="hl", headless=True, control_ceiling="gated")
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    try:
        resp = client.control_acquire(core.entry.session_id, "conv-H", restriction="gated",
                                      policy="builtin")
        assert resp["granted"] is True
        with pytest.raises(client.ShadowError) as ei:
            client.send_line(core.entry.session_id, resp["lease_id"], "tee /tmp/x")
        assert ei.value.code == "confirm_required"
    finally:
        core.terminate_child()
        t.join(timeout=5)
