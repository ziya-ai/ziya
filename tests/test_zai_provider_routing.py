"""
Tests for z.ai (Zhipu / GLM) endpoint support.

z.ai exposes an OpenAI-compatible Chat Completions API, so Ziya routes the
``zai`` endpoint through OpenAIDirectProvider (the same provider used for
OpenAI direct and OpenRouter), pointed at z.ai's base URL.

GLM-5.2 is z.ai's flagship Coding-Plan model (1M context, 131K max output).
Coding-Plan keys are bound to the api/coding/paas/v4 base URL, set via
ZAI_BASE_URL; the code default stays pay-as-you-go (api/paas/v4).

Run:
    pytest tests/test_zai_provider_routing.py -v
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.config.models_config import (
    MODEL_CONFIGS,
    MODEL_FAMILIES,
    ENDPOINT_DEFAULTS,
    DEFAULT_MODELS,
    MODEL_ALIASES,
    DEFAULT_SERVICE_MODELS,
    get_supported_parameters,
    validate_model_parameters,
    get_model_capabilities,
)


class TestZaiEndpointConfig:
    def test_zai_endpoint_exists(self):
        assert "zai" in MODEL_CONFIGS
        assert "zai" in ENDPOINT_DEFAULTS

    def test_zai_default_model_is_glm_52(self):
        assert DEFAULT_MODELS.get("zai") == "glm-5.2"

    def test_zai_default_model_is_defined(self):
        assert DEFAULT_MODELS["zai"] in MODEL_CONFIGS["zai"]

    def test_zai_glm_family_exists(self):
        assert "zai-glm" in MODEL_FAMILIES

    def test_zai_service_model_defined(self):
        assert DEFAULT_SERVICE_MODELS.get("zai") in MODEL_CONFIGS["zai"]

    def test_zai_aliases_resolve_to_real_models(self):
        for alias, target in MODEL_ALIASES.get("zai", {}).items():
            assert target in MODEL_CONFIGS["zai"], (
                f"alias '{alias}' -> '{target}' not in MODEL_CONFIGS['zai']"
            )

    def test_glm_alias_points_to_52(self):
        assert MODEL_ALIASES["zai"]["glm"] == "glm-5.2"


class TestZaiModelConfigs:
    EXPECTED_MODELS = ["glm-5.2", "glm-4.6"]

    def test_all_expected_models_present(self):
        for name in self.EXPECTED_MODELS:
            assert name in MODEL_CONFIGS["zai"], f"{name} missing from zai config"

    def test_all_models_use_zai_glm_family(self):
        for name in self.EXPECTED_MODELS:
            assert MODEL_CONFIGS["zai"][name].get("family") == "zai-glm"

    def test_all_models_native_function_calling(self):
        for name in self.EXPECTED_MODELS:
            assert MODEL_CONFIGS["zai"][name].get("native_function_calling") is True

    def test_glm_52_context_and_output(self):
        cfg = MODEL_CONFIGS["zai"]["glm-5.2"]
        assert cfg.get("token_limit") == 1000000
        assert cfg.get("max_output_tokens") == 131072

    def test_glm_46_context_window(self):
        assert MODEL_CONFIGS["zai"]["glm-4.6"].get("token_limit") == 200000

    def test_model_ids_are_bare_strings(self):
        for name in self.EXPECTED_MODELS:
            mid = MODEL_CONFIGS["zai"][name]["model_id"]
            assert isinstance(mid, str), f"{name} model_id should be a bare string"

    def test_zai_glm_family_reasoning_config_present(self):
        """Pin the reasoning seam wiring on the zai-glm family so a future
        config edit that drops it fails loudly. The provider surfaces
        reasoning_content generically, but the *enable* envelope lives in
        config — if these keys vanish, GLM silently stops thinking."""
        fam = MODEL_FAMILIES["zai-glm"]
        assert fam.get("supports_thinking") is True
        assert fam.get("supports_reasoning_effort") is True
        assert fam.get("reasoning_request") == {"thinking": {"type": "enabled"}}

    def test_zai_glm_supported_efforts_match_effort_ui(self):
        """The effort value set must match the canonical ThinkingConfig.effort
        vocabulary (shared with the Claude effort UI) so ZIYA_THINKING_EFFORT
        maps straight through to reasoning_effort."""
        efforts = MODEL_FAMILIES["zai-glm"].get("supported_efforts", [])
        for level in ("low", "medium", "high", "xhigh", "max"):
            assert level in efforts, f"effort level {level!r} missing"

    def test_glm_52_resolves_thinking_capability(self):
        """The family flag must propagate to the resolved model capabilities."""
        caps = get_model_capabilities("zai", "glm-5.2")
        assert caps["supports_thinking"] is True


class TestZaiParameterResolution:
    def test_supported_parameters(self):
        params = get_supported_parameters("zai", "glm-5.2")
        assert "temperature" in params
        assert "top_p" in params
        assert "max_tokens" in params

    def test_validate_accepts_valid_params(self):
        ok, err, filt = validate_model_parameters(
            "zai", "glm-5.2", {"temperature": 0.5, "max_tokens": 1000}
        )
        assert ok, f"validation failed: {err}"
        assert filt == {"temperature": 0.5, "max_tokens": 1000}

    def test_validate_accepts_large_output(self):
        ok, err, _ = validate_model_parameters(
            "zai", "glm-5.2", {"max_tokens": 131072}
        )
        assert ok, f"validation rejected valid 131K max_tokens: {err}"

    def test_capabilities_reflect_config(self):
        caps = get_model_capabilities("zai", "glm-5.2")
        assert caps["native_function_calling"] is True


class TestZaiFactoryRouting:
    def test_zai_routes_to_openai_direct_provider(self):
        from app.providers.factory import create_provider
        with patch.dict(os.environ, {"ZAI_API_KEY": "test-key"}, clear=False):
            provider = create_provider(
                endpoint="zai", model_id="glm-5.2",
                model_config=MODEL_CONFIGS["zai"]["glm-5.2"],
            )
        assert provider.__class__.__name__ == "OpenAIDirectProvider"
        assert provider.provider_name == "openai"

    def test_zai_default_base_url(self):
        from app.providers.factory import create_provider
        with patch.dict(os.environ, {"ZAI_API_KEY": "test-key"}, clear=False):
            os.environ.pop("ZAI_BASE_URL", None)
            provider = create_provider(
                endpoint="zai", model_id="glm-5.2",
                model_config=MODEL_CONFIGS["zai"]["glm-5.2"],
            )
        assert "z.ai" in str(provider.client.base_url)
        assert "paas/v4" in str(provider.client.base_url)

    def test_zai_coding_plan_base_url_override(self):
        from app.providers.factory import create_provider
        coding_url = "https://api.z.ai/api/coding/paas/v4"
        with patch.dict(os.environ, {"ZAI_API_KEY": "k", "ZAI_BASE_URL": coding_url},
                        clear=False):
            provider = create_provider(
                endpoint="zai", model_id="glm-5.2",
                model_config=MODEL_CONFIGS["zai"]["glm-5.2"],
            )
        assert "coding" in str(provider.client.base_url)

    def test_zai_api_key_from_env(self):
        from app.providers.factory import create_provider
        with patch.dict(os.environ, {"ZAI_API_KEY": "secret-zai-key"}, clear=False):
            provider = create_provider(
                endpoint="zai", model_id="glm-5.2",
                model_config=MODEL_CONFIGS["zai"]["glm-5.2"],
            )
        assert provider.client.api_key == "secret-zai-key"

    def test_zhipuai_key_fallback(self):
        from app.providers.factory import create_provider
        with patch.dict(os.environ, {"ZHIPUAI_API_KEY": "zhipu-key"}, clear=False):
            os.environ.pop("ZAI_API_KEY", None)
            provider = create_provider(
                endpoint="zai", model_id="glm-5.2",
                model_config=MODEL_CONFIGS["zai"]["glm-5.2"],
            )
        assert provider.client.api_key == "zhipu-key"


class TestZaiServiceModelRouting:
    def test_zai_routes_through_openai_compatible(self):
        import app.services.model_resolver as mr
        import inspect
        src = inspect.getsource(mr.call_service_model)
        assert '"zai"' in src or "'zai'" in src, (
            "call_service_model should route 'zai' to the OpenAI-compatible path"
        )

    def test_resolve_service_model_for_zai(self):
        from app.services.model_resolver import resolve_service_model
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": "zai"}, clear=False):
            os.environ.pop("ZIYA_DEFAULT_MODEL", None)
            cfg = resolve_service_model("default")
        assert cfg["endpoint"] == "zai"
        assert cfg["model_id"] == "glm-4.6"


# ── Reasoning / thinking-delta parsing ──────────────────────────
#
# GLM-5.2 streams its chain-of-thought under a `reasoning_content`
# delta attribute (OpenRouter uses `reasoning`).  OpenAIDirectProvider
# emits these as ThinkingDelta events so the orchestrator wraps them in
# a <thinking-data> collapsible block.  These tests drive _do_stream
# directly with fake stream chunks — no network.

from types import SimpleNamespace

from app.providers.base import TextDelta, ThinkingDelta, StreamEnd, UsageEvent
from app.providers.openai_direct import OpenAIDirectProvider


def _delta(**attrs):
    """A fake OpenAI streaming delta.  content/tool_calls default to None
    (the provider accesses them directly); reasoning fields are only
    present when explicitly passed, so getattr returns None otherwise."""
    base = {"content": None, "tool_calls": None}
    base.update(attrs)
    return SimpleNamespace(**base)


def _chunk(delta=None, finish_reason=None, usage=None):
    """A fake stream chunk.  A usage-only final chunk has no choices."""
    if usage is not None:
        return SimpleNamespace(choices=[], usage=usage)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


class _FakeStream:
    """Async-iterable over a fixed list of fake chunks."""
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def _gen():
            for c in self._chunks:
                yield c
        return _gen()


def _provider_with_chunks(chunks):
    """Build an OpenAIDirectProvider whose client returns `chunks`."""
    with patch.dict(os.environ, {"ZAI_API_KEY": "k"}, clear=False):
        from app.providers.factory import create_provider
        provider = create_provider(
            endpoint="zai", model_id="glm-5.2",
            model_config=MODEL_CONFIGS["zai"]["glm-5.2"],
        )

    async def _fake_create(**kwargs):
        return _FakeStream(chunks)

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=_fake_create)
        )
    )
    return provider


async def _collect(provider):
    out = []
    async for ev in provider._do_stream({"model": "glm-5.2", "messages": []}):
        out.append(ev)
    return out


class TestZaiReasoningParsing:
    @pytest.mark.asyncio
    async def test_reasoning_content_emits_thinking_delta(self):
        """GLM's reasoning_content delta -> ThinkingDelta, content -> TextDelta."""
        provider = _provider_with_chunks([
            _chunk(_delta(reasoning_content="thinking...")),
            _chunk(_delta(content="answer")),
            _chunk(_delta(), finish_reason="stop"),
        ])
        events = await _collect(provider)
        thinking = [e for e in events if isinstance(e, ThinkingDelta)]
        text = [e for e in events if isinstance(e, TextDelta)]
        assert len(thinking) == 1 and thinking[0].content == "thinking..."
        assert len(text) == 1 and text[0].content == "answer"
        # Thinking is emitted before the visible content for that delta order.
        assert isinstance(events[0], ThinkingDelta)

    @pytest.mark.asyncio
    async def test_reasoning_fallback_field(self):
        """OpenRouter-style reasoning field also maps to ThinkingDelta."""
        provider = _provider_with_chunks([
            _chunk(_delta(reasoning="cot")),
            _chunk(_delta(), finish_reason="stop"),
        ])
        events = await _collect(provider)
        thinking = [e for e in events if isinstance(e, ThinkingDelta)]
        assert len(thinking) == 1 and thinking[0].content == "cot"

    @pytest.mark.asyncio
    async def test_plain_openai_delta_no_thinking(self):
        """A plain OpenAI delta carries neither reasoning attribute — no-op.

        _delta() produces a namespace WITHOUT reasoning_content/reasoning,
        so getattr returns None and zero ThinkingDelta events are emitted —
        the safety guarantee for OpenAI-direct and OpenRouter sharing this
        provider."""
        provider = _provider_with_chunks([
            _chunk(_delta(content="hello")),
            _chunk(_delta(), finish_reason="stop"),
        ])
        events = await _collect(provider)
        assert not any(isinstance(e, ThinkingDelta) for e in events)
        assert any(isinstance(e, TextDelta) and e.content == "hello" for e in events)

    def test_zai_glm_family_supports_thinking(self):
        """The zai-glm family advertises thinking support."""
        assert MODEL_FAMILIES["zai-glm"].get("supports_thinking") is True
        # And it propagates to the model's resolved capabilities.
        caps = get_model_capabilities("zai", "glm-5.2")
        assert caps["supports_thinking"] is True


class TestZaiUsageParsing:
    """z.ai/OpenAI usage telemetry. The provider must capture usage no
    matter where in the stream it appears, and emit the UsageEvent BEFORE
    StreamEnd — otherwise the orchestrator (which breaks on StreamEnd)
    never records it, producing the 'no usage metrics captured' warning
    on every iteration."""

    @pytest.mark.asyncio
    async def test_usage_chunk_after_finish_reason_still_emitted(self):
        """OpenAI sends usage on a trailing choiceless chunk AFTER the
        finish_reason chunk. StreamEnd must be deferred so it isn't dropped."""
        usage = SimpleNamespace(
            prompt_tokens=100, completion_tokens=40,
            prompt_tokens_details=SimpleNamespace(cached_tokens=10),
        )
        provider = _provider_with_chunks([
            _chunk(_delta(content="answer")),
            _chunk(_delta(), finish_reason="stop"),
            _chunk(usage=usage),  # trailing choiceless usage chunk
        ])
        events = await _collect(provider)
        usages = [e for e in events if isinstance(e, UsageEvent)]
        assert len(usages) == 1
        assert usages[0].input_tokens == 100
        assert usages[0].output_tokens == 40
        assert usages[0].cache_read_tokens == 10
        types = [type(e).__name__ for e in events]
        assert types.index("UsageEvent") < types.index("StreamEnd")

    @pytest.mark.asyncio
    async def test_usage_attached_to_chunk_with_choices(self):
        """Some OpenAI-compatible endpoints attach usage to a chunk that
        also carries choices/finish_reason — it must still be captured."""
        usage = SimpleNamespace(
            prompt_tokens=50, completion_tokens=5, prompt_tokens_details=None,
        )
        choice = SimpleNamespace(delta=_delta(content="hi"), finish_reason="stop")
        combined = SimpleNamespace(choices=[choice], usage=usage)
        provider = _provider_with_chunks([combined])
        events = await _collect(provider)
        usages = [e for e in events if isinstance(e, UsageEvent)]
        assert len(usages) == 1
        assert usages[0].input_tokens == 50
        assert usages[0].cache_read_tokens == 0

    @pytest.mark.asyncio
    async def test_no_usage_still_emits_stream_end(self):
        """An endpoint that never reports usage must still terminate cleanly
        with a StreamEnd and no spurious UsageEvent."""
        provider = _provider_with_chunks([
            _chunk(_delta(content="x")),
            _chunk(_delta(), finish_reason="stop"),
        ])
        events = await _collect(provider)
        assert any(isinstance(e, StreamEnd) for e in events)
        assert not any(isinstance(e, UsageEvent) for e in events)