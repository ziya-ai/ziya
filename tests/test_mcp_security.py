"""
Tests for MCP security system.

Updated: get_execution_registry removed. detect_hallucinated_results,
validate_tool_execution_request, create_anti_hallucination_prompt removed.
SECURE_TOOL_PREFIX/SUFFIX removed. ToolExecutionToken.execution_id and
verify_signature() removed. register_execution() now takes a single
ToolExecutionToken argument.
"""

import pytest
import time
from app.mcp.security import (
    ToolExecutionToken,
    TriggerType,
    ToolExecutionRegistry,
)


class TestTriggerType:
    """Test TriggerType enum."""

    def test_tool_call_type(self):
        assert TriggerType.TOOL_CALL.value == "tool_call"

    def test_context_request_type(self):
        assert TriggerType.CONTEXT_REQUEST.value == "context_request"

    def test_lint_check_type(self):
        assert TriggerType.LINT_CHECK.value == "lint_check"

    def test_diff_validation_type(self):
        assert TriggerType.DIFF_VALIDATION.value == "diff_validation"


class TestToolExecutionToken:
    """Test ToolExecutionToken dataclass."""

    def test_token_creation(self):
        """Token should be creatable with required fields."""
        token = ToolExecutionToken(
            tool_name="test_tool",
            arguments={"key": "value"},
            conversation_id="test_conv",
            trigger_type=TriggerType.TOOL_CALL,
        )
        assert token.tool_name == "test_tool"
        assert token.arguments == {"key": "value"}
        assert token.conversation_id == "test_conv"
        assert token.trigger_type == TriggerType.TOOL_CALL

    def test_token_auto_timestamp(self):
        """Token should auto-generate timestamp if not provided."""
        before = time.time()
        token = ToolExecutionToken(
            tool_name="test",
            arguments={},
            conversation_id="c1",
            trigger_type=TriggerType.TOOL_CALL,
        )
        after = time.time()
        assert before <= token.timestamp <= after

    def test_token_explicit_timestamp(self):
        """Token should accept explicit timestamp."""
        token = ToolExecutionToken(
            tool_name="test",
            arguments={},
            conversation_id="c1",
            trigger_type=TriggerType.TOOL_CALL,
            timestamp=1000.0,
        )
        assert token.timestamp == 1000.0

    def test_token_signature_generated(self):
        """Token should auto-generate a SHA256 signature."""
        token = ToolExecutionToken(
            tool_name="test",
            arguments={"a": 1},
            conversation_id="c1",
            trigger_type=TriggerType.TOOL_CALL,
        )
        assert hasattr(token, 'signature')
        assert len(token.signature) == 64  # SHA256 hex

    def test_different_tokens_different_signatures(self):
        """Different tokens should produce different signatures."""
        token1 = ToolExecutionToken(
            tool_name="tool1", arguments={},
            conversation_id="c1", trigger_type=TriggerType.TOOL_CALL,
        )
        token2 = ToolExecutionToken(
            tool_name="tool2", arguments={},
            conversation_id="c1", trigger_type=TriggerType.TOOL_CALL,
        )
        assert token1.signature != token2.signature


class TestToolExecutionRegistry:
    """Test ToolExecutionRegistry."""

    def test_registry_creation(self):
        """Registry should be instantiable."""
        registry = ToolExecutionRegistry()
        assert registry is not None

    def test_register_execution(self):
        """Should register a token and return an execution ID."""
        registry = ToolExecutionRegistry()
        token = ToolExecutionToken(
            tool_name="test_tool",
            arguments={"key": "value"},
            conversation_id="conv1",
            trigger_type=TriggerType.TOOL_CALL,
        )
        execution_id = registry.register_execution(token)
        assert isinstance(execution_id, str)
        assert len(execution_id) > 0

    def test_complete_execution(self):
        """Should mark execution as complete."""
        registry = ToolExecutionRegistry()
        token = ToolExecutionToken(
            tool_name="test", arguments={},
            conversation_id="c1", trigger_type=TriggerType.TOOL_CALL,
        )
        exec_id = registry.register_execution(token)
        result = registry.complete_execution(exec_id, "success")
        assert result is True

    def test_fail_execution(self):
        """Should mark execution as failed."""
        registry = ToolExecutionRegistry()
        token = ToolExecutionToken(
            tool_name="test", arguments={},
            conversation_id="c1", trigger_type=TriggerType.TOOL_CALL,
        )
        exec_id = registry.register_execution(token)
        result = registry.fail_execution(exec_id, "error message")
        assert result is True

    def test_verify_execution(self):
        """Should verify execution with correct signature."""
        registry = ToolExecutionRegistry()
        token = ToolExecutionToken(
            tool_name="test", arguments={},
            conversation_id="c1", trigger_type=TriggerType.TOOL_CALL,
        )
        exec_id = registry.register_execution(token)
        assert registry.verify_execution(exec_id, token.signature) is True

    def test_verify_wrong_signature(self):
        """Should reject verification with wrong signature."""
        registry = ToolExecutionRegistry()
        token = ToolExecutionToken(
            tool_name="test", arguments={},
            conversation_id="c1", trigger_type=TriggerType.TOOL_CALL,
        )
        exec_id = registry.register_execution(token)
        assert registry.verify_execution(exec_id, "wrong_signature") is False

    def test_verify_unknown_execution(self):
        """Should reject verification of unknown execution ID."""
        registry = ToolExecutionRegistry()
        assert registry.verify_execution("nonexistent", "sig") is False

    def test_get_result(self):
        """Should retrieve stored result."""
        registry = ToolExecutionRegistry()
        token = ToolExecutionToken(
            tool_name="test", arguments={},
            conversation_id="c1", trigger_type=TriggerType.TOOL_CALL,
        )
        exec_id = registry.register_execution(token)
        registry.complete_execution(exec_id, {"output": "hello"})
        result = registry.get_result(exec_id)
        assert result == {"output": "hello"}

    def test_get_result_unknown(self):
        """Should return None for unknown execution."""
        registry = ToolExecutionRegistry()
        assert registry.get_result("nonexistent") is None
