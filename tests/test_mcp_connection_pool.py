"""
Test suite for MCP connection pool functionality.
"""

import pytest
import asyncio
import time
from unittest.mock import Mock, AsyncMock, patch

from app.mcp.connection_pool import (
    MCPConnectionPool, PooledMCPClient, ConnectionState, ConnectionMetrics,
    get_connection_pool
)


class TestConnectionMetrics:
    """Test the ConnectionMetrics class."""
    
    def test_metrics_initialization(self):
        """Test metrics initialization."""
        metrics = ConnectionMetrics(
            created_at=time.time(),
            last_used=time.time(),
            total_requests=0,
            successful_requests=0,
            failed_requests=0,
            avg_response_time=0.0
        )
        
        assert metrics.total_requests == 0
        assert metrics.successful_requests == 0
        assert metrics.failed_requests == 0
        assert metrics.avg_response_time == 0.0
        assert hasattr(metrics, 'response_times')
    
    def test_record_request(self):
        """Test request recording."""
        metrics = ConnectionMetrics(
            created_at=time.time(),
            last_used=time.time(),
            total_requests=0,
            successful_requests=0,
            failed_requests=0,
            avg_response_time=0.0
        )
        
        # Record successful request
        metrics.record_request(True, 0.5)
        assert metrics.total_requests == 1
        assert metrics.successful_requests == 1
        assert metrics.failed_requests == 0
        assert metrics.avg_response_time == 0.5
        
        # Record failed request
        metrics.record_request(False, 1.0)
        assert metrics.total_requests == 2
        assert metrics.successful_requests == 1
        assert metrics.failed_requests == 1
        assert metrics.avg_response_time == 0.75  # (0.5 + 1.0) / 2
    
    def test_response_time_limit(self):
        """Test that response times are limited to last 100."""
        metrics = ConnectionMetrics(
            created_at=time.time(),
            last_used=time.time(),
            total_requests=0,
            successful_requests=0,
            failed_requests=0,
            avg_response_time=0.0
        )
        
        # Record 150 requests
        for i in range(150):
            metrics.record_request(True, i * 0.01)
        
        # Should only keep last 100 response times
        assert len(metrics.response_times) == 100
        assert metrics.response_times[0] == 0.5  # 50 * 0.01
        assert metrics.response_times[-1] == 1.49  # 149 * 0.01


class TestPooledMCPClient:
    """Test the PooledMCPClient class."""
    
    def setup_method(self):
        """Set up test client."""
        self.mock_client = Mock()
        self.pooled_client = PooledMCPClient(
            client=self.mock_client,
            server_name="test_server",
            conversation_id="test_conv"
        )
    
    @pytest.mark.asyncio
    async def test_connect_success(self):
        """Test successful connection."""
        self.mock_client.connect = AsyncMock(return_value=True)
        
        success = await self.pooled_client.connect()
        
        assert success is True
        assert self.pooled_client.state == ConnectionState.CONNECTED
        assert self.pooled_client.metrics.successful_requests == 1
        self.mock_client.connect.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_connect_failure(self):
        """Test failed connection."""
        self.mock_client.connect = AsyncMock(return_value=False)
        
        success = await self.pooled_client.connect()
        
        assert success is False
        assert self.pooled_client.state == ConnectionState.ERROR
        assert self.pooled_client.metrics.failed_requests == 1
    
    @pytest.mark.asyncio
    async def test_connect_already_connected(self):
        """Test connecting when already connected."""
        self.pooled_client.state = ConnectionState.CONNECTED
        self.mock_client.connect = AsyncMock()
        
        success = await self.pooled_client.connect()
        
        assert success is True
        self.mock_client.connect.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_disconnect(self):
        """Test disconnection."""
        self.pooled_client.state = ConnectionState.CONNECTED
        self.mock_client.disconnect = AsyncMock()
        
        await self.pooled_client.disconnect()
        
        assert self.pooled_client.state == ConnectionState.DISCONNECTED
        self.mock_client.disconnect.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_call_tool_success(self):
        """Test successful tool call."""
        self.pooled_client.state = ConnectionState.CONNECTED
        self.mock_client.call_tool = AsyncMock(return_value={"result": "success"})
        
        result = await self.pooled_client.call_tool("test_tool", {"arg": "value"})
        
        assert result == {"result": "success"}
        assert self.pooled_client.metrics.successful_requests == 1
        self.mock_client.call_tool.assert_called_once_with("test_tool", {"arg": "value"})
    
    @pytest.mark.asyncio
    async def test_call_tool_not_connected(self):
        """Test tool call when not connected."""
        self.pooled_client.state = ConnectionState.DISCONNECTED
        
        result = await self.pooled_client.call_tool("test_tool", {})
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_call_tool_error(self):
        """Test tool call with error response."""
        self.pooled_client.state = ConnectionState.CONNECTED
        self.mock_client.call_tool = AsyncMock(return_value={"error": True, "message": "Tool failed"})
        
        result = await self.pooled_client.call_tool("test_tool", {})
        
        assert result == {"error": True, "message": "Tool failed"}
        assert self.pooled_client.metrics.failed_requests == 1
    
    def test_is_healthy_connected(self):
        """Test health check for connected client."""
        self.pooled_client.state = ConnectionState.CONNECTED
        self.pooled_client.metrics.last_used = time.time()
        
        assert self.pooled_client.is_healthy() is True
    
    def test_is_healthy_not_connected(self):
        """Test health check for disconnected client."""
        self.pooled_client.state = ConnectionState.DISCONNECTED
        
        assert self.pooled_client.is_healthy() is False
    
    def test_is_healthy_idle_too_long(self):
        """Test health check for client idle too long."""
        self.pooled_client.state = ConnectionState.CONNECTED
        self.pooled_client.metrics.last_used = time.time() - 2000  # 2000 seconds ago
        
        assert self.pooled_client.is_healthy() is False
    
    def test_is_healthy_low_success_rate(self):
        """Test health check for client with low success rate."""
        self.pooled_client.state = ConnectionState.CONNECTED
        self.pooled_client.metrics.last_used = time.time()
        self.pooled_client.metrics.total_requests = 20
        self.pooled_client.metrics.successful_requests = 5  # 25% success rate
        
        assert self.pooled_client.is_healthy() is False


class TestMCPConnectionPool:
    """Test the MCPConnectionPool class."""
    
    def setup_method(self):
        """Set up test pool."""
        self.pool = MCPConnectionPool()
    
    def teardown_method(self):
        """Clean up after tests."""
        # Don't try to shutdown in teardown - it causes event loop issues
        # The pool will be garbage collected
        pass
    
    def test_set_server_configs(self):
        """Test setting server configurations."""
        configs = {
            "server1": {"command": ["echo", "test1"]},
            "server2": {"command": ["echo", "test2"]}
        }
        
        self.pool.set_server_configs(configs)
        
        assert self.pool.server_configs == configs
    
    @pytest.mark.asyncio
    async def test_get_client_new(self):
        """Test getting a new client."""
        configs = {
            "test_server": {
                "command": ["echo", "test"],
                "enabled": True
            }
        }
        self.pool.set_server_configs(configs)
        
        with patch('app.mcp.connection_pool.MCPClient') as mock_client_class:
            mock_client = Mock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_client_class.return_value = mock_client
            
            client = await self.pool.get_client("test_conv", "test_server")
            
            assert client is not None
            assert client.server_name == "test_server"
            assert client.conversation_id == "test_conv"
            assert "test_conv" in self.pool.pools
            assert "test_server" in self.pool.pools["test_conv"]
    
    @pytest.mark.asyncio
    async def test_get_client_existing_healthy(self):
        """Test getting an existing healthy client."""
        # Set up existing client
        mock_client = Mock()
        pooled_client = PooledMCPClient(mock_client, "test_server", "test_conv")
        pooled_client.state = ConnectionState.CONNECTED
        pooled_client.metrics.last_used = time.time()
        
        self.pool.pools["test_conv"] = {"test_server": pooled_client}
        
        client = await self.pool.get_client("test_conv", "test_server")
        
        assert client is pooled_client
    
    @pytest.mark.asyncio
    async def test_get_client_existing_unhealthy(self):
        """Test getting an existing unhealthy client."""
        configs = {
            "test_server": {
                "command": ["echo", "test"],
                "enabled": True
            }
        }
        self.pool.set_server_configs(configs)
        
        # Set up unhealthy client
        mock_client = Mock()
        mock_client.disconnect = AsyncMock()
        pooled_client = PooledMCPClient(mock_client, "test_server", "test_conv")
        pooled_client.state = ConnectionState.ERROR
        
        self.pool.pools["test_conv"] = {"test_server": pooled_client}
        
        with patch('app.mcp.connection_pool.MCPClient') as mock_client_class:
            new_mock_client = Mock()
            new_mock_client.connect = AsyncMock(return_value=True)
            mock_client_class.return_value = new_mock_client
            
            client = await self.pool.get_client("test_conv", "test_server")
            
            # Should disconnect old client and create new one
            mock_client.disconnect.assert_called_once()
            assert client is not pooled_client
            assert client.client is new_mock_client
    
    @pytest.mark.asyncio
    async def test_get_client_no_config(self):
        """Test getting client for non-existent server."""
        client = await self.pool.get_client("test_conv", "nonexistent_server")
        
        assert client is None
    
    @pytest.mark.asyncio
    async def test_call_tool_specific_server(self):
        """Test calling tool on specific server."""
        # Set up mock client with tool
        mock_client = Mock()
        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_client.tools = [mock_tool]
        
        pooled_client = PooledMCPClient(mock_client, "test_server", "test_conv")
        pooled_client.state = ConnectionState.CONNECTED
        pooled_client.call_tool = AsyncMock(return_value={"result": "success"})
        
        self.pool.pools["test_conv"] = {"test_server": pooled_client}
        
        result = await self.pool.call_tool("test_conv", "test_tool", {}, "test_server")
        
        assert result == {"result": "success"}
        pooled_client.call_tool.assert_called_once_with("test_tool", {})
    
    @pytest.mark.asyncio
    async def test_call_tool_any_server(self):
        """Test calling tool on any available server."""
        configs = {
            "server1": {"command": ["echo", "test1"]},
            "server2": {"command": ["echo", "test2"]}
        }
        self.pool.set_server_configs(configs)
        
        # Set up mock clients
        mock_client1 = Mock()
        mock_client1.tools = []  # No tools
        
        mock_client2 = Mock()
        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_client2.tools = [mock_tool]
        
        pooled_client1 = PooledMCPClient(mock_client1, "server1", "test_conv")
        pooled_client1.state = ConnectionState.CONNECTED
        
        pooled_client2 = PooledMCPClient(mock_client2, "server2", "test_conv")
        pooled_client2.state = ConnectionState.CONNECTED
        pooled_client2.call_tool = AsyncMock(return_value={"result": "success"})
        
        self.pool.pools["test_conv"] = {
            "server1": pooled_client1,
            "server2": pooled_client2
        }
        
        result = await self.pool.call_tool("test_conv", "test_tool", {})
        
        assert result == {"result": "success"}
        pooled_client2.call_tool.assert_called_once_with("test_tool", {})
    
    @pytest.mark.asyncio
    async def test_clear_conversation(self):
        """Test clearing conversation connections."""
        # Set up multiple clients for conversation
        mock_clients = []
        pooled_clients = []
        
        for i in range(3):
            mock_client = Mock()
            mock_client.disconnect = AsyncMock()
            pooled_client = PooledMCPClient(mock_client, f"server{i}", "test_conv")
            mock_clients.append(mock_client)
            pooled_clients.append(pooled_client)
        
        self.pool.pools["test_conv"] = {
            f"server{i}": pooled_clients[i] for i in range(3)
        }
        
        # Also set up different conversation
        other_client = PooledMCPClient(Mock(), "other_server", "other_conv")
        self.pool.pools["other_conv"] = {"other_server": other_client}
        
        with patch('app.mcp.security.get_execution_registry') as mock_registry:
            mock_registry.return_value.clear_conversation = Mock()
            
            await self.pool.clear_conversation("test_conv")
            
            # All clients should be disconnected
            for mock_client in mock_clients:
                mock_client.disconnect.assert_called_once()
            
            # Conversation should be removed
            assert "test_conv" not in self.pool.pools
            
            # Other conversation should remain
            assert "other_conv" in self.pool.pools
            
            # Security registry should be cleared
            mock_registry.return_value.clear_conversation.assert_called_once_with("test_conv")
    
    @pytest.mark.asyncio
    async def test_get_conversation_stats(self):
        """Test getting conversation statistics."""
        # Set up clients with different states
        mock_client1 = Mock()
        pooled_client1 = PooledMCPClient(mock_client1, "server1", "test_conv")
        pooled_client1.state = ConnectionState.CONNECTED
        pooled_client1.metrics.total_requests = 10
        pooled_client1.metrics.successful_requests = 8
        
        mock_client2 = Mock()
        pooled_client2 = PooledMCPClient(mock_client2, "server2", "test_conv")
        pooled_client2.state = ConnectionState.ERROR
        pooled_client2.metrics.total_requests = 5
        pooled_client2.metrics.failed_requests = 5
        
        self.pool.pools["test_conv"] = {
            "server1": pooled_client1,
            "server2": pooled_client2
        }
        
        stats = await self.pool.get_conversation_stats("test_conv")
        
        assert stats["total_connections"] == 2
        assert "server1" in stats["servers"]
        assert "server2" in stats["servers"]
        assert stats["servers"]["server1"]["state"] == "connected"
        assert stats["servers"]["server1"]["healthy"] is True
        assert stats["servers"]["server2"]["state"] == "error"
        assert stats["servers"]["server2"]["healthy"] is False
    
    @pytest.mark.asyncio
    async def test_get_conversation_stats_nonexistent(self):
        """Test getting stats for nonexistent conversation."""
        stats = await self.pool.get_conversation_stats("nonexistent_conv")
        
        assert stats["total_connections"] == 0
        assert stats["servers"] == {}
    
    @pytest.mark.asyncio
    async def test_cleanup_unhealthy_connections(self):
        """Test cleanup of unhealthy connections."""
        # Set up healthy and unhealthy clients
        healthy_client = PooledMCPClient(Mock(), "healthy_server", "test_conv")
        healthy_client.state = ConnectionState.CONNECTED
        healthy_client.metrics.last_used = time.time()
        
        unhealthy_client = PooledMCPClient(Mock(), "unhealthy_server", "test_conv")
        unhealthy_client.state = ConnectionState.ERROR
        unhealthy_client.disconnect = AsyncMock()
        
        self.pool.pools["test_conv"] = {
            "healthy_server": healthy_client,
            "unhealthy_server": unhealthy_client
        }
        
        await self.pool._cleanup_unhealthy_connections()
        
        # Unhealthy client should be removed
        assert "unhealthy_server" not in self.pool.pools["test_conv"]
        unhealthy_client.disconnect.assert_called_once()
        
        # Healthy client should remain
        assert "healthy_server" in self.pool.pools["test_conv"]
    
    @pytest.mark.asyncio
    async def test_shutdown(self):
        """Test pool shutdown."""
        # Set up clients
        mock_clients = []
        for i in range(3):
            mock_client = Mock()
            mock_client.disconnect = AsyncMock()
            pooled_client = PooledMCPClient(mock_client, f"server{i}", "test_conv")
            mock_clients.append(mock_client)
            
            if "test_conv" not in self.pool.pools:
                self.pool.pools["test_conv"] = {}
            self.pool.pools["test_conv"][f"server{i}"] = pooled_client
        
        await self.pool.shutdown()
        
        # All clients should be disconnected
        for mock_client in mock_clients:
            mock_client.disconnect.assert_called_once()
        
        # Pools should be cleared
        assert len(self.pool.pools) == 0


class TestGlobalConnectionPool:
    """Test the global connection pool instance."""
    
    def test_get_connection_pool_singleton(self):
        """Test that get_connection_pool returns singleton."""
        pool1 = get_connection_pool()
        pool2 = get_connection_pool()
        
        assert pool1 is pool2
        assert isinstance(pool1, MCPConnectionPool)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
