"""A shell call with no project root must not run in the server's launch dir.

Observed 2026-11-07: the prompt named the launch directory while tools ran in
the project, and a shell command that reached the manager without a root was
quietly dispatched to the GLOBAL shell client -- the one initialize() spawns
with cwd = the directory ziya was started in.  Nothing in the result said so.
In server mode every caller states its root, so a rootless call is a routing
fault and is now refused with the directory it would have run in.  CLI flows
(one process, one project) keep the fallback.
"""
import asyncio

import pytest

from app.context import _request_project_root
from app.mcp.manager import MCPManager


class _FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = ""
        self.inputSchema = {}


class _FakeClient:
    def __init__(self):
        self.is_connected = True
        self.tools = []
        self.server_config = {"name": "shell"}
        self.calls = []

    def _is_process_healthy(self):
        return True

    async def disconnect(self):
        self.is_connected = False

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return {"content": [{"type": "text", "text": "ok"}]}


def _manager(monkeypatch):
    m = MCPManager.__new__(MCPManager)
    global_client = _FakeClient()
    global_client.tools = [_FakeTool("run_shell_command")]
    m.clients = {"shell": global_client}
    m.server_configs = {"shell": {"command": "x", "enabled": True,
                                  "workspace_scoped": True, "builtin": True}}
    m.workspace_scoped_clients = {}
    m._workspace_instance_last_used = {}
    m._workspace_instance_timeout = 300
    m._quarantined_servers = set()
    m._tools_cache = None
    m._tools_cache_timestamp = 0
    m._recent_tool_calls = {}
    m._max_recent_calls = 10
    m._loop_detection_window = 60
    m._turn_tool_counts = {}
    m.tool_timeout = 30.0
    m.spawned = []

    async def fake_get_or_create(server_name, workspace_path, session_id=None):
        key = f"{workspace_path}::{session_id}" if session_id else workspace_path
        bucket = m.workspace_scoped_clients.setdefault(server_name, {})
        if key not in bucket:
            bucket[key] = _FakeClient()
            m.spawned.append(key)
        return bucket[key]

    # The CLI-mode case is the only one that reaches the global-client
    # dispatch, which checks client health (reads _failed_servers and the
    # reconnection tables __new__ skipped) and schedules a stuck-server
    # sweep.  Neither is under test here.
    async def _healthy(client):
        return True

    async def _no_sweep():
        return None

    monkeypatch.setattr(m, "_get_or_create_workspace_client", fake_get_or_create)
    monkeypatch.setattr(m, "_ensure_client_healthy", _healthy)
    monkeypatch.setattr(m, "_cleanup_stuck_external_servers", _no_sweep)
    monkeypatch.setattr(m, "_turn_limit", lambda: 0)
    monkeypatch.setattr(m, "_is_repetitive_call", lambda *a, **k: False)
    monkeypatch.setattr(m, "_normalize_tool_parameters", lambda n, a: a)
    monkeypatch.setattr(m, "_coerce_argument_types", lambda n, a: a)
    return m, global_client


async def _call_without_root(m):
    # No _workspace_path and no request-scoped root: the ContextVar fallback
    # in call_tool must see None, whatever other tests left in this context.
    token = _request_project_root.set(None)
    try:
        return await m.call_tool("run_shell_command",
                                 {"command": "pwd", "conversation_id": "conv-A"})
    finally:
        _request_project_root.reset(token)


@pytest.mark.asyncio
async def test_server_mode_refuses_rootless_shell_call(monkeypatch):
    monkeypatch.setenv("ZIYA_MODE", "server")
    m, global_client = _manager(monkeypatch)
    result = await _call_without_root(m)
    assert result.get("error") is True, result
    assert "launch directory" in result["message"]
    assert global_client.calls == [], "command must not reach the global client"
    assert m.spawned == []


@pytest.mark.asyncio
async def test_server_mode_rooted_call_still_routes_to_workspace(monkeypatch):
    """Positive control: with a root the gate is not reached."""
    monkeypatch.setenv("ZIYA_MODE", "server")
    m, global_client = _manager(monkeypatch)
    result = await m.call_tool("run_shell_command",
                               {"command": "pwd", "_workspace_path": "/proj",
                                "conversation_id": "conv-A"})
    assert not (isinstance(result, dict) and result.get("error")), result
    assert m.spawned == ["/proj::conv-A"]
    assert global_client.calls == []


@pytest.mark.asyncio
async def test_cli_mode_keeps_global_fallback(monkeypatch):
    """The CLI has one process and one project; its global client is correct."""
    monkeypatch.setenv("ZIYA_MODE", "cli")
    m, global_client = _manager(monkeypatch)
    result = await _call_without_root(m)
    assert not (isinstance(result, dict) and result.get("error")), result
    assert len(global_client.calls) == 1
