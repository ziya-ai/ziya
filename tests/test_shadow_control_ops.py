"""Socket-level control ops (§6.1) against a real headless session.

Headless sessions grant the lease implicitly (the spawning conversation
is the authority, §6.2), so these exercise acquire/heartbeat/status/
wait_idle/release end to end through the client without a terminal.
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


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def controllable(home):
    from app.shadow.pty_host import ShadowCore
    from app.shadow import registry
    # A shell that idles waiting for input, so the session stays live.
    core = ShadowCore(["sh", "-c", "echo ready; while read x; do echo got-$x; done"],
                      label="ctl", headless=True, control_ceiling="gated")
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    time.sleep(0.4)
    yield core
    try:
        core.terminate_child()
        t.join(timeout=5)
    except Exception:
        pass
    registry.load_session(core.entry.session_id)  # reap


def test_acquire_heartbeat_status_release(controllable):
    from app.shadow import client
    e = controllable.entry
    resp = client.request(e, "control_acquire", conversation_id="conv-1",
                          mode="line", restriction="gated", policy="builtin",
                          provenance={"conversation_id": "conv-1"})
    assert "lease_id" in resp
    assert resp["granted"] is True          # headless → implicit grant
    assert resp["restriction"] == "gated"
    lease_id = resp["lease_id"]

    hb = client.request(e, "control_heartbeat", lease_id=lease_id, conversation_id="conv-1")
    assert hb["ok"] is True and hb["granted"] is True

    st = client.request(e, "control_status")
    assert st["lease"]["conversation_id"] == "conv-1" and st["lease"]["granted"] is True
    # The lease_id is the bearer token; status is readable by any peer.
    assert "lease_id" not in st["lease"]

    rel = client.request(e, "control_release", lease_id=lease_id)
    assert rel["ok"] is True
    assert client.request(e, "control_status")["lease"] is None


def test_second_conversation_gets_lease_held(controllable):
    from app.shadow import client
    from app.shadow.client import ShadowError
    e = controllable.entry
    client.request(e, "control_acquire", conversation_id="conv-1", mode="line",
                   restriction="gated", policy="builtin")
    with pytest.raises(ShadowError) as ei:
        client.request(e, "control_acquire", conversation_id="conv-2", mode="line",
                       restriction="gated", policy="builtin")
    assert ei.value.code == "lease_held"


def test_screen_mode_refused(controllable):
    from app.shadow import client
    from app.shadow.client import ShadowError
    with pytest.raises(ShadowError) as ei:
        client.request(controllable.entry, "control_acquire", conversation_id="c",
                       mode="screen", restriction="gated")
    assert ei.value.code == "not_implemented"


def test_send_line_without_lease_is_refused(controllable):
    # send_line is implemented (step 3); with no matching lease it must
    # refuse with no_lease, never write.
    from app.shadow import client
    from app.shadow.client import ShadowError
    with pytest.raises(ShadowError) as ei:
        client.request(controllable.entry, "send_line", lease_id="x", text="ls")
    assert ei.value.code == "no_lease"


def test_wait_idle_resolves_on_quiescence(controllable):
    from app.shadow import client
    # The child printed "ready" at startup and is now blocked on read → idle.
    resp = client.request(controllable.entry, "wait_idle", quiet_ms=200,
                          timeout_ms=4000, timeout=8.0)
    assert resp["idle"] is True


def test_control_disabled_when_ceiling_none(home):
    from app.shadow.pty_host import ShadowCore
    from app.shadow import client, registry
    from app.shadow.client import ShadowError
    core = ShadowCore(["sh", "-c", "sleep 5"], label="noctl", headless=True)  # ceiling none
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start(); time.sleep(0.3)
    try:
        with pytest.raises(ShadowError) as ei:
            client.request(core.entry, "control_acquire", conversation_id="c",
                           mode="line", restriction="gated")
        assert ei.value.code == "control_disabled"
    finally:
        core.terminate_child(); t.join(timeout=5)
