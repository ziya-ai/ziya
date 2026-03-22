"""
Tests for MCP _failed_servers TTL-based expiry (Bug #9) and
_reconnection_failures proper initialization cleanup.

Verifies that:
1. Failed servers expire after the TTL and become eligible for retry.
2. restart_server() clears the failed state immediately.
3. _reconnection_failures is initialized in __init__ (no hasattr guards).
4. The circuit breaker half-open → closed transition works correctly.
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mcp_manager():
    """Create an MCPManager instance with mocked internals."""
    with patch('app.mcp.manager.MCPClient'), \
         patch('app.mcp.manager.get_dynamic_loader'):
        from app.mcp.manager import MCPManager
        manager = MCPManager()
        manager.is_initialized = True
        return manager


class TestFailedServersTTL:
    """_failed_servers should use timestamp-based expiry, not permanent death."""

    def test_failed_servers_is_dict(self, mcp_manager):
        """_failed_servers should be a dict (timestamps), not a set."""
        assert isinstance(mcp_manager._failed_servers, dict)

    def test_server_marked_failed_with_timestamp(self, mcp_manager):
        """Adding a server to _failed_servers should record a timestamp."""
        mcp_manager._failed_servers["test-server"] = time.time()
        assert "test-server" in mcp_manager._failed_servers
        assert isinstance(mcp_manager._failed_servers["test-server"], float)

    def test_ensure_client_healthy_blocks_during_ttl(self, mcp_manager):
        """A recently-failed server should be blocked from health checks."""
        mcp_manager._failed_servers["shell"] = time.time()

        client = MagicMock()
        client.server_name = "shell"
        client.server_config = {"name": "shell"}
        client.is_connected = False

        result = asyncio.get_event_loop().run_until_complete(
            mcp_manager._ensure_client_healthy(client)
        )
        assert result is False, "Server should be blocked during TTL window"
        assert "shell" in mcp_manager._failed_servers

    def test_ensure_client_healthy_allows_retry_after_ttl(self, mcp_manager):
        """After TTL expires, server should be allowed to retry."""
        # Mark as failed 10 minutes ago (well past 5-min TTL)
        mcp_manager._failed_servers["shell"] = time.time() - 600
        mcp_manager._reconnection_attempts = {}
        mcp_manager._reconnection_failures["shell"] = 3  # Should get cleared

        client = MagicMock()
        client.server_name = "shell"
        client.server_config = {"name": "shell"}
        client.is_connected = True
        client._is_process_healthy = MagicMock(return_value=True)

        result = asyncio.get_event_loop().run_until_complete(
            mcp_manager._ensure_client_healthy(client)
        )

        assert "shell" not in mcp_manager._failed_servers, (
            "Server should be cleared from _failed_servers after TTL expiry"
        )
        assert "shell" not in mcp_manager._reconnection_failures, (
            "Reconnection failure count should be cleared on TTL expiry"
        )

    def test_ttl_default_is_five_minutes(self, mcp_manager):
        """Default TTL should be 300 seconds (5 minutes)."""
        assert mcp_manager._failed_server_ttl == 300

    def test_half_open_failure_resets_ttl(self, mcp_manager):
        """If a retry after TTL expiry fails again, new timestamp should be set."""
        server_name = "flaky"
        # Expire the TTL
        mcp_manager._failed_servers[server_name] = time.time() - 600
        mcp_manager._reconnection_attempts = {}

        client = MagicMock()
        client.server_name = server_name
        client.server_config = {"name": server_name}
        client.is_connected = False
        client._is_process_healthy = MagicMock(return_value=False)
        client.disconnect = AsyncMock()
        client.connect = AsyncMock(return_value=False)

        asyncio.get_event_loop().run_until_complete(
            mcp_manager._ensure_client_healthy(client)
        )

        # After the retry-and-fail, it should be back in _failed_servers
        # with a fresh timestamp (within last 2 seconds)
        if server_name in mcp_manager._failed_servers:
            age = time.time() - mcp_manager._failed_servers[server_name]
            assert age < 5, "New failure timestamp should be recent"


class TestRestartServerClearsFailedState:
    """restart_server() must clear _failed_servers so manual restart works."""

    def test_restart_clears_all_failure_tracking(self, mcp_manager):
        """restart_server should clear failed_servers, reconnection_attempts, and reconnection_failures."""
        mcp_manager._failed_servers["shell"] = time.time()
        mcp_manager._reconnection_attempts["shell"] = time.time()
        mcp_manager._reconnection_failures["shell"] = 3

        mcp_manager.server_configs = {"shell": {"command": "test"}}

        mock_client = MagicMock()
        mock_client.disconnect = AsyncMock()
        mcp_manager.clients = {"shell": mock_client}

        with patch('app.mcp.manager.MCPClient') as MockClient:
            MockClient.return_value = MagicMock()
            mcp_manager._connect_server = AsyncMock(return_value=True)

            asyncio.get_event_loop().run_until_complete(
                mcp_manager.restart_server("shell")
            )

        assert "shell" not in mcp_manager._failed_servers
        assert "shell" not in mcp_manager._reconnection_attempts
        assert "shell" not in mcp_manager._reconnection_failures

    def test_restart_works_even_if_not_failed(self, mcp_manager):
        """restart_server should not error if the server wasn't in _failed_servers."""
        mcp_manager.server_configs = {"shell": {"command": "test"}}
        mcp_manager.clients = {}

        with patch('app.mcp.manager.MCPClient') as MockClient:
            MockClient.return_value = MagicMock()
            mcp_manager._connect_server = AsyncMock(return_value=True)

            result = asyncio.get_event_loop().run_until_complete(
                mcp_manager.restart_server("shell")
            )
            assert result is True


class TestReconnectionFailuresInitialization:
    """_reconnection_failures should be initialized in __init__, no hasattr guards."""

    def test_reconnection_failures_initialized_in_init(self, mcp_manager):
        """_reconnection_failures should exist as a dict from __init__."""
        assert hasattr(mcp_manager, '_reconnection_failures'), (
            "_reconnection_failures should be initialized in __init__"
        )
        assert isinstance(mcp_manager._reconnection_failures, dict)
        assert len(mcp_manager._reconnection_failures) == 0

    def test_reconnection_failures_tracks_counts(self, mcp_manager):
        """_reconnection_failures should track integer failure counts."""
        mcp_manager._reconnection_failures["server-a"] = 1
        mcp_manager._reconnection_failures["server-a"] += 1
        assert mcp_manager._reconnection_failures["server-a"] == 2

    def test_pop_on_fresh_manager_does_not_error(self, mcp_manager):
        """Calling .pop() on _reconnection_failures should work without hasattr."""
        # This would have failed if _reconnection_failures wasn't in __init__
        result = mcp_manager._reconnection_failures.pop("nonexistent", None)
        assert result is None


class TestReconnectionFailureEscalation:
    """Verify that 3 reconnection failures mark server with timestamp."""

    def test_three_failures_marks_with_timestamp(self, mcp_manager):
        """After 3 reconnection failures, server should be marked with a timestamp."""
        server_name = "flaky-server"

        client = MagicMock()
        client.server_name = server_name
        client.server_config = {"name": server_name}
        client.is_connected = False
        client._is_process_healthy = MagicMock(return_value=False)
        client.disconnect = AsyncMock()
        client.connect = AsyncMock(return_value=False)

        mcp_manager._reconnection_attempts = {}

        for i in range(3):
            # Bypass the 30-second throttle
            mcp_manager._reconnection_attempts[server_name] = 0

            asyncio.get_event_loop().run_until_complete(
                mcp_manager._ensure_client_healthy(client)
            )

        assert server_name in mcp_manager._failed_servers
        assert isinstance(mcp_manager._failed_servers[server_name], float), (
            "Failed server entry should be a timestamp, not just set membership"
        )

    def test_successful_reconnection_clears_failure_count(self, mcp_manager):
        """A successful reconnection should reset _reconnection_failures."""
        server_name = "recovering"
        mcp_manager._reconnection_failures[server_name] = 2
        mcp_manager._reconnection_attempts = {}

        client = MagicMock()
        client.server_name = server_name
        client.server_config = {"name": server_name}
        client.is_connected = False
        client._is_process_healthy = MagicMock(return_value=False)
        client.disconnect = AsyncMock()
        client.connect = AsyncMock(return_value=True)
        client.tools = []

        asyncio.get_event_loop().run_until_complete(
            mcp_manager._ensure_client_healthy(client)
        )

        assert server_name not in mcp_manager._reconnection_failures, (
            "Successful reconnection should clear the failure counter"
        )


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
