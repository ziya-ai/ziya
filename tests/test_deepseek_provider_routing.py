"""
Tests for DeepSeek model provider routing and response fidelity.

The three DeepSeek models on Bedrock have different invocation formats:

  - deepseek-r1:   No wrapper_class → NovaBedrockProvider (Converse API)
                    Has native_function_calling=false, supports_thinking=true
  - deepseek-v3:   wrapper_class=OpenAIBedrock → OpenAIBedrockProvider (invoke_model)
  - deepseek-v3.2: wrapper_class=OpenAIBedrock → OpenAIBedrockProvider (invoke_model)

This matters because:
  1. The Converse API can mangle newlines for OpenAI-format models
  2. R1 has reasoning/thinking support, v3/v3.2 don't (via config)
  3. R1 explicitly disables native_function_calling
  4. v3/v3.2 are region-locked to us-west-2

Run:
    pytest tests/test_deepseek_provider_routing.py -v
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch
from typing import Any, Dict, List

import pytest

from app.config.models_config import MODEL_CONFIGS, MODEL_FAMILIES


# ---------------------------------------------------------------------------
# Config-level tests — verify the model definitions are correct
# ---------------------------------------------------------------------------

class TestDeepSeekModelConfigs:
    """Verify model_config entries for all three DeepSeek models."""

    def test_r1_has_no_wrapper_class(self):
        cfg = MODEL_CONFIGS["bedrock"]["deepseek-r1"]
        assert "wrapper_class" not in cfg, (
            "deepseek-r1 should NOT have wrapper_class — it uses Converse API"
        )

    def test_r1_disables_native_function_calling(self):
        cfg = MODEL_CONFIGS["bedrock"]["deepseek-r1"]
        assert cfg.get("native_function_calling") is False

    def test_r1_family_supports_thinking(self):
        cfg = MODEL_CONFIGS["bedrock"]["deepseek-r1"]
        family = cfg.get("family")
        assert family == "deepseek"
        family_cfg = MODEL_FAMILIES.get(family, {})
        assert family_cfg.get("supports_thinking") is True

    def test_v3_has_openai_wrapper_class(self):
        cfg = MODEL_CONFIGS["bedrock"]["deepseek-v3"]
        assert cfg.get("wrapper_class") == "OpenAIBedrock"

    def test_v3_2_has_openai_wrapper_class(self):
        cfg = MODEL_CONFIGS["bedrock"]["deepseek-v3.2"]
        assert cfg.get("wrapper_class") == "OpenAIBedrock"

    def test_v3_region_locked_to_us_west_2(self):
        cfg = MODEL_CONFIGS["bedrock"]["deepseek-v3"]
        assert cfg.get("region") == "us-west-2"

    def test_v3_2_region_locked_to_us_west_2(self):
        cfg = MODEL_CONFIGS["bedrock"]["deepseek-v3.2"]
        assert cfg.get("region") == "us-west-2"

    def test_all_deepseek_models_share_family(self):
        for name in ["deepseek-r1", "deepseek-v3", "deepseek-v3.2"]:
            cfg = MODEL_CONFIGS["bedrock"][name]
            assert cfg.get("family") == "deepseek", (
                f"{name} should have family 'deepseek'"
            )

    def test_all_deepseek_have_128k_context(self):
        for name in ["deepseek-r1", "deepseek-v3", "deepseek-v3.2"]:
            cfg = MODEL_CONFIGS["bedrock"][name]
            assert cfg.get("context_window") == 128000


# ---------------------------------------------------------------------------
# Factory routing tests
# ---------------------------------------------------------------------------

class TestDeepSeekFactoryRouting:
    """Verify the provider factory routes each model correctly."""

    def _route(self, model_name: str) -> str:
        """Create a provider for the named model and return its class name."""
        cfg = MODEL_CONFIGS["bedrock"][model_name]
        model_id = cfg["model_id"]["us"]

        with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client",
                   return_value=MagicMock(client=MagicMock())):
            from app.providers.factory import create_provider
            provider = create_provider(
                endpoint="bedrock", model_id=model_id,
                model_config=cfg, aws_profile="ziya", region="us-west-2",
            )
            return provider.__class__.__name__

    def test_r1_routes_to_nova_provider(self):
        """R1 has no wrapper_class → should use NovaBedrockProvider (Converse API)."""
        assert self._route("deepseek-r1") == "NovaBedrockProvider"

    def test_v3_routes_to_openai_provider(self):
        """V3 has wrapper_class=OpenAIBedrock → OpenAIBedrockProvider."""
        assert self._route("deepseek-v3") == "OpenAIBedrockProvider"

    def test_v3_2_routes_to_openai_provider(self):
        """V3.2 has wrapper_class=OpenAIBedrock → OpenAIBedrockProvider."""
        assert self._route("deepseek-v3.2") == "OpenAIBedrockProvider"


# ---------------------------------------------------------------------------
# OpenAI-format response parsing — newline preservation
# ---------------------------------------------------------------------------

def _make_openai_stream_chunks(text: str) -> list:
    """Build mock invoke_model_with_response_stream response body.

    Splits the text into character-level chunks to simulate realistic
    streaming granularity.  Each chunk is an OpenAI Chat Completions
    delta with the text fragment.
    """
    chunks = []
    for char in text:
        chunk_data = {
            "choices": [{
                "delta": {"content": char},
                "finish_reason": None,
            }]
        }
        chunks.append({"chunk": {"bytes": json.dumps(chunk_data).encode()}})

    # Final chunk with finish_reason
    chunks.append({"chunk": {"bytes": json.dumps({
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }).encode()}})
    return chunks


def _make_converse_stream_chunks(text: str) -> list:
    """Build mock converse_stream response body.

    Simulates the Converse API stream format with contentBlockDelta events.
    """
    chunks = [
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
    ]
    # Send text in a single block (Converse API typically sends larger chunks)
    chunks.append({
        "contentBlockDelta": {
            "contentBlockIndex": 0,
            "delta": {"text": text},
        }
    })
    chunks.append({"contentBlockStop": {"contentBlockIndex": 0}})
    chunks.append({"messageStop": {"stopReason": "end_turn"}})
    chunks.append({"metadata": {"usage": {"inputTokens": 100, "outputTokens": 50}}})
    return chunks


class TestDeepSeekNewlinePreservation:
    """The core bug: verify newlines survive the full streaming path."""

    MARKDOWN_WITH_NEWLINES = (
        "# Header\n\n"
        "Paragraph one.\n\n"
        "## Subheader\n\n"
        "- Item 1\n"
        "- Item 2\n"
        "  - Nested\n\n"
        "```python\n"
        "def hello():\n"
        "    print('world')\n"
        "```\n\n"
        "Final line.\n"
    )

    @pytest.mark.asyncio
    async def test_v3_preserves_newlines_via_openai_provider(self):
        """DeepSeek V3 through OpenAIBedrockProvider should preserve all newlines."""
        from app.providers.openai_bedrock import OpenAIBedrockProvider
        from app.providers.base import TextDelta, StreamEnd, UsageEvent

        cfg = MODEL_CONFIGS["bedrock"]["deepseek-v3"]
        mock_chunks = _make_openai_stream_chunks(self.MARKDOWN_WITH_NEWLINES)

        # The provider does getattr(self.bedrock, 'client', self.bedrock)
        # to unwrap persistent client wrappers. Use spec=[] to prevent
        # MagicMock from auto-creating a .client attribute.
        mock_client = MagicMock(spec=[])
        mock_client.invoke_model_with_response_stream = MagicMock(return_value={
            "body": iter(mock_chunks)
        })

        with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client",
                   return_value=MagicMock(client=mock_client)):
            provider = OpenAIBedrockProvider(
                model_id="deepseek.v3-v1:0", model_config=cfg,
                aws_profile="ziya", region="us-west-2",
            )
            # Replace the bedrock client with our mock (no .client attr)
            provider.bedrock = mock_client

        from app.providers.base import ProviderConfig
        config = ProviderConfig(max_output_tokens=4096, temperature=0.7)

        messages = [{"role": "user", "content": "Test newlines"}]
        collected_text = ""
        events = []

        async for event in provider.stream_response(messages, None, [], config):
            events.append(event)
            if isinstance(event, TextDelta):
                collected_text += event.content

        assert collected_text == self.MARKDOWN_WITH_NEWLINES, (
            f"Newlines lost! Expected {self.MARKDOWN_WITH_NEWLINES.count(chr(10))} "
            f"newlines, got {collected_text.count(chr(10))}.\n"
            f"Got: {repr(collected_text[:200])}"
        )

        # Verify we got proper stream end
        end_events = [e for e in events if isinstance(e, StreamEnd)]
        assert len(end_events) == 1
        assert end_events[0].stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_v3_2_preserves_newlines_via_openai_provider(self):
        """DeepSeek V3.2 through OpenAIBedrockProvider should preserve all newlines."""
        from app.providers.openai_bedrock import OpenAIBedrockProvider
        from app.providers.base import TextDelta, StreamEnd

        cfg = MODEL_CONFIGS["bedrock"]["deepseek-v3.2"]
        mock_chunks = _make_openai_stream_chunks(self.MARKDOWN_WITH_NEWLINES)

        mock_client = MagicMock(spec=[])
        mock_client.invoke_model_with_response_stream = MagicMock(return_value={
            "body": iter(mock_chunks)
        })

        with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client",
                   return_value=MagicMock(client=mock_client)):
            provider = OpenAIBedrockProvider(
                model_id="deepseek.v3.2", model_config=cfg,
                aws_profile="ziya", region="us-west-2",
            )
            provider.bedrock = mock_client

        from app.providers.base import ProviderConfig
        config = ProviderConfig(max_output_tokens=4096, temperature=0.7)

        messages = [{"role": "user", "content": "Test newlines"}]
        collected_text = ""

        async for event in provider.stream_response(messages, None, [], config):
            if isinstance(event, TextDelta):
                collected_text += event.content

        assert collected_text == self.MARKDOWN_WITH_NEWLINES

    @pytest.mark.asyncio
    async def test_r1_via_converse_api(self):
        """DeepSeek R1 through NovaBedrockProvider — verify stream works."""
        from app.providers.nova_bedrock import NovaBedrockProvider
        from app.providers.base import TextDelta, StreamEnd

        cfg = MODEL_CONFIGS["bedrock"]["deepseek-r1"]
        mock_chunks = _make_converse_stream_chunks(self.MARKDOWN_WITH_NEWLINES)

        mock_client = MagicMock()
        mock_client.converse_stream.return_value = {
            "stream": iter(mock_chunks)
        }

        with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client",
                   return_value=mock_client):
            provider = NovaBedrockProvider(
                model_id="us.deepseek.r1-v1:0", model_config=cfg,
                aws_profile="ziya", region="us-west-2",
            )
            provider.bedrock = mock_client

        from app.providers.base import ProviderConfig
        config = ProviderConfig(max_output_tokens=4096, temperature=0.7)

        messages = [{"role": "user", "content": "Test"}]
        collected_text = ""
        events = []

        async for event in provider.stream_response(messages, None, [], config):
            events.append(event)
            if isinstance(event, TextDelta):
                collected_text += event.content

        # The Converse API should also preserve newlines when text arrives
        # as a single contentBlockDelta. The real question is whether the
        # Converse API splits/mangles at the AWS service layer.
        assert self.MARKDOWN_WITH_NEWLINES.count("\n") == collected_text.count("\n"), (
            f"Converse API path lost newlines: expected "
            f"{self.MARKDOWN_WITH_NEWLINES.count(chr(10))}, "
            f"got {collected_text.count(chr(10))}"
        )


# ---------------------------------------------------------------------------
# R1-specific: reasoning/thinking content
# ---------------------------------------------------------------------------

class TestDeepSeekR1Reasoning:
    """DeepSeek R1 produces reasoning content that should yield ThinkingDelta."""

    @pytest.mark.asyncio
    async def test_r1_reasoning_via_openai_format(self):
        """If R1 were routed via OpenAIBedrockProvider, reasoning field
        should produce ThinkingDelta events."""
        from app.providers.openai_bedrock import OpenAIBedrockProvider
        from app.providers.base import TextDelta, ThinkingDelta, StreamEnd

        # Build chunks with reasoning field (DeepSeek R1 format)
        chunks = []
        # Reasoning chunk
        chunks.append({"chunk": {"bytes": json.dumps({
            "choices": [{"delta": {"reasoning": "Let me think about this..."}, "finish_reason": None}]
        }).encode()}})
        # Content chunk
        chunks.append({"chunk": {"bytes": json.dumps({
            "choices": [{"delta": {"content": "The answer is 42.\n"}, "finish_reason": None}]
        }).encode()}})
        # End
        chunks.append({"chunk": {"bytes": json.dumps({
            "choices": [{"delta": {}, "finish_reason": "stop"}]
        }).encode()}})

        mock_client = MagicMock(spec=[])
        mock_client.invoke_model_with_response_stream = MagicMock(return_value={
            "body": iter(chunks)
        })

        # Use a config that has supports_thinking=True
        cfg = {**MODEL_CONFIGS["bedrock"]["deepseek-r1"],
               "wrapper_class": "OpenAIBedrock"}  # hypothetical

        with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client",
                   return_value=MagicMock(client=mock_client)):
            provider = OpenAIBedrockProvider(
                model_id="us.deepseek.r1-v1:0", model_config=cfg,
                aws_profile="ziya", region="us-west-2",
            )
            provider.bedrock = mock_client

        from app.providers.base import ProviderConfig
        config = ProviderConfig(max_output_tokens=4096, temperature=0.7)

        events = []
        async for event in provider.stream_response(
            [{"role": "user", "content": "Think about this"}], None, [], config
        ):
            events.append(event)

        thinking_events = [e for e in events if isinstance(e, ThinkingDelta)]
        text_events = [e for e in events if isinstance(e, TextDelta)]

        assert len(thinking_events) == 1
        assert "think about this" in thinking_events[0].content.lower()
        assert len(text_events) == 1
        assert "42" in text_events[0].content


# ---------------------------------------------------------------------------
# Tool suppression — R1 should not get toolConfig
# ---------------------------------------------------------------------------

class TestDeepSeekToolSuppression:
    """R1 has native_function_calling=false, so tools should be suppressed."""

    def test_r1_nova_provider_suppresses_tools(self):
        """NovaBedrockProvider should respect native_function_calling=false."""
        from app.providers.nova_bedrock import NovaBedrockProvider
        from app.providers.base import ProviderConfig

        cfg = MODEL_CONFIGS["bedrock"]["deepseek-r1"]

        with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client",
                   return_value=MagicMock()):
            provider = NovaBedrockProvider(
                model_id="us.deepseek.r1-v1:0", model_config=cfg,
                aws_profile="ziya", region="us-west-2",
            )

        config = ProviderConfig(max_output_tokens=4096)
        messages = [{"role": "user", "content": "test"}]

        # Provide tools — they should be excluded from the request
        tools = [{"name": "test_tool", "description": "test",
                  "input_schema": {"type": "object", "properties": {}}}]

        params = provider._build_converse_params(messages, None, tools, config)

        assert "toolConfig" not in params, (
            "DeepSeek R1 should NOT have toolConfig since "
            "native_function_calling=false"
        )

    def test_v3_openai_provider_does_not_send_tools(self):
        """OpenAIBedrockProvider doesn't support native tools at all."""
        from app.providers.openai_bedrock import OpenAIBedrockProvider
        from app.providers.base import ProviderConfig

        cfg = MODEL_CONFIGS["bedrock"]["deepseek-v3"]

        with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client",
                   return_value=MagicMock(client=MagicMock())):
            provider = OpenAIBedrockProvider(
                model_id="deepseek.v3-v1:0", model_config=cfg,
                aws_profile="ziya", region="us-west-2",
            )

        config = ProviderConfig(max_output_tokens=4096)
        messages = [{"role": "user", "content": "test"}]

        body = provider._build_request_body(messages, None, [], config)

        # OpenAI provider doesn't include tools in the body
        assert "tools" not in body
        assert "tool_choice" not in body


# ---------------------------------------------------------------------------
# Region handling
# ---------------------------------------------------------------------------
# Full-pipeline newline integrity tests
# ---------------------------------------------------------------------------

class TestOpenAIFormatNewlineIntegrity:
    """Integration-style tests using mock Bedrock responses that mirror
    the actual wire format returned by DeepSeek v3/v3.2.

    These verify the full provider pipeline preserves newlines from raw
    API chunks through to the final accumulated text.
    """

    def _make_openai_stream_chunks(self, text_with_newlines):
        """Build mock invoke_model_with_response_stream chunks from text."""
        import json
        # Split on newlines to simulate realistic chunking
        lines = text_with_newlines.split('\n')
        chunks = []
        for i, line in enumerate(lines):
            content = line + ('\n' if i < len(lines) - 1 else '')
            chunk_data = {
                "choices": [{"delta": {"content": content}, "index": 0}]
            }
            chunks.append({"chunk": {"bytes": json.dumps(chunk_data).encode()}})
        # Final chunk with finish_reason
        chunks.append({"chunk": {"bytes": json.dumps({
            "choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]
        }).encode()}})
        return chunks

    @pytest.mark.asyncio
    async def test_full_pipeline_preserves_markdown_newlines(self):
        """Simulate a DeepSeek response with markdown and verify newlines survive."""
        from app.providers.openai_bedrock import OpenAIBedrockProvider
        from app.providers.base import TextDelta, StreamEnd

        markdown_response = (
            "# Header\n"
            "\n"
            "Paragraph one.\n"
            "\n"
            "## Subheader\n"
            "\n"
            "- Item 1\n"
            "- Item 2\n"
            "\n"
            "```python\n"
            "def hello():\n"
            "    print('world')\n"
            "```\n"
            "\n"
            "Done."
        )
        expected_newlines = markdown_response.count('\n')

        mock_chunks = self._make_openai_stream_chunks(markdown_response)

        provider = OpenAIBedrockProvider.__new__(OpenAIBedrockProvider)
        provider.model_id = "deepseek.v3.2"
        provider.model_config = {"wrapper_class": "OpenAIBedrock", "max_output_tokens": 4096}

        mock_response = {"body": mock_chunks}

        accumulated = ""
        async for event in provider._parse_stream(mock_response):
            if isinstance(event, TextDelta):
                accumulated += event.content
            elif isinstance(event, StreamEnd):
                break

        actual_newlines = accumulated.count('\n')
        assert actual_newlines == expected_newlines, (
            f"Newlines lost! Expected {expected_newlines}, got {actual_newlines}.\n"
            f"Expected repr: {repr(markdown_response[:200])}\n"
            f"Got repr:      {repr(accumulated[:200])}")

    def test_sanitize_text_preserves_newlines(self):
        """Verify sanitize_text doesn't strip newlines from model output."""
        from app.mcp.response_validator import sanitize_text

        text = "# Header\n\nLine one.\nLine two.\n\n```python\ndef f():\n    pass\n```\n"
        result = sanitize_text(text)
        assert result.count('\n') == text.count('\n'), (
            f"sanitize_text removed newlines: {repr(text)} → {repr(result)}")

    def test_streaming_optimizer_preserves_newlines(self):
        """Verify StreamingContentOptimizer doesn't eat newlines."""
        from app.utils.streaming_optimizer import StreamingContentOptimizer

        chunks = ["# Header\n", "\n", "Paragraph.\n", "\n", "```python\n",
                   "def f():\n", "    pass\n", "```\n"]
        optimizer = StreamingContentOptimizer()

        output = ""
        for chunk in chunks:
            for out in optimizer.add_content(chunk):
                output += out
        remaining = optimizer.flush_remaining()
        if remaining:
            output += remaining

        expected_newlines = sum(c.count('\n') for c in chunks)
        assert output.count('\n') == expected_newlines, (
            f"Optimizer lost newlines: expected {expected_newlines}, "
            f"got {output.count(chr(10))}")


# ---------------------------------------------------------------------------

class TestDeepSeekRegionHandling:
    """V3/V3.2 are region-locked; R1 uses cross-region inference profile."""

    def test_v3_provider_uses_config_region(self):
        """OpenAIBedrockProvider should honor the region from model config."""
        from app.providers.openai_bedrock import OpenAIBedrockProvider

        cfg = MODEL_CONFIGS["bedrock"]["deepseek-v3"]

        with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client",
                   return_value=MagicMock(client=MagicMock())) as mock_get:
            provider = OpenAIBedrockProvider(
                model_id="deepseek.v3-v1:0", model_config=cfg,
                aws_profile="ziya", region="us-east-1",  # caller says east
            )

        # The provider should use us-west-2 from model config, not us-east-1
        call_args = mock_get.call_args
        assert call_args.kwargs.get("region") == "us-west-2" or \
               call_args[1].get("region") == "us-west-2", (
            f"Expected region us-west-2 from model config, got: {call_args}"
        )

    def test_r1_uses_cross_region_model_id(self):
        """R1 uses 'us.' prefix for cross-region inference."""
        cfg = MODEL_CONFIGS["bedrock"]["deepseek-r1"]
        model_id = cfg["model_id"]["us"]
        assert model_id.startswith("us."), (
            f"R1 should use cross-region prefix 'us.', got: {model_id}"
        )

    def test_v3_uses_direct_model_id(self):
        """V3 uses direct model ID (no cross-region prefix)."""
        cfg = MODEL_CONFIGS["bedrock"]["deepseek-v3"]
        model_id = cfg["model_id"]["us"]
        assert not model_id.startswith("us."), (
            f"V3 should use direct model ID, got: {model_id}"
        )
