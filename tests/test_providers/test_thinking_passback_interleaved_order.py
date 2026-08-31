"""
Interleaved-thinking block ordering in the echoed assistant turn.

The Anthropic API requires thinking/redacted_thinking blocks in the latest
assistant message to remain EXACTLY as they were in the original response,
including their position relative to text and tool_use blocks.  With
adaptive thinking (e.g. fable5 via bedrock-mantle) the model emits thinking
blocks BETWEEN tool_use blocks; coalescing them to the front of the echoed
turn is a modification and the whole request 400s:

    messages.N.content.M: thinking or redacted_thinking blocks in the
    latest assistant message cannot be modified. These blocks must remain
    as they were in the original response.

These tests pin the ordering contract of ``_ordered_assistant_content`` and
of ``build_assistant_message`` on both providers that advertise
``thinking_passback`` (AnthropicDirectProvider — inherited by
BedrockMantleProvider — and BedrockProvider), plus the index plumbing on
``TextDelta``.

The key negative check: WITHOUT ordering metadata the legacy order
(thinking, text, tool_use) must be preserved — that is both the
backward-compatible shape and the correct order for classic
non-interleaved thinking.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.providers.base import LLMProvider, TextDelta


# ---------------------------------------------------------------------------
# Fixtures (mirroring tests/test_providers/test_thinking_passback.py)
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


def _rtb(idx, data="OPAQUE"):
    return {"type": "redacted_thinking", "data": data, "_index": idx}


def _tu(idx, tid="t1", name="mcp_run_shell_command"):
    return {"id": tid, "name": name, "input": {"command": "ls"}, "index": idx}


# ---------------------------------------------------------------------------
# 1. The shared assembler: original order preserved
# ---------------------------------------------------------------------------

class TestOrderedAssistantContent:

    def test_interleaved_thinking_stays_between_tool_uses(self):
        """The fable5/mantle crash shape: thinking between tool_use blocks.

        Original response: [thinking0, tool_use1, thinking2, tool_use3].
        Coalescing thinking to the front (the pre-fix behavior) is exactly
        what the API rejects.
        """
        content = LLMProvider._ordered_assistant_content(
            "",
            [_tu(1, tid="t1"), _tu(3, tid="t2")],
            thinking_blocks=[_tb(0, "first"), _tb(2, "second")],
        )
        assert [b["type"] for b in content] == [
            "thinking", "tool_use", "thinking", "tool_use",
        ]
        assert content[0]["thinking"] == "first"
        assert content[2]["thinking"] == "second"
        assert content[1]["id"] == "t1"
        assert content[3]["id"] == "t2"

    def test_text_placed_at_its_original_index(self):
        """[thinking0, text1, tool_use2, thinking3, tool_use4] round-trips."""
        content = LLMProvider._ordered_assistant_content(
            "Checking.",
            [_tu(2, tid="t1"), _tu(4, tid="t2")],
            thinking_blocks=[_tb(0), _tb(3)],
            text_index=1,
        )
        assert [b["type"] for b in content] == [
            "thinking", "text", "tool_use", "thinking", "tool_use",
        ]

    def test_text_before_thinking_preserved(self):
        """Adaptive thinking may emit text FIRST: [text0, thinking1, tool_use2]."""
        content = LLMProvider._ordered_assistant_content(
            "Answer first.",
            [_tu(2)],
            thinking_blocks=[_tb(1)],
            text_index=0,
        )
        assert [b["type"] for b in content] == ["text", "thinking", "tool_use"]

    def test_redacted_thinking_participates_in_ordering(self):
        content = LLMProvider._ordered_assistant_content(
            "",
            [_tu(1)],
            thinking_blocks=[_rtb(0), _tb(2)],
        )
        assert [b["type"] for b in content] == [
            "redacted_thinking", "tool_use", "thinking",
        ]

    def test_index_metadata_stripped_from_emitted_blocks(self):
        """'_index' and 'index' are Ziya-internal; the API must never see them."""
        content = LLMProvider._ordered_assistant_content(
            "t", [_tu(2)], thinking_blocks=[_tb(0)], text_index=1,
        )
        for block in content:
            assert "_index" not in block
            assert "index" not in block
        # The thinking block itself is otherwise verbatim.
        assert content[0] == {"type": "thinking", "thinking": "plan", "signature": "SIG"}

    def test_input_blocks_not_mutated(self):
        """The caller's thinking dicts keep their '_index' (copied, not popped)."""
        tb = _tb(0)
        LLMProvider._ordered_assistant_content("", [_tu(1)], thinking_blocks=[tb])
        assert tb["_index"] == 0

    def test_synthetic_tool_use_appended_after_real_blocks(self):
        """A tool_use with no index (fake/hallucination-correction call) may
        not displace real blocks from their original positions."""
        synthetic = {"id": "fake", "name": "mcp_run_shell_command",
                     "input": {}, "index": None}
        content = LLMProvider._ordered_assistant_content(
            "",
            [synthetic, _tu(1, tid="real")],
            thinking_blocks=[_tb(0), _tb(2)],
        )
        assert [b["type"] for b in content] == [
            "thinking", "tool_use", "thinking", "tool_use",
        ]
        assert content[1]["id"] == "real"
        assert content[3]["id"] == "fake"

    def test_no_metadata_falls_back_to_legacy_order(self):
        """Blocks without '_index' (legacy callers) keep thinking-first order."""
        content = LLMProvider._ordered_assistant_content(
            "Checking.",
            [{"id": "t1", "name": "n", "input": {}}],
            thinking_blocks=[{"type": "thinking", "thinking": "p", "signature": "S"}],
        )
        assert [b["type"] for b in content] == ["thinking", "text", "tool_use"]

    def test_no_thinking_blocks_is_text_then_tools(self):
        content = LLMProvider._ordered_assistant_content(
            "Checking.", [{"id": "t1", "name": "n", "input": {}}],
        )
        assert [b["type"] for b in content] == ["text", "tool_use"]

    def test_missing_text_index_places_text_before_first_tool(self):
        """Fallback placement matches the classic [thinking, text, tool_use]."""
        content = LLMProvider._ordered_assistant_content(
            "Checking.", [_tu(2)], thinking_blocks=[_tb(0)],
        )
        assert [b["type"] for b in content] == ["thinking", "text", "tool_use"]

    def test_empty_text_emits_no_text_block(self):
        content = LLMProvider._ordered_assistant_content(
            "   ", [_tu(1)], thinking_blocks=[_tb(0)], text_index=2,
        )
        assert [b["type"] for b in content] == ["thinking", "tool_use"]


# ---------------------------------------------------------------------------
# 2. Provider builders route through the assembler
# ---------------------------------------------------------------------------

class TestBuilderInterleavedOrder:

    @pytest.mark.parametrize("provider_fixture", ["bedrock_provider", "anthropic_provider"])
    def test_interleaved_order_survives_build(self, provider_fixture, request):
        provider = request.getfixturevalue(provider_fixture)
        msg = provider.build_assistant_message(
            "Working.",
            [_tu(2, tid="t1"), _tu(4, tid="t2")],
            thinking_blocks=[_tb(0), _tb(3)],
            text_index=1,
        )
        assert msg["role"] == "assistant"
        assert [b["type"] for b in msg["content"]] == [
            "thinking", "text", "tool_use", "thinking", "tool_use",
        ]
        for block in msg["content"]:
            assert "_index" not in block

    @pytest.mark.parametrize("provider_fixture", ["bedrock_provider", "anthropic_provider"])
    def test_legacy_call_shape_unchanged(self, provider_fixture, request):
        """The existing kwarg-free and metadata-free calls keep their shape."""
        provider = request.getfixturevalue(provider_fixture)
        msg = provider.build_assistant_message(
            "Checking.",
            [{"id": "t1", "name": "mcp_run_shell_command", "input": {"command": "ls"}}],
            thinking_blocks=[{"type": "thinking", "thinking": "p", "signature": "S"}],
        )
        assert [b["type"] for b in msg["content"]] == ["thinking", "text", "tool_use"]

    @pytest.mark.parametrize("provider_fixture", ["bedrock_provider", "anthropic_provider"])
    def test_tool_use_name_and_input_preserved(self, provider_fixture, request):
        provider = request.getfixturevalue(provider_fixture)
        msg = provider.build_assistant_message(
            "", [_tu(1, name="mcp_get_messages")], thinking_blocks=[_tb(0)],
        )
        tu_block = msg["content"][1]
        assert tu_block == {
            "type": "tool_use", "id": "t1", "name": "mcp_get_messages",
            "input": {"command": "ls"},
        }


# ---------------------------------------------------------------------------
# 3. TextDelta index plumbing
# ---------------------------------------------------------------------------

class TestTextDeltaIndex:

    def test_default_index_backward_compatible(self):
        assert TextDelta(content="x").index == 0

    @pytest.mark.asyncio
    async def test_anthropic_stream_reports_text_block_index(self, anthropic_provider):
        """Text after a thinking block arrives at content index 1, not 0."""
        events = [
            SimpleNamespace(type="content_block_start", index=0,
                            content_block=SimpleNamespace(type="thinking")),
            SimpleNamespace(type="content_block_delta", index=0,
                            delta=SimpleNamespace(type="thinking_delta", thinking="hm")),
            SimpleNamespace(type="content_block_delta", index=0,
                            delta=SimpleNamespace(type="signature_delta", signature="SIG")),
            SimpleNamespace(type="content_block_stop", index=0),
            SimpleNamespace(type="content_block_delta", index=1,
                            delta=SimpleNamespace(type="text_delta", text="hello")),
            SimpleNamespace(type="message_stop"),
        ]

        class _FakeStream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def __aiter__(self):
                async def _gen():
                    for ev in events:
                        yield ev
                return _gen()

        anthropic_provider.client = SimpleNamespace(
            messages=SimpleNamespace(stream=lambda **kw: _FakeStream())
        )
        out = [e async for e in anthropic_provider._do_stream({})]
        text_deltas = [e for e in out if isinstance(e, TextDelta)]
        assert text_deltas and text_deltas[0].index == 1
