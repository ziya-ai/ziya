"""
Tests for MCP connection pool functionality.

Updated: MCPConnectionPool renamed to ConnectionPool. PooledMCPClient,
ConnectionState, ConnectionMetrics removed. API simplified to
set_server_configs() and call_tool().
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch

from app.mcp.connection_pool import ConnectionPool, get_connection_pool


class TestConnectionPoolInit:
    """Test ConnectionPool initialization."""

    def test_pool_creation(self):
        """ConnectionPool should be instantiable."""
        pool = ConnectionPool()
        assert pool is not None

    def test_get_connection_pool_singleton(self):
        """get_connection_pool should return a ConnectionPool."""
        pool = get_connection_pool()
        assert isinstance(pool, ConnectionPool)

    def test_pool_has_set_server_configs(self):
        """Pool should have set_server_configs method."""
        pool = ConnectionPool()
        assert hasattr(pool, 'set_server_configs')
        assert callable(pool.set_server_configs)

    def test_pool_has_call_tool(self):
        """Pool should have call_tool method."""
        pool = ConnectionPool()
        assert hasattr(pool, 'call_tool')
        assert callable(pool.call_tool)


class TestConnectionPoolConfig:
    """Test ConnectionPool configuration."""

    def test_set_empty_configs(self):
        """Should accept empty server configs."""
        pool = ConnectionPool()
        pool.set_server_configs({})

    def test_set_server_configs(self):
        """Should accept server configuration dict."""
        pool = ConnectionPool()
        pool.set_server_configs({
            "test_server": {
                "command": "echo",
                "args": ["hello"],
            }
        })


class TestConnectionPoolCallTool:
    """Test ConnectionPool tool calling."""

    @pytest.mark.asyncio
    async def test_call_tool_unknown_server(self):
        """Calling tool on unknown server should raise or return error."""
        pool = ConnectionPool()
        with pytest.raises(Exception):
            await pool.call_tool("nonexistent_server", "some_tool", {})
