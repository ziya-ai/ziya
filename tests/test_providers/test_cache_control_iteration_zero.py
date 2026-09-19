"""
The conversation-history cache breakpoint must be placed on ITERATION 0.

Iteration 0 is the first LLM call of every user turn (the stream loop is
``for iteration in range(max_iterations)``), not the first turn of the
conversation.  From commit 17101794 until the fix, both providers returned
history unmarked when ``iteration == 0`` -- a guard whose comment read
"first iteration ... no conversation caching needed", conflating the two.

Consequence, measured in ~/.ziya/usage.db for one conversation before the
fix: every turn's first call cache-wrote ~154K (the system block only) and
processed the ENTIRE history as fresh tokens, growing 41K -> 118K -> 147K
-> 165K over the session; history was only cache-written on iteration 1.
First turn after the fix: iteration 0 cache-wrote 319,912 (system +
history) and fresh input fell to 6,140.

These tests fail against the guarded code and pass without it.  They are
deliberately NOT written as "iteration 0 places at least one marker",
because a provider could satisfy that with the system block alone; they
assert the marker lands on the same conversation message it lands on at
iteration 1, i.e. the history breakpoint itself.
"""
from __future__ import annotations

import copy

import pytest

from app.providers.anthropic_direct import AnthropicDirectProvider
from app.providers.bedrock import BedrockProvider


@pytest.fixture(autouse=True)
def _no_cache_kill_switch(monkeypatch):
    monkeypatch.delenv("ZIYA_DISABLE_PROMPT_CACHE", raising=False)


@pytest.fixture(params=["anthropic_direct", "bedrock"])
def provider(request):
    cls = {
        "anthropic_direct": AnthropicDirectProvider,
        "bedrock": BedrockProvider,
    }[request.param]
    # __init__ wants credentials; prepare_cache_control reads no instance state.
    return request.param, cls, object.__new__(cls)


def _history(n):
    """A plain multi-turn history: user/assistant alternating, string content."""
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"message {i}"})
    return msgs


def _stamped_indices(messages):
    out = []
    for mi, m in enumerate(messages):
        content = m.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "cache_control" in block:
                    out.append(mi)
    return out


class TestIterationZeroPlacesTheHistoryBreakpoint:

    def test_iteration_zero_marks_the_same_message_as_iteration_one(self, provider):
        kind, cls, prov = provider
        msgs = _history(12)
        at_zero = cls.prepare_cache_control(prov, copy.deepcopy(msgs), iteration=0)
        at_one = cls.prepare_cache_control(prov, copy.deepcopy(msgs), iteration=1)
        assert _stamped_indices(at_one), f"{kind}: fixture broken -- iteration 1 placed no marker"
        assert _stamped_indices(at_zero) == _stamped_indices(at_one), (
            f"{kind}: iteration 0 must mark the history boundary exactly as "
            f"iteration 1 does; got {_stamped_indices(at_zero)} vs {_stamped_indices(at_one)}"
        )

    def test_iteration_zero_marker_is_on_history_not_on_the_newest_message(self, provider):
        kind, cls, prov = provider
        msgs = _history(12)
        out = cls.prepare_cache_control(prov, msgs, iteration=0)
        idx = _stamped_indices(out)
        assert len(idx) == 1, f"{kind}: expected exactly one history marker, got {idx}"
        # The newest user message is the one thing that is new this turn; a
        # marker there would cache nothing reusable.
        assert idx[0] < len(msgs) - 1

    def test_short_history_floor_still_applies_at_iteration_zero(self, provider):
        kind, cls, prov = provider
        floor = 3 if kind == "anthropic_direct" else 6
        msgs = _history(floor - 1)
        out = cls.prepare_cache_control(prov, copy.deepcopy(msgs), iteration=0)
        assert _stamped_indices(out) == [], f"{kind}: below-floor history must stay unmarked"

    def test_input_is_not_mutated(self, provider):
        kind, cls, prov = provider
        msgs = _history(12)
        snapshot = copy.deepcopy(msgs)
        cls.prepare_cache_control(prov, msgs, iteration=0)
        assert msgs == snapshot

    def test_kill_switch_still_suppresses_marker(self, provider, monkeypatch):
        kind, cls, prov = provider
        monkeypatch.setenv("ZIYA_DISABLE_PROMPT_CACHE", "1")
        out = cls.prepare_cache_control(prov, _history(12), iteration=0)
        assert _stamped_indices(out) == []
