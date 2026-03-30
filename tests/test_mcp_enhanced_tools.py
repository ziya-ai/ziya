"""
Test suite for enhanced MCP tools with security features.

Covers:
  - Trigger parsing (tool calls, context requests, lint, diff validation)
  - SecureMCPTool._format_result (content extraction and truncation)
  - SecureMCPTool._arun (pool-based execution with timeout)
  - create_secure_mcp_tools factory
"""

import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from app.mcp.enhanced_tools import (
    SecureMCPTool, parse_enhanced_triggers, process_enhanced_triggers,
    execute_context_request, execute_lint_check, execute_diff_validation,
    create_secure_mcp_tools, _reset_counter_async,
    CONTEXT_REQUEST_OPEN, CONTEXT_REQUEST_CLOSE,
    LINT_CHECK_OPEN, LINT_CHECK_CLOSE,
    DIFF_VALIDATION_OPEN, DIFF_VALIDATION_CLOSE,
)


# ---------------------------------------------------------------------------
# Trigger Parsing
# ---------------------------------------------------------------------------

class TestEnhancedTriggerParsing:
    """Test parsing of enhanced trigger patterns."""

    def test_parse_tool_call_triggers(self):
        from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE

        content = (
            f"Some text before "
            f'{TOOL_SENTINEL_OPEN}<n>test_tool</n>'
            f'<arguments>{{"key": "value"}}</arguments>'
            f"{TOOL_SENTINEL_CLOSE} Some text after"
        )

        triggers = parse_enhanced_triggers(content)
        assert len(triggers) == 1
        assert triggers[0]["type"] == "tool_call"
        assert triggers[0]["tool_name"] == "test_tool"
        assert triggers[0]["arguments"] == {"key": "value"}

    def test_parse_context_request_triggers(self):
        content = f"Need context: {CONTEXT_REQUEST_OPEN}path/to/file.py{CONTEXT_REQUEST_CLOSE}"
        triggers = parse_enhanced_triggers(content)
        assert len(triggers) == 1
        assert triggers[0]["type"] == "context_request"
        assert triggers[0]["file_path"] == "path/to/file.py"

    def test_parse_lint_check_triggers(self):
        diff_content = "- old\n+ new"
        content = f"Check: {LINT_CHECK_OPEN}{diff_content}{LINT_CHECK_CLOSE}"
        triggers = parse_enhanced_triggers(content)
        assert len(triggers) == 1
        assert triggers[0]["type"] == "lint_check"
        assert diff_content.strip() in triggers[0]["diff_content"]

    def test_parse_diff_validation_triggers(self):
        diff_content = "some diff content"
        content = f"Validate: {DIFF_VALIDATION_OPEN}{diff_content}{DIFF_VALIDATION_CLOSE}"
        triggers = parse_enhanced_triggers(content)
        assert len(triggers) == 1
        assert triggers[0]["type"] == "diff_validation"
        assert triggers[0]["diff_content"] == diff_content

    def test_parse_multiple_triggers(self):
        from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
        content = (
            f"{CONTEXT_REQUEST_OPEN}file.py{CONTEXT_REQUEST_CLOSE}\n"
            f'{TOOL_SENTINEL_OPEN}<n>tool</n><arguments>{{"a":1}}</arguments>{TOOL_SENTINEL_CLOSE}'
        )
        triggers = parse_enhanced_triggers(content)
        assert len(triggers) >= 2

    def test_parse_malformed_json_in_tool_call(self):
        from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
        content = f'{TOOL_SENTINEL_OPEN}<n>tool</n><arguments>not json</arguments>{TOOL_SENTINEL_CLOSE}'
        # Should not crash — malformed JSON handled gracefully
        triggers = parse_enhanced_triggers(content)
        # Either no trigger (skipped) or a trigger with None/str arguments
        for t in triggers:
            if t["type"] == "tool_call":
                assert t["tool_name"] == "tool"


# ---------------------------------------------------------------------------
# SecureMCPTool._format_result
# ---------------------------------------------------------------------------

class TestSecureMCPToolFormatResult:
    """Test result formatting and truncation."""

    @pytest.fixture
    def tool(self):
        return SecureMCPTool(
            name="test_tool",
            description="A test tool",
            mcp_tool_name="test_tool",
            server_name="test-server",
        )

    def test_format_result_dict_with_content_list(self, tool):
        result = {"content": [{"text": "Tool output here"}]}
        formatted = tool._format_result(result, 1.5)
        assert "Tool output here" in formatted

    def test_format_result_string_content(self, tool):
        result = {"content": "Simple string output"}
        formatted = tool._format_result(result, 0.5)
        assert "Simple string output" in formatted

    def test_format_result_truncation(self, tool):
        large_content = "x" * 20000
        result = {"content": large_content}
        formatted = tool._format_result(result, 1.0)
        assert "truncated" in formatted.lower()
        assert len(formatted) < len(large_content)

    def test_format_result_plain_string(self, tool):
        formatted = tool._format_result("direct string", 0.1)
        assert "direct string" in formatted

    def test_format_result_none(self, tool):
        formatted = tool._format_result(None, 0.1)
        assert isinstance(formatted, str)


# ---------------------------------------------------------------------------
# SecureMCPTool._arun
# ---------------------------------------------------------------------------

class TestSecureMCPToolArun:
    """Test the async run method with connection pool."""

    @pytest.fixture
    def tool(self):
        return SecureMCPTool(
            name="test_tool",
            description="A test tool",
            mcp_tool_name="test_tool",
            server_name="test-server",
        )

    @pytest.mark.asyncio
    async def test_arun_success(self, tool):
        mock_pool = MagicMock()
        mock_pool.call_tool = AsyncMock(return_value={"content": [{"text": "result"}]})

        with patch('app.mcp.connection_pool.get_connection_pool', return_value=mock_pool):
            result = await tool._arun(tool_input={"key": "value"}, conversation_id="test-conv")
        assert "result" in result

    @pytest.mark.asyncio
    async def test_arun_timeout(self, tool):
        mock_pool = MagicMock()
        mock_pool.call_tool = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch('app.mcp.connection_pool.get_connection_pool', return_value=mock_pool):
            result = await tool._arun(tool_input={"key": "value"}, conversation_id="test-conv")
        assert "timed out" in result.lower() or "timeout" in result.lower()

    @pytest.mark.asyncio
    async def test_arun_no_result(self, tool):
        mock_pool = MagicMock()
        mock_pool.call_tool = AsyncMock(return_value=None)

        with patch('app.mcp.connection_pool.get_connection_pool', return_value=mock_pool):
            result = await tool._arun(tool_input={"key": "value"}, conversation_id="test-conv")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_arun_mcp_error(self, tool):
        mock_pool = MagicMock()
        mock_pool.call_tool = AsyncMock(side_effect=RuntimeError("connection lost"))

        with patch('app.mcp.connection_pool.get_connection_pool', return_value=mock_pool):
            result = await tool._arun(tool_input={"key": "value"}, conversation_id="test-conv")
        assert "error" in result.lower() or "failed" in result.lower()


# ---------------------------------------------------------------------------
# create_secure_mcp_tools factory
# ---------------------------------------------------------------------------

class TestSecureToolCreation:

    def test_create_secure_mcp_tools_success(self):
        mock_manager = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.description = "A tool"
        mock_tool.inputSchema = {"type": "object", "properties": {}}
        mock_manager.get_tools.return_value = [mock_tool]

        mock_pool = MagicMock()
        mock_pool.get_server_names.return_value = ["test-server"]

        with patch('app.mcp.manager.get_mcp_manager', return_value=mock_manager), \
             patch('app.mcp.connection_pool.get_connection_pool', return_value=mock_pool):
            tools = create_secure_mcp_tools()
        assert isinstance(tools, list)

    def test_create_secure_mcp_tools_not_initialized(self):
        """When MCP manager is None, still returns direct (built-in) tools."""
        with patch('app.mcp.manager.get_mcp_manager', return_value=None):
            tools = create_secure_mcp_tools()
        # Direct tools (file_read, ast_*, etc.) are always returned
        assert isinstance(tools, list)
        # No SecureMCPTool instances — only DirectMCPTools
        secure_tools = [t for t in tools if isinstance(t, SecureMCPTool)]
        assert len(secure_tools) == 0

    def test_create_secure_mcp_tools_exception(self):
        """When MCP manager raises, still returns direct (built-in) tools."""
        with patch('app.mcp.manager.get_mcp_manager', side_effect=Exception("init failed")):
            tools = create_secure_mcp_tools()
        assert isinstance(tools, list)
        secure_tools = [t for t in tools if isinstance(t, SecureMCPTool)]
        assert len(secure_tools) == 0
