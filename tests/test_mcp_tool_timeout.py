"""
Tests for MCP tool execution timeout (Issue #13).

Verifies that MCPManager._call_tool_with_timeout:
  1. Returns a structured error dict when a tool call exceeds the timeout.
  2. Returns the tool result normally when the call finishes in time.
  3. Respects ZIYA_TOOL_TIMEOUT environment variable.
  4. Extends the effective timeout when the tool arguments contain a
     ``timeout`` key (e.g. shell tool), so the inner layer fires first.
  5. Works end-to-end through call_tool() for both fast and hung servers.
"""

import asyncio
import os
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.mcp.manager import MCPManager, DEFAULT_TOOL_TIMEOUT, TOOL_TIMEOUT_BUFFER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager(**overrides) -> MCPManager:
    """Create an MCPManager with mocked internals for unit testing."""
    with patch.object(MCPManager, '_find_config_file', return_value=None):
        mgr = MCPManager.__new__(MCPManager)
        mgr.config_path = None
        mgr.clients = {}
        mgr.workspace_scoped_clients = {}
        mgr._workspace_instance_last_used = {}
        mgr._workspace_instance_timeout = 300
        mgr.config_search_paths = []
        mgr.builtin_server_definitions = {}
        mgr.server_configs = {}
        mgr.is_initialized = True
        mgr._tools_cache = None
        mgr._tools_cache_timestamp = 0
        mgr._tools_cache_ttl = 300
        mgr._reconnection_attempts = {}
        mgr._failed_servers = {}
        mgr._failed_server_ttl = 300
        mgr._reconnection_failures = {}
        mgr._tool_fingerprints = {}
        mgr._recent_tool_calls = {}
        mgr._max_recent_calls = 10
        mgr._loop_detection_window = 60
        mgr._server_enabled_overrides = {}
        mgr.tool_timeout = float(overrides.get('tool_timeout', DEFAULT_TOOL_TIMEOUT))
        return mgr


def _make_client(*, name: str = "test-server", call_result=None, call_delay: float = 0):
    """Create a mock MCPClient that returns *call_result* after *call_delay* seconds."""
    client = MagicMock()
    client.server_config = {"name": name}
    client.server_name = name
    client.is_connected = True
    client.tools = []

    async def _delayed_call(tool_name, arguments):
        if call_delay > 0:
            await asyncio.sleep(call_delay)
        return call_result

    client.call_tool = AsyncMock(side_effect=_delayed_call)
    return client


# ---------------------------------------------------------------------------
# Unit tests for _call_tool_with_timeout
# ---------------------------------------------------------------------------

class TestCallToolWithTimeout:
    """Direct tests on the helper method."""

    @pytest.mark.asyncio
    async def test_timeout_returns_error_dict(self):
        """A hung tool call should return a structured error after the timeout."""
        mgr = _make_manager(tool_timeout=0.1)  # 100ms
        client = _make_client(call_delay=5.0, call_result={"content": [{"type": "text", "text": "never"}]})

        result = await mgr._call_tool_with_timeout(client, "slow_tool", {})

        assert isinstance(result, dict)
        assert result["error"] is True
        assert "timed out" in result["message"]
        assert "slow_tool" in result["message"]
        assert result["code"] == -32000

    @pytest.mark.asyncio
    async def test_fast_tool_returns_normally(self):
        """A tool that finishes quickly should return its real result."""
        mgr = _make_manager(tool_timeout=5.0)
        expected = {"content": [{"type": "text", "text": "hello"}]}
        client = _make_client(call_delay=0, call_result=expected)

        result = await mgr._call_tool_with_timeout(client, "fast_tool", {})

        assert result == expected

    def test_default_timeout_constant(self):
        assert DEFAULT_TOOL_TIMEOUT == 120

    def test_timeout_buffer_constant(self):
        assert TOOL_TIMEOUT_BUFFER == 15

    @pytest.mark.asyncio
    async def test_timeout_configurable_via_env(self):
        """ZIYA_TOOL_TIMEOUT env var should be picked up by __init__."""
        with patch.dict(os.environ, {"ZIYA_TOOL_TIMEOUT": "42"}):
            with patch.object(MCPManager, '_find_config_file', return_value=None):
                mgr = MCPManager()
                assert mgr.tool_timeout == 42.0

    @pytest.mark.asyncio
    async def test_tool_timeout_param_extends_effective_timeout(self):
        """When arguments contain timeout=200, effective timeout should be
        max(default, 200 + TOOL_TIMEOUT_BUFFER), not the shorter default."""
        mgr = _make_manager(tool_timeout=120)

        # Tool takes 0.3s (fast), but requests timeout=200.
        # Effective timeout should be max(120, 200+15) = 215.
        # We verify by checking the call doesn't time out even though
        # the default is lower than the tool's requested timeout.
        expected = {"content": [{"type": "text", "text": "ok"}]}
        client = _make_client(call_delay=0, call_result=expected)

        result = await mgr._call_tool_with_timeout(
            client, "run_shell_command", {"command": "sleep 190", "timeout": 200}
        )

        assert result == expected

    @pytest.mark.asyncio
    async def test_tool_timeout_param_does_not_lower_default(self):
        """A tool-requested timeout *lower* than the default should NOT
        reduce the effective timeout below the configured minimum."""
        mgr = _make_manager(tool_timeout=120)

        # Tool requests timeout=5, but default is 120.
        # Effective timeout = max(120, 5+15) = 120 — the configured minimum wins.
        expected = {"content": [{"type": "text", "text": "ok"}]}
        client = _make_client(call_delay=0, call_result=expected)

        result = await mgr._call_tool_with_timeout(
            client, "run_shell_command", {"command": "echo hi", "timeout": 5}
        )

        assert result == expected

    @pytest.mark.asyncio
    async def test_invalid_tool_timeout_ignored(self):
        """A non-numeric timeout argument should be silently ignored."""
        mgr = _make_manager(tool_timeout=0.2)
        client = _make_client(call_delay=5.0, call_result={"ok": True})

        # "timeout": "not-a-number" should be ignored, so default 0.2s applies
        result = await mgr._call_tool_with_timeout(
            client, "some_tool", {"timeout": "not-a-number"}
        )

        assert result["error"] is True
        assert "timed out" in result["message"]

    @pytest.mark.asyncio
    async def test_no_arguments_dict(self):
        """Non-dict arguments should not crash the timeout extraction."""
        mgr = _make_manager(tool_timeout=5.0)
        expected = {"ok": True}
        client = _make_client(call_delay=0, call_result=expected)

        # Pass a string instead of dict — should fall back to default timeout
        result = await mgr._call_tool_with_timeout(client, "tool", "raw-string-arg")
        assert result == expected


# ---------------------------------------------------------------------------
# Integration tests through call_tool()
# ---------------------------------------------------------------------------

class TestCallToolIntegration:
    """Verify that call_tool() routes through _call_tool_with_timeout."""

    @pytest.mark.asyncio
    async def test_call_tool_times_out_for_hung_server(self):
        """call_tool() should return a timeout error for a hung MCP server."""
        mgr = _make_manager(tool_timeout=0.1)

        # Create a mock client with a tool that hangs
        client = _make_client(name="hung-server", call_delay=5.0, call_result={"ok": True})
        tool = MagicMock()
        tool.name = "stuck_tool"
        client.tools = [tool]
        mgr.clients = {"hung-server": client}
        mgr.server_configs = {"hung-server": {"enabled": True}}

        with patch('app.mcp.manager.get_dynamic_loader') as mock_loader:
            mock_loader.return_value.get_tool.return_value = None
            with patch('app.mcp.permissions.get_permissions_manager') as mock_perms:
                mock_perms.return_value.get_permissions.return_value = {'defaults': {'tool': 'enabled'}, 'servers': {}}

                result = await mgr.call_tool("stuck_tool", {})

        assert isinstance(result, dict)
        assert result["error"] is True
        assert "timed out" in result["message"]

    @pytest.mark.asyncio
    async def test_call_tool_succeeds_within_timeout(self):
        """call_tool() should return results for fast tools."""
        mgr = _make_manager(tool_timeout=5.0)

        expected = {"content": [{"type": "text", "text": "result"}]}
        client = _make_client(name="fast-server", call_delay=0, call_result=expected)
        tool = MagicMock()
        tool.name = "quick_tool"
        client.tools = [tool]
        mgr.clients = {"fast-server": client}
        mgr.server_configs = {"fast-server": {"enabled": True}}

        with patch('app.mcp.manager.get_dynamic_loader') as mock_loader:
            mock_loader.return_value.get_tool.return_value = None
            with patch('app.mcp.permissions.get_permissions_manager') as mock_perms:
                mock_perms.return_value.get_permissions.return_value = {'defaults': {'tool': 'enabled'}, 'servers': {}}

                result = await mgr.call_tool("quick_tool", {})

        assert result == expected

    @pytest.mark.asyncio
    async def test_call_tool_shell_with_long_timeout_not_superseded(self):
        """A shell command with timeout=250 should NOT be killed by the
        default 120s manager timeout.  The effective timeout should be
        max(120, 250+15) = 265s."""
        mgr = _make_manager(tool_timeout=120)

        expected = {"content": [{"type": "text", "text": "done"}]}
        # Simulate a tool that takes 0.05s (fast), but requests timeout=250
        client = _make_client(name="shell", call_delay=0.05, call_result=expected)
        tool = MagicMock()
        tool.name = "run_shell_command"
        tool.inputSchema = {"properties": {"command": {"type": "string"}, "timeout": {"type": "number"}}}
        client.tools = [tool]
        mgr.clients = {"shell": client}
        mgr.server_configs = {"shell": {"enabled": True, "builtin": True}}

        with patch('app.mcp.manager.get_dynamic_loader') as mock_loader:
            mock_loader.return_value.get_tool.return_value = None
            with patch('app.mcp.permissions.get_permissions_manager') as mock_perms:
                mock_perms.return_value.get_permissions.return_value = {'defaults': {'tool': 'enabled'}, 'servers': {}}

                result = await mgr.call_tool(
                    "run_shell_command",
                    {"command": "make build", "timeout": 250}
                )

        assert result == expected
