"""
Tests for app.providers.bedrock — the Bedrock LLM provider.

Tests verify:
  1. Request body building (anthropic_version, max_tokens, tools, thinking)
  2. Cache control strategy (4-block limit)
  3. Stream parsing (text, tool use, thinking, usage events)
  4. Message formatting (assistant messages, tool results)
  5. Feature support queries
  6. Error classification
"""

import pytest
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
def mock_bedrock_client():
    """Create a mock boto3 bedrock-runtime client."""
    return MagicMock()


@pytest.fixture
def bedrock_provider(mock_bedrock_client):
    """Create a BedrockProvider with mocked client."""
    with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client") as mock_get:
        mock_get.return_value = mock_bedrock_client
        from app.providers.bedrock import BedrockProvider
        
        provider = BedrockProvider(
            model_id="anthropic.claude-sonnet-4-20250514-v1:0",
            model_config={
                "family": "claude",
                "supports_thinking": True,
                "supports_adaptive_thinking": True,
                "supports_extended_context": True,
                "extended_context_header": "max-context-2025-01-01",
            },
            aws_profile="test",
            region="us-west-2",
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
# Request Body Building Tests
# ---------------------------------------------------------------------------

class TestBuildRequestBody:
    """Tests for _build_request_body()."""

    def test_basic_body_structure(self, bedrock_provider, basic_config):
        """Body should include anthropic_version, max_tokens, messages."""
        messages = [{"role": "user", "content": "Hello"}]
        body = bedrock_provider._build_request_body(messages, None, [], basic_config)
        
        assert body["anthropic_version"] == "bedrock-2023-05-31"
        assert body["max_tokens"] == 8192
        assert body["messages"] == messages
        assert body["temperature"] == 0.5

    def test_system_prompt_caching_large(self, bedrock_provider, basic_config):
        """System prompts > 1024 chars should get cache_control."""
        large_system = "x" * 2000
        messages = [{"role": "user", "content": "Hi"}]
        body = bedrock_provider._build_request_body(messages, large_system, [], basic_config)
        
        assert isinstance(body["system"], list)
        assert body["system"][0]["type"] == "text"
        assert body["system"][0]["text"] == large_system
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_system_prompt_no_caching_small(self, bedrock_provider, basic_config):
        """System prompts <= 1024 chars should not get cache_control."""
        small_system = "Short system prompt"
        messages = [{"role": "user", "content": "Hi"}]
        body = bedrock_provider._build_request_body(messages, small_system, [], basic_config)
        
        assert body["system"] == small_system

    def test_tools_included(self, bedrock_provider, basic_config):
        """Tools should be included with tool_choice=auto."""
        tools = [{"name": "test_tool", "description": "A test", "input_schema": {}}]
        messages = [{"role": "user", "content": "Hi"}]
        body = bedrock_provider._build_request_body(messages, None, tools, basic_config)
        
        assert body["tools"] == tools
        assert body["tool_choice"] == {"type": "auto"}

    def test_tools_suppressed(self, bedrock_provider):
        """When suppress_tools=True, tools should not be in body."""
        config = ProviderConfig(suppress_tools=True)
        tools = [{"name": "test_tool", "description": "A test", "input_schema": {}}]
        messages = [{"role": "user", "content": "Hi"}]
        body = bedrock_provider._build_request_body(messages, None, tools, config)
        
        assert "tools" not in body
        assert "tool_choice" not in body

    def test_temperature_none_excluded(self, bedrock_provider):
        """Temperature=None should not be included in body."""
        config = ProviderConfig(temperature=None)
        messages = [{"role": "user", "content": "Hi"}]
        body = bedrock_provider._build_request_body(messages, None, [], config)
        
        assert "temperature" not in body

    def test_adaptive_thinking(self, bedrock_provider):
        """Adaptive thinking should set thinking.type=adaptive and effort."""
        thinking = ThinkingConfig(enabled=True, mode="adaptive", effort="high")
        config = ProviderConfig(thinking=thinking)
        messages = [{"role": "user", "content": "Hi"}]
        body = bedrock_provider._build_request_body(messages, None, [], config)
        
        assert body["thinking"] == {"type": "adaptive"}
        assert body["output_config"]["effort"] == "high"
        assert "effort-2025-11-24" in body["anthropic_beta"]

    def test_standard_thinking(self, bedrock_provider):
        """Standard thinking should set thinking.type=enabled with budget."""
        thinking = ThinkingConfig(enabled=True, mode="enabled", budget_tokens=32000)
        config = ProviderConfig(thinking=thinking)
        messages = [{"role": "user", "content": "Hi"}]
        body = bedrock_provider._build_request_body(messages, None, [], config)
        
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 32000}


# ---------------------------------------------------------------------------
# Cache Control Tests
# ---------------------------------------------------------------------------

class TestCacheControl:
    """Tests for prepare_cache_control()."""

    def test_no_cache_first_iteration(self, bedrock_provider):
        """First iteration should not add cache markers to messages."""
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
            {"role": "user", "content": "msg5"},
            {"role": "assistant", "content": "msg6"},
        ]
        result = bedrock_provider.prepare_cache_control(messages, iteration=0)
        
        # Should return unchanged
        assert result == messages

    def test_no_cache_short_conversation(self, bedrock_provider):
        """Short conversations (< 6 messages) should not get cache markers."""
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
        ]
        result = bedrock_provider.prepare_cache_control(messages, iteration=5)
        
        assert result == messages

    def test_cache_marker_at_boundary(self, bedrock_provider):
        """Cache marker should be placed at len-4 boundary."""
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
            {"role": "user", "content": "msg5"},
            {"role": "assistant", "content": "msg6"},
        ]
        result = bedrock_provider.prepare_cache_control(messages, iteration=2)
        
        # Boundary is at index 2 (6-4=2)
        boundary_msg = result[2]
        assert isinstance(boundary_msg["content"], list)
        assert boundary_msg["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_cache_strips_existing_markers(self, bedrock_provider):
        """Existing cache_control markers should be stripped."""
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "msg1", "cache_control": {"type": "ephemeral"}}]},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
            {"role": "user", "content": "msg5"},
            {"role": "assistant", "content": "msg6"},
        ]
        result = bedrock_provider.prepare_cache_control(messages, iteration=2)
        
        # Original marker at index 0 should be stripped
        first_block = result[0]["content"][0]
        assert "cache_control" not in first_block


# ---------------------------------------------------------------------------
# Message Formatting Tests
# ---------------------------------------------------------------------------

class TestMessageFormatting:
    """Tests for build_assistant_message() and build_tool_result_message()."""

    def test_assistant_message_text_only(self, bedrock_provider):
        """Assistant message with text only."""
        msg = bedrock_provider.build_assistant_message("Hello there!", [])
        
        assert msg["role"] == "assistant"
        assert len(msg["content"]) == 1
        assert msg["content"][0] == {"type": "text", "text": "Hello there!"}

    def test_assistant_message_strips_mcp_prefix(self, bedrock_provider):
        """Tool names should have mcp_ prefix stripped."""
        tool_uses = [{"id": "t1", "name": "mcp_run_shell_command", "input": {"command": "ls"}}]
        msg = bedrock_provider.build_assistant_message("", tool_uses)
        
        assert msg["content"][0]["name"] == "run_shell_command"

    def test_assistant_message_text_and_tools(self, bedrock_provider):
        """Assistant message with both text and tools."""
        tool_uses = [{"id": "t1", "name": "read_file", "input": {"path": "/tmp/x"}}]
        msg = bedrock_provider.build_assistant_message("Let me check that file.", tool_uses)
        
        assert len(msg["content"]) == 2
        assert msg["content"][0]["type"] == "text"
        assert msg["content"][1]["type"] == "tool_use"
        assert msg["content"][1]["id"] == "t1"

    def test_assistant_message_empty_text_excluded(self, bedrock_provider):
        """Empty/whitespace text should not create a text block."""
        tool_uses = [{"id": "t1", "name": "foo", "input": {}}]
        msg = bedrock_provider.build_assistant_message("   ", tool_uses)
        
        assert len(msg["content"]) == 1
        assert msg["content"][0]["type"] == "tool_use"

    def test_tool_result_message(self, bedrock_provider):
        """Tool results should be formatted correctly."""
        results = [
            {"tool_use_id": "t1", "content": "file contents here"},
            {"tool_use_id": "t2", "content": "command output"},
        ]
        msg = bedrock_provider.build_tool_result_message(results)
        
        assert msg["role"] == "user"
        assert len(msg["content"]) == 2
        assert msg["content"][0]["type"] == "tool_result"
        assert msg["content"][0]["tool_use_id"] == "t1"
        assert msg["content"][1]["tool_use_id"] == "t2"


# ---------------------------------------------------------------------------
# Feature Support Tests
# ---------------------------------------------------------------------------

class TestFeatureSupport:
    """Tests for supports_feature()."""

    def test_thinking_from_model_config(self, bedrock_provider):
        """supports_thinking should come from model_config."""
        assert bedrock_provider.supports_feature("thinking") is True

    def test_adaptive_thinking_from_model_config(self, bedrock_provider):
        """supports_adaptive_thinking should come from model_config."""
        assert bedrock_provider.supports_feature("adaptive_thinking") is True

    def test_extended_context_from_model_config(self, bedrock_provider):
        """supports_extended_context should come from model_config."""
        assert bedrock_provider.supports_feature("extended_context") is True

    def test_cache_control_always_true(self, bedrock_provider):
        """Bedrock Claude always supports cache_control."""
        assert bedrock_provider.supports_feature("cache_control") is True

    def test_assistant_prefill_default_true(self, bedrock_provider):
        """assistant_prefill defaults to True."""
        assert bedrock_provider.supports_feature("assistant_prefill") is True

    def test_unknown_feature_false(self, bedrock_provider):
        """Unknown features should return False."""
        assert bedrock_provider.supports_feature("nonexistent_feature") is False

    def test_provider_name(self, bedrock_provider):
        """Provider name should be 'bedrock'."""
        assert bedrock_provider.provider_name == "bedrock"


# ---------------------------------------------------------------------------
# Error Classification Tests
# ---------------------------------------------------------------------------

class TestErrorClassification:
    """Tests for _classify_error()."""

    def test_throttle_exception(self):
        from app.providers.bedrock import BedrockProvider
        
        assert BedrockProvider._classify_error("ThrottlingException: Rate exceeded") == ErrorType.THROTTLE

    def test_too_many_tokens(self):
        from app.providers.bedrock import BedrockProvider
        
        assert BedrockProvider._classify_error("Too many tokens in request") == ErrorType.THROTTLE

    def test_too_many_requests(self):
        from app.providers.bedrock import BedrockProvider
        
        assert BedrockProvider._classify_error("Too many requests") == ErrorType.THROTTLE

    def test_rate_limit_lowercase(self):
        from app.providers.bedrock import BedrockProvider
        
        assert BedrockProvider._classify_error("rate limit exceeded") == ErrorType.THROTTLE

    def test_context_limit_input_too_long(self):
        from app.providers.bedrock import BedrockProvider
        
        assert BedrockProvider._classify_error("Input is too long for model") == ErrorType.CONTEXT_LIMIT

    def test_context_limit_prompt_too_long(self):
        from app.providers.bedrock import BedrockProvider
        
        assert BedrockProvider._classify_error("prompt is too long") == ErrorType.CONTEXT_LIMIT

    def test_read_timeout(self):
        from app.providers.bedrock import BedrockProvider
        
        assert BedrockProvider._classify_error("Read timed out") == ErrorType.READ_TIMEOUT

    def test_timeout_lowercase(self):
        from app.providers.bedrock import BedrockProvider
        
        assert BedrockProvider._classify_error("request timeout after 30s") == ErrorType.READ_TIMEOUT

    def test_overloaded(self):
        from app.providers.bedrock import BedrockProvider
        
        assert BedrockProvider._classify_error("Service overloaded") == ErrorType.OVERLOADED

    def test_overloaded_529(self):
        from app.providers.bedrock import BedrockProvider
        
        assert BedrockProvider._classify_error("Error 529: overloaded") == ErrorType.OVERLOADED

    def test_unknown_error(self):
        from app.providers.bedrock import BedrockProvider
        
        assert BedrockProvider._classify_error("Something unexpected happened") == ErrorType.UNKNOWN


# ---------------------------------------------------------------------------
# Stream Parsing Tests (using mock stream data)
# ---------------------------------------------------------------------------

class TestStreamParsing:
    """Tests for _parse_stream() with mock Bedrock stream data."""

    def _make_chunk(self, data: dict) -> dict:
        """Create a mock Bedrock stream chunk."""
        return {"chunk": {"bytes": json.dumps(data).encode("utf-8")}}

    @pytest.mark.asyncio
    async def test_text_delta(self, bedrock_provider, basic_config):
        """Text deltas should yield TextDelta events."""
        chunks = [
            self._make_chunk({"type": "content_block_delta", "index": 0, 
                            "delta": {"type": "text_delta", "text": "Hello"}}),
            self._make_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "text_delta", "text": " world"}}),
            self._make_chunk({"type": "message_stop", "stop_reason": "end_turn"}),
        ]
        mock_response = {"body": iter(chunks)}
        
        events = []
        async for event in bedrock_provider._parse_stream(mock_response, basic_config):
            events.append(event)
        
        assert len(events) == 3
        assert isinstance(events[0], TextDelta)
        assert events[0].content == "Hello"
        assert isinstance(events[1], TextDelta)
        assert events[1].content == " world"
        assert isinstance(events[2], StreamEnd)

    @pytest.mark.asyncio
    async def test_tool_use_flow(self, bedrock_provider, basic_config):
        """Tool use should yield Start, Input(s), End events."""
        chunks = [
            self._make_chunk({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "tool_use", "id": "t1", "name": "read_file"}}),
            self._make_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "input_json_delta", "partial_json": '{"path":'}}),
            self._make_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "input_json_delta", "partial_json": '"/tmp/x"}'}}),
            self._make_chunk({"type": "content_block_stop", "index": 0}),
            self._make_chunk({"type": "message_stop", "stop_reason": "tool_use"}),
        ]
        mock_response = {"body": iter(chunks)}
        
        events = []
        async for event in bedrock_provider._parse_stream(mock_response, basic_config):
            events.append(event)
        
        assert isinstance(events[0], ToolUseStart)
        assert events[0].id == "t1"
        assert events[0].name == "read_file"
        
        assert isinstance(events[1], ToolUseInput)
        assert isinstance(events[2], ToolUseInput)
        
        assert isinstance(events[3], ToolUseEnd)
        assert events[3].id == "t1"
        assert events[3].input == {"path": "/tmp/x"}
        
        assert isinstance(events[4], StreamEnd)
        assert events[4].stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_usage_event(self, bedrock_provider, basic_config):
        """Usage metrics should yield UsageEvent."""
        chunks = [
            self._make_chunk({
                "type": "message_stop",
                "amazon-bedrock-invocationMetrics": {
                    "inputTokenCount": 1000,
                    "outputTokenCount": 200,
                    "cacheReadInputTokenCount": 500,
                    "cacheWriteInputTokenCount": 100,
                }
            }),
        ]
        mock_response = {"body": iter(chunks)}
        
        events = []
        async for event in bedrock_provider._parse_stream(mock_response, basic_config):
            events.append(event)
        
        # Should have both UsageEvent and StreamEnd
        usage_events = [e for e in events if isinstance(e, UsageEvent)]
        assert len(usage_events) == 1
        assert usage_events[0].input_tokens == 1000
        assert usage_events[0].output_tokens == 200
        assert usage_events[0].cache_read_tokens == 500
        assert usage_events[0].cache_write_tokens == 100

    @pytest.mark.asyncio
    async def test_thinking_delta(self, bedrock_provider, basic_config):
        """Thinking deltas should yield ThinkingDelta events."""
        chunks = [
            self._make_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": "Let me think..."}}),
            self._make_chunk({"type": "message_stop", "stop_reason": "end_turn"}),
        ]
        mock_response = {"body": iter(chunks)}
        
        events = []
        async for event in bedrock_provider._parse_stream(mock_response, basic_config):
            events.append(event)
        
        assert isinstance(events[0], ThinkingDelta)
        assert events[0].content == "Let me think..."

    @pytest.mark.asyncio
    async def test_empty_stream(self, bedrock_provider, basic_config):
        """Empty stream should yield no events."""
        mock_response = {"body": iter([])}
        
        events = []
        async for event in bedrock_provider._parse_stream(mock_response, basic_config):
            events.append(event)
        
        assert events == []

    @pytest.mark.asyncio
    async def test_chunks_without_chunk_key_skipped(self, bedrock_provider, basic_config):
        """Chunks without 'chunk' key should be skipped."""
        chunks = [
            {"metadata": "something"},  # No 'chunk' key
            self._make_chunk({"type": "message_stop", "stop_reason": "end_turn"}),
        ]
        mock_response = {"body": iter(chunks)}
        
        events = []
        async for event in bedrock_provider._parse_stream(mock_response, basic_config):
            events.append(event)
        
        assert len(events) == 1
        assert isinstance(events[0], StreamEnd)
