"""send_line: the first PTY write under a control lease (design doc §6.1a, §6.3).

End-to-end against a real headless child where practical, plus core-method
guard checks (altscreen / echo-off) that are awkward to force over the socket.
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


def _wait(pred, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _run_core(argv, ceiling="unrestricted"):
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(argv, label="ctl", headless=True, control_ceiling=ceiling)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    return core, t


def test_unrestricted_lease_runs_a_command_end_to_end(home):
    from app.shadow import client
    # A shell that reads lines and echoes a marker, so we can prove the
    # bytes send_line wrote actually reached the child.
    core, t = _run_core(["sh", "-c", "while read x; do echo GOT:$x; done"])
    try:
        sid = core.entry.session_id
        acq = client.request(core.entry, "control_acquire",
                             conversation_id="conv-1", mode="line",
                             restriction="unrestricted", policy="none")
        assert acq["granted"] is True  # headless → implicit grant
        lease = acq["lease_id"]
        resp = client.request(core.entry, "send_line", lease_id=lease, text="hello-world")
        assert resp["ok"] is True
        assert _wait(lambda: any(
            r["t"] == "output" and "GOT:hello-world" in r["text"]
            for r in client.tail(sid, 5)))
        # wait_idle resolves after the burst quiesces
        wi = client.request(core.entry, "wait_idle", quiet_ms=200, timeout_ms=4000)
        assert wi["idle"] is True
    finally:
        core.terminate_child()
        t.join(timeout=5)


def test_strict_denies_off_allowlist_and_does_not_write(home):
    from app.shadow import client
    core, t = _run_core(["sh", "-c", "while read x; do echo GOT:$x; done"])
    try:
        acq = client.request(core.entry, "control_acquire", conversation_id="c",
                             mode="line", restriction="strict", policy="builtin")
        assert acq["granted"] and acq["restriction"] == "strict"
        # Allowed under the builtin read-only set: runs.
        r = client.request(core.entry, "send_line", lease_id=acq["lease_id"], text="echo ok")
        assert r["ok"] is True
        assert _wait(lambda: any(r["t"] == "output" and "GOT:echo ok" in r["text"]
                                 for r in client.tail(core.entry.session_id, 5)))
        # Off the allowlist under strict: refused, never prompted, never written.
        with pytest.raises(client.ShadowError) as ei:
            client.request(core.entry, "send_line", lease_id=acq["lease_id"],
                           text="echo denied-marker > /tmp/nope")
        assert ei.value.code == "command_denied"
        time.sleep(0.3)
        assert not any(r["t"] == "output" and "denied-marker" in r["text"]
                       for r in client.tail(core.entry.session_id, 10))
    finally:
        core.terminate_child()
        t.join(timeout=5)


def test_send_line_requires_a_matching_granted_lease(home):
    from app.shadow import client
    core, t = _run_core(["sh", "-c", "while read x; do :; done"])
    try:
        with pytest.raises(client.ShadowError) as ei:
            client.request(core.entry, "send_line", lease_id="deadbeef", text="ls")
        assert ei.value.code == "no_lease"
    finally:
        core.terminate_child()
        t.join(timeout=5)


def test_control_disabled_when_ceiling_none(home):
    from app.shadow import client
    core, t = _run_core(["sh", "-c", "while read x; do :; done"], ceiling="none")
    try:
        with pytest.raises(client.ShadowError) as ei:
            client.request(core.entry, "control_acquire", conversation_id="c",
                          mode="line", restriction="gated", policy="none")
        assert ei.value.code == "control_disabled"
    finally:
        core.terminate_child()
        t.join(timeout=5)


# --- core-method guard checks (altscreen / echo-off) -------------------------

def test_send_line_refuses_in_altscreen(home):
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(["sh", "-c", "sleep 5"], label="g", headless=True,
                      control_ceiling="unrestricted")
    core.spawn()
    try:
        core.segmenter.altscreen = True
        assert core.send_line_to_child("ls")["error"] == "altscreen_active"
    finally:
        core.terminate_child()
        core.close()


def test_send_line_refuses_when_echo_off(home):
    from app.shadow.pty_host import ShadowCore
    # Child disables echo (as a password prompt does) and holds it.
    core = ShadowCore(["sh", "-c", "stty -echo; sleep 5"], label="g", headless=True,
                      control_ceiling="unrestricted")
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    try:
        assert _wait(lambda: core.send_line_to_child("secret").get("error") == "echo_off",
                     timeout=4.0)
    finally:
        core.terminate_child()
        t.join(timeout=5)


def test_gated_confirm_required_does_not_write(home):
    """Under a *named* policy, a mutating command in gated mode returns
    confirm_required and writes nothing (the banner/keystroke is phase 4)."""
    import json
    from app.shadow import client, policy
    # Write a named policy allowing only read-only ls/echo.
    d = policy.policies_dir()
    (d / "ro.json").write_text(json.dumps({"allowedCommands": ["ls", "echo", "cat"]}))
    core, t = _run_core(["sh", "-c", "while read x; do echo GOT:$x; done"], ceiling="gated")
    try:
        acq = client.request(core.entry, "control_acquire", conversation_id="c",
                             mode="line", restriction="gated", policy="named:ro")
        # 'rm' is destructive → NOT_ALLOWED → gated → confirm_required
        with pytest.raises(client.ShadowError) as ei:
            client.request(core.entry, "send_line", lease_id=acq["lease_id"],
                          text="rm -rf /tmp/x")
        assert ei.value.code == "confirm_required"
        # and the child never saw it
        time.sleep(0.4)
        assert not any("GOT:rm" in r.get("text", "")
                       for r in client.tail(core.entry.session_id, 5))
        # a read-only command under the same lease runs
        r = client.request(core.entry, "send_line", lease_id=acq["lease_id"], text="echo fine")
        assert r["ok"] is True
    finally:
        core.terminate_child()
        t.join(timeout=5)
