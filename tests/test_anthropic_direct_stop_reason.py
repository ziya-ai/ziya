"""Characterization tests for AnthropicDirectProvider._do_stream stop_reason handling.

Regression guard for the bedrock-mantle / claude-fable-5 failure where a
max_tokens truncation was mislabeled as a clean "end_turn" at message_stop,
causing the continuation decider to end the stream mid-answer.

The real stop reason arrives on the *message_delta* event as
``delta.stop_reason``.  message_stop carries no stop reason of its own, so the
provider must remember the value seen on message_delta and emit it at
message_stop instead of a hardcoded default.
"""
import types

import pytest

from app.providers.anthropic_direct import AnthropicDirectProvider
from app.providers.base import StreamEnd, TextDelta


# ---------------------------------------------------------------------------
# Fake Anthropic streaming client
# ---------------------------------------------------------------------------

def _evt(event_type, **attrs):
    """Build a lightweight event object mimicking the Anthropic SDK shape."""
    return types.SimpleNamespace(type=event_type, **attrs)


class _FakeStream:
    """Async context manager yielding a fixed sequence of SDK-shaped events."""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):  # pragma: no cover - trivial delegation
        for event in self._events:
            yield event


class _FakeMessages:
    def __init__(self, events):
        self._events = events

    def stream(self, **_kwargs):
        return _FakeStream(self._events)


class _FakeClient:
    def __init__(self, events):
        self.messages = _FakeMessages(events)


def _make_provider(events):
    """Construct a provider without running __init__ (no API key required)."""
    provider = AnthropicDirectProvider.__new__(AnthropicDirectProvider)
    provider.client = _FakeClient(events)
    return provider


async def _collect(provider):
    return [e async for e in provider._do_stream({})]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_tokens_stop_reason_propagates():
    """A max_tokens delta must surface as StreamEnd(stop_reason='max_tokens').

    This is the core regression: the truncation was being reported as
    'end_turn', so the orchestrator ended the stream mid-answer.
    """
    events = [
        _evt("content_block_delta",
             index=0,
             delta=types.SimpleNamespace(type="text_delta", text="The bug is clear: `DirectoryBrowser")),
        _evt("message_delta",
             delta=types.SimpleNamespace(stop_reason="max_tokens"),
             usage=None),
        _evt("message_stop"),
    ]
    out = await _collect(_make_provider(events))

    ends = [e for e in out if isinstance(e, StreamEnd)]
    assert len(ends) == 1
    assert ends[0].stop_reason == "max_tokens"


@pytest.mark.asyncio
async def test_end_turn_default_when_no_delta_stop_reason():
    """Absent any message_delta stop_reason, the end defaults to 'end_turn'."""
    events = [
        _evt("content_block_delta",
             index=0,
             delta=types.SimpleNamespace(type="text_delta", text="hello")),
        _evt("message_stop"),
    ]
    out = await _collect(_make_provider(events))

    ends = [e for e in out if isinstance(e, StreamEnd)]
    assert len(ends) == 1
    assert ends[0].stop_reason == "end_turn"
    assert any(isinstance(e, TextDelta) and e.content == "hello" for e in out)


@pytest.mark.asyncio
async def test_tool_use_stop_reason_propagates():
    """tool_use stop reason must also survive to message_stop."""
    events = [
        _evt("message_delta",
             delta=types.SimpleNamespace(stop_reason="tool_use"),
             usage=None),
        _evt("message_stop"),
    ]
    out = await _collect(_make_provider(events))

    ends = [e for e in out if isinstance(e, StreamEnd)]
    assert len(ends) == 1
    assert ends[0].stop_reason == "tool_use"
