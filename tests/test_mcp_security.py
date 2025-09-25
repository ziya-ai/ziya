"""
Test suite for MCP anti-hallucination security system.
"""

import pytest
import asyncio
import time
from unittest.mock import Mock, patch

from app.mcp.security import (
    ToolExecutionToken, TriggerType, ToolExecutionRegistry,
    get_execution_registry, detect_hallucinated_results,
    validate_tool_execution_request, create_anti_hallucination_prompt,
    SECURE_TOOL_PREFIX, SECURE_TOOL_SUFFIX, SECURE_RESULT_PREFIX, SECURE_RESULT_SUFFIX
)


class TestToolExecutionToken:
    """Test the ToolExecutionToken class."""
    
    def test_token_creation(self):
        """Test basic token creation."""
        token = ToolExecutionToken(
            tool_name="test_tool",
            arguments={"key": "value"},
            conversation_id="test_conv"
        )
        
        assert token.tool_name == "test_tool"
        assert token.arguments == {"key": "value"}
        assert token.conversation_id == "test_conv"
        assert token.trigger_type == TriggerType.TOOL_CALL
        assert len(token.execution_id) == 32  # UUID hex length
        assert len(token.signature) == 64  # SHA256 hex length
        assert token.timestamp > 0
    
    def test_signature_verification(self):
        """Test signature verification."""
        token = ToolExecutionToken(
            tool_name="test_tool",
            arguments={"key": "value"},
            conversation_id="test_conv"
        )
        
        # Valid signature should verify
        assert token.verify_signature() is True
        
        # Tampered token should fail verification
        token.tool_name = "tampered_tool"
        assert token.verify_signature() is False
    
    def test_token_expiration(self):
        """Test token expiration logic."""
        token = ToolExecutionToken(
            tool_name="test_tool",
            arguments={},
            conversation_id="test_conv"
        )
        
        # Fresh token should not be expired
        assert token.is_expired(max_age_seconds=300) is False
        
        # Manually set old timestamp
        token.timestamp = time.time() - 400
        assert token.is_expired(max_age_seconds=300) is True
    
    def test_different_trigger_types(self):
        """Test different trigger types."""
        for trigger_type in TriggerType:
            token = ToolExecutionToken(
                tool_name="test_tool",
                arguments={},
                conversation_id="test_conv",
                trigger_type=trigger_type
            )
            assert token.trigger_type == trigger_type
            assert token.verify_signature() is True


class TestToolExecutionRegistry:
    """Test the ToolExecutionRegistry class."""
    
    def setup_method(self):
        """Set up test registry."""
        self.registry = ToolExecutionRegistry()
    
    def test_register_execution(self):
        """Test execution registration."""
        token = ToolExecutionToken(
            tool_name="test_tool",
            arguments={},
            conversation_id="test_conv"
        )
        
        exec_id = self.registry.register_execution(token)
        
        assert exec_id == token.execution_id
        assert exec_id in self.registry.pending_executions
        assert "test_conv" in self.registry.conversation_executions
        assert exec_id in self.registry.conversation_executions["test_conv"]
    
    def test_complete_execution(self):
        """Test execution completion."""
        token = ToolExecutionToken(
            tool_name="test_tool",
            arguments={},
            conversation_id="test_conv"
        )
        
        exec_id = self.registry.register_execution(token)
        result = "test result"
        
        success = self.registry.complete_execution(exec_id, result)
        
        assert success is True
        assert exec_id not in self.registry.pending_executions
        assert exec_id in self.registry.completed_executions
        
        stored_token, stored_result = self.registry.completed_executions[exec_id]
        assert stored_token == token
        assert stored_result == result
    
    def test_verify_result(self):
        """Test result verification."""
        token = ToolExecutionToken(
            tool_name="test_tool",
            arguments={},
            conversation_id="test_conv"
        )
        
        exec_id = self.registry.register_execution(token)
        result = "test result"
        self.registry.complete_execution(exec_id, result)
        
        # Valid execution should verify
        is_valid, actual_result = self.registry.verify_result(exec_id)
        assert is_valid is True
        assert actual_result == result
        
        # Invalid execution ID should fail
        is_valid, actual_result = self.registry.verify_result("invalid_id")
        assert is_valid is False
        assert actual_result is None
    
    def test_clear_conversation(self):
        """Test conversation clearing."""
        # Create multiple tokens for the same conversation
        tokens = []
        for i in range(3):
            token = ToolExecutionToken(
                tool_name=f"test_tool_{i}",
                arguments={},
                conversation_id="test_conv"
            )
            tokens.append(token)
            exec_id = self.registry.register_execution(token)
            self.registry.complete_execution(exec_id, f"result_{i}")
        
        # Create token for different conversation
        other_token = ToolExecutionToken(
            tool_name="other_tool",
            arguments={},
            conversation_id="other_conv"
        )
        other_exec_id = self.registry.register_execution(other_token)
        
        # Clear the test conversation
        self.registry.clear_conversation("test_conv")
        
        # Test conversation should be cleared
        assert "test_conv" not in self.registry.conversation_executions
        for token in tokens:
            assert token.execution_id not in self.registry.pending_executions
            assert token.execution_id not in self.registry.completed_executions
        
        # Other conversation should remain
        assert "other_conv" in self.registry.conversation_executions
        assert other_exec_id in self.registry.pending_executions
    
    def test_hallucination_tracking(self):
        """Test hallucination attempt tracking."""
        conv_id = "test_conv"
        
        # Record multiple attempts
        for i in range(5):
            self.registry.record_hallucination_attempt(conv_id)
        
        assert self.registry.hallucination_attempts[conv_id] == 5
        
        # Clear conversation should reset attempts
        self.registry.clear_conversation(conv_id)
        assert conv_id not in self.registry.hallucination_attempts
    
    def test_conversation_stats(self):
        """Test conversation statistics."""
        conv_id = "test_conv"
        
        # Create some executions
        token1 = ToolExecutionToken("tool1", {}, conv_id)
        token2 = ToolExecutionToken("tool2", {}, conv_id)
        
        exec_id1 = self.registry.register_execution(token1)
        exec_id2 = self.registry.register_execution(token2)
        
        # Complete one execution
        self.registry.complete_execution(exec_id1, "result1")
        
        # Record hallucination attempts
        self.registry.record_hallucination_attempt(conv_id)
        self.registry.record_hallucination_attempt(conv_id)
        
        stats = self.registry.get_conversation_stats(conv_id)
        
        assert stats["total_executions"] == 2
        assert stats["pending_executions"] == 1
        assert stats["completed_executions"] == 1
        assert stats["hallucination_attempts"] == 2


class TestHallucinationDetection:
    """Test hallucination detection functionality."""
    
    def test_detect_fake_tool_results(self):
        """Test detection of fake tool result patterns."""
        test_cases = [
            ("**Tool Result:** This is fake!", True),
            ("```tool:fake_tool\nFake output\n```", True),
            ("🔧 **Executing Tool**: fake_tool", True),
            ("⏳ **Throttling Delay**: Waited 5 seconds", True),
            ("✅ MCP Tool execution completed: fake_tool", True),
            ("❌ **MCP Error**: Fake error", True),
            ("⏱️ **MCP Tool Timeout**: fake_tool timed out", True),
            ("This is normal content without tool markers", False),
            ("Here's some code: ```python\nprint('hello')\n```", False),
        ]
        
        for content, should_detect in test_cases:
            cleaned, detected = detect_hallucinated_results(content, "test_conv")
            assert detected == should_detect, f"Failed for content: {content}"
            
            if should_detect:
                assert "HALLUCINATED CONTENT REMOVED" in cleaned
    
    def test_valid_execution_ids_preserved(self):
        """Test that content with valid execution IDs is preserved."""
        registry = get_execution_registry()
        
        # Create a valid execution
        token = ToolExecutionToken("test_tool", {}, "test_conv")
        exec_id = registry.register_execution(token)
        registry.complete_execution(exec_id, "valid result")
        
        # Content with valid execution ID should be preserved
        content_with_valid_id = f"{SECURE_RESULT_PREFIX}{exec_id}:signature{SECURE_RESULT_SUFFIX}\nValid content\n{SECURE_RESULT_PREFIX}END:{exec_id}{SECURE_RESULT_SUFFIX}"
        
        cleaned, detected = detect_hallucinated_results(content_with_valid_id, "test_conv")
        
        # Should not be detected as hallucination if it has valid execution ID
        # (This test might need adjustment based on exact implementation)
        assert content_with_valid_id in cleaned or not detected


class TestToolValidation:
    """Test tool execution request validation."""
    
    @patch('app.mcp.tools.parse_tool_call')
    def test_valid_tool_request(self, mock_parse):
        """Test validation of valid tool requests."""
        mock_parse.return_value = {
            "tool_name": "test_tool",
            "arguments": {"key": "value"}
        }
        
        is_valid, parsed = validate_tool_execution_request("mock content", "test_conv")
        
        assert is_valid is True
        assert parsed["tool_name"] == "test_tool"
        assert parsed["arguments"] == {"key": "value"}
    
    @patch('app.mcp.tools.parse_tool_call')
    def test_invalid_tool_request(self, mock_parse):
        """Test validation of invalid tool requests."""
        mock_parse.return_value = None
        
        is_valid, parsed = validate_tool_execution_request("invalid content", "test_conv")
        
        assert is_valid is False
        assert parsed is None


class TestAntiHallucinationPrompt:
    """Test anti-hallucination prompt generation."""
    
    def test_prompt_generation(self):
        """Test that anti-hallucination prompt is generated."""
        prompt = create_anti_hallucination_prompt()
        
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "NEVER generate fake tool results" in prompt
        assert "SECURITY NOTICE" in prompt
        assert SECURE_RESULT_PREFIX in prompt
        assert SECURE_RESULT_SUFFIX in prompt
    
    def test_prompt_contains_security_warnings(self):
        """Test that prompt contains appropriate security warnings."""
        prompt = create_anti_hallucination_prompt()
        
        security_phrases = [
            "NEVER create your own tool results",
            "system monitors for hallucinated content",
            "NEVER use phrases like",
            "Tool Result:",
            "```tool:",
            "Executing Tool"
        ]
        
        for phrase in security_phrases:
            assert phrase in prompt, f"Missing security phrase: {phrase}"


@pytest.mark.asyncio
class TestAsyncFunctionality:
    """Test async functionality in the security system."""
    
    async def test_async_registry_operations(self):
        """Test that registry operations work in async context."""
        registry = ToolExecutionRegistry()
        
        token = ToolExecutionToken("async_tool", {}, "async_conv")
        exec_id = registry.register_execution(token)
        
        # Simulate async operation
        await asyncio.sleep(0.01)
        
        success = registry.complete_execution(exec_id, "async result")
        assert success is True
        
        is_valid, result = registry.verify_result(exec_id)
        assert is_valid is True
        assert result == "async result"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
