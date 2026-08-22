"""Prompt caching must never stamp a signed thinking block.

THE INCIDENT.  A long fable5 (bedrock-mantle) turn died mid-tool-loop with

    400 invalid_request_error
    messages.19.content.1: `thinking` or `redacted_thinking` blocks in the
    latest assistant message cannot be modified. These blocks must remain
    as they were in the original response.

Thinking passback itself was correct and live-verified on all three Claude
paths.  What no test covered was its INTERACTION with prompt caching: two
independently-correct halves that had never met.  The evidence for that
being a real gap rather than a guess is mechanical --
``test_thinking_passback.py`` and ``test_thinking_passback_live_contract.py``
contain zero occurrences of ``cache_control``, and
``test_bedrock_mantle_cache.py`` contains zero occurrences of ``thinking``.

THE MECHANISM.  ``prepare_cache_control`` picks a boundary message and
stamps ``cache_control`` onto its LAST content block.  Adding a key to a
thinking block is a modification, and the API rejects the whole request --
so the failure is not a degraded cache, it is a dead turn.

Reaching it needs an assistant message whose last block is a thinking
block, i.e. one with no text and no tool_use.  That shape is reachable
from ``streaming_tool_executor`` and is not exotic:

  1. line 3952 enters the append branch on
     ``assistant_text.strip() or tools_executed_this_iteration`` -- so an
     iteration with prose but NO tool calls qualifies, and ``tool_uses``
     is built from ``tool_results`` and stays empty;
  2. line 3969 then runs ``_sanitize_assistant_text``, which "truncates at
     the first fabrication boundary" -- a contamination on the first line
     truncates to the empty string;
  3. ``build_assistant_message("", [], thinking_blocks=[...])`` emits a
     content list of thinking blocks and nothing else;
  4. the next iteration stamps its last block.

With exactly two thinking blocks the stamped index is ``content.1``,
which is the coordinate the API reported.

WHAT THESE TESTS PIN.  The invariant is one-directional and both halves
matter: a thinking block must never be stamped, AND a message that has a
legal target must still get one.  Asserting only the first would be
satisfied by deleting prompt caching outright, which would silently cost
every long conversation its cache hits -- so every "not stamped" assertion
here is paired with a positive one that caching still happened.
"""

import copy

import pytest

from app.providers.anthropic_direct import AnthropicDirectProvider
from app.providers.bedrock import BedrockProvider

THINKING_TYPES = ("thinking", "redacted_thinking")


@pytest.fixture(autouse=True)
def _no_cache_kill_switch(monkeypatch):
    """The providers honour a disable flag; a stray export would make every
    test here vacuously green by returning the messages untouched."""
    monkeypatch.delenv("ZIYA_DISABLE_PROMPT_CACHE", raising=False)


@pytest.fixture(params=["anthropic_direct", "bedrock"])
def provider(request):
    """Both providers, because both have the defect and neither inherits
    the other's fix.  ``BedrockMantleProvider`` -- the path fable5 actually
    uses -- subclasses ``AnthropicDirectProvider`` and overrides only
    __init__/_estimate_request_tokens/provider_name, so covering the parent
    covers it.  __init__ is bypassed: it wants credentials and a model
    config, and none of the methods under test read instance state.
    """
    cls = {
        "anthropic_direct": AnthropicDirectProvider,
        "bedrock": BedrockProvider,
    }[request.param]
    prov = object.__new__(cls)
    return request.param, cls, prov


def _thinking(i, signature="sig-bytes-from-the-api"):
    return {"type": "thinking", "thinking": f"reasoning {i}", "signature": signature}


def _redacted(i):
    return {"type": "redacted_thinking", "data": f"opaque-ciphertext-{i}"}


def _stamped(messages):
    """Every (message_idx, block_idx, type) carrying a cache_control key."""
    out = []
    for mi, m in enumerate(messages):
        content = m.get("content")
        if isinstance(content, list):
            for bi, block in enumerate(content):
                if isinstance(block, dict) and "cache_control" in block:
                    out.append((mi, bi, block.get("type")))
    return out


def _illegal(messages):
    """Stamps that land on a thinking block -- the 400 this file is about.

    Reported in the API's own coordinate form so a failure message can be
    compared directly against an incident report.
    """
    return [
        f"messages.{mi}.content.{bi}"
        for mi, bi, btype in _stamped(messages)
        if btype in THINKING_TYPES
    ]


def _boundary_index(kind, n):
    """Where each provider places its marker, per its own implementation.

    Hardcoding one index would silently stop testing a provider the day it
    retunes its boundary; deriving it keeps the fixture honest.
    """
    return n - 2 if kind == "anthropic_direct" else n - 4


def _conversation(kind, prov, cls, bad_tail_blocks):
    """A tool-loop history whose BOUNDARY message ends with ``bad_tail_blocks``.

    Padded to clear each provider's minimum length (anthropic >= 3,
    bedrock >= 6) and positioned so the boundary lands on the crafted
    message rather than on a neighbour.
    """
    good = cls.build_assistant_message(
        prov, "some prose", [{"id": "t0", "name": "sh", "input": {}}],
        thinking_blocks=[_thinking(0)],
    )
    result = cls.build_tool_result_message(prov, [{"tool_use_id": "t0", "content": "out"}])
    # The message under test: thinking blocks only, which is what
    # build_assistant_message emits for empty text and no tool_uses.
    victim = cls.build_assistant_message(prov, "", [], thinking_blocks=bad_tail_blocks)

    n = 3 if kind == "anthropic_direct" else 9
    msgs = [{"role": "user", "content": "go"}]
    while len(msgs) < n:
        msgs.append(copy.deepcopy(good if len(msgs) % 2 else result))
    msgs[_boundary_index(kind, n)] = victim
    return msgs


class TestTheBoundaryLandsWhereTheFixtureThinks:
    """Without this the suite could pass by never testing the victim at all."""

    def test_the_victim_is_the_boundary_message(self, provider):
        kind, cls, prov = provider
        msgs = _conversation(kind, prov, cls, [_thinking(1), _thinking(2)])
        idx = _boundary_index(kind, len(msgs))
        content = msgs[idx]["content"]
        assert all(b["type"] in THINKING_TYPES for b in content), (
            f"fixture is wrong: boundary message {idx} is not the all-thinking "
            f"message, so this file would prove nothing"
        )

    def test_the_victim_shape_is_what_the_builder_emits(self, provider):
        """The all-thinking message is the builder's own output for the
        executor's empty-text/no-tool case, not something hand-rolled."""
        kind, cls, prov = provider
        built = cls.build_assistant_message(
            prov, "", [], thinking_blocks=[_thinking(1), _thinking(2)],
        )
        assert [b["type"] for b in built["content"]] == ["thinking", "thinking"], (
            "build_assistant_message no longer emits a thinking-only turn for "
            "empty text and no tool_uses; re-derive the reachable path before "
            "trusting the rest of this file"
        )


class TestAThinkingBlockIsNeverStamped:
    """The invariant. Each case is a shape the API rejects outright."""

    def test_two_thinking_blocks_reproduces_the_reported_coordinate(self, provider):
        kind, cls, prov = provider
        msgs = _conversation(kind, prov, cls, [_thinking(1), _thinking(2)])
        out = cls.prepare_cache_control(prov, msgs, iteration=1)
        bad = _illegal(out)
        assert not bad, (
            f"[{kind}] cache_control was stamped onto a signed thinking block "
            f"at {bad}; the API rejects the entire request with 400 "
            f"'thinking blocks in the latest assistant message cannot be "
            f"modified' -- this is the reported incident, at content index 1"
        )

    def test_a_single_thinking_block_is_not_stamped(self, provider):
        kind, cls, prov = provider
        msgs = _conversation(kind, prov, cls, [_thinking(1)])
        out = cls.prepare_cache_control(prov, msgs, iteration=1)
        assert not _illegal(out), f"[{kind}] stamped a lone thinking block"

    def test_a_redacted_thinking_block_is_not_stamped(self, provider):
        """redacted_thinking carries opaque server bytes and is named in the
        same error; it must be treated identically to a readable block."""
        kind, cls, prov = provider
        msgs = _conversation(kind, prov, cls, [_thinking(1), _redacted(2)])
        out = cls.prepare_cache_control(prov, msgs, iteration=1)
        assert not _illegal(out), f"[{kind}] stamped a redacted_thinking block"

    def test_the_thinking_blocks_are_returned_byte_identical(self, provider):
        """Beyond cache_control: the boundary message's thinking blocks must
        come back exactly as supplied.  Byte-equality is load-bearing --
        a tampered signature is refused by the API on its own."""
        kind, cls, prov = provider
        blocks = [_thinking(1), _thinking(2)]
        msgs = _conversation(kind, prov, cls, blocks)
        idx = _boundary_index(kind, len(msgs))
        before = copy.deepcopy(msgs[idx]["content"])
        out = cls.prepare_cache_control(prov, msgs, iteration=1)
        assert out[idx]["content"] == before, (
            f"[{kind}] the boundary message's thinking blocks were altered: "
            f"{out[idx]['content']!r} != {before!r}"
        )


class TestCachingStillHappens:
    """The other direction.  Deleting prompt caching would satisfy every
    assertion above while quietly costing each long conversation its cache
    hits, so the fix has to be selective rather than absent."""

    def test_a_normal_turn_is_still_stamped(self, provider):
        kind, cls, prov = provider
        good = cls.build_assistant_message(
            prov, "prose", [{"id": "t9", "name": "sh", "input": {}}],
            thinking_blocks=[_thinking(0)],
        )
        n = 3 if kind == "anthropic_direct" else 9
        result = cls.build_tool_result_message(
            prov, [{"tool_use_id": "t9", "content": "out"}])
        msgs = [{"role": "user", "content": "go"}]
        while len(msgs) < n:
            msgs.append(copy.deepcopy(good if len(msgs) % 2 else result))
        msgs[_boundary_index(kind, n)] = copy.deepcopy(good)

        out = cls.prepare_cache_control(prov, msgs, iteration=1)
        stamped = _stamped(out)
        assert stamped, (
            f"[{kind}] no cache breakpoint was placed at all; prompt caching "
            f"is what makes long sessions economical, so a fix that disables "
            f"it is not a fix"
        )
        assert not _illegal(out)
        assert all(t not in THINKING_TYPES for _, _, t in stamped)

    def test_the_marker_falls_back_to_the_last_legal_block(self, provider):
        """A turn of [thinking, text] must still cache -- on the text block.
        Skipping the whole message would forfeit a breakpoint that is
        available, which is a silent cost rather than a correctness bug and
        therefore exactly the kind that survives unnoticed."""
        kind, cls, prov = provider
        msgs = _conversation(kind, prov, cls, [_thinking(1)])
        idx = _boundary_index(kind, len(msgs))
        msgs[idx]["content"].append({"type": "text", "text": "trailing prose"})

        out = cls.prepare_cache_control(prov, msgs, iteration=1)
        assert not _illegal(out), f"[{kind}] stamped the thinking block"
        assert any(mi == idx and t == "text" for mi, _, t in _stamped(out)), (
            f"[{kind}] the boundary message had a legal text block to carry "
            f"the marker and it was not used: {_stamped(out)}"
        )
