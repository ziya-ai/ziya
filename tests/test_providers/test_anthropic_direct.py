"""
Tests for app.providers.anthropic_direct — the Anthropic Direct LLM provider.

Tests verify:
  1. Request building (model, max_tokens, tools, thinking)
  2. Cache control strategy (no 4-block limit)
  3. Message formatting (assistant messages, tool results)
  4. Feature support queries
  5. Error classification
"""

import pytest
import sys
import json
from unittest.mock import MagicMock, patch, AsyncMock
from typing import List, Dict, Any

from app.providers.base import (
    ProviderConfig,
    ThinkingConfig,
    TextDelta,
    ToolUseStart,
    ToolUseInput,
    ToolUseEnd,
    UsageEvent,
    ThinkingDelta,
    StreamEnd,
    ErrorEvent,
    ErrorType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_anthropic_module():
    """Create a mock anthropic module and inject into sys.modules."""
    mock_anthropic = MagicMock()
    mock_client = MagicMock()
    mock_anthropic.AsyncAnthropic.return_value = mock_client
    
    # Inject into sys.modules so 'import anthropic' finds our mock
    with patch.dict(sys.modules, {'anthropic': mock_anthropic}):
        yield mock_anthropic


@pytest.fixture
def anthropic_provider(mock_anthropic_module):
    """Create an AnthropicDirectProvider with mocked client."""
    from app.providers.anthropic_direct import AnthropicDirectProvider
    
    provider = AnthropicDirectProvider(
        model_id="claude-sonnet-4-20250514",
        model_config={
            "family": "claude",
            "supports_thinking": True,
            "supports_adaptive_thinking": True,
        },
        api_key="sk-test-key",
    )
    return provider


@pytest.fixture
def basic_config():
    """Basic ProviderConfig for tests."""
    return ProviderConfig(
        max_output_tokens=8192,
        temperature=0.5,
        iteration=0,
    )


# ---------------------------------------------------------------------------
# Request Building Tests
# ---------------------------------------------------------------------------

class TestBuildRequest:
    """Tests for _build_request()."""

    def test_basic_request_structure(self, anthropic_provider, basic_config):
        """Request should include model, max_tokens, messages."""
        messages = [{"role": "user", "content": "Hello"}]
        request = anthropic_provider._build_request(messages, None, [], basic_config)
        
        assert request["model"] == "claude-sonnet-4-20250514"
        assert request["max_tokens"] == 8192
        assert request["messages"] == messages
        assert request["temperature"] == 0.5

    def test_system_prompt_always_cached(self, anthropic_provider, basic_config):
        """System prompts should always get cache_control (no size threshold)."""
        short_system = "Short"
        messages = [{"role": "user", "content": "Hi"}]
        request = anthropic_provider._build_request(messages, short_system, [], basic_config)
        
        assert isinstance(request["system"], list)
        assert request["system"][0]["type"] == "text"
        assert request["system"][0]["text"] == short_system
        assert request["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_tools_included(self, anthropic_provider, basic_config):
        """Tools should be included when provided."""
        tools = [{"name": "test_tool", "description": "A test", "input_schema": {}}]
        messages = [{"role": "user", "content": "Hi"}]
        request = anthropic_provider._build_request(messages, None, tools, basic_config)
        
        assert request["tools"] == tools

    def test_tools_suppressed(self, anthropic_provider):
        """When suppress_tools=True, tools should not be in request."""
        config = ProviderConfig(suppress_tools=True)
        tools = [{"name": "test_tool", "description": "A test", "input_schema": {}}]
        messages = [{"role": "user", "content": "Hi"}]
        request = anthropic_provider._build_request(messages, None, tools, config)
        
        assert "tools" not in request

    def test_temperature_none_excluded(self, anthropic_provider):
        """Temperature=None should not be included in request."""
        config = ProviderConfig(temperature=None)
        messages = [{"role": "user", "content": "Hi"}]
        request = anthropic_provider._build_request(messages, None, [], config)
        
        assert "temperature" not in request

    def test_adaptive_thinking(self, anthropic_provider):
        """Adaptive thinking should set thinking with budget."""
        thinking = ThinkingConfig(enabled=True, mode="adaptive", budget_tokens=16000)
        config = ProviderConfig(thinking=thinking)
        messages = [{"role": "user", "content": "Hi"}]
        request = anthropic_provider._build_request(messages, None, [], config)
        
        assert request["thinking"] == {"type": "adaptive"}
    def test_standard_thinking(self, anthropic_provider):
        """Standard thinking should set thinking.type=enabled with budget."""
        thinking = ThinkingConfig(enabled=True, mode="enabled", budget_tokens=32000)
        config = ProviderConfig(thinking=thinking)
        messages = [{"role": "user", "content": "Hi"}]
        request = anthropic_provider._build_request(messages, None, [], config)
        
        assert request["thinking"] == {"type": "enabled", "budget_tokens": 32000}


# ---------------------------------------------------------------------------
# Cache Control Tests
# ---------------------------------------------------------------------------

class TestCacheControl:
    """Tests for prepare_cache_control()."""

    def test_no_cache_first_iteration(self, anthropic_provider):
        """First iteration should not add cache markers to messages."""
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
        ]
        result = anthropic_provider.prepare_cache_control(messages, iteration=0)
        
        # Should return unchanged
        assert result == messages

    def test_no_cache_short_conversation(self, anthropic_provider):
        """Short conversations (< 3 messages) should not get cache markers."""
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
        ]
        result = anthropic_provider.prepare_cache_control(messages, iteration=5)
        
        assert result == messages

    def test_cache_marker_at_second_to_last(self, anthropic_provider):
        """Cache marker should be placed at second-to-last message."""
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
        ]
        result = anthropic_provider.prepare_cache_control(messages, iteration=2)
        
        # Boundary is at index -2 (second-to-last)
        boundary_msg = result[-2]
        assert isinstance(boundary_msg["content"], list)
        assert boundary_msg["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_cache_on_multiblock_content(self, anthropic_provider):
        """Cache marker on multiblock content should go on last block."""
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "part1"},
                {"type": "text", "text": "part2"},
            ]},
            {"role": "user", "content": "msg3"},
        ]
        result = anthropic_provider.prepare_cache_control(messages, iteration=2)
        
        # Cache marker should be on last block of second-to-last message
        boundary_content = result[-2]["content"]
        assert "cache_control" not in boundary_content[0]
        assert boundary_content[-1]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# Message Formatting Tests
# ---------------------------------------------------------------------------

class TestMessageFormatting:
    """Tests for build_assistant_message() and build_tool_result_message()."""

    def test_assistant_message_text_only(self, anthropic_provider):
        """Assistant message with text only."""
        msg = anthropic_provider.build_assistant_message("Hello there!", [])
        
        assert msg["role"] == "assistant"
        assert len(msg["content"]) == 1
        assert msg["content"][0] == {"type": "text", "text": "Hello there!"}

    def test_assistant_message_keeps_tool_name(self, anthropic_provider):
        """Anthropic direct should NOT strip mcp_ prefix (unlike Bedrock)."""
        tool_uses = [{"id": "t1", "name": "mcp_run_shell_command", "input": {"command": "ls"}}]
        msg = anthropic_provider.build_assistant_message("", tool_uses)
        
        # Anthropic direct keeps the original name
        assert msg["content"][0]["name"] == "mcp_run_shell_command"

    def test_assistant_message_text_and_tools(self, anthropic_provider):
        """Assistant message with both text and tools."""
        tool_uses = [{"id": "t1", "name": "read_file", "input": {"path": "/tmp/x"}}]
        msg = anthropic_provider.build_assistant_message("Let me check that file.", tool_uses)
        
        assert len(msg["content"]) == 2
        assert msg["content"][0]["type"] == "text"
        assert msg["content"][1]["type"] == "tool_use"
        assert msg["content"][1]["id"] == "t1"

    def test_assistant_message_empty_text_excluded(self, anthropic_provider):
        """Empty/whitespace text should not create a text block."""
        tool_uses = [{"id": "t1", "name": "foo", "input": {}}]
        msg = anthropic_provider.build_assistant_message("   ", tool_uses)
        
        assert len(msg["content"]) == 1
        assert msg["content"][0]["type"] == "tool_use"

    def test_tool_result_message(self, anthropic_provider):
        """Tool results should be formatted correctly."""
        results = [
            {"tool_use_id": "t1", "content": "file contents here"},
            {"tool_use_id": "t2", "content": "command output"},
        ]
        msg = anthropic_provider.build_tool_result_message(results)
        
        assert msg["role"] == "user"
        assert len(msg["content"]) == 2
        assert msg["content"][0]["type"] == "tool_result"
        assert msg["content"][0]["tool_use_id"] == "t1"


# ---------------------------------------------------------------------------
# Feature Support Tests
# ---------------------------------------------------------------------------

class TestFeatureSupport:
    """Tests for supports_feature()."""

    def test_thinking_from_model_config(self, anthropic_provider):
        """supports_thinking should come from model_config."""
        assert anthropic_provider.supports_feature("thinking") is True

    def test_adaptive_thinking_from_model_config(self, anthropic_provider):
        """supports_adaptive_thinking should come from model_config."""
        assert anthropic_provider.supports_feature("adaptive_thinking") is True

    def test_extended_context_always_false(self, anthropic_provider):
        """Anthropic direct has 200k native, so extended_context is False."""
        assert anthropic_provider.supports_feature("extended_context") is False

    def test_cache_control_always_true(self, anthropic_provider):
        """Anthropic API always supports cache_control."""
        assert anthropic_provider.supports_feature("cache_control") is True

    def test_assistant_prefill_always_true(self, anthropic_provider):
        """Anthropic direct always supports assistant_prefill."""
        assert anthropic_provider.supports_feature("assistant_prefill") is True

    def test_unknown_feature_false(self, anthropic_provider):
        """Unknown features should return False."""
        assert anthropic_provider.supports_feature("nonexistent_feature") is False

    def test_provider_name(self, anthropic_provider):
        """Provider name should be 'anthropic'."""
        assert anthropic_provider.provider_name == "anthropic"


# ---------------------------------------------------------------------------
# Error Classification Tests
# ---------------------------------------------------------------------------

class TestErrorClassification:
    """Tests for _classify_error()."""

    def test_rate_limit_429(self):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        
        assert AnthropicDirectProvider._classify_error("Error 429: rate limit") == ErrorType.THROTTLE

    def test_too_many_requests(self):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        
        assert AnthropicDirectProvider._classify_error("too many requests") == ErrorType.THROTTLE

    def test_overloaded_529(self):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        
        assert AnthropicDirectProvider._classify_error("529 overloaded") == ErrorType.OVERLOADED

    def test_overloaded_lowercase(self):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        
        assert AnthropicDirectProvider._classify_error("service is overloaded") == ErrorType.OVERLOADED

    def test_timeout(self):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        
        assert AnthropicDirectProvider._classify_error("request timeout") == ErrorType.READ_TIMEOUT

    def test_prompt_too_long(self):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        
        assert AnthropicDirectProvider._classify_error("prompt is too long") == ErrorType.CONTEXT_LIMIT

    def test_too_large(self):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        
        assert AnthropicDirectProvider._classify_error("input too large") == ErrorType.CONTEXT_LIMIT

    def test_unknown_error(self):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        
        assert AnthropicDirectProvider._classify_error("Something unexpected happened") == ErrorType.UNKNOWN


# ---------------------------------------------------------------------------
# Initialization Tests
# ---------------------------------------------------------------------------

class TestInitialization:
    """Tests for provider initialization."""

    def test_requires_api_key(self):
        """Should raise ValueError if no API key provided."""
        mock_anthropic = MagicMock()
        
        with patch.dict(sys.modules, {'anthropic': mock_anthropic}):
            with patch.dict("os.environ", {}, clear=True):
                # Ensure ANTHROPIC_API_KEY is not set
                import os
                env_backup = os.environ.get("ANTHROPIC_API_KEY")
                if "ANTHROPIC_API_KEY" in os.environ:
                    del os.environ["ANTHROPIC_API_KEY"]
                
                try:
                    from importlib import reload
                    import app.providers.anthropic_direct as adp_module
                    # Reload to pick up the patched sys.modules
                    reload(adp_module)
                    
                    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                        adp_module.AnthropicDirectProvider(
                            model_id="claude-sonnet-4-20250514",
                            model_config={},
                            api_key=None,
                        )
                finally:
                    if env_backup:
                        os.environ["ANTHROPIC_API_KEY"] = env_backup

    def test_api_key_from_param(self, mock_anthropic_module):
        """API key from parameter should be used."""
        from app.providers.anthropic_direct import AnthropicDirectProvider
        
        provider = AnthropicDirectProvider(
            model_id="claude-sonnet-4-20250514",
            model_config={},
            api_key="sk-explicit-key",
        )
        
        mock_anthropic_module.AsyncAnthropic.assert_called_with(api_key="sk-explicit-key")

    def test_api_key_from_env(self):
        """API key from environment should be used if param not provided."""
        mock_anthropic = MagicMock()
        
        with patch.dict(sys.modules, {'anthropic': mock_anthropic}):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-env-key"}):
                from importlib import reload
                import app.providers.anthropic_direct as adp_module
                reload(adp_module)
                
                provider = adp_module.AnthropicDirectProvider(
                    model_id="claude-sonnet-4-20250514",
                    model_config={},
                )
                
                mock_anthropic.AsyncAnthropic.assert_called_with(api_key="sk-env-key")
