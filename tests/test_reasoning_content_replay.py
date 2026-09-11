"""
reasoning_content replay on OpenAI-compatible assistant history.

DeepSeek / DwarfStar ``ds4-server`` REQUIRE the model's streamed
chain-of-thought echoed back on assistant messages when tools are present
(ds4 CLIENTS.md: ``requiresReasoningContentOnAssistantMessages``); without it
the server re-renders the whole prompt prefix every turn instead of hitting
its KV cache. z.ai GLM streams the same ``reasoning_content`` channel and
benefits identically. OpenAIDirectProvider streamed it to the UI (as
ThinkingDelta) and then dropped it from history.

This wires a provider-local capture → replay path, gated per model by
``replay_reasoning_content`` so plain OpenAI-direct payloads are byte-identical:

  1. ``_do_stream`` accumulates ``delta.reasoning_content`` and emits ONE
     ``ThinkingBlock(block_type="reasoning_content")`` at stream end.
  2. the orchestrator collects that flat block separately from the signed
     Anthropic passback blocks and hands it back as ``reasoning_content=``.
  3. ``build_assistant_message`` writes it as a top-level string.
  4. ``_build_request`` passes that field straight through on replay.

All four hops are exercised here against fake stream chunks — no server.

Run:
    pytest tests/test_reasoning_content_replay.py -v
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.providers.base import (
    ProviderConfig, TextDelta, ThinkingDelta, ThinkingBlock, StreamEnd,
)
from app.providers.openai_direct import OpenAIDirectProvider


@pytest.fixture(autouse=True)
def _openai_key(monkeypatch):
    """The AsyncOpenAI client the provider builds in __init__ refuses to
    construct without a key, even against a local/compatible server that
    ignores it. Every test here builds a provider, so set a placeholder."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    yield


# ── fake stream plumbing (mirrors tests/test_zai_provider_routing.py) ──

def _delta(**attrs):
    base = {"content": None, "tool_calls": None}
    base.update(attrs)
    return SimpleNamespace(**base)


def _chunk(delta=None, finish_reason=None):
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def _gen():
            for c in self._chunks:
                yield c
        return _gen()


def _provider(model_config, chunks):
    with patch.dict(os.environ, {"OPENAI_API_KEY": "k"}, clear=False):
        p = OpenAIDirectProvider(model_id="m", model_config=model_config)

    async def _fake_create(**kwargs):
        return _FakeStream(chunks)

    p.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_fake_create))
    )
    return p


async def _collect(provider):
    out = []
    async for ev in provider._do_stream({"model": "m", "messages": []}):
        out.append(ev)
    return out


REASONING_CHUNKS = [
    _chunk(_delta(reasoning_content="Let me ")),
    _chunk(_delta(reasoning_content="think.")),
    _chunk(_delta(content="Answer.")),
    _chunk(_delta(), finish_reason="stop"),
]


# ── 1. capture: the carrier block is emitted only when opted in ────────

class TestReasoningCapture:
    @pytest.mark.asyncio
    async def test_carrier_block_emitted_when_flag_set(self):
        p = _provider({"replay_reasoning_content": True}, REASONING_CHUNKS)
        events = await _collect(p)
        blocks = [e for e in events if isinstance(e, ThinkingBlock)]
        assert len(blocks) == 1
        assert blocks[0].block_type == "reasoning_content"
        assert blocks[0].content == "Let me think."
        # Display path is unchanged: the same reasoning still streams as deltas.
        deltas = [e for e in events if isinstance(e, ThinkingDelta)]
        assert "".join(d.content for d in deltas) == "Let me think."
        # And the carrier lands before StreamEnd, or the orchestrator (which
        # breaks on StreamEnd) would never see it.
        types = [type(e).__name__ for e in events]
        assert types.index("ThinkingBlock") < types.index("StreamEnd")

    @pytest.mark.asyncio
    async def test_no_carrier_block_when_flag_unset(self):
        """Plain OpenAI-direct / a model that did not opt in: reasoning still
        displays but is NOT captured for replay — no behavioural change."""
        p = _provider({}, REASONING_CHUNKS)
        events = await _collect(p)
        assert not any(isinstance(e, ThinkingBlock) for e in events)
        # Visible text and thinking deltas are exactly as before.
        assert any(isinstance(e, TextDelta) and e.content == "Answer." for e in events)
        assert any(isinstance(e, ThinkingDelta) for e in events)

    @pytest.mark.asyncio
    async def test_no_carrier_block_when_no_reasoning_streamed(self):
        """A reasoning-capable model that happened to answer with no CoT this
        turn must not emit an empty carrier."""
        p = _provider({"replay_reasoning_content": True}, [
            _chunk(_delta(content="hi")),
            _chunk(_delta(), finish_reason="stop"),
        ])
        events = await _collect(p)
        assert not any(isinstance(e, ThinkingBlock) for e in events)


# ── 2. replay: build_assistant_message writes the field, gated ─────────

class TestBuildAssistantMessage:
    def test_writes_reasoning_content_when_flag_set(self):
        p = OpenAIDirectProvider(model_id="m", model_config={"replay_reasoning_content": True})
        msg = p.build_assistant_message("answer", [], reasoning_content="my cot")
        assert msg["reasoning_content"] == "my cot"
        assert msg["role"] == "assistant"
        assert msg["content"] == "answer"

    def test_omits_reasoning_content_when_flag_unset(self):
        p = OpenAIDirectProvider(model_id="m", model_config={})
        msg = p.build_assistant_message("answer", [], reasoning_content="my cot")
        assert "reasoning_content" not in msg

    def test_two_arg_call_still_works(self):
        """Every non-reasoning caller (and the pre-existing plain path) invokes
        it with just (text, tool_uses); the new params must be optional."""
        p = OpenAIDirectProvider(model_id="m", model_config={})
        msg = p.build_assistant_message("answer", [])
        assert msg["content"] == "answer"

    def test_reasoning_coexists_with_tool_calls(self):
        p = OpenAIDirectProvider(model_id="m", model_config={"replay_reasoning_content": True})
        msg = p.build_assistant_message(
            "", [{"id": "c1", "name": "read", "input": {"x": 1}}],
            reasoning_content="deciding to read",
        )
        assert msg["reasoning_content"] == "deciding to read"
        assert msg["tool_calls"][0]["id"] == "c1"


# ── 3. round-trip: a replayed message survives _build_request ──────────

class TestBuildRequestPassthrough:
    def _cfg(self, **extra):
        return ProviderConfig(max_output_tokens=256, temperature=0.3, **extra)

    def test_reasoning_content_passes_through_to_wire(self):
        """An assistant message carrying reasoning_content (as
        build_assistant_message produced it last turn) must reach the request
        payload unchanged — that is what lets the server match its prefix."""
        p = OpenAIDirectProvider(model_id="m", model_config={"replay_reasoning_content": True})
        history = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a", "reasoning_content": "prior cot",
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
        ]
        kwargs = p._build_request(history, None, [], self._cfg())
        sent = kwargs["messages"]
        assistant = [m for m in sent if m["role"] == "assistant"][0]
        assert assistant["reasoning_content"] == "prior cot"
        # The tool binding is preserved alongside it (regression guard).
        assert assistant["tool_calls"][0]["id"] == "c1"
        assert any(m["role"] == "tool" and m["tool_call_id"] == "c1" for m in sent)

    def test_absent_reasoning_content_is_not_invented(self):
        p = OpenAIDirectProvider(model_id="m", model_config={"replay_reasoning_content": True})
        history = [{"role": "assistant", "content": "a"}]
        kwargs = p._build_request(history, None, [], self._cfg())
        assert "reasoning_content" not in kwargs["messages"][0]


# ── 4. the feature gate the orchestrator keys on ──────────────────────

class TestFeatureGate:
    def test_supports_feature_reflects_config(self):
        on = OpenAIDirectProvider(model_id="m", model_config={"replay_reasoning_content": True})
        off = OpenAIDirectProvider(model_id="m", model_config={})
        assert on.supports_feature("reasoning_content_replay") is True
        assert off.supports_feature("reasoning_content_replay") is False


# ── 5. config wiring: the key is valid and GLM opts in ────────────────

class TestConfigWiring:
    def test_key_is_valid_on_models_and_families(self):
        from app.config.models_config import (
            _VALID_MODEL_CONFIG_KEYS, _VALID_FAMILY_KEYS,
        )
        assert "replay_reasoning_content" in _VALID_MODEL_CONFIG_KEYS
        assert "replay_reasoning_content" in _VALID_FAMILY_KEYS

    def test_zai_glm_family_opts_in(self):
        from app.config.models_config import MODEL_FAMILIES
        assert MODEL_FAMILIES["zai-glm"].get("replay_reasoning_content") is True

    def test_glm_model_resolves_the_capability(self):
        """The family flag must reach the model_config the FACTORY hands the
        provider — get_model_config merges family keys — since the provider
        reads self.model_config.get('replay_reasoning_content'). (This is a
        different dict from get_model_capabilities, the curated UI view, which
        deliberately exposes only a fixed capability subset.)"""
        from app.agents.models import ModelManager
        cfg = ModelManager.get_model_config("zai", "glm-5.2")
        assert cfg.get("replay_reasoning_content") is True

    def test_config_validation_has_no_local_or_zai_issue(self):
        from app.config.models_config import validate_model_configs
        issues = [i for i in validate_model_configs()
                  if "replay_reasoning_content" in i]
        assert not issues, issues


# ── 6. local discovery turns it on for a reasoning-effort server ──────

class TestLocalDiscoveryEnablesReplay:
    def test_apply_discovery_sets_flag_for_reasoning_effort_model(self):
        """A DwarfStar/DeepSeek model (supported_parameters names
        reasoning_effort) gets replay enabled from discovery alone."""
        from app.utils import local_models as lm
        found = lm.DiscoveredModel(
            runtime="openai-compatible", name="deepseek-v4-flash",
            context_length=300000, supports_tools=True, supports_vision=None,
            supports_thinking=True, supports_reasoning_effort=True,
        )
        entry = lm.apply_discovery(lm.synthesize_local_model_entry("deepseek-v4-flash"), found)
        assert entry.get("replay_reasoning_content") is True

    def test_apply_discovery_leaves_flag_off_without_reasoning(self):
        from app.utils import local_models as lm
        found = lm.DiscoveredModel(
            runtime="ollama", name="qwen2.5-coder:7b", context_length=32768,
            supports_tools=True, supports_vision=False, supports_thinking=False,
            supports_reasoning_effort=False,
        )
        entry = lm.apply_discovery(lm.synthesize_local_model_entry("qwen2.5-coder:7b"), found)
        assert "replay_reasoning_content" not in entry
