"""
Cache-behavior parity tests for BedrockMantleProvider.

Mantle inherits its request building and cache_control strategy from
AnthropicDirectProvider, but the endpoint behind the gateway is Bedrock —
which enforces the 4-cache-breakpoint limit and reports cache
effectiveness through the same usage counters the executor uses to set
throttle_state['cache_working'].  These tests pin the behaviors that must
match the direct BedrockProvider path:

  1. Exactly one conversation cache breakpoint per request.
  2. Stale cache_control markers in incoming history are stripped
     (accumulation past 4 breakpoints breaks caching on both endpoints).
  3. ZIYA_DISABLE_PROMPT_CACHE is honored (parity with BedrockProvider).
  4. System prompt caching and usage cache metrics survive inheritance.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.providers.base import ProviderConfig, StreamEnd, UsageEvent


def _bare_provider(cls, model_id="anthropic.claude-fable-5"):
    """Construct a provider without touching boto3/anthropic/network."""
    p = cls.__new__(cls)
    p.model_id = model_id
    p.model_config = {"family": "claude"}
    p.client = MagicMock()
    return p


@pytest.fixture
def mantle_provider():
    from app.providers.bedrock_mantle import BedrockMantleProvider
    return _bare_provider(BedrockMantleProvider)


@pytest.fixture
def anthropic_provider():
    from app.providers.anthropic_direct import AnthropicDirectProvider
    return _bare_provider(AnthropicDirectProvider, model_id="claude-sonnet-4")


def _history(n=6, with_markers=False):
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        block = {"type": "text", "text": f"msg{i}"}
        if with_markers:
            block["cache_control"] = {"type": "ephemeral"}
        msgs.append({"role": role, "content": [block]})
    return msgs


def _count_markers(messages):
    count = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            count += sum(
                1 for b in c if isinstance(b, dict) and "cache_control" in b
            )
    return count


class TestMarkerPlacement:
    def test_boundary_marker_second_to_last(self, mantle_provider):
        msgs = _history(6)
        out = mantle_provider.prepare_cache_control(msgs, iteration=2)
        assert _count_markers(out) == 1
        assert out[-2]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_iteration_zero_untouched(self, mantle_provider):
        msgs = _history(6)
        assert mantle_provider.prepare_cache_control(msgs, iteration=0) == msgs

    def test_short_conversation_untouched(self, mantle_provider):
        msgs = _history(2)
        assert mantle_provider.prepare_cache_control(msgs, iteration=3) == msgs

    def test_original_messages_not_mutated(self, mantle_provider):
        msgs = _history(6)
        mantle_provider.prepare_cache_control(msgs, iteration=2)
        assert _count_markers(msgs) == 0

    def test_string_content_wrapped(self, mantle_provider):
        msgs = [{"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"}]
        out = mantle_provider.prepare_cache_control(msgs, iteration=1)
        boundary = out[-2]["content"]
        assert isinstance(boundary, list)
        assert boundary[0]["cache_control"] == {"type": "ephemeral"}


class TestStaleMarkerStripping:
    """Incoming history can already carry cache_control markers (persisted
    turns, legacy wrapper paths).  Without stripping, each iteration adds
    a marker on top of the old ones and the request exceeds the API's
    4-breakpoint limit — the direct BedrockProvider strips; the inherited
    Anthropic path must too.
    """

    def test_stale_markers_are_stripped(self, mantle_provider):
        msgs = _history(8, with_markers=True)
        out = mantle_provider.prepare_cache_control(msgs, iteration=2)
        assert _count_markers(out) == 1

    def test_full_request_within_breakpoint_limit(self, mantle_provider):
        config = ProviderConfig(max_output_tokens=1024, iteration=2)
        msgs = _history(10, with_markers=True)
        request = mantle_provider._build_request(msgs, "S" * 2000, [], config)
        total = _count_markers(request["messages"])
        system = request.get("system")
        if isinstance(system, list):
            total += sum(
                1 for b in system
                if isinstance(b, dict) and "cache_control" in b
            )
        assert total <= 4
        # 1 system + 1 conversation boundary
        assert total == 2

    def test_anthropic_direct_strips_identically(self, anthropic_provider):
        """The parent class must behave the same — the fix lives there."""
        msgs = _history(8, with_markers=True)
        out = anthropic_provider.prepare_cache_control(msgs, iteration=2)
        assert _count_markers(out) == 1


class TestDisableToggle:
    """ZIYA_DISABLE_PROMPT_CACHE parity — BedrockProvider honors it; the
    mantle/anthropic path must too or the diagnostic toggle silently does
    nothing on those endpoints.
    """

    def test_disable_env_skips_all_markers(self, mantle_provider):
        msgs = _history(8)
        with patch.dict(os.environ, {"ZIYA_DISABLE_PROMPT_CACHE": "1"}):
            out = mantle_provider.prepare_cache_control(msgs, iteration=2)
        assert _count_markers(out) == 0

    def test_disable_env_off_places_marker(self, mantle_provider):
        msgs = _history(8)
        with patch.dict(os.environ, {"ZIYA_DISABLE_PROMPT_CACHE": "0"}):
            out = mantle_provider.prepare_cache_control(msgs, iteration=2)
        assert _count_markers(out) == 1


class TestSystemPromptCaching:
    def test_system_prompt_always_gets_marker(self, mantle_provider):
        config = ProviderConfig(max_output_tokens=1024, iteration=0)
        request = mantle_provider._build_request(
            [{"role": "user", "content": "hi"}], "system text", [], config)
        assert request["system"][0]["cache_control"] == {"type": "ephemeral"}


class _FakeStreamCM:
    """Minimal async-context-manager + async-iterator standing in for
    anthropic.AsyncAnthropic().messages.stream(...)."""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for e in self._events:
            yield e


class TestUsageCacheMetrics:
    """The executor decides throttle_state['cache_working'] from
    UsageEvent.cache_read_tokens — mantle must surface the same fields
    the direct Bedrock path does or cache health looks permanently dead.
    """

    async def test_cache_fields_surfaced_from_message_start(self, mantle_provider):
        events = [
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(usage=SimpleNamespace(
                    input_tokens=1200,
                    cache_read_input_tokens=45000,
                    cache_creation_input_tokens=800,
                )),
            ),
            SimpleNamespace(type="message_stop"),
        ]
        mantle_provider.client.messages.stream = MagicMock(
            return_value=_FakeStreamCM(events))

        got = [e async for e in mantle_provider._do_stream(
            {"model": "m", "messages": []})]

        usage = [e for e in got if isinstance(e, UsageEvent)]
        assert usage, "message_start usage must yield a UsageEvent"
        assert usage[0].input_tokens == 1200
        assert usage[0].cache_read_tokens == 45000
        assert usage[0].cache_write_tokens == 800
        assert isinstance(got[-1], StreamEnd)

    async def test_missing_cache_fields_default_zero(self, mantle_provider):
        events = [
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(usage=SimpleNamespace(input_tokens=10)),
            ),
            SimpleNamespace(type="message_stop"),
        ]
        mantle_provider.client.messages.stream = MagicMock(
            return_value=_FakeStreamCM(events))
        got = [e async for e in mantle_provider._do_stream(
            {"model": "m", "messages": []})]
        usage = [e for e in got if isinstance(e, UsageEvent)][0]
        assert usage.cache_read_tokens == 0
        assert usage.cache_write_tokens == 0
