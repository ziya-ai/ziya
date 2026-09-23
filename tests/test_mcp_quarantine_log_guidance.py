"""
The rug-pull quarantine log line is the only thing a user sees at startup.

It used to end in ``(see reauthorize_server())`` -- the name of a Python
method, which a user cannot call.  The remedy is a button in the MCP Servers
panel; the line must say so, and name the server the button is on.
"""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from app.mcp.manager import MCPManager
from app.mcp.client import MCPTool
from app.mcp.tool_guard import fingerprint_tools


@pytest.fixture
def manager(tmp_path):
    m = MCPManager()
    m.clients = {}
    m.server_configs = {"fetch": {"builtin": False, "trusted": False}}
    m._tool_fingerprints = {
        "fetch": fingerprint_tools(
            [{"name": "fetch", "description": "Retrieves a URL.", "inputSchema": {}}]),
    }
    m._quarantined_servers = set()
    m._fingerprint_store_path = tmp_path / "fp.json"
    m._force_accepted_fingerprints = {}
    m._force_accept_store_path = tmp_path / "force.json"
    return m


@pytest.mark.asyncio
async def test_quarantine_log_tells_user_where_to_click(manager):
    # The ZIYA logger sets propagate=False (app/utils/logging_utils.py), so
    # caplog never sees its records, and its handler is bound at import time
    # to pytest's global-capture stderr, which capfd suspends -- so neither
    # capture sees the line.  Assert on the logger call instead.
    client = MagicMock()
    client.connect = AsyncMock(return_value=True)
    client.server_config = {}
    client.logs = []
    client.tools = [MCPTool(name="fetch", description="Retrieves a URL. Now evil.",
                            inputSchema={})]
    fake_perms = MagicMock()
    fake_perms.get_permissions.return_value = {"defaults": {"tool": "enabled"}, "servers": {}}

    fake_logger = MagicMock()
    with patch("app.mcp.permissions.get_permissions_manager", return_value=fake_perms), \
         patch("app.mcp.manager.logger", fake_logger):
        await manager._connect_server("fetch", client)

    assert "fetch" in manager._quarantined_servers  # the path under test ran
    lines = [str(c.args[0]) for c in fake_logger.error.call_args_list
             if c.args and "SECURITY" in str(c.args[0])]
    assert lines, "no SECURITY quarantine line was logged"
    msg = lines[0]
    # No internal symbol: a user cannot call a Python method.
    assert "reauthorize_server" not in msg
    # The actual remedy, and the server it applies to.
    assert "MCP Servers" in msg
    assert "Re-authorize" in msg
    assert "'fetch'" in msg
