"""
Tests for OpenAIBedrockProvider — the provider for models that speak
the OpenAI Chat Completions wire format on Bedrock (DeepSeek, Kimi,
MiniMax, GLM, Qwen, OpenAI-GPT-OSS).

Verifies:
  1. Factory routing: wrapper_class="OpenAIBedrock" → OpenAIBedrockProvider
  2. Request body follows OpenAI Chat Completions schema
  3. Newlines in streamed content are preserved (the bug this provider fixes)
  4. Reasoning/thinking deltas are emitted for DeepSeek R1
  5. Anthropic content-block arrays are flattened to strings
  6. finish_reason → StreamEnd mapping is correct
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Factory routing
# ---------------------------------------------------------------------------

class TestFactoryRouting:
    """Verify create_provider routes OpenAIBedrock wrapper_class correctly."""

    def test_deepseek_v3_routes_to_openai_bedrock_provider(self):
        """DeepSeek v3 has wrapper_class=OpenAIBedrock → OpenAIBedrockProvider."""
        from app.config.models_config import MODEL_CONFIGS

        model_config = MODEL_CONFIGS["bedrock"]["deepseek-v3"]
        assert model_config.get("wrapper_class") == "OpenAIBedrock", (
            "deepseek-v3 should have wrapper_class='OpenAIBedrock'"
        )

        with patch(
            "app.providers.openai_bedrock.OpenAIBedrockProvider.__init__",
            return_value=None,
        ) as mock_init:
            from app.providers.factory import create_provider
            provider = create_provider(
                endpoint="bedrock",
                model_id="deepseek.v3-v1:0",
                model_config=model_config,
                aws_profile="test",
                region="us-west-2",
            )
            mock_init.assert_called_once()

    def test_nova_pro_still_routes_to_nova_provider(self):
        """Nova Pro (no wrapper_class) should still use NovaBedrockProvider."""
        from app.config.models_config import MODEL_CONFIGS

        model_config = MODEL_CONFIGS["bedrock"]["nova-pro"]
        assert model_config.get("wrapper_class") is None or "OpenAI" not in model_config.get("wrapper_class", "")

        with patch(
            "app.providers.nova_bedrock.NovaBedrockProvider.__init__",
            return_value=None,
        ) as mock_init:
            from app.providers.factory import create_provider
            provider = create_provider(
                endpoint="bedrock",
                model_id="us.amazon.nova-pro-v1:0",
                model_config=model_config,
                aws_profile="test",
                region="us-west-2",
            )
            mock_init.assert_called_once()

    def test_claude_still_routes_to_bedrock_provider(self):
        """Claude (family=claude) should still use BedrockProvider."""
        from app.config.models_config import MODEL_CONFIGS

        model_config = MODEL_CONFIGS["bedrock"]["sonnet4.0"]
        assert model_config.get("family") == "claude"

        with patch(
            "app.providers.bedrock.BedrockProvider.__init__",
            return_value=None,
        ) as mock_init:
            from app.providers.factory import create_provider
            provider = create_provider(
                endpoint="bedrock",
                model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
                model_config=model_config,
                aws_profile="test",
                region="us-west-2",
            )
            mock_init.assert_called_once()

    def test_all_openai_wrapper_models_detected(self):
        """Every model with wrapper_class=OpenAIBedrock should be identified."""
        from app.config.models_config import MODEL_CONFIGS

        openai_models = []
        for name, cfg in MODEL_CONFIGS.get("bedrock", {}).items():
            if cfg.get("wrapper_class") == "OpenAIBedrock":
                openai_models.append(name)

        assert len(openai_models) >= 5, (
            f"Expected at least 5 OpenAIBedrock models, got {len(openai_models)}: "
            f"{openai_models}"
        )
        # Verify known models are in the list
        for expected in ["deepseek-v3", "deepseek-v3.2", "kimi-k2.5"]:
            assert expected in openai_models, (
                f"Expected '{expected}' in OpenAIBedrock models"
            )


# ---------------------------------------------------------------------------
# Request body format
# ---------------------------------------------------------------------------

class TestRequestBodyFormat:
    """Verify the request body follows OpenAI Chat Completions schema."""

    def _make_provider(self):
        from app.providers.openai_bedrock import OpenAIBedrockProvider
        from app.providers.base import ProviderConfig

        with patch(
            "app.providers.bedrock_client_cache.get_persistent_bedrock_client",
            return_value=MagicMock(),
        ):
            return OpenAIBedrockProvider(
                model_id="deepseek.v3-v1:0",
                model_config={
                    "family": "deepseek",
                    "wrapper_class": "OpenAIBedrock",
                    "max_output_tokens": 4096,
                    "region": "us-west-2",
                },
                aws_profile="test",
                region="us-west-2",
            )

    def test_body_has_openai_schema_keys(self):
        from app.providers.base import ProviderConfig
        provider = self._make_provider()
        body = provider._build_request_body(
            messages=[{"role": "user", "content": "hello"}],
            system_content="You are helpful.",
            tools=[],
            config=ProviderConfig(max_output_tokens=2048, temperature=0.5),
        )
        assert "messages" in body
        assert "max_completion_tokens" in body
        assert "temperature" in body
        assert body["max_completion_tokens"] == 2048
        assert body["temperature"] == 0.5

    def test_system_message_prepended(self):
        from app.providers.base import ProviderConfig
        provider = self._make_provider()
        body = provider._build_request_body(
            messages=[{"role": "user", "content": "hi"}],
            system_content="Be concise.",
            tools=[],
            config=ProviderConfig(),
        )
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "Be concise."
        assert body["messages"][1]["role"] == "user"

    def test_anthropic_content_blocks_flattened(self):
        """Anthropic-style [{type: text, text: ...}] arrays → plain strings."""
        from app.providers.base import ProviderConfig
        provider = self._make_provider()
        body = provider._build_request_body(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Line 1"},
                    {"type": "text", "text": "Line 2"},
                ],
            }],
            system_content=None,
            tools=[],
            config=ProviderConfig(),
        )
        user_msg = body["messages"][0]
        assert isinstance(user_msg["content"], str)
        assert "Line 1" in user_msg["content"]
        assert "Line 2" in user_msg["content"]

    def test_max_tokens_capped_to_model_limit(self):
        from app.providers.base import ProviderConfig
        provider = self._make_provider()
        body = provider._build_request_body(
            messages=[{"role": "user", "content": "hi"}],
            system_content=None,
            tools=[],
            config=ProviderConfig(max_output_tokens=99999),
        )
        # Model config has max_output_tokens=4096
        assert body["max_completion_tokens"] == 4096


# ---------------------------------------------------------------------------
# Stream parsing — newline preservation
# ---------------------------------------------------------------------------

def _make_stream_event(delta_content=None, delta_reasoning=None,
                       finish_reason=None, usage=None):
    """Build a raw Bedrock streaming event in OpenAI format."""
    choice: Dict[str, Any] = {"index": 0}
    delta: Dict[str, Any] = {}
    if delta_content is not None:
        delta["content"] = delta_content
    if delta_reasoning is not None:
        delta["reasoning"] = delta_reasoning
    choice["delta"] = delta
    if finish_reason:
        choice["finish_reason"] = finish_reason

    chunk: Dict[str, Any] = {"choices": [choice]}
    if usage:
        chunk["usage"] = usage

    return {
        "chunk": {
            "bytes": json.dumps(chunk).encode("utf-8")
        }
    }


class TestStreamParsing:
    """Verify stream parsing preserves newlines and maps events correctly."""

    @pytest.fixture
    def provider(self):
        from app.providers.openai_bedrock import OpenAIBedrockProvider
        with patch(
            "app.providers.bedrock_client_cache.get_persistent_bedrock_client",
            return_value=MagicMock(),
        ):
            return OpenAIBedrockProvider(
                model_id="deepseek.v3-v1:0",
                model_config={"family": "deepseek", "max_output_tokens": 4096},
                aws_profile="test",
                region="us-west-2",
            )

    @pytest.mark.asyncio
    async def test_newlines_preserved_in_text_deltas(self, provider):
        """The core bug: newlines in streamed content must survive."""
        from app.providers.base import TextDelta, StreamEnd

        # Simulate DeepSeek sending markdown with real newlines
        content_with_newlines = "# Header\n\nParagraph one.\n\n## Subheader\n\n- Item 1\n- Item 2\n"
        events = [
            _make_stream_event(delta_content=content_with_newlines),
            _make_stream_event(finish_reason="stop"),
        ]

        response = {"body": iter(events)}
        collected = []
        async for ev in provider._parse_stream(response):
            collected.append(ev)

        text_events = [e for e in collected if isinstance(e, TextDelta)]
        assert len(text_events) == 1

        # The content must contain real newline characters
        assert "\n" in text_events[0].content, (
            f"Newlines lost! Got: {repr(text_events[0].content)}"
        )
        assert text_events[0].content == content_with_newlines

    @pytest.mark.asyncio
    async def test_multi_chunk_newlines_preserved(self, provider):
        """Newlines split across multiple chunks are all preserved."""
        from app.providers.base import TextDelta, StreamEnd

        events = [
            _make_stream_event(delta_content="Line 1\n"),
            _make_stream_event(delta_content="Line 2\n"),
            _make_stream_event(delta_content="\nLine 4\n"),
            _make_stream_event(finish_reason="stop"),
        ]

        response = {"body": iter(events)}
        collected = []
        async for ev in provider._parse_stream(response):
            collected.append(ev)

        text_events = [e for e in collected if isinstance(e, TextDelta)]
        combined = "".join(e.content for e in text_events)
        assert combined == "Line 1\nLine 2\n\nLine 4\n", (
            f"Newlines lost! Got: {repr(combined)}"
        )

    @pytest.mark.asyncio
    async def test_reasoning_yields_thinking_delta(self, provider):
        """DeepSeek R1 reasoning content should yield ThinkingDelta."""
        from app.providers.base import ThinkingDelta, TextDelta, StreamEnd

        events = [
            _make_stream_event(delta_reasoning="Let me think about this..."),
            _make_stream_event(delta_content="The answer is 42."),
            _make_stream_event(finish_reason="stop"),
        ]

        response = {"body": iter(events)}
        collected = []
        async for ev in provider._parse_stream(response):
            collected.append(ev)

        thinking = [e for e in collected if isinstance(e, ThinkingDelta)]
        text = [e for e in collected if isinstance(e, TextDelta)]
        assert len(thinking) == 1
        assert "think about this" in thinking[0].content
        assert len(text) == 1
        assert "42" in text[0].content

    @pytest.mark.asyncio
    async def test_finish_reason_mapping(self, provider):
        """finish_reason values map to correct StreamEnd stop_reasons."""
        from app.providers.base import StreamEnd

        for fr, expected_sr in [
            ("stop", "end_turn"),
            ("length", "max_tokens"),
            ("tool_calls", "tool_use"),
        ]:
            events = [_make_stream_event(finish_reason=fr)]
            response = {"body": iter(events)}
            collected = []
            async for ev in provider._parse_stream(response):
                collected.append(ev)

            ends = [e for e in collected if isinstance(e, StreamEnd)]
            assert len(ends) == 1, f"Expected 1 StreamEnd for finish_reason={fr}"
            assert ends[0].stop_reason == expected_sr, (
                f"finish_reason={fr} → expected {expected_sr}, "
                f"got {ends[0].stop_reason}"
            )

    @pytest.mark.asyncio
    async def test_usage_event_emitted(self, provider):
        """Usage stats in the response are forwarded as UsageEvent."""
        from app.providers.base import UsageEvent

        events = [
            _make_stream_event(delta_content="hi"),
            _make_stream_event(
                finish_reason="stop",
                usage={"prompt_tokens": 100, "completion_tokens": 50},
            ),
        ]
        response = {"body": iter(events)}
        collected = []
        async for ev in provider._parse_stream(response):
            collected.append(ev)

        usage = [e for e in collected if isinstance(e, UsageEvent)]
        assert len(usage) >= 1
        assert usage[-1].input_tokens == 100
        assert usage[-1].output_tokens == 50


# ---------------------------------------------------------------------------
# Provider interface compliance
# ---------------------------------------------------------------------------

class TestProviderInterface:
    """Verify LLMProvider abstract methods are implemented."""

    @pytest.fixture
    def provider(self):
        from app.providers.openai_bedrock import OpenAIBedrockProvider
        with patch(
            "app.providers.bedrock_client_cache.get_persistent_bedrock_client",
            return_value=MagicMock(),
        ):
            return OpenAIBedrockProvider(
                model_id="deepseek.v3-v1:0",
                model_config={"family": "deepseek"},
                aws_profile="test",
                region="us-west-2",
            )

    def test_provider_name(self, provider):
        assert provider.provider_name == "openai_bedrock"

    def test_build_assistant_message(self, provider):
        msg = provider.build_assistant_message("Hello\nWorld", [])
        assert msg["role"] == "assistant"
        assert "Hello" in msg["content"]

    def test_build_tool_result_message(self, provider):
        msg = provider.build_tool_result_message([
            {"tool_use_id": "t1", "content": "result text"},
        ])
        assert msg["role"] == "user"
        assert "result text" in msg["content"]

    def test_supports_feature_thinking(self, provider):
        # Base config doesn't have supports_thinking
        assert not provider.supports_feature("thinking")

    def test_supports_feature_with_config(self):
        from app.providers.openai_bedrock import OpenAIBedrockProvider
        with patch(
            "app.providers.bedrock_client_cache.get_persistent_bedrock_client",
            return_value=MagicMock(),
        ):
            p = OpenAIBedrockProvider(
                model_id="deepseek.r1-v1:0",
                model_config={"family": "deepseek", "supports_thinking": True},
                aws_profile="test",
                region="us-west-2",
            )
        assert p.supports_feature("thinking")
