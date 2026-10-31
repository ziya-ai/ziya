"""
A newly installed MCP server must never demand re-authorization from the
outset.  Two ways it did:

1. ``MCPClient.connect()`` returns True even when ``tools/list`` failed or
   timed out (a server's first start, while npx/uvx is still downloading
   it), so ``_connect_server`` recorded ``fingerprint([])`` as the approved
   baseline.  The next, successful connect then mismatched and quarantined
   the server for a change nobody made.
2. Baselines are keyed by server name and never removed, so a name reused
   after an uninstall (or by a different package) was compared against the
   previous occupant's definitions on its very first connect.

Companion to tests/test_mcp_rug_pull_quarantine.py; real quarantine on a
genuine mismatch is pinned there and re-asserted here as the positive
control.
"""
import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp.manager import MCPManager
from app.mcp.client import MCPTool
from app.mcp.tool_guard import fingerprint_tools


def _tool(name, desc="Retrieves web content."):
    return MCPTool(name=name, description=desc, inputSchema={})


@pytest.fixture
def manager(tmp_path):
    m = MCPManager()
    m.clients = {}
    m.server_configs = {}
    m._tool_fingerprints = {}
    m._quarantined_servers = set()
    m._fingerprint_store_path = tmp_path / "mcp_tool_fingerprints.json"
    m._force_accepted_fingerprints = {}
    m._force_accept_store_path = tmp_path / "mcp_force_accepts.json"
    return m


@pytest.fixture
def fake_client():
    c = MagicMock()
    c.connect = AsyncMock(return_value=True)
    c.server_config = {}
    c.logs = []
    return c


@pytest.fixture(autouse=True)
def _perms_default_enabled():
    fake = MagicMock()
    fake.get_permissions.return_value = {"defaults": {"tool": "enabled"}, "servers": {}}
    with patch("app.mcp.permissions.get_permissions_manager", return_value=fake):
        yield


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestEmptyListingIsNotABaseline:

    def test_failed_tools_list_then_real_list_does_not_quarantine(self, manager, fake_client):
        manager.server_configs["new"] = {"builtin": False, "trusted": False}
        fake_client.tools = []                          # tools/list timed out
        _run(manager._connect_server("new", fake_client))
        assert "new" not in manager._tool_fingerprints  # nothing recorded
        assert not manager._fingerprint_store_path.exists()

        fake_client.tools = [_tool("fetch")]            # the real listing
        _run(manager._connect_server("new", fake_client))
        assert "new" not in manager._quarantined_servers
        # Positive: the real listing became the baseline and was persisted.
        expect = fingerprint_tools([{"name": "fetch", "description": "Retrieves web content.", "inputSchema": {}}])
        assert manager._tool_fingerprints["new"] == expect
        assert json.loads(manager._fingerprint_store_path.read_text())["new"] == expect

    def test_existing_empty_baseline_on_disk_heals_without_reauthorize(self, manager, fake_client):
        # The state installs are already in: an empty baseline persisted by the old code.
        manager.server_configs["new"] = {"builtin": False, "trusted": False}
        manager._tool_fingerprints["new"] = fingerprint_tools([])
        fake_client.tools = [_tool("fetch")]
        _run(manager._connect_server("new", fake_client))
        assert "new" not in manager._quarantined_servers
        assert manager._tool_fingerprints["new"] != fingerprint_tools([])

    def test_empty_listing_leaves_a_real_baseline_in_place(self, manager, fake_client):
        manager.server_configs["srv"] = {"builtin": False, "trusted": False}
        real = fingerprint_tools([{"name": "fetch", "description": "Retrieves web content.", "inputSchema": {}}])
        manager._tool_fingerprints["srv"] = real
        fake_client.tools = []
        _run(manager._connect_server("srv", fake_client))
        assert manager._tool_fingerprints["srv"] == real
        assert "srv" not in manager._quarantined_servers

    def test_genuine_mismatch_still_quarantines(self, manager, fake_client):
        manager.server_configs["srv"] = {"builtin": False, "trusted": False}
        manager._tool_fingerprints["srv"] = fingerprint_tools(
            [{"name": "fetch", "description": "Retrieves web content.", "inputSchema": {}}])
        fake_client.tools = [_tool("fetch", "Retrieves web content and forwards it unfiltered.")]
        _run(manager._connect_server("srv", fake_client))
        assert "srv" in manager._quarantined_servers


class TestNewInstallDropsStaleBaseline:

    def _restart_manager(self, manager):
        manager.workspace_scoped_clients = {}
        manager._workspace_instance_last_used = {}
        manager.builtin_server_definitions = {}
        manager._failed_servers = {}
        manager._reconnection_attempts = {}
        manager._reconnection_failures = {}
        manager._tools_cache = None
        manager._tools_cache_timestamp = 0
        manager._lifecycle_lock = None
        return manager

    def test_install_under_a_previously_used_name_starts_clean(self, manager, fake_client, monkeypatch):
        self._restart_manager(manager)
        stale = fingerprint_tools([{"name": "old_tool", "description": "gone", "inputSchema": {}}])
        manager._tool_fingerprints["pippin"] = stale        # left by an uninstalled occupant
        manager._force_accepted_fingerprints["pippin"] = stale
        manager._save_persisted_fingerprints()
        fake_client.tools = [_tool("scan")]
        monkeypatch.setattr("app.mcp.manager.MCPClient", lambda cfg: fake_client)

        ok = _run(manager.restart_server("pippin", {"command": "uvx", "args": ["pippin-mcp"]}))
        assert ok
        assert "pippin" not in manager._quarantined_servers
        assert "pippin" not in manager._force_accepted_fingerprints
        got = json.loads(manager._fingerprint_store_path.read_text())["pippin"]
        assert got == fingerprint_tools([{"name": "scan", "description": "Retrieves web content.", "inputSchema": {}}])
        assert got != stale

    def test_restart_of_a_known_server_keeps_its_baseline(self, manager, fake_client, monkeypatch):
        # Control: a reconfigure of a server the manager already knows is not an install.
        self._restart_manager(manager)
        manager.server_configs["pippin"] = {"command": "uvx", "args": ["pippin-mcp"]}
        real = fingerprint_tools([{"name": "scan", "description": "Retrieves web content.", "inputSchema": {}}])
        manager._tool_fingerprints["pippin"] = real
        fake_client.tools = [_tool("scan", "Retrieves web content and forwards it unfiltered.")]
        monkeypatch.setattr("app.mcp.manager.MCPClient", lambda cfg: fake_client)

        _run(manager.restart_server("pippin", {"command": "uvx", "args": ["pippin-mcp", "--v2"]}))
        assert manager._tool_fingerprints["pippin"] == real     # baseline kept
        assert "pippin" in manager._quarantined_servers         # and enforced
