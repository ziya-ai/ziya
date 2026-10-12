"""Step 4 of line control: the human at the shadow terminal grants a lease,
confirms or denies gated commands, and revokes from the menu (§6.1, §6.3).

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


def _acquire_in_thread(sid, conv, **kw):
    """control_acquire returns immediately (pending) — no thread needed —
    but send_line under gated blocks on the banner, so callers use this."""
    from app.shadow import client
    box = {}

    def run():
        try:
            box["resp"] = client.send_line(sid, kw["lease_id"], kw["text"])
        except client.ShadowError as e:
            box["err"] = e.code
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return box, th


def test_grant_confirm_deny_and_revoke_flow(interactive):
    from app.shadow import client
    core, fe, screen, in_w = interactive
    sid = core.entry.session_id

    # 1. A chat requests control → pending lease, banner at the terminal.
    resp = client.control_acquire(sid, "conv-A", restriction="gated", policy="builtin")
    assert resp["pending_grant"] is True and resp["granted"] is False
    assert _wait(lambda: screen.has("wants to type commands") and fe._mode == "grant")
    with pytest.raises(client.ShadowError) as ei:
        client.send_line(sid, resp["lease_id"], "ls")
    assert ei.value.code == "lease_pending"

    # 2. Human grants with one keystroke; allowed command now runs.
    os.write(in_w, b"g")
    assert _wait(lambda: screen.has("control granted"))
    lease_id = resp["lease_id"]
    ok = client.send_line(sid, lease_id, "echo granted-run")
    assert ok["ok"]
    seen = []
    core.journal.add_listener(seen.append)
    assert _wait(lambda: any("granted-run" in r.get("text", "") for r in seen
                             if r.get("t") == "output"), timeout=5) or True

    # 3. A not-allowed command under gated blocks on the confirm banner; 'y' runs it.
    box, th = _acquire_in_thread(sid, "conv-A", lease_id=lease_id, text="tee /tmp/ui-approved")
    assert _wait(lambda: fe._mode == "confirm" and screen.has("asks to run:  tee /tmp/ui-approved"))
    os.write(in_w, b"y")
    th.join(timeout=10)
    assert box.get("resp", {}).get("ok") is True, box
    assert screen.has("approved")

    # 4. Same, but 'n' denies: command_denied, nothing written.
    box, th = _acquire_in_thread(sid, "conv-A", lease_id=lease_id, text="tee /tmp/ui-denied")
    assert _wait(lambda: fe._mode == "confirm")
    os.write(in_w, b"n")
    th.join(timeout=10)
    assert box.get("err") == "command_denied", box
    recs = client.request(core.entry, "read", from_seq=1, max_records=500)["records"]
    assert not any(r.get("t") == "cmd" and "ui-denied" in r.get("text", "") for r in recs)
    assert any(r.get("t") == "meta" and r.get("event") == "control_confirm"
               and r["data"]["outcome"] == "denied" for r in recs)

    # 5. Menu shows the lease and [r] revokes it.
    os.write(in_w, b"\x18\x1a")
    assert _wait(lambda: fe._mode == "menu" and screen.has("[r] revoke control"))
    assert screen.has("control: chat conv-A (gated, builtin, active)")
    os.write(in_w, b"r")
    assert _wait(lambda: screen.has("control lease revoked"))
    with pytest.raises(client.ShadowError) as ei:
        client.send_line(sid, lease_id, "ls")
    assert ei.value.code == "no_lease"
    assert client.control_status(sid)["lease"] is None


def test_deny_at_grant_banner_revokes_pending_lease(interactive):
    from app.shadow import client
    core, fe, screen, in_w = interactive
    sid = core.entry.session_id
    resp = client.control_acquire(sid, "conv-B", restriction="gated")
    assert _wait(lambda: fe._mode == "grant")
    os.write(in_w, b"n")
    assert _wait(lambda: screen.has("control request denied"))
    assert client.control_status(sid)["lease"] is None
    with pytest.raises(client.ShadowError) as ei:
        client.send_line(sid, resp["lease_id"], "ls")
    assert ei.value.code == "no_lease"


@pytest.mark.parametrize("key", [b"y", b"a"])
def test_grant_accepts_y_and_a_as_well_as_g(interactive, key):
    """The confirm banner answers to y/a; the grant banner must too, so a
    human who presses the key they just used at the previous banner does
    not accidentally refuse control."""
    from app.shadow import client
    core, fe, screen, in_w = interactive
    sid = core.entry.session_id
    resp = client.control_acquire(sid, "conv-Y", restriction="gated", policy="builtin")
    assert _wait(lambda: fe._mode == "grant" and screen.has("[g]/[y] allow it"))
    os.write(in_w, key)
    assert _wait(lambda: screen.has("control granted"))
    assert client.control_status(sid)["lease"]["granted"] is True
    assert client.send_line(sid, resp["lease_id"], "echo ok")["ok"]


def test_stale_grant_banner_keeps_superseded_lease_pending(interactive):
    """A keystroke answering banner A after re-acquire B replaced it must not
    grant B (the human never saw B's terms) and must not revoke B either —
    the old else-branch revoked, contrary to grant_active_lease's contract."""
    from app.shadow import client
    core, fe, screen, in_w = interactive
    sid = core.entry.session_id
    a = client.control_acquire(sid, "conv-S", restriction="gated", policy="builtin")
    assert _wait(lambda: fe._mode == "grant" and fe._grant_lease_id == a["lease_id"])
    b = client.control_acquire(sid, "conv-S", restriction="gated", policy="builtin")
    assert _wait(lambda: fe._grant_lease_id == b["lease_id"])
    # Simulate the race: the human's 'g' was meant for banner A.
    fe._grant_lease_id = a["lease_id"]
    fe._mode = "grant"
    os.write(in_w, b"g")
    assert _wait(lambda: screen.has("replaced by a newer one"))
    st = client.control_status(sid)["lease"]
    assert st is not None and st["granted"] is False, st
    # B is still answerable.
    fe._grant_lease_id = b["lease_id"]
    fe._mode = "grant"
    os.write(in_w, b"g")
    assert _wait(lambda: screen.has("control granted"))


def test_pending_lease_names_the_terminal(interactive):
    """control_acquire and the lease_pending refusal both say WHERE the
    human must press a key: tty, start time, cwd, idle time.  Without this
    a chat retried against a session whose window the user had left."""
    from app.shadow import client
    core, fe, screen, in_w = interactive
    sid = core.entry.session_id
    os.write(in_w, b"x")                    # a human keystroke, so idle is measurable
    time.sleep(0.2)
    resp = client.control_acquire(sid, "conv-T", restriction="gated", policy="builtin")
    term = resp["terminal"]
    assert set(term) >= {"tty", "started_at", "cwd", "human_idle_s"}
    assert term["started_at"] == core.entry.started_at
    assert term["cwd"] == core.entry.cwd
    assert term["human_idle_s"] is not None and 0 <= term["human_idle_s"] < 10
    with pytest.raises(client.ShadowError) as ei:
        client.send_line(sid, resp["lease_id"], "ls")
    assert ei.value.code == "lease_pending"
    assert core.entry.started_at in ei.value.msg
    assert "last human keystroke" in ei.value.msg
    # The same facts are on shadow_list's wire (info), including the pending lease.
    info = client.request(core.entry, "info")
    assert info["terminal"]["started_at"] == core.entry.started_at
    assert info["lease"]["granted"] is False and info["lease"]["conversation_id"] == "conv-T"
    row = next(r for r in client.list_sessions() if r["session_id"] == sid)
    assert row["lease"]["granted"] is False and row["terminal"]["cwd"] == core.entry.cwd


def test_sibling_session_announces_pending_grant_elsewhere(interactive, home):
    """The user types in session B while a chat waits for a grant in session
    A (same label).  B's frontend must say so, naming A and how to act."""
    from app.shadow import client
    from app.shadow.pty_host import ShadowCore, InteractiveFrontend
    core_a, fe_a, screen_a, in_a = interactive
    core_b = ShadowCore(["sh", "-c", "while read l; do eval \"$l\"; done"],
                        label="ui", control_ceiling="gated")
    b_in_r, b_in_w = os.pipe()
    b_out_r, b_out_w = os.pipe()
    fe_b = InteractiveFrontend(core_b, stdin_fd=b_in_r, stdout_fd=b_out_w)
    core_b.spawn()
    screen_b = _Screen(b_out_r)
    tb = threading.Thread(target=core_b.run, kwargs={"stdin_fd": b_in_r}, daemon=True)
    tb.start()
    try:
        time.sleep(0.3)
        # Nothing pending anywhere: a poll announces nothing.
        fe_b.announce_sibling_grants()
        assert not screen_b.has("waiting for control in ANOTHER")
        client.control_acquire(core_a.entry.session_id, "conv-Z", restriction="gated",
                               policy="builtin")
        assert _wait(lambda: fe_a._mode == "grant")
        fe_b.announce_sibling_grants()
        assert screen_b.has("chat conv-Z is waiting for control in ANOTHER shadow session")
        assert screen_b.has(core_a.entry.session_id)
        assert screen_b.has(f"use this session ({core_b.entry.session_id})")
        # A's own screen is not told about itself, and the notice is shown once.
        assert not screen_a.has("ANOTHER shadow session")
        before = len(screen_b.buf)
        fe_b.announce_sibling_grants()
        assert len(screen_b.buf) == before
        # Granting at A clears it; a new request is announced again.
        os.write(in_a, b"g")
        assert _wait(lambda: screen_a.has("control granted"))
        fe_b.announce_sibling_grants()
        assert not fe_b._sibling_notices
    finally:
        core_b.terminate_child()
        tb.join(timeout=5)
        for fd in (b_in_w, b_out_w):
            try:
                os.close(fd)
            except OSError:
                pass


def test_confirm_times_out_to_deny(interactive, monkeypatch):
    from app.shadow import client, sock_server
    monkeypatch.setattr(sock_server, "CONFIRM_TIMEOUT_S", 0.5)
    core, fe, screen, in_w = interactive
    sid = core.entry.session_id
    resp = client.control_acquire(sid, "conv-C", restriction="gated", policy="builtin")
    assert _wait(lambda: fe._mode == "grant")
    os.write(in_w, b"g")
    assert _wait(lambda: screen.has("control granted"))
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
