"""
Baseline token measurement lives on the provider, not the orchestrator.

Regression: StreamingToolExecutor used to hand-build an Anthropic Messages
body and fire it at a raw boto3 client whenever a Bedrock client existed,
gated only on ``self.bedrock is None``. Every non-Claude Bedrock family
(Kimi/MiniMax/GLM via the OpenAI-compatible invoke body, GPT-6/Nova via
Converse) took that path and the backend rejected the Anthropic-shaped
tools array::

    ValidationException ... Invalid 'tools': missing field `type`

and because botocore's ClientError was outside the executor's except tuple,
the user's whole stream died.

The fix moves the probe behind ``LLMProvider.measure_baseline_tokens``:
  * default implementation returns None (can't measure -> executor estimates)
  * BedrockProvider (Claude) owns the invoke_model probe and its error handling
  * AnthropicDirectProvider reuses its count_tokens pre-flight
  * the executor no longer knows any wire format at all

Pre-fix these tests fail with AttributeError (method missing) and the
source-layering guard fails on the ``anthropic_version`` literal.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.providers.base import LLMProvider


SYSTEM = "You are a helpful assistant. " * 200  # ~6k chars
TOOLS: List[Dict[str, Any]] = [
    {"name": "read_file", "description": "Read a file",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
    {"name": "list_dir", "description": "List a directory",
     "input_schema": {"type": "object", "properties": {}}},
]


# ---------------------------------------------------------------------------
# Base contract
# ---------------------------------------------------------------------------

class _StubProvider(LLMProvider):
    async def stream_response(self, *a, **k):  # pragma: no cover - never awaited
        yield None

    def build_assistant_message(self, *a, **k):
        return {}

    def build_tool_result_message(self, *a, **k):
        return {}


def test_base_default_cannot_measure():
    assert _StubProvider().measure_baseline_tokens(SYSTEM, TOOLS) is None


# ---------------------------------------------------------------------------
# BedrockProvider (Claude): the probe moved here intact
# ---------------------------------------------------------------------------

def _bedrock_provider(raw_client: MagicMock):
    from app.providers.bedrock import BedrockProvider

    wrapped = MagicMock()
    wrapped.unwrap.return_value = raw_client
    with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client",
               return_value=wrapped):
        return BedrockProvider(
            model_id="us.anthropic.claude-sonnet-4-5",
            model_config={"family": "claude", "region": "us-west-2"},
            aws_profile="test", region="us-west-2",
        )


def _invoke_response(usage: Dict[str, int]) -> Dict[str, Any]:
    body = MagicMock()
    body.read.return_value = json.dumps({"usage": usage, "stop_reason": "end_turn",
                                         "content": []}).encode()
    return {"body": body}


def test_bedrock_probe_sums_fresh_cached_and_created_tokens():
    raw = MagicMock()
    raw.invoke_model.return_value = _invoke_response(
        {"input_tokens": 12, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 2400})
    p = _bedrock_provider(raw)

    assert p.measure_baseline_tokens(SYSTEM, TOOLS) == 2412

    # The probe goes to the UNWRAPPED client and mirrors a real request shape.
    raw.invoke_model.assert_called_once()
    kwargs = raw.invoke_model.call_args.kwargs
    assert kwargs["modelId"] == "us.anthropic.claude-sonnet-4-5"
    sent = json.loads(kwargs["body"])
    assert sent["anthropic_version"] == "bedrock-2023-05-31"
    assert sent["system"][0]["text"] == SYSTEM
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["tools"] == TOOLS


def test_bedrock_probe_swallows_client_error():
    raw = MagicMock()
    raw.invoke_model.side_effect = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "missing field `type`"}},
        "InvokeModel")
    p = _bedrock_provider(raw)
    assert p.measure_baseline_tokens(SYSTEM, TOOLS) is None


def test_bedrock_probe_discards_implausible_count():
    raw = MagicMock()
    raw.invoke_model.return_value = _invoke_response({"input_tokens": 3})
    p = _bedrock_provider(raw)
    assert p.measure_baseline_tokens(SYSTEM, TOOLS) is None


# ---------------------------------------------------------------------------
# AnthropicDirectProvider: delegates to the existing pre-flight
# ---------------------------------------------------------------------------

def test_anthropic_direct_delegates_to_estimate_request_tokens():
    from app.providers.anthropic_direct import AnthropicDirectProvider

    with patch("anthropic.AsyncAnthropic"):
        p = AnthropicDirectProvider(model_id="claude-sonnet-4-5",
                                    model_config={"family": "claude"}, api_key="k")
    with patch.object(p, "_estimate_request_tokens", return_value=1234) as est:
        assert p.measure_baseline_tokens(SYSTEM, TOOLS) == 1234
    sent = est.call_args.args[0]
    assert sent["model"] == "claude-sonnet-4-5"
    assert sent["system"] == SYSTEM
    assert sent["tools"] == TOOLS


# ---------------------------------------------------------------------------
# The regression: an OpenAI-wire Bedrock family must NOT receive the probe
# ---------------------------------------------------------------------------

def test_openai_bedrock_family_does_not_probe_with_anthropic_body():
    from app.providers.openai_bedrock import OpenAIBedrockProvider

    raw = MagicMock()
    with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client",
               return_value=raw):
        p = OpenAIBedrockProvider(
            model_id="moonshotai.kimi-k2.5",
            model_config={"family": "kimi", "wrapper_class": "OpenAIBedrock",
                          "max_output_tokens": 4096, "region": "us-west-2"},
            aws_profile="test", region="us-west-2",
        )
    assert p.measure_baseline_tokens(SYSTEM, TOOLS) is None
    raw.invoke_model.assert_not_called()


# ---------------------------------------------------------------------------
# Layering guard: the orchestrator knows no wire format
# ---------------------------------------------------------------------------

def test_executor_source_contains_no_provider_body_format():
    import app.streaming_tool_executor as ste

    src = inspect.getsource(ste)
    assert "anthropic_version" not in src, "executor is building an Anthropic body again"
    assert ".invoke_model(" not in src, "executor is calling a Bedrock client directly again"
    assert "messages.count_tokens" not in src, "executor duplicates the Anthropic count_tokens path"
    # The seam actually exists: the executor asks the provider.
    assert "measure_baseline_tokens" in src
