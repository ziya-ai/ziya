"""Headless spawn / kill (design doc §6.2) — the whole loop through the
chat tools against a real detached host process.

The spawned host is a separate Python process (``-m app.shadow.headless``)
started in its own session; these tests exercise the announce pipe, the
implicit strict lease, send_line under it, owner-only kill, unlink-on-exit
and the idle watchdog.  POSIX only, same floor as the feature.
"""
import asyncio
import json
import os
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX pty only")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Make the spawned subprocess import ``app`` the way this test does.
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(p for p in sys.path if p))
    # The spawn allowlist ships as just ``ssh``; these tests drive a local
    # ``sh`` (and one deliberately missing binary), so widen it here.
    from app.shadow.policy import policies_dir
    (policies_dir() / "spawn.json").write_text(json.dumps(
        {"allowedCommands": ["ssh", "sh", "definitely-not-a-binary-xyz"]}))
    yield tmp_path


def _run(coro):
    return asyncio.run(coro)


def _wait(pred, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.1)
    return False


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_spawn_send_kill_round_trip(home):
    from app.mcp.tools.shadow_tools import ShadowSpawnTool, ShadowSendTool, ShadowKillTool
    from app.shadow import registry, client

    sp = _run(ShadowSpawnTool().execute(
        argv=["sh", "-c", "while read l; do eval \"$l\"; done"], label="spawned",
        conversation_id="owner-conv"))
    assert sp["ok"], sp
    sid = sp["session_id"]
    # Implicit lease: granted, and strict regardless of what was asked.
    assert sp["granted"] is True and sp["restriction"] == "strict"
    entry = registry.load_session(sid)
    assert entry is not None and entry.headless and entry.spawned_by == {"conversation_id": "owner-conv"}
    assert _alive(entry.pid) and entry.pid != os.getpid()   # a real, separate host process

    # Drive it: a read-only command flows under strict/none.
    out = _run(ShadowSendTool().execute(session=sid, line="echo spawned-hello",
                                        conversation_id="owner-conv"))
    assert out["ok"], out
    assert "spawned-hello" in out["transcript"]

    # Another conversation may not kill it, and cannot take the lease.
    denied = _run(ShadowKillTool().execute(session=sid, conversation_id="other-conv"))
    assert denied["ok"] is False and denied["error"] == "not_owner"
    with pytest.raises(client.ShadowError) as ei:
        client.control_acquire(sid, "other-conv", restriction="gated")
    assert ei.value.code == "lease_held"

    # Owner kills it: host exits, registry/journal/socket are reaped.
    k = _run(ShadowKillTool().execute(session=sid, conversation_id="owner-conv"))
    assert k["ok"] and k["killed"]
    assert _wait(lambda: not _alive(entry.pid), timeout=10)
    assert _wait(lambda: registry.load_session(sid) is None)
    assert not os.path.exists(entry.journal)


def test_spawned_lease_is_clamped_to_strict_even_if_unrestricted_requested(home):
    from app.shadow import client, registry
    sid = client.spawn_headless(["sh", "-c", "read x"], label="clamp",
                                spawned_by={"conversation_id": "c1"})
    pid = registry.load_session(sid).pid
    try:
        # Asked for unrestricted with the default (builtin) policy: clamped to strict,
        # and under strict a mutating command is refused outright — never confirm_required.
        resp = client.control_acquire(sid, "c1", restriction="unrestricted", policy="builtin")
        assert resp["restriction"] == "strict"
        with pytest.raises(client.ShadowError) as ei:
            client.send_line(sid, resp["lease_id"], "echo x > /tmp/nope")
        assert ei.value.code == "command_denied"
        with pytest.raises(client.ShadowError) as ei:
            client.send_line(sid, resp["lease_id"], "tee /tmp/nope")
        assert ei.value.code == "command_denied"
        # policy 'none' would switch the policy axis off while the lease is
        # clamped strict — total control with no human anywhere — so a spawned
        # session can never hold it (§6.3: none is only meaningful with
        # unrestricted, which a spawned session cannot be).
        with pytest.raises(client.ShadowError) as ei:
            client.control_acquire(sid, "c1", restriction="unrestricted", policy="none")
        assert ei.value.code == "policy_requires_unrestricted"
    finally:
        client.kill_session(sid, conversation_id="c1")
        _wait(lambda: not _alive(pid))


def test_kill_refuses_interactive_style_sessions(home):
    """A headless session with no spawned_by (a test/CLI core, not an agent
    spawn) is not killable from chat."""
    import threading
    from app.shadow.pty_host import ShadowCore
    from app.shadow import client
    core = ShadowCore(["sh", "-c", "read x"], label="notspawned", headless=True)
    core.spawn()
    t = threading.Thread(target=core.run, kwargs={"stdin_fd": None}, daemon=True)
    t.start()
    try:
        with pytest.raises(client.ShadowError) as ei:
            client.kill_session(core.entry.session_id, conversation_id="c1")
        assert ei.value.code == "not_headless"
    finally:
        core.handle_input(b"x\r")
        t.join(timeout=5)


def test_idle_watchdog_ends_an_abandoned_session(home):
    from app.shadow import client, registry
    from app.shadow.headless import _WATCH_TICK_S
    # No lease is ever acquired, nothing is journaled: it must shut itself down.
    sid = client.spawn_headless(["sh", "-c", "read x"], label="idle",
                                spawned_by={"conversation_id": "c1"}, idle_timeout_s=1)
    entry = registry.load_session(sid)
    assert entry is not None
    assert _wait(lambda: registry.load_session(sid) is None, timeout=_WATCH_TICK_S * 3 + 5)
    assert _wait(lambda: not _alive(entry.pid), timeout=10)
    assert not os.path.exists(entry.journal)


def test_spawn_of_missing_binary_does_not_hang_and_reaps_itself(home):
    """fork() succeeds before exec() fails, so the spawn itself returns an id;
    the child dies at once and the host must end and unlink within seconds
    rather than linger as an empty session."""
    from app.shadow import client, registry
    t0 = time.time()
    sid = client.spawn_headless(["definitely-not-a-binary-xyz"], spawned_by={"conversation_id": "c1"})
    assert time.time() - t0 < client.SPAWN_ANNOUNCE_TIMEOUT_S
    assert _wait(lambda: registry.load_session(sid) is None, timeout=10)
