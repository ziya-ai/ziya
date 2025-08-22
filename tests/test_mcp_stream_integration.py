"""
Test suite for MCP stream integration functionality.
"""

import pytest
import asyncio
import os
from unittest.mock import Mock, AsyncMock, patch

from app.mcp.stream_integration import (
    SecureStreamProcessor, initialize_secure_streaming, cleanup_secure_streaming,
    get_enhanced_system_prompt, detect_and_execute_mcp_tools_secure,
    create_mcp_tools_secure, should_use_secure_tools
)


class TestSecureStreamProcessor:
    """Test the SecureStreamProcessor class."""
    
    def setup_method(self):
        """Set up test processor."""
        self.processor = SecureStreamProcessor("test_conv")
    
    @pytest.mark.asyncio
    async def test_process_stream_chunk_clean_content(self):
        """Test processing clean content without triggers."""
        content = "This is normal content without any triggers or hallucinations."
        
        with patch('app.mcp.stream_integration.detect_hallucinated_results') as mock_detect:
            mock_detect.return_value = (content, False)
            
            cleaned, has_triggers, triggers = await self.processor.process_stream_chunk(content)
            
            assert cleaned == content
            assert has_triggers is False
            assert len(triggers) == 0
            mock_detect.assert_called_once_with(content, "test_conv")
    
    @pytest.mark.asyncio
    async def test_process_stream_chunk_with_hallucination(self):
        """Test processing content with hallucinated results."""
        content = "Normal content **Tool Result:** This is fake!"
        cleaned_content = "Normal content ⚠️ **[HALLUCINATED CONTENT REMOVED]**"
        
        with patch('app.mcp.stream_integration.detect_hallucinated_results') as mock_detect:
            mock_detect.return_value = (cleaned_content, True)
            
            cleaned, has_triggers, triggers = await self.processor.process_stream_chunk(content)
            
            assert cleaned == cleaned_content
            assert "HALLUCINATED CONTENT REMOVED" in cleaned
            mock_detect.assert_called_once_with(content, "test_conv")
    
    @pytest.mark.asyncio
    async def test_process_stream_chunk_with_triggers(self):
        """Test processing content with triggers."""
        from app.mcp.enhanced_tools import CONTEXT_REQUEST_OPEN, CONTEXT_REQUEST_CLOSE
        
        content = f"Need file: {CONTEXT_REQUEST_OPEN}test.py{CONTEXT_REQUEST_CLOSE}"
        
        with patch('app.mcp.stream_integration.detect_hallucinated_results') as mock_detect, \
             patch('app.mcp.stream_integration.parse_enhanced_triggers') as mock_parse:
            
            mock_detect.return_value = (content, False)
            mock_parse.return_value = [{
                "type": "context_request",
                "file_path": "test.py",
                "raw_match": f"{CONTEXT_REQUEST_OPEN}test.py{CONTEXT_REQUEST_CLOSE}"
            }]
            
            cleaned, has_triggers, triggers = await self.processor.process_stream_chunk(content)
            
            assert cleaned == content
            assert has_triggers is True
            assert len(triggers) == 1
            assert triggers[0]["type"] == "context_request"
    
    @pytest.mark.asyncio
    async def test_process_stream_chunk_duplicate_triggers(self):
        """Test that duplicate triggers are filtered out."""
        from app.mcp.enhanced_tools import CONTEXT_REQUEST_OPEN, CONTEXT_REQUEST_CLOSE
        
        content = f"Need file: {CONTEXT_REQUEST_OPEN}test.py{CONTEXT_REQUEST_CLOSE}"
        
        with patch('app.mcp.stream_integration.detect_hallucinated_results') as mock_detect, \
             patch('app.mcp.stream_integration.parse_enhanced_triggers') as mock_parse:
            
            mock_detect.return_value = (content, False)
            trigger = {
                "type": "context_request",
                "file_path": "test.py",
                "raw_match": f"{CONTEXT_REQUEST_OPEN}test.py{CONTEXT_REQUEST_CLOSE}"
            }
            mock_parse.return_value = [trigger]
            
            # Process same content twice
            cleaned1, has_triggers1, triggers1 = await self.processor.process_stream_chunk(content)
            cleaned2, has_triggers2, triggers2 = await self.processor.process_stream_chunk(content)
            
            # First time should find triggers
            assert has_triggers1 is True
            assert len(triggers1) == 1
            
            # Second time should filter out duplicates
            assert has_triggers2 is False
            assert len(triggers2) == 0
    
    @pytest.mark.asyncio
    async def test_execute_triggers_context_request(self):
        """Test executing context request triggers."""
        triggers = [{
            "type": "context_request",
            "file_path": "test.py",
            "raw_match": "<CONTEXT_REQUEST>test.py</CONTEXT_REQUEST>"
        }]
        
        with patch('app.mcp.enhanced_tools.execute_context_request') as mock_execute:
            mock_execute.return_value = "📄 **File Context**: test.py\n```\nfile content\n```"
            
            results = await self.processor.execute_triggers(triggers)
            
            assert len(results) == 1
            assert "📄 **File Context**" in list(results.values())[0]
            mock_execute.assert_called_once_with("test.py", "test_conv")
    
    @pytest.mark.asyncio
    async def test_execute_triggers_lint_check(self):
        """Test executing lint check triggers."""
        triggers = [{
            "type": "lint_check",
            "diff_content": "some diff",
            "raw_match": "<LINT_CHECK>some diff</LINT_CHECK>"
        }]
        
        with patch('app.mcp.enhanced_tools.execute_lint_check') as mock_execute:
            mock_execute.return_value = "🔍 **Lint Check**: Analysis complete"
            
            results = await self.processor.execute_triggers(triggers)
            
            assert len(results) == 1
            assert "🔍 **Lint Check**" in list(results.values())[0]
            mock_execute.assert_called_once_with("some diff", "test_conv")
    
    @pytest.mark.asyncio
    async def test_execute_triggers_diff_validation(self):
        """Test executing diff validation triggers."""
        triggers = [{
            "type": "diff_validation",
            "diff_content": "some diff",
            "raw_match": "<DIFF_VALIDATION>some diff</DIFF_VALIDATION>"
        }]
        
        with patch('app.mcp.enhanced_tools.execute_diff_validation') as mock_execute:
            mock_execute.return_value = "✅ **Diff Validation**: No critical errors"
            
            results = await self.processor.execute_triggers(triggers)
            
            assert len(results) == 1
            assert "✅ **Diff Validation**" in list(results.values())[0]
            mock_execute.assert_called_once_with("some diff", "test_conv")
    
    @pytest.mark.asyncio
    async def test_execute_triggers_tool_call_skipped(self):
        """Test that tool call triggers are skipped (handled elsewhere)."""
        triggers = [{
            "type": "tool_call",
            "tool_name": "test_tool",
            "arguments": {"key": "value"},
            "raw_match": "<TOOL_CALL>...</TOOL_CALL>"
        }]
        
        results = await self.processor.execute_triggers(triggers)
        
        assert len(results) == 0  # Tool calls are handled by main streaming loop
    
    @pytest.mark.asyncio
    async def test_execute_triggers_error_handling(self):
        """Test error handling in trigger execution."""
        triggers = [{
            "type": "context_request",
            "file_path": "test.py",
            "raw_match": "<CONTEXT_REQUEST>test.py</CONTEXT_REQUEST>"
        }]
        
        with patch('app.mcp.enhanced_tools.execute_context_request') as mock_execute:
            mock_execute.side_effect = Exception("Test error")
            
            results = await self.processor.execute_triggers(triggers)
            
            assert len(results) == 1
            result = list(results.values())[0]
            assert "❌ **Trigger Error**" in result
            assert "Test error" in result
    
    def test_apply_trigger_results(self):
        """Test applying trigger results to content."""
        content = "Before <TRIGGER>placeholder</TRIGGER> After"
        results = {"<TRIGGER>placeholder</TRIGGER>": "REPLACED_CONTENT"}
        
        modified = self.processor.apply_trigger_results(content, results)
        
        assert modified == "Before REPLACED_CONTENT After"
    
    @pytest.mark.asyncio
    async def test_detect_tool_completion_with_sentinels(self):
        """Test detecting complete tool calls with sentinels."""
        from app.config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
        
        content = f"Some text {TOOL_SENTINEL_OPEN}<n>test_tool</n><arguments>{{}}</arguments>{TOOL_SENTINEL_CLOSE} more text"
        
        with patch('app.mcp.stream_integration.parse_enhanced_triggers') as mock_parse:
            mock_parse.return_value = [{
                "type": "tool_call",
                "tool_name": "test_tool",
                "arguments": {}
            }]
            
            has_complete, tool_info = await self.processor.detect_tool_completion(content)
            
            assert has_complete is True
            assert tool_info["tool_name"] == "test_tool"
    
    @pytest.mark.asyncio
    async def test_detect_tool_completion_no_tools(self):
        """Test detecting tool completion with no tools present."""
        content = "Just normal content without any tool calls"
        
        has_complete, tool_info = await self.processor.detect_tool_completion(content)
        
        assert has_complete is False
        assert tool_info is None
    
    @pytest.mark.asyncio
    async def test_execute_secure_tool_success(self):
        """Test successful secure tool execution."""
        tool_info = {
            "tool_name": "test_tool",
            "arguments": {"key": "value"}
        }
        
        with patch.object(self.processor, 'pool') as mock_pool:
            mock_pool.call_tool = AsyncMock(return_value={
                "content": [{"text": "Tool executed successfully"}]
            })
            
            result = await self.processor.execute_secure_tool(tool_info)
            
            assert "```tool:test_tool" in result
            assert "Tool executed successfully" in result
            mock_pool.call_tool.assert_called_once_with(
                "test_conv", "test_tool", {"key": "value"}
            )
    
    @pytest.mark.asyncio
    async def test_execute_secure_tool_not_found(self):
        """Test secure tool execution when tool not found."""
        tool_info = {
            "tool_name": "nonexistent_tool",
            "arguments": {}
        }
        
        with patch.object(self.processor, 'pool') as mock_pool:
            mock_pool.call_tool = AsyncMock(return_value=None)
            
            result = await self.processor.execute_secure_tool(tool_info)
            
            assert "❌ **Secure Tool Error**" in result
            assert "not found or failed" in result
    
    @pytest.mark.asyncio
    async def test_execute_secure_tool_mcp_error(self):
        """Test secure tool execution with MCP error."""
        tool_info = {
            "tool_name": "test_tool",
            "arguments": {}
        }
        
        with patch.object(self.processor, 'pool') as mock_pool:
            mock_pool.call_tool = AsyncMock(return_value={
                "error": True,
                "message": "Tool execution failed"
            })
            
            result = await self.processor.execute_secure_tool(tool_info)
            
            assert "❌ **MCP Server Error**" in result
            assert "Tool execution failed" in result
    
    @pytest.mark.asyncio
    async def test_execute_secure_tool_exception(self):
        """Test secure tool execution with exception."""
        tool_info = {
            "tool_name": "test_tool",
            "arguments": {}
        }
        
        with patch.object(self.processor, 'pool') as mock_pool:
            mock_pool.call_tool = AsyncMock(side_effect=Exception("Test error"))
            
            result = await self.processor.execute_secure_tool(tool_info)
            
            assert "❌ **Secure Tool Exception**" in result
            assert "Test error" in result
    
    def test_format_tool_result_dict_content(self):
        """Test formatting tool result with dict content."""
        result = {
            "content": [{"text": "Tool output"}]
        }
        
        formatted = self.processor._format_tool_result("test_tool", result)
        
        assert "```tool:test_tool" in formatted
        assert "Tool output" in formatted
        assert formatted.endswith("```")
    
    def test_format_tool_result_string_content(self):
        """Test formatting tool result with string content."""
        result = {"content": "Simple output"}
        
        formatted = self.processor._format_tool_result("test_tool", result)
        
        assert "```tool:test_tool" in formatted
        assert "Simple output" in formatted
    
    def test_format_tool_result_plain_string(self):
        """Test formatting plain string result."""
        result = "Plain string result"
        
        formatted = self.processor._format_tool_result("test_tool", result)
        
        assert "```tool:test_tool" in formatted
        assert "Plain string result" in formatted
    
    @pytest.mark.asyncio
    async def test_cleanup(self):
        """Test processor cleanup."""
        with patch.object(self.processor, 'pool') as mock_pool:
            mock_pool.clear_conversation = AsyncMock()
            
            await self.processor.cleanup()
            
            mock_pool.clear_conversation.assert_called_once_with("test_conv")


class TestStreamIntegrationFunctions:
    """Test module-level integration functions."""
    
    @pytest.mark.asyncio
    async def test_initialize_secure_streaming(self):
        """Test initializing secure streaming."""
        processor = await initialize_secure_streaming("test_conv")
        
        assert isinstance(processor, SecureStreamProcessor)
        assert processor.conversation_id == "test_conv"
    
    @pytest.mark.asyncio
    async def test_cleanup_secure_streaming(self):
        """Test cleaning up secure streaming."""
        with patch('app.mcp.stream_integration.get_connection_pool') as mock_pool, \
             patch('app.mcp.stream_integration.get_execution_registry') as mock_registry:
            
            mock_pool_instance = Mock()
            mock_pool_instance.clear_conversation = AsyncMock()
            mock_pool.return_value = mock_pool_instance
            
            mock_registry_instance = Mock()
            mock_registry_instance.clear_conversation = Mock()
            mock_registry.return_value = mock_registry_instance
            
            await cleanup_secure_streaming("test_conv")
            
            mock_pool_instance.clear_conversation.assert_called_once_with("test_conv")
            mock_registry_instance.clear_conversation.assert_called_once_with("test_conv")
    
    def test_get_enhanced_system_prompt(self):
        """Test getting enhanced system prompt."""
        prompt = get_enhanced_system_prompt()
        
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "ENHANCED TRIGGER SYSTEM" in prompt
        assert "CONTEXT_REQUEST" in prompt
        assert "LINT_CHECK" in prompt
        assert "DIFF_VALIDATION" in prompt
        assert "NEVER generate fake responses" in prompt
    
    def test_should_use_secure_tools_enabled(self):
        """Test should_use_secure_tools when enabled."""
        with patch.dict(os.environ, {"ZIYA_ENABLE_MCP": "true"}):
            assert should_use_secure_tools() is True
        
        with patch.dict(os.environ, {"ZIYA_ENABLE_MCP": "1"}):
            assert should_use_secure_tools() is True
        
        with patch.dict(os.environ, {"ZIYA_ENABLE_MCP": "yes"}):
            assert should_use_secure_tools() is True
    
    def test_should_use_secure_tools_disabled(self):
        """Test should_use_secure_tools when disabled."""
        with patch.dict(os.environ, {"ZIYA_ENABLE_MCP": "false"}):
            assert should_use_secure_tools() is False
        
        with patch.dict(os.environ, {"ZIYA_ENABLE_MCP": "0"}):
            assert should_use_secure_tools() is False
        
        with patch.dict(os.environ, {}):  # Not set
            assert should_use_secure_tools() is False
    
    @pytest.mark.asyncio
    async def test_detect_and_execute_mcp_tools_secure_disabled(self):
        """Test secure tool detection when disabled."""
        with patch('app.mcp.stream_integration.should_use_secure_tools') as mock_should_use:
            mock_should_use.return_value = False
            
            result = await detect_and_execute_mcp_tools_secure(
                "test content", "test_conv", set()
            )
            
            assert result == "test content"
    
    @pytest.mark.asyncio
    async def test_detect_and_execute_mcp_tools_secure_enabled(self):
        """Test secure tool detection when enabled."""
        content = "Test content with triggers"
        
        with patch('app.mcp.stream_integration.should_use_secure_tools') as mock_should_use, \
             patch('app.mcp.stream_integration.SecureStreamProcessor') as mock_processor_class:
            
            mock_should_use.return_value = True
            
            # Mock processor
            mock_processor = Mock()
            mock_processor.process_stream_chunk = AsyncMock(return_value=(
                content, True, [{"type": "context_request"}]
            ))
            mock_processor.execute_triggers = AsyncMock(return_value={
                "<TRIGGER>": "RESULT"
            })
            mock_processor.apply_trigger_results = Mock(return_value="Modified content")
            mock_processor_class.return_value = mock_processor
            
            result = await detect_and_execute_mcp_tools_secure(
                content, "test_conv", set()
            )
            
            assert result == "Modified content"
            mock_processor.process_stream_chunk.assert_called_once_with(content)
            mock_processor.execute_triggers.assert_called_once()
            mock_processor.apply_trigger_results.assert_called_once()
    
    def test_create_mcp_tools_secure_disabled(self):
        """Test secure tool creation when disabled."""
        with patch('app.mcp.stream_integration.should_use_secure_tools') as mock_should_use:
            mock_should_use.return_value = False
            
            tools = create_mcp_tools_secure()
            
            assert tools == []
    
    def test_create_mcp_tools_secure_enabled(self):
        """Test secure tool creation when enabled."""
        with patch('app.mcp.stream_integration.should_use_secure_tools') as mock_should_use, \
             patch('app.mcp.stream_integration.create_secure_mcp_tools') as mock_create:
            
            mock_should_use.return_value = True
            mock_tools = [Mock(), Mock()]
            mock_create.return_value = mock_tools
            
            tools = create_mcp_tools_secure()
            
            assert tools == mock_tools
            mock_create.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
