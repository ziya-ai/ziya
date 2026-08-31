"""
Per-content-block text segments and synthetic tool placement in the echoed
assistant turn.

Third distinct cause of the mantle 400:

    messages.N.content.M: `thinking` or `redacted_thinking` blocks in the
    latest assistant message cannot be modified. These blocks must remain
    as they were in the original response.

surviving both the cache-control fix (no marker on a thinking block) and the
interleaved-order fix (blocks emitted in original index order).  Two residual
ways the echoed turn still deviated from the signed original:

1. **Synthetic tool_use collided with the text slot.**  ``next_synthetic``
   was seeded from thinking + real-tool indices only, EXCLUDING
   ``text_index``.  For the classic fake-dispatch shape
   ``[thinking0, text1]`` the first fabricated call got key ``1.0`` — tying
   the text block's key — and the stable sort slotted it BETWEEN thinking
   and text, displacing a real block from its original position.

2. **Multi-text-block turns were merged into one text block.**  Adaptive
   thinking (fable5 via bedrock-mantle) interleaves thinking between prose
   segments: ``[th0, text1, th2, text3, th4, text5]``.  Merging all text
   into a single block at ``text_index`` left later thinking blocks
   adjacent/shifted out of their original positions — the observed
   ``messages.33.content.4`` coordinate is exactly ``th4`` no longer
   sitting at index 4.  ``text_segments`` now carries per-block text so
   every thinking block keeps its absolute index, and a segment emptied by
   fence excision is replaced by a placeholder rather than vacating a slot
   a later thinking block depends on.

Also covers the executor-side seam: ``TextDeltaState.text_block_marks``
records where each content block's text begins in ``assistant_text``, set
from the block index the bridge now threads onto text chunks.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.providers.base import LLMProvider
from app.text_delta_processor import TextDeltaState, process_text_delta


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


def _syn(tid="fake_0"):
    """A synthesized (fake-dispatch / hallucination-correction) tool call."""
    return {"id": tid, "name": "mcp_run_shell_command", "input": {}, "index": None}


# ---------------------------------------------------------------------------
# 1. Synthetic tool_use must never displace the text block
# ---------------------------------------------------------------------------

class TestSyntheticToolNeverDisplacesText:

    def test_synthetic_tool_lands_after_the_text_block(self):
        """[thinking0, text1] + one fake call → [thinking, text, tool_use].

        Pre-fix: next_synthetic ignored text_index, tied the text block's
        sort key, and the stable sort emitted the fabricated call BETWEEN
        thinking and text.
        """
        content = LLMProvider._ordered_assistant_content(
            "prose", [_syn()], thinking_blocks=[_tb(0)], text_index=1,
        )
        assert [b["type"] for b in content] == ["thinking", "text", "tool_use"]

    def test_multiple_synthetic_tools_all_after_text_in_dispatch_order(self):
        """The mantle-log shape: several fake dispatches in one turn."""
        content = LLMProvider._ordered_assistant_content(
            "prose",
            [_syn("fake_0"), _syn("fake_1"), _syn("fake_2"), _syn("fake_3")],
            thinking_blocks=[_tb(0)], text_index=1,
        )
        assert [b["type"] for b in content] == (
            ["thinking", "text"] + ["tool_use"] * 4
        )
        assert [b["id"] for b in content[2:]] == [
            "fake_0", "fake_1", "fake_2", "fake_3"]


# ---------------------------------------------------------------------------
# 2. Per-block text segments keep thinking blocks at their absolute indices
# ---------------------------------------------------------------------------

class TestTextSegments:

    def test_thinking_blocks_keep_absolute_positions(self):
        """th0,text1,th2,text3,th4,text5 + fakes: th4 must SIT at index 4.

        Merged text put th4 at position 3 while its signature belongs to
        index 4 — the observed messages.N.content.4 rejection.
        """
        content = LLMProvider._ordered_assistant_content(
            "merged fallback — unused",
            [_syn("fake_2"), _syn("fake_3")],
            thinking_blocks=[_tb(0, "t0"), _tb(2, "t2"), _tb(4, "t4")],
            text_index=1,
            text_segments=[(1, "a"), (3, "b"), (5, "c")],
        )
        assert [b["type"] for b in content] == [
            "thinking", "text", "thinking", "text", "thinking", "text",
            "tool_use", "tool_use",
        ]
        assert content[0]["thinking"] == "t0"
        assert content[2]["thinking"] == "t2"
        assert content[4]["thinking"] == "t4"

    def test_segments_replace_the_merged_text_block(self):
        content = LLMProvider._ordered_assistant_content(
            "MERGED — must not appear",
            [],
            thinking_blocks=[_tb(0), _tb(2)],
            text_index=1,
            text_segments=[(1, "one"), (3, "two")],
        )
        texts = [b["text"] for b in content if b["type"] == "text"]
        assert texts == ["one", "two"]

    def test_excised_segment_before_later_thinking_keeps_its_slot(self):
        """A text block that held only a fabricated fence sanitizes to
        empty; a placeholder must occupy its slot or the next thinking
        block shifts left out of its original position."""
        content = LLMProvider._ordered_assistant_content(
            "x", [],
            thinking_blocks=[_tb(0), _tb(2), _tb(4, "t4")],
            text_index=1,
            text_segments=[(1, "a"), (3, "   "), (5, "c")],
        )
        assert [b["type"] for b in content] == [
            "thinking", "text", "thinking", "text", "thinking", "text",
        ]
        assert content[4]["thinking"] == "t4"
        assert "removed" in content[3]["text"]

    def test_trailing_empty_segment_is_dropped(self):
        """No later thinking block depends on the slot → no placeholder."""
        content = LLMProvider._ordered_assistant_content(
            "a", [],
            thinking_blocks=[_tb(0)],
            text_index=1,
            text_segments=[(1, "a"), (3, "")],
        )
        assert [b["type"] for b in content] == ["thinking", "text"]

    def test_single_merged_text_behavior_unchanged(self):
        """No segments → the existing single-text-block shape (guard)."""
        content = LLMProvider._ordered_assistant_content(
            "prose",
            [{"id": "t1", "name": "n", "input": {}, "index": 2}],
            thinking_blocks=[_tb(0)], text_index=1,
        )
        assert [b["type"] for b in content] == ["thinking", "text", "tool_use"]


# ---------------------------------------------------------------------------
# 3. Provider builders accept and route text_segments
# ---------------------------------------------------------------------------

class TestBuilderSegmentsPassthrough:

    @pytest.mark.parametrize(
        "provider_fixture", ["anthropic_provider", "bedrock_provider"])
    def test_builder_accepts_text_segments(self, provider_fixture, request):
        p = request.getfixturevalue(provider_fixture)
        msg = p.build_assistant_message(
            "merged", [], thinking_blocks=[_tb(0), _tb(2)],
            text_index=1, text_segments=[(1, "one"), (3, "two")],
        )
        assert msg["role"] == "assistant"
        assert [b["type"] for b in msg["content"]] == [
            "thinking", "text", "thinking", "text"]


# ---------------------------------------------------------------------------
# 4. Executor seam: TextDeltaState records per-block offsets
# ---------------------------------------------------------------------------

def _make_executor():
    """Minimal mock executor (mirrors tests/test_text_delta_processor.py)."""
    executor = MagicMock()
    executor._normalize_fence_spacing.side_effect = lambda text, tracker: text
    executor._update_code_block_tracker.return_value = None
    executor._block_opening_buffer = ""
    optimizer = MagicMock()
    optimizer.add_content.side_effect = lambda t: [t] if t else []
    optimizer.flush_remaining.return_value = ""
    executor._content_optimizer = optimizer
    executor._fake_tool_ticks = 0
    executor._fake_tool_buffer = ""
    return executor


class TestBlockMarkRecording:

    def test_marks_record_block_transitions(self):
        ex = _make_executor()
        state = TextDeltaState()
        state.current_block_index = 1
        process_text_delta(ex, "first ", state)
        process_text_delta(ex, "block. ", state)
        state.current_block_index = 3
        process_text_delta(ex, "second block.", state)
        assert [m[1] for m in state.text_block_marks] == [1, 3]
        off1 = state.text_block_marks[1][0]
        assert state.assistant_text[:off1].startswith("first ")
        assert state.assistant_text[off1:] == "second block."

    def test_one_mark_per_block_not_per_delta(self):
        ex = _make_executor()
        state = TextDeltaState()
        state.current_block_index = 1
        for chunk in ("a", "b", "c"):
            process_text_delta(ex, chunk, state)
        assert len(state.text_block_marks) == 1

    def test_no_index_records_no_marks(self):
        """Providers that never set an index keep the legacy behavior."""
        ex = _make_executor()
        state = TextDeltaState()
        process_text_delta(ex, "plain", state)
        assert state.text_block_marks == []
