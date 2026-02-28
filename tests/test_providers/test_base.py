"""
Tests for app.providers.base — the provider interface, stream events, and config.

These tests verify:
  1. StreamEvent dataclasses are frozen, slotted, and dispatchable via isinstance
  2. ProviderConfig defaults and overrides work correctly
  3. ThinkingConfig wiring
  4. LLMProvider ABC enforces the contract
  5. Default method implementations behave correctly
  6. ErrorType enum covers all expected categories
"""

import pytest
from dataclasses import FrozenInstanceError

from app.providers.base import (
    ErrorEvent,
    ErrorType,
    LLMProvider,
    ProviderConfig,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingConfig,
    ThinkingDelta,
    ToolUseEnd,
    ToolUseInput,
    ToolUseStart,
    UsageEvent,
)


# -----------------------------------------------------------------------
# StreamEvent dataclass tests
# -----------------------------------------------------------------------

class TestTextDelta:
    def test_creation(self):
        td = TextDelta(content="hello")
        assert td.content == "hello"

    def test_isinstance_stream_event(self):
        td = TextDelta(content="x")
        assert isinstance(td, StreamEvent)
        assert isinstance(td, TextDelta)

    def test_frozen(self):
        td = TextDelta(content="x")
        with pytest.raises(FrozenInstanceError):
            td.content = "y"

    def test_empty_content(self):
        td = TextDelta(content="")
        assert td.content == ""


class TestToolUseStart:
    def test_creation(self):
        ev = ToolUseStart(id="tool_123", name="run_shell_command", index=0)
        assert ev.id == "tool_123"
        assert ev.name == "run_shell_command"
        assert ev.index == 0

    def test_default_index(self):
        ev = ToolUseStart(id="t1", name="foo")
        assert ev.index == 0

    def test_isinstance(self):
        ev = ToolUseStart(id="t1", name="foo")
        assert isinstance(ev, StreamEvent)
        assert not isinstance(ev, TextDelta)


class TestToolUseInput:
    def test_creation(self):
        ev = ToolUseInput(partial_json='{"command":', index=2)
        assert ev.partial_json == '{"command":'
        assert ev.index == 2

    def test_default_index(self):
        ev = ToolUseInput(partial_json="")
        assert ev.index == 0


class TestToolUseEnd:
    def test_creation(self):
        ev = ToolUseEnd(id="t1", name="read_file", input={"path": "/tmp/x"}, index=1)
        assert ev.id == "t1"
        assert ev.name == "read_file"
        assert ev.input == {"path": "/tmp/x"}
        assert ev.index == 1

    def test_empty_input(self):
        ev = ToolUseEnd(id="t1", name="foo", input={})
        assert ev.input == {}


class TestUsageEvent:
    def test_defaults(self):
        ev = UsageEvent()
        assert ev.input_tokens == 0
        assert ev.output_tokens == 0
        assert ev.cache_read_tokens == 0
        assert ev.cache_write_tokens == 0

    def test_partial_fill(self):
        ev = UsageEvent(input_tokens=1000, cache_read_tokens=500)
        assert ev.input_tokens == 1000
        assert ev.output_tokens == 0
        assert ev.cache_read_tokens == 500

    def test_isinstance(self):
        assert isinstance(UsageEvent(), StreamEvent)


class TestThinkingDelta:
    def test_creation(self):
        ev = ThinkingDelta(content="Let me think...")
        assert ev.content == "Let me think..."
        assert isinstance(ev, StreamEvent)


class TestErrorEvent:
    def test_defaults(self):
        ev = ErrorEvent(message="boom")
        assert ev.message == "boom"
        assert ev.error_type == ErrorType.UNKNOWN
        assert ev.retryable is False
        assert ev.status_code is None

    def test_throttle(self):
        ev = ErrorEvent(
            message="Too many requests",
            error_type=ErrorType.THROTTLE,
            retryable=True,
            status_code=429,
        )
        assert ev.error_type == ErrorType.THROTTLE
        assert ev.retryable is True
        assert ev.status_code == 429

    def test_all_error_types(self):
        """Every ErrorType variant should be instantiable in an ErrorEvent."""
        for et in ErrorType:
            ev = ErrorEvent(message="test", error_type=et)
            assert ev.error_type == et


class TestStreamEnd:
    def test_default_stop_reason(self):
        ev = StreamEnd()
        assert ev.stop_reason == "end_turn"

    def test_tool_use_stop_reason(self):
        ev = StreamEnd(stop_reason="tool_use")
        assert ev.stop_reason == "tool_use"


# -----------------------------------------------------------------------
# Event dispatch via isinstance — the core pattern the orchestrator uses
# -----------------------------------------------------------------------

class TestEventDispatch:
    """The orchestrator dispatches events with isinstance. Verify it works
    cleanly for all types."""

    def _dispatch(self, event: StreamEvent) -> str:
        if isinstance(event, TextDelta):
            return "text"
        if isinstance(event, ToolUseStart):
            return "tool_start"
        if isinstance(event, ToolUseInput):
            return "tool_input"
        if isinstance(event, ToolUseEnd):
            return "tool_end"
        if isinstance(event, UsageEvent):
            return "usage"
        if isinstance(event, ThinkingDelta):
            return "thinking"
        if isinstance(event, ErrorEvent):
            return "error"
        if isinstance(event, StreamEnd):
            return "stream_end"
        return "unknown"

    def test_all_event_types_dispatched(self):
        events = [
            TextDelta(content="hi"),
            ToolUseStart(id="t1", name="foo"),
            ToolUseInput(partial_json="{}"),
            ToolUseEnd(id="t1", name="foo", input={}),
            UsageEvent(input_tokens=10),
            ThinkingDelta(content="hmm"),
            ErrorEvent(message="oops"),
            StreamEnd(),
        ]
        expected = [
            "text", "tool_start", "tool_input", "tool_end",
            "usage", "thinking", "error", "stream_end",
        ]
        results = [self._dispatch(e) for e in events]
        assert results == expected


# -----------------------------------------------------------------------
# ErrorType enum
# -----------------------------------------------------------------------

class TestErrorType:
    def test_all_variants(self):
        expected = {"THROTTLE", "CONTEXT_LIMIT", "READ_TIMEOUT", "OVERLOADED", "AUTH", "UNKNOWN"}
        actual = {e.name for e in ErrorType}
        assert actual == expected


# -----------------------------------------------------------------------
# ProviderConfig
# -----------------------------------------------------------------------

class TestProviderConfig:
    def test_defaults(self):
        cfg = ProviderConfig()
        assert cfg.max_output_tokens == 16384
        assert cfg.temperature == 0.3
        assert cfg.thinking is None
        assert cfg.enable_cache is True
        assert cfg.use_extended_context is False
        assert cfg.suppress_tools is False
        assert cfg.model_config == {}
        assert cfg.iteration == 0

    def test_override(self):
        cfg = ProviderConfig(
            max_output_tokens=8192,
            temperature=0.0,
            suppress_tools=True,
            iteration=5,
        )
        assert cfg.max_output_tokens == 8192
        assert cfg.temperature == 0.0
        assert cfg.suppress_tools is True
        assert cfg.iteration == 5

    def test_with_thinking(self):
        thinking = ThinkingConfig(enabled=True, mode="adaptive", effort="max")
        cfg = ProviderConfig(thinking=thinking)
        assert cfg.thinking.enabled is True
        assert cfg.thinking.mode == "adaptive"
        assert cfg.thinking.effort == "max"

    def test_model_config_passthrough(self):
        mc = {"family": "claude", "supports_thinking": True}
        cfg = ProviderConfig(model_config=mc)
        assert cfg.model_config["family"] == "claude"

    def test_none_temperature(self):
        """Temperature=None is valid (some models don't support it)."""
        cfg = ProviderConfig(temperature=None)
        assert cfg.temperature is None


class TestThinkingConfig:
    def test_defaults(self):
        tc = ThinkingConfig()
        assert tc.enabled is False
        assert tc.mode == "adaptive"
        assert tc.effort == "high"
        assert tc.budget_tokens == 16000

    def test_standard_thinking(self):
        tc = ThinkingConfig(enabled=True, mode="enabled", budget_tokens=32000)
        assert tc.enabled is True
        assert tc.mode == "enabled"
        assert tc.budget_tokens == 32000


# -----------------------------------------------------------------------
# LLMProvider ABC enforcement
# -----------------------------------------------------------------------

class TestLLMProviderABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            LLMProvider()

    def test_must_implement_stream_response(self):
        """A subclass that doesn't implement stream_response should fail."""

        class Incomplete(LLMProvider):
            def build_assistant_message(self, text, tool_uses):
                return {}

            def build_tool_result_message(self, tool_results):
                return {}

        with pytest.raises(TypeError):
            Incomplete()

    def test_must_implement_build_assistant_message(self):
        class Incomplete(LLMProvider):
            async def stream_response(self, messages, system_content, tools, config):
                yield  # pragma: no cover

            def build_tool_result_message(self, tool_results):
                return {}

        with pytest.raises(TypeError):
            Incomplete()

    def test_must_implement_build_tool_result_message(self):
        class Incomplete(LLMProvider):
            async def stream_response(self, messages, system_content, tools, config):
                yield  # pragma: no cover

            def build_assistant_message(self, text, tool_uses):
                return {}

        with pytest.raises(TypeError):
            Incomplete()

    def test_complete_implementation_succeeds(self):
        """A fully implemented subclass should instantiate fine."""

        class Complete(LLMProvider):
            async def stream_response(self, messages, system_content, tools, config):
                yield TextDelta(content="ok")

            def build_assistant_message(self, text, tool_uses):
                return {"role": "assistant", "content": text}

            def build_tool_result_message(self, tool_results):
                return {"role": "user", "content": tool_results}

        provider = Complete()
        assert provider is not None


# -----------------------------------------------------------------------
# Default method implementations
# -----------------------------------------------------------------------

class TestLLMProviderDefaults:
    """Test default implementations of optional methods on LLMProvider."""

    def _make_provider(self):
        class Minimal(LLMProvider):
            async def stream_response(self, messages, system_content, tools, config):
                yield TextDelta(content="ok")

            def build_assistant_message(self, text, tool_uses):
                return {}

            def build_tool_result_message(self, tool_results):
                return {}

        return Minimal()

    def test_prepare_cache_control_passthrough(self):
        """Default prepare_cache_control returns messages unchanged."""
        p = self._make_provider()
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        result = p.prepare_cache_control(msgs, iteration=3)
        assert result is msgs  # same object, not copied

    def test_supports_feature_default_false(self):
        p = self._make_provider()
        assert p.supports_feature("thinking") is False
        assert p.supports_feature("extended_context") is False
        assert p.supports_feature("cache_control") is False
        assert p.supports_feature("nonexistent_feature") is False

    def test_provider_name_default(self):
        p = self._make_provider()
        assert p.provider_name == "Minimal"
