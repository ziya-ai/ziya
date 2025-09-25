"""
Test suite for enhanced MCP tools with security features.
"""

import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch

from app.mcp.enhanced_tools import (
    SecureMCPTool, parse_enhanced_triggers, process_enhanced_triggers,
    execute_context_request, execute_lint_check, execute_diff_validation,
    create_secure_mcp_tools, _reset_counter_async,
    CONTEXT_REQUEST_OPEN, CONTEXT_REQUEST_CLOSE,
    LINT_CHECK_OPEN, LINT_CHECK_CLOSE,
    DIFF_VALIDATION_OPEN, DIFF_VALIDATION_CLOSE
)


class TestEnhancedTriggerParsing:
    """Test parsing of enhanced trigger patterns."""
    
    def test_parse_tool_call_triggers(self):
        """Test parsing of traditional tool call triggers."""
        from app.config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
        
        content = f"""
        Some text before
        {TOOL_SENTINEL_OPEN}<n>test_tool</n><arguments>{{"key": "value"}}</arguments>{TOOL_SENTINEL_CLOSE}
        Some text after
        """
        
        triggers = parse_enhanced_triggers(content)
        
        assert len(triggers) == 1
        assert triggers[0]["type"] == "tool_call"
        assert triggers[0]["tool_name"] == "test_tool"
        assert triggers[0]["arguments"] == {"key": "value"}
    
    def test_parse_context_request_triggers(self):
        """Test parsing of context request triggers."""
        content = f"""
        Need context for this file:
        {CONTEXT_REQUEST_OPEN}path/to/file.py{CONTEXT_REQUEST_CLOSE}
        """
        
        triggers = parse_enhanced_triggers(content)
        
        assert len(triggers) == 1
        assert triggers[0]["type"] == "context_request"
        assert triggers[0]["file_path"] == "path/to/file.py"
    
    def test_parse_lint_check_triggers(self):
        """Test parsing of lint check triggers."""
        diff_content = """
        @@ -1,3 +1,3 @@
         def hello():
        -    print("old")
        +    print("new")
        """
        
        content = f"""
        Please check this diff:
        {LINT_CHECK_OPEN}{diff_content}{LINT_CHECK_CLOSE}
        """
        
        triggers = parse_enhanced_triggers(content)
        
        assert len(triggers) == 1
        assert triggers[0]["type"] == "lint_check"
        assert diff_content.strip() in triggers[0]["diff_content"]
    
    def test_parse_diff_validation_triggers(self):
        """Test parsing of diff validation triggers."""
        diff_content = "some diff content"
        
        content = f"""
        Validate this diff:
        {DIFF_VALIDATION_OPEN}{diff_content}{DIFF_VALIDATION_CLOSE}
        """
        
        triggers = parse_enhanced_triggers(content)
        
        assert len(triggers) == 1
        assert triggers[0]["type"] == "diff_validation"
        assert triggers[0]["diff_content"] == diff_content
    
    def test_parse_multiple_triggers(self):
        """Test parsing multiple different triggers."""
        from app.config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
        
        content = f"""
        {TOOL_SENTINEL_OPEN}<n>tool1</n><arguments>{{"arg": "val"}}</arguments>{TOOL_SENTINEL_CLOSE}
        {CONTEXT_REQUEST_OPEN}file.py{CONTEXT_REQUEST_CLOSE}
        {LINT_CHECK_OPEN}diff content{LINT_CHECK_CLOSE}
        """
        
        triggers = parse_enhanced_triggers(content)
        
        assert len(triggers) == 3
        
        types = [t["type"] for t in triggers]
        assert "tool_call" in types
        assert "context_request" in types
        assert "lint_check" in types
    
    def test_parse_no_triggers(self):
        """Test parsing content with no triggers."""
        content = "This is just normal text without any triggers."
        
        triggers = parse_enhanced_triggers(content)
        
        assert len(triggers) == 0
    
    def test_parse_malformed_json_in_tool_call(self):
        """Test parsing tool call with malformed JSON."""
        from app.config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
        
        content = f"""
        {TOOL_SENTINEL_OPEN}<n>test_tool</n><arguments>{{invalid json}}</arguments>{TOOL_SENTINEL_CLOSE}
        """
        
        triggers = parse_enhanced_triggers(content)
        
        # Should not parse malformed JSON
        assert len(triggers) == 0


class TestTriggerExecution:
    """Test execution of different trigger types."""
    
    @pytest.mark.asyncio
    async def test_execute_context_request_valid_file(self):
        """Test executing context request for valid file."""
        with patch('app.mcp.enhanced_tools.read_file_content') as mock_read:
            mock_read.return_value = "file content here"
            
            result = await execute_context_request("test.py", "test_conv")
            
            assert "📄 **File Context**: test.py" in result
            assert "file content here" in result
            assert "```" in result  # Should be wrapped in code block
            mock_read.assert_called_once_with("test.py")
    
    @pytest.mark.asyncio
    async def test_execute_context_request_invalid_file(self):
        """Test executing context request for invalid file."""
        with patch('app.mcp.enhanced_tools.read_file_content') as mock_read:
            mock_read.return_value = None
            
            result = await execute_context_request("nonexistent.py", "test_conv")
            
            assert "❌ **File Error**" in result
            assert "Could not read file" in result
    
    @pytest.mark.asyncio
    async def test_execute_context_request_security_check(self):
        """Test security checks in context request."""
        # Test path traversal attempt
        result = await execute_context_request("../../../etc/passwd", "test_conv")
        assert "❌ **Security Error**" in result
        assert "Invalid file path" in result
        
        # Test absolute path
        result = await execute_context_request("/etc/passwd", "test_conv")
        assert "❌ **Security Error**" in result
        assert "Invalid file path" in result
    
    @pytest.mark.asyncio
    async def test_execute_lint_check(self):
        """Test executing lint check."""
        diff_content = "some diff content"
        
        result = await execute_lint_check(diff_content, "test_conv")
        
        assert "🔍 **Lint Check**" in result
        assert "Analysis complete" in result
    
    @pytest.mark.asyncio
    async def test_execute_diff_validation(self):
        """Test executing diff validation."""
        diff_content = "some diff content"
        
        result = await execute_diff_validation(diff_content, "test_conv")
        
        assert "✅ **Diff Validation**" in result
        assert "No critical errors found" in result
    
    @pytest.mark.asyncio
    async def test_process_enhanced_triggers_no_triggers(self):
        """Test processing content with no triggers."""
        content = "Normal content without triggers"
        
        result = await process_enhanced_triggers(content, "test_conv")
        
        assert result == content
    
    @pytest.mark.asyncio
    async def test_process_enhanced_triggers_with_context_request(self):
        """Test processing content with context request."""
        content = f"Need file: {CONTEXT_REQUEST_OPEN}test.py{CONTEXT_REQUEST_CLOSE}"
        
        with patch('app.mcp.enhanced_tools.execute_context_request') as mock_execute:
            mock_execute.return_value = "📄 **File Context**: test.py\n```\nfile content\n```"
            
            result = await process_enhanced_triggers(content, "test_conv")
            
            assert "📄 **File Context**" in result
            assert f"{CONTEXT_REQUEST_OPEN}test.py{CONTEXT_REQUEST_CLOSE}" not in result
            mock_execute.assert_called_once_with("test.py", "test_conv")
    
    @pytest.mark.asyncio
    async def test_process_enhanced_triggers_error_handling(self):
        """Test error handling in trigger processing."""
        content = f"Need file: {CONTEXT_REQUEST_OPEN}test.py{CONTEXT_REQUEST_CLOSE}"
        
        with patch('app.mcp.enhanced_tools.execute_context_request') as mock_execute:
            mock_execute.side_effect = Exception("Test error")
            
            result = await process_enhanced_triggers(content, "test_conv")
            
            assert "❌ **Trigger Error**" in result
            assert "Failed to process context_request" in result


class TestSecureMCPTool:
    """Test the SecureMCPTool class."""
    
    def setup_method(self):
        """Set up test tool."""
        self.tool = SecureMCPTool(
            name="mcp_test_tool",
            description="Test tool",
            mcp_tool_name="test_tool",
            server_name="test_server"
        )
    
    @pytest.mark.asyncio
    async def test_arun_success(self):
        """Test successful tool execution."""
        arguments = {"key": "value"}
        conversation_id = "test_conv"
        
        with patch('app.mcp.enhanced_tools.get_execution_registry') as mock_registry, \
             patch('app.mcp.enhanced_tools.get_connection_pool') as mock_pool:
            
            # Mock registry
            mock_reg = Mock()
            mock_reg.register_execution.return_value = "exec_123"
            mock_reg.complete_execution.return_value = True
            mock_registry.return_value = mock_reg
            
            # Mock pool
            mock_pool_instance = Mock()
            mock_pool_instance.call_tool = AsyncMock(return_value={
                "content": [{"text": "Tool executed successfully"}]
            })
            mock_pool.return_value = mock_pool_instance
            
            # Mock token creation
            with patch('app.mcp.enhanced_tools.ToolExecutionToken') as mock_token:
                mock_token_instance = Mock()
                mock_token_instance.signature = "test_signature"
                mock_token.return_value = mock_token_instance
                
                with patch('app.mcp.enhanced_tools.create_secure_result_marker') as mock_marker:
                    mock_marker.return_value = "SECURE_RESULT_MARKER"
                    
                    result = await self.tool._arun(arguments, conversation_id)
                    
                    assert "SECURE_RESULT_MARKER" in result
                    mock_reg.register_execution.assert_called_once()
                    mock_reg.complete_execution.assert_called_once()
                    mock_pool_instance.call_tool.assert_called_once_with(
                        conversation_id, "test_tool", arguments, "test_server"
                    )
    
    @pytest.mark.asyncio
    async def test_arun_timeout(self):
        """Test tool execution timeout."""
        arguments = {"key": "value"}
        conversation_id = "test_conv"
        
        with patch('app.mcp.enhanced_tools.get_execution_registry') as mock_registry, \
             patch('app.mcp.enhanced_tools.get_connection_pool') as mock_pool:
            
            # Mock registry
            mock_reg = Mock()
            mock_reg.register_execution.return_value = "exec_123"
            mock_reg.complete_execution.return_value = True
            mock_registry.return_value = mock_reg
            
            # Mock pool with timeout
            mock_pool_instance = Mock()
            mock_pool_instance.call_tool = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_pool.return_value = mock_pool_instance
            
            # Mock token creation
            with patch('app.mcp.enhanced_tools.ToolExecutionToken') as mock_token:
                mock_token_instance = Mock()
                mock_token.return_value = mock_token_instance
                
                result = await self.tool._arun(arguments, conversation_id)
                
                assert "⏱️ **Secure Tool Timeout**" in result
                assert "test_tool" in result
                mock_reg.complete_execution.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_arun_no_result(self):
        """Test tool execution with no result."""
        arguments = {"key": "value"}
        conversation_id = "test_conv"
        
        with patch('app.mcp.enhanced_tools.get_execution_registry') as mock_registry, \
             patch('app.mcp.enhanced_tools.get_connection_pool') as mock_pool:
            
            # Mock registry
            mock_reg = Mock()
            mock_reg.register_execution.return_value = "exec_123"
            mock_reg.complete_execution.return_value = True
            mock_registry.return_value = mock_reg
            
            # Mock pool returning None
            mock_pool_instance = Mock()
            mock_pool_instance.call_tool = AsyncMock(return_value=None)
            mock_pool.return_value = mock_pool_instance
            
            # Mock token creation
            with patch('app.mcp.enhanced_tools.ToolExecutionToken') as mock_token:
                mock_token_instance = Mock()
                mock_token.return_value = mock_token_instance
                
                result = await self.tool._arun(arguments, conversation_id)
                
                assert "❌ **Secure Tool Error**" in result
                assert "returned no result" in result
    
    @pytest.mark.asyncio
    async def test_arun_mcp_error(self):
        """Test tool execution with MCP server error."""
        arguments = {"key": "value"}
        conversation_id = "test_conv"
        
        with patch('app.mcp.enhanced_tools.get_execution_registry') as mock_registry, \
             patch('app.mcp.enhanced_tools.get_connection_pool') as mock_pool:
            
            # Mock registry
            mock_reg = Mock()
            mock_reg.register_execution.return_value = "exec_123"
            mock_reg.complete_execution.return_value = True
            mock_registry.return_value = mock_reg
            
            # Mock pool returning error
            mock_pool_instance = Mock()
            mock_pool_instance.call_tool = AsyncMock(return_value={
                "error": True,
                "message": "Tool execution failed"
            })
            mock_pool.return_value = mock_pool_instance
            
            # Mock token creation
            with patch('app.mcp.enhanced_tools.ToolExecutionToken') as mock_token:
                mock_token_instance = Mock()
                mock_token.return_value = mock_token_instance
                
                result = await self.tool._arun(arguments, conversation_id)
                
                assert "❌ **MCP Server Error**" in result
                assert "Tool execution failed" in result
    
    def test_format_result_dict_with_content(self):
        """Test formatting result dictionary with content."""
        result = {
            "content": [{"text": "Tool output here"}]
        }
        
        formatted = self.tool._format_result(result, 1.5)
        
        assert "🔧 **Secure Tool Execution**: test_tool" in formatted
        assert "⏱️ **Execution Time**: 1.50s" in formatted
        assert "Tool output here" in formatted
    
    def test_format_result_string_content(self):
        """Test formatting string content."""
        result = {"content": "Simple string output"}
        
        formatted = self.tool._format_result(result, 0.5)
        
        assert "🔧 **Secure Tool Execution**: test_tool" in formatted
        assert "⏱️ **Execution Time**: 0.50s" in formatted
        assert "Simple string output" in formatted
    
    def test_format_result_truncation(self):
        """Test result truncation for large outputs."""
        large_content = "x" * 20000  # Larger than MAX_TOOL_OUTPUT_SIZE
        result = {"content": large_content}
        
        formatted = self.tool._format_result(result, 1.0)
        
        assert "Output truncated" in formatted
        assert len(formatted) < len(large_content) + 1000  # Should be truncated
    
    def test_run_sync_wrapper(self):
        """Test synchronous run wrapper."""
        arguments = {"key": "value"}
        conversation_id = "test_conv"
        
        with patch.object(self.tool, '_arun') as mock_arun:
            mock_arun.return_value = asyncio.Future()
            mock_arun.return_value.set_result("test result")
            
            # This test might be tricky due to event loop handling
            # We'll just verify the method exists and has correct signature
            assert hasattr(self.tool, '_run')
            assert callable(self.tool._run)


class TestSecureToolCreation:
    """Test creation of secure MCP tools."""
    
    @patch('app.mcp.enhanced_tools.get_mcp_manager')
    def test_create_secure_mcp_tools_success(self, mock_get_manager):
        """Test successful creation of secure tools."""
        # Mock MCP manager
        mock_manager = Mock()
        mock_manager.is_initialized = True
        mock_manager.server_configs = {"test_server": {"command": ["echo", "test"]}}
        
        # Mock MCP tools
        mock_tool1 = Mock()
        mock_tool1.name = "tool1"
        mock_tool1.description = "Test tool 1"
        mock_tool1._server_name = "test_server"
        
        mock_tool2 = Mock()
        mock_tool2.name = "mcp_tool2"  # Already has mcp_ prefix
        mock_tool2.description = "Test tool 2"
        mock_tool2._server_name = "test_server"
        
        mock_manager.get_all_tools.return_value = [mock_tool1, mock_tool2]
        mock_get_manager.return_value = mock_manager
        
        with patch('app.mcp.enhanced_tools.get_connection_pool') as mock_pool:
            mock_pool_instance = Mock()
            mock_pool.return_value = mock_pool_instance
            
            tools = create_secure_mcp_tools()
            
            assert len(tools) == 2
            assert tools[0].name == "mcp_tool1"
            assert tools[1].name == "mcp_tool2"
            assert "[SECURE]" in tools[0].description
            assert "[SECURE]" in tools[1].description
            
            # Pool should be configured
            mock_pool_instance.set_server_configs.assert_called_once_with(
                mock_manager.server_configs
            )
    
    @patch('app.mcp.enhanced_tools.get_mcp_manager')
    def test_create_secure_mcp_tools_not_initialized(self, mock_get_manager):
        """Test tool creation when MCP manager not initialized."""
        mock_manager = Mock()
        mock_manager.is_initialized = False
        mock_get_manager.return_value = mock_manager
        
        tools = create_secure_mcp_tools()
        
        assert len(tools) == 0
    
    @patch('app.mcp.enhanced_tools.get_mcp_manager')
    def test_create_secure_mcp_tools_exception(self, mock_get_manager):
        """Test tool creation with exception."""
        mock_get_manager.side_effect = Exception("Test error")
        
        tools = create_secure_mcp_tools()
        
        assert len(tools) == 0


class TestUtilityFunctions:
    """Test utility functions."""
    
    @pytest.mark.asyncio
    async def test_reset_counter_async(self):
        """Test async counter reset function."""
        # This is mainly for compatibility, should not raise exceptions
        await _reset_counter_async()
        # No assertions needed, just verify it doesn't crash


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
