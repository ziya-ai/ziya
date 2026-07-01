"""
Regression test: restart_server must tear down workspace-scoped instances too
(ASR F-004 follow-up).

The shell server is workspace-scoped — its live subprocesses live in
``workspace_scoped_clients["shell"][instance_key]``, NOT in ``self.clients``.
A restart that only tore down ``self.clients`` left the per-workspace
subprocesses running with their ORIGINAL env, so a "restart" reported success
while a stale config (e.g. a pre-signature allowlist, or an escalation that
should now be clamped by the signature gate) kept serving tool calls until the
subprocess happened to respawn on its own.

This pins the fix: after ``restart_server("shell", ...)``, the workspace-scoped
instances for that server are disconnected and dropped from both
``workspace_scoped_clients`` and ``_workspace_instance_last_used``, so the next
tool call respawns a fresh subprocess that re-reads its env and re-runs the
escalation-signature gate.
"""

import asyncio

import pytest

from app.mcp.manager import MCPManager


class _FakeClient:
    """Minimal MCPClient stand-in that records disconnect()."""

    def __init__(self, *, connected=True):
        self.is_connected = connected
        self.disconnected = False
        self.tools = []
        self.resources = []
        self.prompts = []

    async def disconnect(self):
        self.disconnected = True
        self.is_connected = False

    async def connect(self):
        self.is_connected = True
        return True


@pytest.fixture
def manager(monkeypatch):
    mgr = MCPManager.__new__(MCPManager)
    # Minimal state restart_server / its teardown touch.
    mgr.clients = {}
    mgr.workspace_scoped_clients = {}
    mgr._workspace_instance_last_used = {}
    mgr.server_configs = {
        "shell": {"command": "x", "args": [], "builtin": True, "workspace_scoped": True}
    }
    mgr.builtin_server_definitions = {}
    mgr._failed_servers = {}
    mgr._reconnection_attempts = {}
    mgr._reconnection_failures = {}
    mgr._tools_cache = None
    mgr._tools_cache_timestamp = 0
    mgr._tool_fingerprints = {}
    return mgr


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_restart_disconnects_workspace_instances(manager, monkeypatch):
    # Two live workspace-scoped shell subprocesses, as if two conversations
    # had each spawned their own.
    ws_a = _FakeClient()
    ws_b = _FakeClient()
    manager.workspace_scoped_clients["shell"] = {
        "/proj/a::conv-a": ws_a,
        "/proj/b::conv-b": ws_b,
    }
    manager._workspace_instance_last_used["shell"] = {
        "/proj/a::conv-a": 1.0,
        "/proj/b::conv-b": 2.0,
    }

    # Stub out the new-client connect path so restart "succeeds" without a real
    # subprocess; we only care that the workspace instances were torn down.
    monkeypatch.setattr(
        "app.mcp.manager.MCPClient", lambda cfg: _FakeClient(connected=False)
    )

    async def _fake_connect(name, client):
        client.is_connected = True
        return True

    monkeypatch.setattr(manager, "_connect_server", _fake_connect)

    ok = _run(manager.restart_server("shell", {"command": "x", "args": []}))
    assert ok is True

    # Both workspace subprocesses must have been disconnected ...
    assert ws_a.disconnected is True
    assert ws_b.disconnected is True
    # ... and the cache entries removed so the next call respawns fresh.
    assert "shell" not in manager.workspace_scoped_clients
    assert "shell" not in manager._workspace_instance_last_used


def test_restart_with_no_workspace_instances_is_safe(manager, monkeypatch):
    # No workspace instances exist — restart must not error.
    monkeypatch.setattr(
        "app.mcp.manager.MCPClient", lambda cfg: _FakeClient(connected=False)
    )

    async def _fake_connect(name, client):
        return True

    monkeypatch.setattr(manager, "_connect_server", _fake_connect)

    ok = _run(manager.restart_server("shell", {"command": "x", "args": []}))
    assert ok is True
    assert "shell" not in manager.workspace_scoped_clients


def test_restart_disconnect_error_does_not_abort(manager, monkeypatch):
    """A disconnect failure on one workspace instance must not abort the
    restart — it should be logged and the restart proceed."""
    class _BadClient(_FakeClient):
        async def disconnect(self):
            raise OSError("subprocess already dead")

    bad = _BadClient()
    manager.workspace_scoped_clients["shell"] = {"/proj/a::conv-a": bad}
    manager._workspace_instance_last_used["shell"] = {"/proj/a::conv-a": 1.0}

    monkeypatch.setattr(
        "app.mcp.manager.MCPClient", lambda cfg: _FakeClient(connected=False)
    )

    async def _fake_connect(name, client):
        return True

    monkeypatch.setattr(manager, "_connect_server", _fake_connect)

    ok = _run(manager.restart_server("shell", {"command": "x", "args": []}))
    assert ok is True
    # Even though disconnect raised, the cache entry is gone (pop happened first).
    assert "shell" not in manager.workspace_scoped_clients
