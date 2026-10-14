"""Chat-side line-control tools driving a real headless session end to end.

Every session grants the lease on acquire — the session's control ceiling
is the human's grant (§6.1) — so the whole shadow_control -> shadow_send ->
shadow_release loop needs no terminal keystroke.  These run a real ``sh``
child in a PTY.
"""
import asyncio
import os
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")

CONV = "conv-control-test"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def spawned(home):
    """A live headless, controllable (unrestricted ceiling) session."""
    import threading
    from app.shadow.pty_host import ShadowCore
    core = ShadowCore(["sh", "-i"], label="ctl", headless=True,
                      control_ceiling="unrestricted")
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    time.sleep(0.6)
    yield core
    core.terminate_child()
    t.join(timeout=5)
    # tear down any keepalive threads the tools started
    from app.mcp.tools import shadow_tools
    for k in list(shadow_tools._LEASES):
        shadow_tools._forget_lease(k)


def _tools():
    from app.mcp.tools.shadow_tools import (
        ShadowControlTool, ShadowSendTool, ShadowReleaseTool,
    )
    return ShadowControlTool(), ShadowSendTool(), ShadowReleaseTool()


def test_control_send_release_loop(spawned):
    ctl, send, rel = _tools()
    sid = spawned.entry.session_id

    acq = _run(ctl.execute(session=sid, restriction="unrestricted", conversation_id=CONV))
    assert acq["ok"] and acq["granted"] is True and not acq.get("pending_grant")

    out = _run(send.execute(session=sid, line="echo control-loop-works", conversation_id=CONV))
    assert out["ok"], out
    assert "control-loop-works" in out["transcript"]

    r = _run(rel.execute(session=sid, conversation_id=CONV))
    assert r["ok"] and r["released"] is True

    # after release, send is refused
    out2 = _run(send.execute(session=sid, line="echo nope", conversation_id=CONV))
    assert out2["ok"] is False and out2["error"] == "no_lease"


def test_interactive_session_grants_on_acquire(home):
    """An interactive session started with --allow-control: the ceiling is
    the grant, so shadow_control is active at once (no pending state for the
    model to wait on) and shadow_send runs without anyone pressing a key."""
    import threading
    from app.shadow.pty_host import ShadowCore
    from app.mcp.tools import shadow_tools
    core = ShadowCore(["sh", "-c", "while read l; do eval \"$l\"; done"],
                      label="iv", control_ceiling="gated")
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    try:
        time.sleep(0.4)
        ctl, send, _ = _tools()
        sid = core.entry.session_id
        acq = _run(ctl.execute(session=sid, restriction="gated", conversation_id=CONV))
        assert acq["ok"] and acq["granted"] is True
        assert "pending_grant" not in acq
        assert acq["note"] == "Lease active — use shadow_send to run commands."
        out = _run(send.execute(session=sid, line="echo no-keystroke-needed", conversation_id=CONV))
        assert out["ok"], out
        assert "no-keystroke-needed" in out["transcript"]
    finally:
        core.terminate_child()
        t.join(timeout=5)
        for k in list(shadow_tools._LEASES):
            shadow_tools._forget_lease(k)


def test_send_without_control_is_refused(spawned):
    _, send, _ = _tools()
    out = _run(send.execute(session=spawned.entry.session_id, line="ls", conversation_id=CONV))
    assert out["ok"] is False and out["error"] == "no_lease"


def test_gated_lease_refuses_mutating_until_confirmed(spawned, home):
    """Under a gated lease with a real (named) policy, a read-only command
    flows but a mutating one (redirection, off-list) is refused with
    confirm_required — a headless session has nobody to ask, so here it must
    refuse rather than run.  (gated + policy 'none' is degenerate: 'none'
    allows everything, so nothing is ever classified mutating — §6.3.)"""
    import json
    from app.shadow import policy as pol
    (pol.policies_dir() / "gateddiag.json").write_text(
        json.dumps({"allowedCommands": ["echo", "cat", "ls", "true"]}))
    ctl, send, rel = _tools()
    sid = spawned.entry.session_id
    _run(ctl.execute(session=sid, restriction="gated", policy="named:gateddiag",
                     conversation_id=CONV))
    # read-only, on the allowlist → flows
    ok = _run(send.execute(session=sid, line="echo hi", conversation_id=CONV))
    assert ok["ok"] and "hi" in ok["transcript"]
    # a redirection is mutating (target unverifiable remotely) → confirm_required
    denied = _run(send.execute(session=sid, line="echo x > /tmp/ctl_probe_$$", conversation_id=CONV))
    assert denied["ok"] is False and denied["error"] == "confirm_required"
    # off the allowlist → also confirm_required under gated
    off = _run(send.execute(session=sid, line="whoami", conversation_id=CONV))
    assert off["ok"] is False and off["error"] == "confirm_required"
    _run(rel.execute(session=sid, conversation_id=CONV))


def test_strict_named_policy_denies_off_list(spawned, home):
    """A strict lease under a named policy refuses an off-allowlist command
    outright (no prompt)."""
    import json
    from app.shadow.sock_server import short_socket_dir  # ensures dirs exist
    from app.shadow import policy as pol
    pdir = pol.policies_dir()
    (pdir / "roready.json").write_text(json.dumps({"allowedCommands": ["echo", "true"]}))
    ctl, send, rel = _tools()
    sid = spawned.entry.session_id
    _run(ctl.execute(session=sid, restriction="strict", policy="named:roready",
                     conversation_id=CONV))
    ok = _run(send.execute(session=sid, line="echo allowed", conversation_id=CONV))
    assert ok["ok"] and "allowed" in ok["transcript"]
    denied = _run(send.execute(session=sid, line="whoami", conversation_id=CONV))
    assert denied["ok"] is False and denied["error"] == "command_denied"
    _run(rel.execute(session=sid, conversation_id=CONV))


def test_keepalive_holds_lease_past_timeout(spawned):
    """The keepalive thread keeps a lease alive past the dead-man window;
    stopping it lets the lease expire."""
    from app.shadow import client, lease
    from app.mcp.tools import shadow_tools
    ctl, _, _ = _tools()
    sid = spawned.entry.session_id
    _run(ctl.execute(session=sid, restriction="unrestricted", conversation_id=CONV))
    # Live well past the dead-man timeout — keepalive should hold it.
    time.sleep(lease.HEARTBEAT_TIMEOUT_S + 2.0)
    st = client.control_status(sid)
    assert st["lease"] is not None and st["lease"]["conversation_id"] == CONV
    # Stop the keepalive; the lease should now expire on its own.
    for k in list(shadow_tools._LEASES):
        shadow_tools._forget_lease(k)
    time.sleep(lease.HEARTBEAT_TIMEOUT_S + 2.0)
    st2 = client.control_status(sid)
    assert st2["lease"] is None
