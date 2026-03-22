"""
Tests for MCPClient.get_resource() error handling.

Regression test for a bug where the except block in get_resource() referenced
undefined variables `name` and `arguments` (copy-paste residue from call_tool),
causing a secondary NameError that masked the original exception.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock


class TestGetResource:
    """Test MCPClient.get_resource() behavior."""

    def _make_client(self):
        """Create an MCPClient with minimal mocking."""
        from app.mcp.client import MCPClient

        client = MCPClient({"name": "test-server", "command": ["echo"]})
        client.is_connected = True
        return client

    @pytest.mark.asyncio
    async def test_get_resource_returns_text_on_success(self):
        """Happy path: resource read returns text content."""
        client = self._make_client()
        client._send_request = AsyncMock(return_value={
            "contents": [{"text": "hello world"}]
        })

        result = await client.get_resource("file:///test.txt")
        assert result == "hello world"
        client._send_request.assert_called_once_with(
            "resources/read", {"uri": "file:///test.txt"}
        )

    @pytest.mark.asyncio
    async def test_get_resource_returns_none_on_empty_contents(self):
        """Returns None when server returns empty contents array."""
        client = self._make_client()
        client._send_request = AsyncMock(return_value={"contents": []})

        result = await client.get_resource("file:///empty.txt")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_resource_returns_none_on_missing_contents(self):
        """Returns None when server response has no 'contents' key."""
        client = self._make_client()
        client._send_request = AsyncMock(return_value={"something_else": True})

        result = await client.get_resource("file:///missing.txt")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_resource_returns_none_on_null_response(self):
        """Returns None when _send_request returns None."""
        client = self._make_client()
        client._send_request = AsyncMock(return_value=None)

        result = await client.get_resource("file:///null.txt")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_resource_returns_none_on_exception(self):
        """Regression: exception must not crash with NameError on undefined variables.

        Before the fix, the except block tried to call tools/call with undefined
        `name` and `arguments`, producing a NameError that masked the real error.
        Now it logs and returns None.
        """
        client = self._make_client()
        client._send_request = AsyncMock(
            side_effect=ConnectionError("server went away")
        )

        # Must NOT raise — should return None gracefully
        result = await client.get_resource("file:///crash.txt")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_resource_exception_does_not_call_tools(self):
        """Verify the except block does NOT attempt a tools/call fallback."""
        client = self._make_client()

        call_log = []
        original_send = AsyncMock(side_effect=RuntimeError("boom"))

        async def tracking_send(method, params):
            call_log.append(method)
            return await original_send(method, params)

        client._send_request = tracking_send

        result = await client.get_resource("file:///track.txt")
        assert result is None
        # Only the initial resources/read should have been attempted
        assert call_log == ["resources/read"]

    @pytest.mark.asyncio
    async def test_get_resource_returns_empty_string_for_empty_text(self):
        """Returns empty string when text field exists but is empty."""
        client = self._make_client()
        client._send_request = AsyncMock(return_value={
            "contents": [{"text": ""}]
        })

        result = await client.get_resource("file:///empty-text.txt")
        assert result == ""
