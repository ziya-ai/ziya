"""
Tests for app.providers.openai_direct — the OpenAI-compatible provider.

Tests verify:
  1. Request building (OpenAI format, system message, tool conversion)
  2. Anthropic→OpenAI message format conversion (tool_use/tool_result)
  3. Stream parsing (text deltas, tool call deltas, usage, finish_reason)
  4. Message formatting (assistant with tool_calls, tool results)
  5. Multi-tool-result handling (_multi_tool_results)
  6. Retry logic
  7. Error classification
  8. Feature support
  9. OpenRouter wiring (same class, different base_url)
"""

import asyncio
import json
import os
import pytest
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any

from app.providers.base import (
    ErrorEvent,
    ErrorType,
    ProviderConfig,
    StreamEnd,
    TextDelta,
    ThinkingConfig,
    ToolUseEnd,
    ToolUseInput,
    ToolUseStart,
    UsageEvent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_openai_module():
    """Mock the openai module so we don't need real credentials."""
    mock_mod = MagicMock()
    mock_client = MagicMock()
    mock_mod.AsyncOpenAI.return_value = mock_client
    with patch.dict(sys.modules, {"openai": mock_mod}):
        yield mock_mod


@pytest.fixture
def provider(mock_openai_module):
    """Create an OpenAIDirectProvider with mocked client."""
    from app.providers.openai_direct import OpenAIDirectProvider

    p = OpenAIDirectProvider(
        model_id="gpt-4.1",
        model_config={
            "family": "openai-gpt",
            "supports_thinking": False,
            "max_output_tokens": 16384,
        },
        api_key="sk-test-key",
    )
    return p


@pytest.fixture
def basic_config():
    return ProviderConfig(max_output_tokens=4096, temperature=0.5)


# ---------------------------------------------------------------------------
# Request building
# ---------------------------------------------------------------------------

class TestBuildRequest:
    def test_basic_structure(self, provider, basic_config):
        messages = [{"role": "user", "content": "hello"}]
        req = provider._build_request(messages, None, [], basic_config)

        assert req["model"] == "gpt-4.1"
        assert req["max_tokens"] == 4096
        assert req["temperature"] == 0.5
        assert req["stream"] is True
        assert req["stream_options"] == {"include_usage": True}
        assert "tools" not in req

    def test_system_message_prepended(self, provider, basic_config):
        messages = [{"role": "user", "content": "hi"}]
        req = provider._build_request(messages, "You are helpful.", [], basic_config)

        assert req["messages"][0] == {"role": "system", "content": "You are helpful."}
        assert req["messages"][1] == {"role": "user", "content": "hi"}

    def test_no_system_when_none(self, provider, basic_config):
        messages = [{"role": "user", "content": "hi"}]
        req = provider._build_request(messages, None, [], basic_config)

        assert req["messages"][0] == {"role": "user", "content": "hi"}

    def test_tools_converted_to_openai_format(self, provider, basic_config):
        anthropic_tools = [{
            "name": "run_shell_command",
            "description": "Run a shell command",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        }]
        req = provider._build_request(
            [{"role": "user", "content": "hi"}], None, anthropic_tools, basic_config
        )

        assert len(req["tools"]) == 1
        tool = req["tools"][0]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "run_shell_command"
        assert tool["function"]["description"] == "Run a shell command"
        assert tool["function"]["parameters"]["required"] == ["command"]
        assert req["tool_choice"] == "auto"

    def test_tools_suppressed(self, provider):
        tools = [{"name": "foo", "description": "bar", "input_schema": {}}]
        config = ProviderConfig(suppress_tools=True)
        req = provider._build_request(
            [{"role": "user", "content": "hi"}], None, tools, config
        )

        assert "tools" not in req

    def test_temperature_none_excluded(self, provider):
        config = ProviderConfig(temperature=None)
        req = provider._build_request(
            [{"role": "user", "content": "hi"}], None, [], config
        )

        assert "temperature" not in req


# ---------------------------------------------------------------------------
# Anthropic -> OpenAI message format conversion
# ---------------------------------------------------------------------------

class TestAnthropicMessageConversion:
    """The orchestrator builds conversation in Anthropic format.
    The provider must translate to OpenAI format."""

    def test_tool_result_blocks_become_tool_messages(self, provider, basic_config):
        messages = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "file contents"},
                {"type": "tool_result", "tool_use_id": "t2", "content": "command output"},
            ]},
        ]
        req = provider._build_request(messages, None, [], basic_config)

        assert len(req["messages"]) == 2
        assert req["messages"][0] == {"role": "tool", "tool_call_id": "t1", "content": "file contents"}
        assert req["messages"][1] == {"role": "tool", "tool_call_id": "t2", "content": "command output"}

    def test_assistant_tool_use_becomes_tool_calls(self, provider, basic_config):
        messages = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "t1", "name": "run_shell_command",
                 "input": {"command": "ls"}},
            ]},
        ]
        req = provider._build_request(messages, None, [], basic_config)

        msg = req["messages"][0]
        assert msg["role"] == "assistant"
        assert msg["content"] == "Let me check."
        assert len(msg["tool_calls"]) == 1
        tc = msg["tool_calls"][0]
        assert tc["id"] == "t1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "run_shell_command"
        assert json.loads(tc["function"]["arguments"]) == {"command": "ls"}

    def test_assistant_text_only_no_tool_calls(self, provider, basic_config):
        messages = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Just text, no tools."},
            ]},
        ]
        req = provider._build_request(messages, None, [], basic_config)

        msg = req["messages"][0]
        assert msg["role"] == "assistant"
        assert msg["content"] == "Just text, no tools."
        assert "tool_calls" not in msg

    def test_plain_string_messages_pass_through(self, provider, basic_config):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        req = provider._build_request(messages, None, [], basic_config)

        assert req["messages"][0] == {"role": "user", "content": "hello"}
        assert req["messages"][1] == {"role": "assistant", "content": "hi there"}

    def test_full_conversation_roundtrip(self, provider, basic_config):
        """Simulate a full Anthropic-format conversation and verify conversion."""
        messages = [
            {"role": "user", "content": "List files"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Sure, running ls:"},
                {"type": "tool_use", "id": "t1", "name": "run_shell_command",
                 "input": {"command": "ls -la"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "file1.py\nfile2.py"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Found 2 files."},
            ]},
        ]
        req = provider._build_request(messages, "You are helpful.", [], basic_config)

        assert req["messages"][0] == {"role": "system", "content": "You are helpful."}
        assert req["messages"][1] == {"role": "user", "content": "List files"}
        assert req["messages"][2]["role"] == "assistant"
        assert req["messages"][2]["content"] == "Sure, running ls:"
        assert len(req["messages"][2]["tool_calls"]) == 1
        assert req["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": "file1.py\nfile2.py"}
        assert req["messages"][4] == {"role": "assistant", "content": "Found 2 files."}


# ---------------------------------------------------------------------------
# Message formatting (output side)
# ---------------------------------------------------------------------------

class TestMessageFormatting:
    def test_assistant_text_only(self, provider):
        msg = provider.build_assistant_message("Hello!", [])
        assert msg["role"] == "assistant"
        assert msg["content"] == "Hello!"
        assert "tool_calls" not in msg

    def test_assistant_empty_text_is_none(self, provider):
        msg = provider.build_assistant_message("  ", [{"id": "t1", "name": "foo", "input": {}}])
        assert msg["content"] is None
        assert len(msg["tool_calls"]) == 1

    def test_assistant_with_tools(self, provider):
        tool_uses = [
            {"id": "t1", "name": "run_shell_command", "input": {"command": "ls"}},
            {"id": "t2", "name": "read_file", "input": {"path": "/tmp/x"}},
        ]
        msg = provider.build_assistant_message("Running:", tool_uses)

        assert msg["content"] == "Running:"
        assert len(msg["tool_calls"]) == 2
        assert msg["tool_calls"][0]["function"]["name"] == "run_shell_command"
        assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"command": "ls"}
        assert msg["tool_calls"][1]["function"]["name"] == "read_file"

    def test_tool_result_single(self, provider):
        msg = provider.build_tool_result_message([
            {"tool_use_id": "t1", "content": "result here"},
        ])
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "t1"
        assert msg["content"] == "result here"

    def test_tool_result_multiple_wrapped(self, provider):
        msg = provider.build_tool_result_message([
            {"tool_use_id": "t1", "content": "result1"},
            {"tool_use_id": "t2", "content": "result2"},
        ])
        assert msg["role"] == "_multi_tool_results"
        assert len(msg["results"]) == 2
        assert msg["results"][0]["role"] == "tool"
        assert msg["results"][0]["tool_call_id"] == "t1"
        assert msg["results"][1]["tool_call_id"] == "t2"


# ---------------------------------------------------------------------------
# Stream parsing — mock OpenAI SSE chunks
# ---------------------------------------------------------------------------

class MockDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls

class MockToolCallDelta:
    def __init__(self, index, id=None, function=None):
        self.index = index
        self.id = id
        self.function = function

class MockFunction:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments

class MockChoice:
    def __init__(self, delta=None, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason

class MockChunk:
    def __init__(self, choices=None, usage=None):
        self.choices = choices or []
        self.usage = usage


class _AsyncIter:
    """Wraps a list into an async iterator (mimics OpenAI stream object)."""
    def __init__(self, items):
        self._items = items
        self._idx = 0
    def __aiter__(self):
        return self
    async def __anext__(self):
        if self._idx >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._idx]
        self._idx += 1
        return item


class TestStreamParsing:
    @pytest.mark.asyncio
    async def test_text_streaming(self, provider):
        chunks = [
            MockChunk(choices=[MockChoice(delta=MockDelta(content="Hello"))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(content=" world"))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(), finish_reason="stop")]),
        ]

        async def mock_create(**kwargs):
            return _AsyncIter(chunks)

        provider.client.chat.completions.create = mock_create

        events = []
        async for event in provider.stream_response(
            [{"role": "user", "content": "hi"}], None, [], ProviderConfig()
        ):
            events.append(event)

        assert isinstance(events[0], TextDelta)
        assert events[0].content == "Hello"
        assert isinstance(events[1], TextDelta)
        assert events[1].content == " world"
        assert isinstance(events[2], StreamEnd)
        assert events[2].stop_reason == "stop"

    @pytest.mark.asyncio
    async def test_tool_call_streaming(self, provider):
        chunks = [
            MockChunk(choices=[MockChoice(delta=MockDelta(tool_calls=[
                MockToolCallDelta(index=0, id="call_abc", function=MockFunction(name="run_shell_command")),
            ]))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(tool_calls=[
                MockToolCallDelta(index=0, function=MockFunction(arguments='{"command":')),
            ]))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(tool_calls=[
                MockToolCallDelta(index=0, function=MockFunction(arguments='"ls"}')),
            ]))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(), finish_reason="tool_calls")]),
        ]

        async def mock_create(**kwargs):
            return _AsyncIter(chunks)

        provider.client.chat.completions.create = mock_create

        events = []
        async for event in provider.stream_response(
            [{"role": "user", "content": "hi"}], None, [], ProviderConfig()
        ):
            events.append(event)

        assert isinstance(events[0], ToolUseStart)
        assert events[0].id == "call_abc"
        assert events[0].name == "run_shell_command"

        assert isinstance(events[1], ToolUseInput)
        assert events[1].partial_json == '{"command":'

        assert isinstance(events[2], ToolUseInput)
        assert events[2].partial_json == '"ls"}'

        assert isinstance(events[3], ToolUseEnd)
        assert events[3].name == "run_shell_command"
        assert events[3].input == {"command": "ls"}

        assert isinstance(events[4], StreamEnd)
        assert events[4].stop_reason == "tool_calls"

    @pytest.mark.asyncio
    async def test_usage_in_final_chunk(self, provider):
        class MockUsage:
            prompt_tokens = 500
            completion_tokens = 100
            prompt_tokens_details = None

        chunks = [
            MockChunk(choices=[MockChoice(delta=MockDelta(content="Hi"))]),
            MockChunk(choices=[MockChoice(delta=MockDelta(), finish_reason="stop")]),
            MockChunk(choices=[], usage=MockUsage()),
        ]

        async def mock_create(**kwargs):
            return _AsyncIter(chunks)

        provider.client.chat.completions.create = mock_create

        events = []
        async for event in provider.stream_response(
            [{"role": "user", "content": "hi"}], None, [], ProviderConfig()
        ):
            events.append(event)

        usage_events = [e for e in events if isinstance(e, UsageEvent)]
        assert len(usage_events) == 1
        assert usage_events[0].input_tokens == 500
        assert usage_events[0].output_tokens == 100

    @pytest.mark.asyncio
    async def test_usage_with_cached_tokens(self, provider):
        class MockDetails:
            cached_tokens = 200

        class MockUsage:
            prompt_tokens = 500
            completion_tokens = 100
            prompt_tokens_details = MockDetails()

        chunks = [
            MockChunk(choices=[MockChoice(delta=MockDelta(), finish_reason="stop")]),
            MockChunk(choices=[], usage=MockUsage()),
        ]

        async def mock_create(**kwargs):
            return _AsyncIter(chunks)

        provider.client.chat.completions.create = mock_create

        events = []
        async for event in provider.stream_response(
            [{"role": "user", "content": "hi"}], None, [], ProviderConfig()
        ):
            events.append(event)

        usage_events = [e for e in events if isinstance(e, UsageEvent)]
        assert len(usage_events) == 1
        assert usage_events[0].cache_read_tokens == 200

    @pytest.mark.asyncio
    async def test_empty_stream(self, provider):
        async def mock_create(**kwargs):
            return _AsyncIter([])

        provider.client.chat.completions.create = mock_create

        events = []
        async for event in provider.stream_response(
            [{"role": "user", "content": "hi"}], None, [], ProviderConfig()
        ):
            events.append(event)

        assert events == []


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_throttle_retried(self, provider):
        """Throttled requests should be retried with backoff.

        Patches asyncio.sleep so the exponential backoff delays
        don't cause 8+ seconds of real wall time.
        """
        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("429 rate limit exceeded")
            return _AsyncIter([MockChunk(choices=[MockChoice(delta=MockDelta(), finish_reason="stop")])])

        provider.client.chat.completions.create = mock_create

        events = []
        with patch("app.providers.openai_direct.asyncio.sleep", new_callable=AsyncMock):
            async for event in provider.stream_response(
                [{"role": "user", "content": "hi"}], None, [], ProviderConfig()
            ):
                events.append(event)

        assert call_count == 3
        assert isinstance(events[0], StreamEnd)

    @pytest.mark.asyncio
    async def test_throttle_exhausted_yields_error(self, provider):
        """After max retries, a throttle error event should be yielded."""
        async def mock_create(**kwargs):
            raise Exception("429 too many requests")

        provider.client.chat.completions.create = mock_create

        events = []
        with patch("app.providers.openai_direct.asyncio.sleep", new_callable=AsyncMock):
            async for event in provider.stream_response(
                [{"role": "user", "content": "hi"}], None, [], ProviderConfig()
            ):
                events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], ErrorEvent)
        assert events[0].error_type == ErrorType.THROTTLE


# ---------------------------------------------------------------------------
# Feature support
# ---------------------------------------------------------------------------

class TestFeatureSupport:
    def test_no_cache_control(self, provider):
        assert provider.supports_feature("cache_control") is False

    def test_no_extended_context(self, provider):
        assert provider.supports_feature("extended_context") is False

    def test_no_adaptive_thinking(self, provider):
        assert provider.supports_feature("adaptive_thinking") is False

    def test_assistant_prefill(self, provider):
        assert provider.supports_feature("assistant_prefill") is True

    def test_thinking_from_config(self, provider):
        assert provider.supports_feature("thinking") is False

    def test_provider_name(self, provider):
        assert provider.provider_name == "openai"

    def test_unknown_feature(self, provider):
        assert provider.supports_feature("nonexistent") is False


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

class TestErrorClassification:
    def test_429(self):
        from app.providers.openai_direct import OpenAIDirectProvider
        assert OpenAIDirectProvider._classify_error("Error 429") == ErrorType.THROTTLE

    def test_rate_limit(self):
        from app.providers.openai_direct import OpenAIDirectProvider
        assert OpenAIDirectProvider._classify_error("rate limit exceeded") == ErrorType.THROTTLE

    def test_too_many(self):
        from app.providers.openai_direct import OpenAIDirectProvider
        assert OpenAIDirectProvider._classify_error("too many requests") == ErrorType.THROTTLE

    def test_503(self):
        from app.providers.openai_direct import OpenAIDirectProvider
        assert OpenAIDirectProvider._classify_error("503 service unavailable") == ErrorType.OVERLOADED

    def test_timeout(self):
        from app.providers.openai_direct import OpenAIDirectProvider
        assert OpenAIDirectProvider._classify_error("request timeout") == ErrorType.READ_TIMEOUT

    def test_context_limit(self):
        from app.providers.openai_direct import OpenAIDirectProvider
        assert OpenAIDirectProvider._classify_error("context length limit exceeded") == ErrorType.CONTEXT_LIMIT

    def test_unknown(self):
        from app.providers.openai_direct import OpenAIDirectProvider
        assert OpenAIDirectProvider._classify_error("Something else") == ErrorType.UNKNOWN


# ---------------------------------------------------------------------------
# OpenRouter wiring (same provider, different base_url)
# ---------------------------------------------------------------------------

class TestOpenRouterWiring:
    def test_factory_creates_openai_provider_for_openrouter(self):
        """OpenRouter endpoint should create OpenAIDirectProvider with custom base_url."""
        with patch.dict(sys.modules, {"openai": MagicMock()}):
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}):
                from app.providers.factory import create_provider
                from app.providers.openai_direct import OpenAIDirectProvider

                provider = create_provider(
                    endpoint="openrouter",
                    model_id="anthropic/claude-sonnet-4",
                    model_config={},
                )

                assert isinstance(provider, OpenAIDirectProvider)
                assert provider.provider_name == "openai"

    def test_factory_creates_openai_provider_for_openai(self):
        """OpenAI endpoint should create OpenAIDirectProvider."""
        with patch.dict(sys.modules, {"openai": MagicMock()}):
            from app.providers.factory import create_provider
            from app.providers.openai_direct import OpenAIDirectProvider

            provider = create_provider(
                endpoint="openai",
                model_id="gpt-4.1",
                model_config={},
                api_key="sk-test",
            )

            assert isinstance(provider, OpenAIDirectProvider)

    def test_factory_openrouter_uses_custom_base_url(self):
        """OpenRouter should pass the OpenRouter base URL to the provider."""
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.AsyncOpenAI.return_value = mock_client

        with patch.dict(sys.modules, {"openai": mock_openai}):
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}):
                from importlib import reload
                import app.providers.openai_direct as oai_mod
                reload(oai_mod)

                from app.providers.factory import create_provider
                provider = create_provider(
                    endpoint="openrouter",
                    model_id="anthropic/claude-sonnet-4",
                    model_config={},
                )

                # Verify AsyncOpenAI was called with the OpenRouter base URL
                mock_openai.AsyncOpenAI.assert_called_with(
                    api_key="sk-or-test",
                    base_url="https://openrouter.ai/api/v1",
                )
