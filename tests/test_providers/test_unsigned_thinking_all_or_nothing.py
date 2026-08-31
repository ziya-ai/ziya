"""
Unsigned thinking blocks: surfaced at the stream layer, all-or-nothing at
the assembler.

The API rejects a readable ``thinking`` block whose signature is missing or
empty — but the old behavior (silently dropping the unsigned block inside the
provider stream parser) was worse than illegal: with a multi-block turn the
drop left a GAP, shifting every later block out of its original position,
which the API rejects with the same 400 the passback machinery exists to
prevent:

    messages.N.content.M: thinking or redacted_thinking blocks in the
    latest assistant message cannot be modified. These blocks must remain
    as they were in the original response.

The fix has two halves, pinned here:

  1. Stream layer (both providers): an unsigned readable block is YIELDED
     as a ``ThinkingBlock`` with ``signature=None`` (plus a warning log)
     instead of vanishing, so downstream has full-turn information.
     (The per-provider stream tests live in test_thinking_passback.py.)

  2. Assembler (``LLMProvider._ordered_assistant_content``): if ANY
     readable thinking block is unsigned, ALL thinking blocks are dropped
     from the echoed turn.  Echoing none is the known-good shape —
     identical to ZIYA_DISABLE_THINKING_PASSBACK — whereas a partial set
     is the known-bad gapped shape.  Text and tool_use blocks survive.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.providers.base import LLMProvider


# ---------------------------------------------------------------------------
# Fixtures (mirroring test_thinking_passback_interleaved_order.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def bedrock_provider():
    with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client") as mock_get:
        mock_get.return_value = MagicMock()
        from app.providers.bedrock import BedrockProvider
        return BedrockProvider(
            model_id="anthropic.claude-sonnet-4-20250514-v1:0",
            model_config={
                "family": "claude",
                "supports_thinking": True,
                "supports_adaptive_thinking": True,
            },
            aws_profile="test",
            region="us-west-2",
        )


@pytest.fixture
def anthropic_provider():
    from app.providers.anthropic_direct import AnthropicDirectProvider
    p = AnthropicDirectProvider.__new__(AnthropicDirectProvider)
    p.model_id = "claude-sonnet-4-20250514"
    p.model_config = {
        "family": "claude",
        "supports_thinking": True,
        "supports_adaptive_thinking": True,
    }
    p.client = None
    return p


def _tb(idx, text="plan", sig="SIG"):
    return {"type": "thinking", "thinking": text, "signature": sig, "_index": idx}


def _utb(idx, text="unsigned"):
    """An UNSIGNED readable thinking block, as the stream layer now yields."""
    return {"type": "thinking", "thinking": text, "signature": None, "_index": idx}


def _rtb(idx, data="OPAQUE"):
    return {"type": "redacted_thinking", "data": data, "_index": idx}


def _tu(idx, tid="t1", name="mcp_run_shell_command"):
    return {"id": tid, "name": name, "input": {"command": "ls"}, "index": idx}


# ---------------------------------------------------------------------------
# 1. The assembler guard: all-or-nothing
# ---------------------------------------------------------------------------

class TestAllOrNothingGuard:

    def test_unsigned_among_signed_drops_all_thinking(self):
        """One unsigned block poisons the set: NO thinking is echoed.

        A partial set [signed0, <gap>, signed4] shifts signed4 to sent
        position 1 — the exact modification the API rejects — so the only
        legal degradations are 'all' (impossible: one is unsigned) or
        'none'.
        """
        content = LLMProvider._ordered_assistant_content(
            "text",
            [_tu(1)],
            thinking_blocks=[_tb(0, "signed"), _utb(2), _tb(4, "also signed")],
            text_index=3,
        )
        assert [b["type"] for b in content] == ["text", "tool_use"]

    def test_text_and_tool_use_survive_the_guard(self):
        content = LLMProvider._ordered_assistant_content(
            "kept text",
            [_tu(1, tid="kept")],
            thinking_blocks=[_utb(0)],
        )
        types = [b["type"] for b in content]
        assert "thinking" not in types and "redacted_thinking" not in types
        assert {"text", "tool_use"} <= set(types)
        assert content[0]["text"] == "kept text"
        assert any(b.get("id") == "kept" for b in content)

    def test_empty_string_signature_counts_as_unsigned(self):
        content = LLMProvider._ordered_assistant_content(
            "", [_tu(1)],
            thinking_blocks=[_tb(0, sig="")],
        )
        assert [b["type"] for b in content] == ["tool_use"]

    def test_redacted_thinking_does_not_trip_the_guard(self):
        """redacted_thinking has no signature BY DESIGN (opaque data)."""
        content = LLMProvider._ordered_assistant_content(
            "", [_tu(1)],
            thinking_blocks=[_rtb(0), _tb(2)],
        )
        assert [b["type"] for b in content] == [
            "redacted_thinking", "tool_use", "thinking",
        ]

    def test_all_signed_turn_unaffected(self):
        """Non-vacuity: the guard must not eat legitimate passback."""
        content = LLMProvider._ordered_assistant_content(
            "", [_tu(1)],
            thinking_blocks=[_tb(0), _tb(2)],
        )
        assert [b["type"] for b in content] == [
            "thinking", "tool_use", "thinking",
        ]

    def test_guard_applies_on_legacy_no_metadata_path(self):
        """Blocks without '_index' (legacy callers) are guarded too."""
        content = LLMProvider._ordered_assistant_content(
            "t", [{"id": "t1", "name": "n", "input": {}}],
            thinking_blocks=[{"type": "thinking", "thinking": "p",
                              "signature": None}],
        )
        assert [b["type"] for b in content] == ["text", "tool_use"]

    def test_guard_with_text_segments_degrades_to_merged_text(self):
        """With thinking gone, segment positions no longer matter; the
        merged text is emitted once, not per-segment placeholders."""
        content = LLMProvider._ordered_assistant_content(
            "one two",
            [],
            thinking_blocks=[_utb(0), _tb(2)],
            text_index=1,
            text_segments=[(1, "one"), (3, "two")],
        )
        assert [b["type"] for b in content] == ["text"]
        assert content[0]["text"] == "one two"


# ---------------------------------------------------------------------------
# 2. Both provider builders route through the guard
# ---------------------------------------------------------------------------

class TestBuilderGuard:

    @pytest.mark.parametrize(
        "provider_fixture", ["anthropic_provider", "bedrock_provider"])
    def test_unsigned_block_suppresses_turn_passback(self, provider_fixture, request):
        p = request.getfixturevalue(provider_fixture)
        msg = p.build_assistant_message(
            "text", [_tu(1)],
            thinking_blocks=[_tb(0), _utb(2)],
            text_index=3,
        )
        types = [b["type"] for b in msg["content"]]
        assert "thinking" not in types
        assert "tool_use" in types and "text" in types

    @pytest.mark.parametrize(
        "provider_fixture", ["anthropic_provider", "bedrock_provider"])
    def test_signed_turn_still_echoed(self, provider_fixture, request):
        """Non-vacuity at the builder level."""
        p = request.getfixturevalue(provider_fixture)
        msg = p.build_assistant_message(
            "text", [_tu(2)], thinking_blocks=[_tb(0)], text_index=1,
        )
        assert [b["type"] for b in msg["content"]] == [
            "thinking", "text", "tool_use",
        ]
