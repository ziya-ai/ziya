"""
Regression: a server started via ``restart_server(name, new_config)`` must be
visible in ``get_server_status()`` immediately.

The registry install path hands the freshly written config to
``restart_server`` so the new server comes up without a full MCP
reinitialize. That call created the client and connected it — the tools were
live — but never stored ``new_config`` in ``server_configs``. Both
``get_server_status()`` and the MCP status modal enumerate ``server_configs``,
so the user was told "Successfully installed", opened the servers view, and
the server was not there until a manual reload re-read mcp_config.json from
disk. (Every shell-config caller had been working around the same omission
by writing ``server_configs["shell"]`` itself before calling restart.)
"""
import asyncio

import pytest

from app.mcp.manager import MCPManager


class _FakeClient:
    def __init__(self, connected=False):
        self.is_connected = connected
        self.resources = []
        self.tools = []
        self.prompts = []
        self.capabilities = {}

    async def disconnect(self):
        self.is_connected = False


@pytest.fixture
def manager():
    mgr = MCPManager.__new__(MCPManager)
    mgr.clients = {}
    mgr.workspace_scoped_clients = {}
    mgr._workspace_instance_last_used = {}
    mgr.server_configs = {
        "shell": {"command": "x", "args": [], "builtin": True},
    }
    mgr.builtin_server_definitions = {}
    mgr._failed_servers = {}
    mgr._reconnection_attempts = {}
    mgr._reconnection_failures = {}
    mgr._quarantined_servers = set()
    mgr._tools_cache = None
    mgr._tools_cache_timestamp = 0
    mgr._tool_fingerprints = {}
    mgr._lifecycle_lock = None
    return mgr


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _stub_connect(manager, monkeypatch):
    monkeypatch.setattr(
        "app.mcp.manager.MCPClient", lambda cfg: _FakeClient(connected=False)
    )

    async def _fake_connect(name, client):
        client.is_connected = True
        return True

    monkeypatch.setattr(manager, "_connect_server", _fake_connect)


def test_new_server_appears_in_status_after_restart_with_config(manager, monkeypatch):
    """The install seam: restart with a config the manager has never seen."""
    _stub_connect(manager, monkeypatch)
    cfg = {
        "enabled": True,
        "command": "quip-mcp-server",
        "description": "Comprehensive MCP server for Amazon Quip.",
        "registry_provider": "amazon-internal",
        "service_id": "quip-mcp",
    }

    ok = _run(manager.restart_server("quip_mcp", cfg))
    assert ok is True
    assert "quip_mcp" in manager.clients  # was already true before the fix

    # What the modal actually renders from.
    status = manager.get_server_status()
    assert "quip_mcp" in status
    assert status["quip_mcp"]["connected"] is True
    assert status["quip_mcp"]["builtin"] is False

    stored = manager.server_configs["quip_mcp"]
    assert stored["command"] == "quip-mcp-server"
    assert stored["description"] == "Comprehensive MCP server for Amazon Quip."
    # A user/registry server is never a builtin, mirroring the config loader.
    assert stored["builtin"] is False


def test_restart_with_config_replaces_existing_entry_and_keeps_builtin_flag(manager, monkeypatch):
    """Shell config re-apply: the new config must win, and builtin must survive."""
    _stub_connect(manager, monkeypatch)
    new_cfg = {"command": "y", "args": ["-u"], "builtin": True, "env": {"K": "v"}}

    ok = _run(manager.restart_server("shell", new_cfg))
    assert ok is True

    stored = manager.server_configs["shell"]
    assert stored["command"] == "y"
    assert stored["env"] == {"K": "v"}
    assert stored["builtin"] is True
    assert manager.get_server_status()["shell"]["builtin"] is True


def test_restart_with_config_omitting_builtin_keeps_prior_flag(manager, monkeypatch):
    """A caller that forgets `builtin` on a builtin server must not demote it."""
    _stub_connect(manager, monkeypatch)
    ok = _run(manager.restart_server("shell", {"command": "z", "args": []}))
    assert ok is True
    assert manager.server_configs["shell"]["builtin"] is True


def test_restart_without_config_is_unchanged(manager, monkeypatch):
    """Control: the no-config path still reads the stored config."""
    _stub_connect(manager, monkeypatch)
    ok = _run(manager.restart_server("shell"))
    assert ok is True
    assert manager.server_configs["shell"]["command"] == "x"
    assert "shell" in manager.get_server_status()
