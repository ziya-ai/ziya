"""
Tests for the OpenAI-compatible reasoning seam.

Two layers under test, both designed to generalize across the whole
OpenAI-compatible family (z.ai GLM, DeepSeek R1, vLLM, Ollama, etc.)
rather than hard-coding any single vendor:

  Layer 1 — OUTPUT (universal): OpenAIDirectProvider._do_stream surfaces a
    chunk's non-standard `delta.reasoning_content` as a ThinkingDelta.
    `reasoning_content` is the cross-vendor convention for streamed
    chain-of-thought; the final answer stays on `delta.content`.

  Layer 2 — INPUT (config-driven, no per-vendor code): _build_request
    renders the canonical ThinkingConfig into a vendor-specific request
    envelope declared in model_config (`reasoning_request`) plus an
    optional `reasoning_effort` key, merged via the SDK's `extra_body`
    escape hatch. A raw `ProviderConfig.extra_body` passthrough covers
    anything else.

  Plus: the _build_provider_config latent-bug fix in StreamingToolExecutor
    — the non-adaptive `supports_thinking` branch must actually build a
    ThinkingConfig (it previously read env vars but never assigned one).
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.providers.base import (
    ProviderConfig,
    ThinkingConfig,
    TextDelta,
    ThinkingDelta,
    ToolUseStart,
    StreamEnd,
    UsageEvent,
)
from app.providers.openai_direct import OpenAIDirectProvider


# ── Fixtures / helpers ──────────────────────────────────────────

_GLM_CONFIG = {
    "supports_thinking": True,
    "supports_reasoning_effort": True,
    "reasoning_request": {"thinking": {"type": "enabled"}},
    "thinking_effort_default": "high",
}


# Sentinel marking "reasoning_content was not passed" so _delta can OMIT
# the attribute entirely (a default-arg walrus is a SyntaxError).
_UNSET = object()


# Sentinel marking "reasoning_content was not passed" so _delta can OMIT the
# attribute entirely (a default-arg walrus is a SyntaxError, so it lives here).
_UNSET = object()


def _make_provider(model_config=None):
    return OpenAIDirectProvider(
        model_id="glm-5.2",
        model_config=model_config if model_config is not None else dict(_GLM_CONFIG),
        api_key="test-key",
    )


def _delta(content=None, reasoning_content=_UNSET, tool_calls=None):
    """Build a fake ChoiceDelta-like object.

    When reasoning_content is left at the sentinel, the attribute is
    OMITTED entirely (exercises the getattr-default safety on endpoints
    that never emit the field). Pass None or a string to set it.
    """
    ns = SimpleNamespace(content=content, tool_calls=tool_calls)
    if reasoning_content is not _UNSET:
        ns.reasoning_content = reasoning_content
    return ns


def _chunk(delta=None, finish_reason=None, usage=None):
    if delta is None and finish_reason is None:
        # usage-only chunk: empty choices
        return SimpleNamespace(choices=[], usage=usage)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _mock_stream(provider, chunks):
    """Wire provider.client.chat.completions.create to yield chunks."""
    async def _agen():
        for c in chunks:
            yield c

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_agen())
            )
        )
    )


async def _collect(provider):
    return [e async for e in provider._do_stream({})]


# ── Layer 1: reasoning_content → ThinkingDelta ──────────────────

class TestReasoningOutput:
    @pytest.mark.asyncio
    async def test_reasoning_content_becomes_thinking_delta(self):
        provider = _make_provider()
        _mock_stream(provider, [
            _chunk(_delta(reasoning_content="let me think...")),
            _chunk(_delta(content="the answer")),
            _chunk(finish_reason="stop"),
        ])
        events = await _collect(provider)
        thinking = [e for e in events if isinstance(e, ThinkingDelta)]
        text = [e for e in events if isinstance(e, TextDelta)]
        assert len(thinking) == 1
        assert thinking[0].content == "let me think..."
        assert len(text) == 1
        assert text[0].content == "the answer"

    @pytest.mark.asyncio
    async def test_thinking_emitted_before_text_in_same_chunk(self):
        # A chunk carrying BOTH reasoning and content must emit the
        # ThinkingDelta first so the UI opens the thinking block before
        # the answer text arrives.
        provider = _make_provider()
        _mock_stream(provider, [
            _chunk(_delta(reasoning_content="reasoning", content="answer")),
            _chunk(finish_reason="stop"),
        ])
        events = await _collect(provider)
        types = [type(e).__name__ for e in events if isinstance(e, (ThinkingDelta, TextDelta))]
        assert types == ["ThinkingDelta", "TextDelta"]

    @pytest.mark.asyncio
    async def test_multiple_reasoning_chunks_each_emit(self):
        provider = _make_provider()
        _mock_stream(provider, [
            _chunk(_delta(reasoning_content="step 1 ")),
            _chunk(_delta(reasoning_content="step 2")),
            _chunk(_delta(content="done")),
            _chunk(finish_reason="stop"),
        ])
        events = await _collect(provider)
        thinking = [e.content for e in events if isinstance(e, ThinkingDelta)]
        assert thinking == ["step 1 ", "step 2"]

    @pytest.mark.asyncio
    async def test_absent_reasoning_attr_is_safe(self):
        # Plain OpenAI / endpoints that never emit reasoning_content: the
        # attribute is entirely absent. getattr-default must not raise and
        # must emit no ThinkingDelta.
        provider = _make_provider(model_config={})
        _mock_stream(provider, [
            _chunk(_delta(content="just text")),   # no reasoning_content attr
            _chunk(finish_reason="stop"),
        ])
        events = await _collect(provider)
        assert not any(isinstance(e, ThinkingDelta) for e in events)
        assert any(isinstance(e, TextDelta) and e.content == "just text" for e in events)

    @pytest.mark.asyncio
    async def test_empty_reasoning_content_emits_nothing(self):
        # Falsy reasoning_content (None or "") must not emit a ThinkingDelta.
        provider = _make_provider()
        _mock_stream(provider, [
            _chunk(_delta(reasoning_content=None, content="x")),
            _chunk(_delta(reasoning_content="", content="y")),
            _chunk(finish_reason="stop"),
        ])
        events = await _collect(provider)
        assert not any(isinstance(e, ThinkingDelta) for e in events)

    @pytest.mark.asyncio
    async def test_usage_chunk_still_parsed_with_reasoning_present(self):
        provider = _make_provider()
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5,
                                prompt_tokens_details=SimpleNamespace(cached_tokens=2))
        _mock_stream(provider, [
            _chunk(_delta(reasoning_content="r")),
            _chunk(_delta(content="a")),
            _chunk(finish_reason="stop"),
            _chunk(usage=usage),
        ])
        events = await _collect(provider)
        usages = [e for e in events if isinstance(e, UsageEvent)]
        assert len(usages) == 1
        assert usages[0].input_tokens == 10
        assert usages[0].cache_read_tokens == 2


# ── Layer 2: extra_body request seam ────────────────────────────

class TestReasoningRequestSeam:
    def _req(self, provider, config):
        return provider._build_request([], None, [], config)

    def test_thinking_enabled_emits_vendor_envelope_and_effort(self):
        provider = _make_provider()
        cfg = ProviderConfig(
            thinking=ThinkingConfig(enabled=True, mode="enabled", effort="high"),
        )
        kwargs = self._req(provider, cfg)
        assert kwargs["extra_body"] == {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        }

    def test_effort_value_threaded_from_config(self):
        provider = _make_provider()
        cfg = ProviderConfig(
            thinking=ThinkingConfig(enabled=True, mode="enabled", effort="xhigh"),
        )
        assert self._req(provider, cfg)["extra_body"]["reasoning_effort"] == "xhigh"

    def test_no_thinking_means_no_extra_body(self):
        provider = _make_provider()
        cfg = ProviderConfig(thinking=None)
        assert "extra_body" not in self._req(provider, cfg)

    def test_thinking_present_but_disabled_means_no_extra_body(self):
        provider = _make_provider()
        cfg = ProviderConfig(thinking=ThinkingConfig(enabled=False))
        assert "extra_body" not in self._req(provider, cfg)

    def test_envelope_without_effort_support(self):
        # A vendor that has a reasoning envelope but no reasoning_effort key.
        provider = _make_provider(model_config={
            "supports_thinking": True,
            "reasoning_request": {"thinking": {"type": "enabled"}},
            # no supports_reasoning_effort
        })
        cfg = ProviderConfig(thinking=ThinkingConfig(enabled=True, effort="high"))
        assert self._req(provider, cfg)["extra_body"] == {"thinking": {"type": "enabled"}}

    def test_no_envelope_but_effort_supported(self):
        # OpenAI o-series shape: reasoning_effort only, no envelope.
        provider = _make_provider(model_config={
            "supports_thinking": True,
            "supports_reasoning_effort": True,
        })
        cfg = ProviderConfig(thinking=ThinkingConfig(enabled=True, effort="medium"))
        assert self._req(provider, cfg)["extra_body"] == {"reasoning_effort": "medium"}

    def test_raw_extra_body_passthrough_without_thinking(self):
        provider = _make_provider(model_config={})
        cfg = ProviderConfig(thinking=None, extra_body={"custom_vendor_param": 42})
        assert self._req(provider, cfg)["extra_body"] == {"custom_vendor_param": 42}

    def test_raw_extra_body_merges_with_reasoning(self):
        provider = _make_provider()
        cfg = ProviderConfig(
            thinking=ThinkingConfig(enabled=True, effort="low"),
            extra_body={"tool_stream": True},
        )
        eb = self._req(provider, cfg)["extra_body"]
        assert eb["thinking"] == {"type": "enabled"}
        assert eb["reasoning_effort"] == "low"
        assert eb["tool_stream"] is True

    def test_raw_extra_body_overrides_reasoning_keys(self):
        # Explicit per-request extra_body wins over the config-derived value.
        provider = _make_provider()
        cfg = ProviderConfig(
            thinking=ThinkingConfig(enabled=True, effort="low"),
            extra_body={"reasoning_effort": "max"},
        )
        assert self._req(provider, cfg)["extra_body"]["reasoning_effort"] == "max"

    def test_provider_config_extra_body_defaults_empty(self):
        assert ProviderConfig().extra_body == {}


# ── _build_provider_config latent-bug fix ───────────────────────

class TestBuildProviderConfigThinking:
    """The non-adaptive supports_thinking branch must build a ThinkingConfig.

    Bypasses __init__ (which constructs a real provider + boto3 client) via
    object.__new__, since _build_provider_config only reads self.model_config
    and the optional override attrs.
    """

    def _executor(self, model_config):
        from app.streaming_tool_executor import StreamingToolExecutor
        ex = object.__new__(StreamingToolExecutor)
        ex.model_config = model_config
        return ex

    def test_non_adaptive_thinking_builds_config_when_mode_on(self):
        ex = self._executor({"supports_thinking": True, "thinking_effort_default": "high"})
        with patch.dict(os.environ, {"ZIYA_THINKING_MODE": "true"}, clear=False):
            os.environ.pop("ZIYA_THINKING_EFFORT", None)
            cfg = ex._build_provider_config(0)
        assert cfg.thinking is not None
        assert cfg.thinking.enabled is True
        assert cfg.thinking.mode == "enabled"
        assert cfg.thinking.effort == "high"  # from thinking_effort_default

    def test_non_adaptive_thinking_none_when_mode_off(self):
        ex = self._executor({"supports_thinking": True})
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIYA_THINKING_MODE", None)
            cfg = ex._build_provider_config(0)
        assert cfg.thinking is None

    def test_effort_env_override_respected(self):
        ex = self._executor({"supports_thinking": True, "thinking_effort_default": "high"})
        with patch.dict(os.environ,
                        {"ZIYA_THINKING_MODE": "true", "ZIYA_THINKING_EFFORT": "low"},
                        clear=False):
            cfg = ex._build_provider_config(0)
        assert cfg.thinking.effort == "low"

    def test_adaptive_branch_still_builds_adaptive_config(self):
        ex = self._executor({"supports_adaptive_thinking": True,
                              "thinking_effort_default": "medium"})
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIYA_THINKING_EFFORT", None)
            cfg = ex._build_provider_config(0)
        assert cfg.thinking is not None
        assert cfg.thinking.mode == "adaptive"
        assert cfg.thinking.effort == "medium"

    def test_no_thinking_support_yields_none(self):
        ex = self._executor({})
        cfg = ex._build_provider_config(0)
        assert cfg.thinking is None
