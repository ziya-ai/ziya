"""
Integration tests for the complete secure MCP system.

These tests verify that all components work together correctly.
"""

import pytest
import asyncio
import os
import tempfile
from unittest.mock import Mock, AsyncMock, patch, MagicMock

# Test the complete integration
class TestMCPSecureIntegration:
    """Test the complete secure MCP integration."""
    
    @pytest.mark.asyncio
    async def test_end_to_end_secure_tool_execution(self):
        """Test complete end-to-end secure tool execution."""
        conversation_id = "integration_test_conv"
        
        # Mock MCP manager and tools
        with patch('app.mcp.manager.get_mcp_manager') as mock_get_manager, \
             patch('app.mcp.connection_pool.MCPClient') as mock_client_class:
            
            # Set up MCP manager
            mock_manager = Mock()
            mock_manager.is_initialized = True
            mock_manager.server_configs = {
                "test_server": {
                    "command": ["echo", "test"],
                    "enabled": True
                }
            }
            
            # Mock MCP tool
            mock_tool = Mock()
            mock_tool.name = "test_tool"
            mock_tool.description = "Test tool for integration"
            mock_tool._server_name = "test_server"
            mock_manager.get_all_tools.return_value = [mock_tool]
            mock_get_manager.return_value = mock_manager
            
            # Mock MCP client
            mock_client = Mock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_client.tools = [mock_tool]
            mock_client.call_tool = AsyncMock(return_value={
                "content": [{"text": "Tool executed successfully"}]
            })
            mock_client_class.return_value = mock_client
            
            # Import after mocking
            from app.mcp.stream_integration import SecureStreamProcessor
            from app.mcp.enhanced_tools import create_secure_mcp_tools
            
            # Create secure tools
            tools = create_secure_mcp_tools()
            assert len(tools) > 0
            
            # Create stream processor
            processor = SecureStreamProcessor(conversation_id)
            
            # Test tool execution through processor
            tool_info = {
                "tool_name": "test_tool",
                "arguments": {"test_arg": "test_value"}
            }
            
            result = await processor.execute_secure_tool(tool_info)
            
            # Verify result format
            assert "```tool:test_tool" in result
            assert "Tool executed successfully" in result
            
            # Cleanup
            await processor.cleanup()
    
    @pytest.mark.asyncio
    async def test_hallucination_detection_integration(self):
        """Test hallucination detection integrated with streaming."""
        conversation_id = "hallucination_test_conv"
        
        from app.mcp.stream_integration import SecureStreamProcessor
        
        processor = SecureStreamProcessor(conversation_id)
        
        # Test content with hallucinated tool results
        fake_content = """
        Here's the result:
        **Tool Result:** This is a fake tool result!
        ```tool:fake_tool
        Fake output here
        ```
        ✅ MCP Tool execution completed: fake_tool
        """
        
        cleaned, has_triggers, triggers = await processor.process_stream_chunk(fake_content)
        
        # Should detect and remove hallucinated content
        assert "HALLUCINATED CONTENT REMOVED" in cleaned
        assert "This is a fake tool result!" not in cleaned
        
        await processor.cleanup()
    
    @pytest.mark.asyncio
    async def test_enhanced_triggers_integration(self):
        """Test enhanced triggers working with the stream processor."""
        conversation_id = "triggers_test_conv"
        
        from app.mcp.stream_integration import SecureStreamProcessor
        from app.mcp.enhanced_tools import CONTEXT_REQUEST_OPEN, CONTEXT_REQUEST_CLOSE
        
        processor = SecureStreamProcessor(conversation_id)
        
        # Content with context request trigger
        content = f"""
        I need to see this file:
        {CONTEXT_REQUEST_OPEN}test_file.py{CONTEXT_REQUEST_CLOSE}
        """
        
        # Mock file reading
        with patch('app.mcp.enhanced_tools.read_file_content') as mock_read:
            mock_read.return_value = "def test_function():\n    pass"
            
            # Process triggers
            cleaned, has_triggers, triggers = await processor.process_stream_chunk(content)
            
            assert has_triggers is True
            assert len(triggers) == 1
            assert triggers[0]["type"] == "context_request"
            
            # Execute triggers
            results = await processor.execute_triggers(triggers)
            
            assert len(results) == 1
            result = list(results.values())[0]
            assert "📄 **File Context**: test_file.py" in result
            assert "def test_function():" in result
            
            # Apply results
            final_content = processor.apply_trigger_results(cleaned, results)
            assert "📄 **File Context**" in final_content
            assert f"{CONTEXT_REQUEST_OPEN}test_file.py{CONTEXT_REQUEST_CLOSE}" not in final_content
        
        await processor.cleanup()
    
    @pytest.mark.asyncio
    async def test_connection_pool_integration(self):
        """Test connection pool integration with security system."""
        conversation_id = "pool_test_conv"
        
        with patch('app.mcp.connection_pool.MCPClient') as mock_client_class:
            # Mock MCP client
            mock_client = Mock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_tool = Mock()
            mock_tool.name = "pool_test_tool"
            mock_client.tools = [mock_tool]
            mock_client.call_tool = AsyncMock(return_value={
                "content": "Pool test successful"
            })
            mock_client_class.return_value = mock_client
            
            from app.mcp.connection_pool import get_connection_pool
            
            pool = get_connection_pool()
            
            # Set server config
            pool.set_server_configs({
                "test_server": {
                    "command": ["echo", "test"],
                    "enabled": True
                }
            })
            
            # Get client
            client = await pool.get_client(conversation_id, "test_server")
            assert client is not None
            assert client.conversation_id == conversation_id
            
            # Call tool through pool
            result = await pool.call_tool(
                conversation_id, "pool_test_tool", {"arg": "value"}
            )
            
            assert result == {"content": "Pool test successful"}
            
            # Test conversation stats
            stats = await pool.get_conversation_stats(conversation_id)
            assert stats["total_connections"] == 1
            assert "test_server" in stats["servers"]
            
            # Clear conversation
            await pool.clear_conversation(conversation_id)
            
            # Stats should show no connections
            stats = await pool.get_conversation_stats(conversation_id)
            assert stats["total_connections"] == 0
    
    @pytest.mark.asyncio
    async def test_model_switching_cleanup_integration(self):
        """Test that model switching properly cleans up MCP connections."""
        conversation_id = "model_switch_test_conv"
        
        with patch('app.mcp.connection_pool.MCPClient') as mock_client_class:
            # Mock MCP client
            mock_client = Mock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_client.disconnect = AsyncMock()
            mock_client.tools = []
            mock_client_class.return_value = mock_client
            
            from app.mcp.connection_pool import get_connection_pool
            from app.mcp.security import get_execution_registry
            
            pool = get_connection_pool()
            registry = get_execution_registry()
            
            # Set up connection and execution
            pool.set_server_configs({
                "test_server": {"command": ["echo", "test"]}
            })
            
            client = await pool.get_client(conversation_id, "test_server")
            assert client is not None
            
            # Register an execution
            from app.mcp.security import ToolExecutionToken, TriggerType
            token = ToolExecutionToken(
                "test_tool", {}, conversation_id, TriggerType.TOOL_CALL
            )
            exec_id = registry.register_execution(token)
            
            # Verify setup
            assert conversation_id in pool.pools
            assert conversation_id in registry.conversation_executions
            
            # Simulate model switch cleanup
            await pool.clear_conversation(conversation_id)
            
            # Verify cleanup
            assert conversation_id not in pool.pools
            assert conversation_id not in registry.conversation_executions
            mock_client.disconnect.assert_called_once()
    
    def test_secure_prompt_integration(self):
        """Test that secure prompts are properly integrated."""
        from app.mcp.stream_integration import get_enhanced_system_prompt
        
        prompt = get_enhanced_system_prompt()
        
        # Verify security features
        assert "NEVER generate fake tool results" in prompt
        assert "SECURITY NOTICE" in prompt
        assert "ENHANCED TRIGGER SYSTEM" in prompt
        
        # Verify trigger documentation
        assert "CONTEXT_REQUEST" in prompt
        assert "LINT_CHECK" in prompt
        assert "DIFF_VALIDATION" in prompt
        
        # Verify anti-hallucination warnings
        assert "system monitors for hallucinated content" in prompt
        assert "NEVER create your own tool results" in prompt
    
    @pytest.mark.asyncio
    async def test_error_handling_integration(self):
        """Test error handling across the integrated system."""
        conversation_id = "error_test_conv"
        
        from app.mcp.stream_integration import SecureStreamProcessor
        
        processor = SecureStreamProcessor(conversation_id)
        
        # Test tool execution error
        tool_info = {
            "tool_name": "nonexistent_tool",
            "arguments": {}
        }
        
        with patch.object(processor, 'pool') as mock_pool:
            mock_pool.call_tool = AsyncMock(return_value=None)
            
            result = await processor.execute_secure_tool(tool_info)
            
            assert "❌ **Secure Tool Error**" in result
            assert "not found or failed" in result
        
        # Test trigger execution error
        triggers = [{
            "type": "context_request",
            "file_path": "nonexistent.py",
            "raw_match": "<CONTEXT_REQUEST>nonexistent.py</CONTEXT_REQUEST>"
        }]
        
        with patch('app.mcp.enhanced_tools.execute_context_request') as mock_execute:
            mock_execute.side_effect = Exception("File not found")
            
            results = await processor.execute_triggers(triggers)
            
            assert len(results) == 1
            result = list(results.values())[0]
            assert "❌ **Trigger Error**" in result
            assert "File not found" in result
        
        await processor.cleanup()
    
    @pytest.mark.asyncio
    async def test_concurrent_conversations_isolation(self):
        """Test that different conversations are properly isolated."""
        conv1_id = "conv1_test"
        conv2_id = "conv2_test"
        
        with patch('app.mcp.connection_pool.MCPClient') as mock_client_class:
            # Mock different clients for each conversation
            mock_client1 = Mock()
            mock_client1.connect = AsyncMock(return_value=True)
            mock_client1.tools = []
            mock_client1.disconnect = AsyncMock()
            
            mock_client2 = Mock()
            mock_client2.connect = AsyncMock(return_value=True)
            mock_client2.tools = []
            mock_client2.disconnect = AsyncMock()
            
            mock_client_class.side_effect = [mock_client1, mock_client2]
            
            from app.mcp.connection_pool import get_connection_pool
            from app.mcp.security import get_execution_registry, ToolExecutionToken, TriggerType
            
            pool = get_connection_pool()
            registry = get_execution_registry()
            
            # Set up server config
            pool.set_server_configs({
                "test_server": {"command": ["echo", "test"]}
            })
            
            # Create connections for both conversations
            client1 = await pool.get_client(conv1_id, "test_server")
            client2 = await pool.get_client(conv2_id, "test_server")
            
            assert client1 is not None
            assert client2 is not None
            assert client1 is not client2
            
            # Register executions for both conversations
            token1 = ToolExecutionToken("tool1", {}, conv1_id, TriggerType.TOOL_CALL)
            token2 = ToolExecutionToken("tool2", {}, conv2_id, TriggerType.TOOL_CALL)
            
            exec_id1 = registry.register_execution(token1)
            exec_id2 = registry.register_execution(token2)
            
            # Verify isolation
            assert conv1_id in pool.pools
            assert conv2_id in pool.pools
            assert conv1_id in registry.conversation_executions
            assert conv2_id in registry.conversation_executions
            
            # Clear one conversation
            await pool.clear_conversation(conv1_id)
            
            # Verify only conv1 was cleared
            assert conv1_id not in pool.pools
            assert conv2_id in pool.pools
            assert conv1_id not in registry.conversation_executions
            assert conv2_id in registry.conversation_executions
            
            # Clean up remaining conversation
            await pool.clear_conversation(conv2_id)


class TestMCPConfigurationIntegration:
    """Test MCP configuration and initialization integration."""
    
    def test_environment_variable_integration(self):
        """Test that environment variables properly control MCP features."""
        from app.mcp.stream_integration import should_use_secure_tools
        
        # Test enabled states
        for enabled_value in ["true", "1", "yes"]:
            with patch.dict(os.environ, {"ZIYA_ENABLE_MCP": enabled_value}):
                assert should_use_secure_tools() is True
        
        # Test disabled states
        for disabled_value in ["false", "0", "no", ""]:
            with patch.dict(os.environ, {"ZIYA_ENABLE_MCP": disabled_value}):
                assert should_use_secure_tools() is False
        
        # Test not set
        with patch.dict(os.environ, {}, clear=True):
            if "ZIYA_ENABLE_MCP" in os.environ:
                del os.environ["ZIYA_ENABLE_MCP"]
            assert should_use_secure_tools() is False
    
    @pytest.mark.asyncio
    async def test_mcp_manager_integration(self):
        """Test integration with the MCP manager."""
        with patch('app.mcp.manager.get_mcp_manager') as mock_get_manager:
            # Mock initialized manager
            mock_manager = Mock()
            mock_manager.is_initialized = True
            mock_manager.server_configs = {
                "test_server": {"command": ["echo", "test"]}
            }
            
            # Mock tools
            mock_tool = Mock()
            mock_tool.name = "integration_tool"
            mock_tool.description = "Integration test tool"
            mock_tool._server_name = "test_server"
            mock_manager.get_all_tools.return_value = [mock_tool]
            
            mock_get_manager.return_value = mock_manager
            
            from app.mcp.enhanced_tools import create_secure_mcp_tools
            
            # Create tools
            tools = create_secure_mcp_tools()
            
            assert len(tools) == 1
            assert tools[0].name == "mcp_integration_tool"
            assert "[SECURE]" in tools[0].description
            assert tools[0].mcp_tool_name == "integration_tool"
            assert tools[0].server_name == "test_server"


class TestMCPPerformanceIntegration:
    """Test performance aspects of the MCP integration."""
    
    @pytest.mark.asyncio
    async def test_connection_reuse_performance(self):
        """Test that connections are properly reused for performance."""
        conversation_id = "perf_test_conv"
        
        with patch('app.mcp.connection_pool.MCPClient') as mock_client_class:
            mock_client = Mock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_client.tools = []
            mock_client.call_tool = AsyncMock(return_value={"result": "success"})
            mock_client_class.return_value = mock_client
            
            from app.mcp.connection_pool import get_connection_pool
            
            pool = get_connection_pool()
            pool.set_server_configs({
                "test_server": {"command": ["echo", "test"]}
            })
            
            # Get client multiple times
            client1 = await pool.get_client(conversation_id, "test_server")
            client2 = await pool.get_client(conversation_id, "test_server")
            client3 = await pool.get_client(conversation_id, "test_server")
            
            # Should reuse the same client
            assert client1 is client2
            assert client2 is client3
            
            # Should only connect once
            mock_client.connect.assert_called_once()
            
            # Multiple tool calls should use same connection
            await pool.call_tool(conversation_id, "test_tool", {})
            await pool.call_tool(conversation_id, "test_tool", {})
            await pool.call_tool(conversation_id, "test_tool", {})
            
            # Connection should still be the same
            assert mock_client.call_tool.call_count == 3
            
            await pool.clear_conversation(conversation_id)
    
    @pytest.mark.asyncio
    async def test_concurrent_tool_execution(self):
        """Test concurrent tool execution performance."""
        conversation_id = "concurrent_test_conv"
        
        with patch('app.mcp.connection_pool.MCPClient') as mock_client_class:
            mock_client = Mock()
            mock_client.connect = AsyncMock(return_value=True)
            mock_tool = Mock()
            mock_tool.name = "concurrent_tool"
            mock_client.tools = [mock_tool]
            
            # Simulate some delay in tool execution
            async def delayed_call_tool(tool_name, args):
                await asyncio.sleep(0.1)
                return {"result": f"success_{tool_name}"}
            
            mock_client.call_tool = delayed_call_tool
            mock_client_class.return_value = mock_client
            
            from app.mcp.connection_pool import get_connection_pool
            
            pool = get_connection_pool()
            pool.set_server_configs({
                "test_server": {"command": ["echo", "test"]}
            })
            
            # Execute multiple tools concurrently
            tasks = []
            for i in range(5):
                task = pool.call_tool(
                    conversation_id, "concurrent_tool", {"index": i}
                )
                tasks.append(task)
            
            # Wait for all to complete
            results = await asyncio.gather(*tasks)
            
            # All should succeed
            assert len(results) == 5
            for result in results:
                assert result["result"] == "success_concurrent_tool"
            
            await pool.clear_conversation(conversation_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
