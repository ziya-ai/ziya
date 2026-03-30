"""
Tests for MCP client timeout alignment with tool-requested timeouts.

Verifies that when a tool call includes a `timeout` parameter (e.g. shell
commands requesting 120s), the MCP client's readline timeout is extended
to accommodate it, preventing premature disconnects.
"""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch


class TestReadlineTimeoutAlignment:
    """Verify _send_request uses tool-requested timeout for readline."""

    def _make_client(self, server_name="test-shell"):
        """Create an MCPClient with a mock process."""
        from app.mcp.client import MCPClient

        client = MCPClient({"name": server_name, "command": ["echo"]})
        client.is_connected = True

        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.stdin.drain = AsyncMock()
        mock_process.stdout = MagicMock()
        client.process = mock_process

        return client, mock_process

    @pytest.mark.asyncio
    async def test_default_timeout_is_30s(self):
        """Non-tool requests use the default 30s readline timeout."""
        client, mock_process = self._make_client()

        # Respond immediately with a matching request ID
        async def readline():
            return json.dumps({
                "jsonrpc": "2.0",
                "id": client.request_id + 1,
                "result": {"content": [{"type": "text", "text": "ok"}]}
            }).encode() + b"\n"

        mock_process.stdout.readline = readline

        captured_timeouts = []
        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro, timeout=None):
            captured_timeouts.append(timeout)
            return await original_wait_for(coro, timeout=timeout)

        with patch("asyncio.wait_for", side_effect=spy_wait_for):
            await client._send_request("initialize", {})

        # Should have used 30s (the default for non-external servers)
        assert any(t == 30.0 for t in captured_timeouts), \
            f"Expected 30.0s timeout, got: {captured_timeouts}"

    @pytest.mark.asyncio
    async def test_tool_timeout_extends_readline(self):
        """tools/call with timeout=120 extends readline timeout to 130."""
        client, mock_process = self._make_client()

        async def readline():
            return json.dumps({
                "jsonrpc": "2.0",
                "id": client.request_id + 1,
                "result": {"content": [{"type": "text", "text": "ok"}]}
            }).encode() + b"\n"

        mock_process.stdout.readline = readline

        captured_timeouts = []
        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro, timeout=None):
            captured_timeouts.append(timeout)
            return await original_wait_for(coro, timeout=timeout)

        with patch("asyncio.wait_for", side_effect=spy_wait_for):
            await client._send_request("tools/call", {
                "name": "run_shell_command",
                "arguments": {"command": "sleep 100", "timeout": 120}
            })

        # Should have used 130s (120 + 10s buffer)
        assert any(t == 130.0 for t in captured_timeouts), \
            f"Expected 130.0s timeout, got: {captured_timeouts}"

    @pytest.mark.asyncio
    async def test_tool_timeout_string_converted(self):
        """timeout passed as a string is converted to float."""
        client, mock_process = self._make_client()

        async def readline():
            return json.dumps({
                "jsonrpc": "2.0",
                "id": client.request_id + 1,
                "result": {"content": [{"type": "text", "text": "ok"}]}
            }).encode() + b"\n"

        mock_process.stdout.readline = readline

        captured_timeouts = []
        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro, timeout=None):
            captured_timeouts.append(timeout)
            return await original_wait_for(coro, timeout=timeout)

        with patch("asyncio.wait_for", side_effect=spy_wait_for):
            await client._send_request("tools/call", {
                "name": "run_shell_command",
                "arguments": {"command": "echo hi", "timeout": "60"}
            })

        assert any(t == 70.0 for t in captured_timeouts), \
            f"Expected 70.0s timeout for string '60', got: {captured_timeouts}"

    @pytest.mark.asyncio
    async def test_tool_timeout_does_not_shrink_external_default(self):
        """A small tool timeout doesn't shrink below the external server default."""
        client, mock_process = self._make_client(server_name="fetch-external")

        async def readline():
            return json.dumps({
                "jsonrpc": "2.0",
                "id": client.request_id + 1,
                "result": {"content": [{"type": "text", "text": "ok"}]}
            }).encode() + b"\n"

        mock_process.stdout.readline = readline

        captured_timeouts = []
        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro, timeout=None):
            captured_timeouts.append(timeout)
            return await original_wait_for(coro, timeout=timeout)

        with patch("asyncio.wait_for", side_effect=spy_wait_for):
            await client._send_request("tools/call", {
                "name": "fetch",
                "arguments": {"url": "https://example.com", "timeout": 5}
            })

        # External server default is 60s, tool timeout+10=15s.
        # max(60, 15) = 60. Should NOT shrink below 60.
        assert any(t == 60.0 for t in captured_timeouts), \
            f"Expected 60.0s (external default preserved), got: {captured_timeouts}"

    @pytest.mark.asyncio
    async def test_invalid_tool_timeout_uses_default(self):
        """Invalid timeout value falls back to default."""
        client, mock_process = self._make_client()

        async def readline():
            return json.dumps({
                "jsonrpc": "2.0",
                "id": client.request_id + 1,
                "result": {"content": [{"type": "text", "text": "ok"}]}
            }).encode() + b"\n"

        mock_process.stdout.readline = readline

        captured_timeouts = []
        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro, timeout=None):
            captured_timeouts.append(timeout)
            return await original_wait_for(coro, timeout=timeout)

        with patch("asyncio.wait_for", side_effect=spy_wait_for):
            await client._send_request("tools/call", {
                "name": "run_shell_command",
                "arguments": {"command": "echo hi", "timeout": "not-a-number"}
            })

        # Should fall back to default 30s
        assert any(t == 30.0 for t in captured_timeouts), \
            f"Expected 30.0s (fallback), got: {captured_timeouts}"

    @pytest.mark.asyncio
    async def test_no_tool_timeout_uses_default(self):
        """Tool call without timeout param uses default."""
        client, mock_process = self._make_client()

        async def readline():
            return json.dumps({
                "jsonrpc": "2.0",
                "id": client.request_id + 1,
                "result": {"content": [{"type": "text", "text": "ok"}]}
            }).encode() + b"\n"

        mock_process.stdout.readline = readline

        captured_timeouts = []
        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro, timeout=None):
            captured_timeouts.append(timeout)
            return await original_wait_for(coro, timeout=timeout)

        with patch("asyncio.wait_for", side_effect=spy_wait_for):
            await client._send_request("tools/call", {
                "name": "run_shell_command",
                "arguments": {"command": "echo hi"}
            })

        # No timeout param → default 30s
        assert any(t == 30.0 for t in captured_timeouts), \
            f"Expected 30.0s (no timeout param), got: {captured_timeouts}"

    @pytest.mark.asyncio
    async def test_timeout_error_message_reflects_actual_duration(self):
        """Error message reports the actual timeout duration, not hardcoded 30s."""
        client, mock_process = self._make_client()

        # Force a TimeoutError on readline
        async def timeout_readline():
            raise asyncio.TimeoutError()

        mock_process.stdout.readline = timeout_readline

        result = await client._send_request("tools/call", {
            "name": "run_shell_command",
            "arguments": {"command": "sleep 200", "timeout": 120}
        })

        assert result["error"] is True
        # The message should say 130 (120+10), not 30
        assert "130 seconds" in result["message"], \
            f"Expected '130 seconds' in error message, got: {result['message']}"


class TestTimeoutChainDocumentation:
    """Verify the three timeout layers are properly aligned."""

    def test_shell_server_accepts_timeout_param(self):
        """Shell server schema advertises timeout parameter."""
        import importlib
        shell_mod = importlib.import_module("app.mcp_servers.shell_server")
        server = shell_mod.ShellServer()

        # Get tool list via handle_request (dict-based MCP protocol)
        loop = asyncio.new_event_loop()
        try:
            response = loop.run_until_complete(
                server.handle_request({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {}
                })
            )
        finally:
            loop.close()

        tools = response["result"]["tools"]
        shell_tool = next(t for t in tools if t["name"] == "run_shell_command")
        props = shell_tool["inputSchema"]["properties"]

        assert "timeout" in props, "Shell tool must expose timeout parameter"
        assert props["timeout"]["type"] == "number"

    def test_shell_server_max_timeout_matches_tool_exec(self):
        """Shell server max_timeout should not exceed TOOL_EXEC_TIMEOUT."""
        import importlib
        import os

        shell_mod = importlib.import_module("app.mcp_servers.shell_server")
        server = shell_mod.ShellServer()

        tool_exec_timeout = int(os.environ.get('TOOL_EXEC_TIMEOUT', '300'))

        assert server.max_timeout <= tool_exec_timeout, \
            f"Shell max_timeout ({server.max_timeout}) exceeds TOOL_EXEC_TIMEOUT ({tool_exec_timeout})"
