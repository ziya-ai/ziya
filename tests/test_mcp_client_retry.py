"""
Tests for MCP client retry logic.

Ensures that policy blocks (BLOCKED errors from shell server) are NOT retried,
while transient errors are retried with appropriate backoff.
"""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch


class TestSendRequestRetryLogic:
    """Test _send_request retry behavior for different error types."""

    def _make_client(self):
        """Create an MCPClient with a mock process for testing."""
        from app.mcp.client import MCPClient

        client = MCPClient({"name": "test-server", "command": ["echo"]})
        client.is_connected = True

        # Create a mock async process with stdin/stdout
        mock_process = MagicMock()
        mock_process.returncode = None  # process is alive
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.stdin.drain = AsyncMock()
        mock_process.stdout = MagicMock()
        client.process = mock_process

        return client, mock_process

    def _make_readline(self, client, error_code, error_message, counter):
        """Create a readline mock that tracks call count and matches request IDs."""
        async def counting_readline():
            counter["count"] += 1
            response = json.dumps({
                "jsonrpc": "2.0",
                "id": client.request_id,
                "error": {"code": error_code, "message": error_message}
            }) + "\n"
            return response.encode("utf-8")
        return counting_readline

    @pytest.mark.asyncio
    async def test_blocked_error_not_retried(self):
        """BLOCKED shell errors should return immediately without retrying."""
        client, mock_process = self._make_client()
        counter = {"count": 0}

        blocked_msg = (
            "🚫 BLOCKED: '{' is not allowed\n\n"
            "📋 Allowed commands: ls, cat, grep\n\n"
            "💡 Tip: configure in Shell Configuration settings."
        )
        mock_process.stdout.readline = self._make_readline(client, -32602, blocked_msg, counter)

        result = await client._send_request("tools/call", {
            "name": "run_shell_command",
            "arguments": {"command": "{"}
        })

        assert result is not None
        assert result.get("error") is True
        assert "BLOCKED" in result.get("message", "")
        assert result.get("policy_block") is True
        assert counter["count"] == 1, f"Expected 1 attempt, got {counter['count']} — BLOCKED errors must not retry"

    @pytest.mark.asyncio
    async def test_write_blocked_error_not_retried(self):
        """WRITE BLOCKED errors should also return immediately."""
        client, mock_process = self._make_client()
        counter = {"count": 0}

        mock_process.stdout.readline = self._make_readline(
            client, -32602, "🚫 WRITE BLOCKED: sed -i is not allowed", counter
        )

        result = await client._send_request("tools/call", {
            "name": "run_shell_command",
            "arguments": {"command": "sed -i 's/old/new/' file.py"}
        })

        assert result.get("error") is True
        assert result.get("policy_block") is True
        assert counter["count"] == 1

    @pytest.mark.asyncio
    async def test_security_block_not_retried(self):
        """SECURITY BLOCK errors should return immediately (pre-existing behavior)."""
        client, mock_process = self._make_client()
        counter = {"count": 0}

        mock_process.stdout.readline = self._make_readline(
            client, -32602, "SECURITY BLOCK: dangerous operation", counter
        )

        result = await client._send_request("tools/call", {
            "name": "run_shell_command",
            "arguments": {"command": "rm -rf /"}
        })

        assert result.get("error") is True
        assert counter["count"] == 1

    @pytest.mark.asyncio
    async def test_timeout_error_not_retried(self):
        """Timeout errors should fail immediately to let model try alternatives."""
        client, mock_process = self._make_client()
        counter = {"count": 0}

        mock_process.stdout.readline = self._make_readline(
            client, -32603, "Command timed out after 30 seconds", counter
        )

        result = await client._send_request("tools/call", {
            "name": "run_shell_command",
            "arguments": {"command": "sleep 999"}
        })

        assert result.get("error") is True
        assert counter["count"] == 1

    @pytest.mark.asyncio
    async def test_transient_error_is_retried(self):
        """ExtractArticle.js errors should be retried (transient fetch failures)."""
        client, mock_process = self._make_client()
        counter = {"count": 0}

        async def retry_then_succeed():
            counter["count"] += 1
            if counter["count"] <= 2:
                response = json.dumps({
                    "jsonrpc": "2.0",
                    "id": client.request_id,
                    "error": {
                        "code": -32603,
                        "message": "ExtractArticle.js failed with non-zero exit status"
                    }
                }) + "\n"
            else:
                response = json.dumps({
                    "jsonrpc": "2.0",
                    "id": client.request_id,
                    "result": {"content": [{"type": "text", "text": "success"}]}
                }) + "\n"
            return response.encode("utf-8")

        mock_process.stdout.readline = retry_then_succeed

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._send_request("tools/call", {
                "name": "fetch",
                "arguments": {"url": "https://example.com"}
            })

        # Should have retried at least once
        assert counter["count"] >= 2, f"Expected retries, got {counter['count']} attempts"
        # Third attempt should succeed
        if counter["count"] >= 3:
            assert result is not None
            assert "error" not in result or not result.get("error")

    @pytest.mark.asyncio
    async def test_generic_patterns_dont_match_blocked(self):
        """Verify that tightened external_server_errors patterns don't match BLOCKED messages."""
        blocked_msg = (
            "🚫 BLOCKED: '{' is not allowed\n\n"
            "📋 Allowed commands: ls, cat, grep\n\n"
            "💡 Tip: configure in Shell Configuration settings."
        )

        # These are the tightened patterns from the fix
        external_server_errors = [
            "ExtractArticle.js", "non-zero exit status",
            "temporary failure", "temporarily unavailable", "server is busy"
        ]

        should_retry = any(pattern in blocked_msg for pattern in external_server_errors)
        assert not should_retry, (
            "BLOCKED message incorrectly matched external_server_errors pattern. "
            "This would cause blocked commands to be retried."
        )

    @pytest.mark.asyncio
    async def test_old_broad_patterns_match_unrelated_errors(self):
        """The OLD overly-broad patterns would cause spurious retries for non-transient errors."""
        old_patterns = [
            "ExtractArticle.js", "non-zero exit status", "Command", "returned",
            "cache", "processing", "temporary", "busy"
        ]
        new_patterns = [
            "ExtractArticle.js", "non-zero exit status",
            "temporary failure", "temporarily unavailable", "server is busy"
        ]

        # These are non-transient errors that should NOT trigger retries
        non_transient_errors = [
            "Command not found: foobar",           # "Command" matches
            "Error processing request parameters",  # "processing" matches
            "Resource busy: file locked by user",   # "busy" matches
            "Invalid cache key format",             # "cache" matches
            "Function returned unexpected type",    # "returned" matches
        ]

        for error_msg in non_transient_errors:
            old_would_retry = any(p in error_msg for p in old_patterns)
            new_would_retry = any(p in error_msg for p in new_patterns)
            assert old_would_retry, f"Old patterns should have matched: {error_msg}"
            assert not new_would_retry, f"New patterns should NOT match: {error_msg}"
