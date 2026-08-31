"""
Tests for extended-thinking passback across a tool round-trip.

Background: the tool loop rebuilds the assistant turn each iteration from
``assistant_text`` + ``tool_use`` blocks.  Thinking was discarded, so the model
re-derived its plan from the tool_result alone on every round -- observable as
thinking summaries that restate the same root cause five times in one chain,
and billed as fresh reasoning tokens each time.

Passback echoes the COMPLETED, SIGNED thinking blocks back as the leading
content blocks of the assistant message carrying the tool_use.  The signature
is what lets the API verify the block; an unsigned block is rejected, so it
must not be sent at all.

These tests pin the parts that are cheap to get wrong and expensive to notice:

  1. Signature capture on both wire formats (raw Bedrock JSON, Anthropic SDK
     objects) -- the two are parsed by separate code paths.
  2. Block ORDER in the rebuilt turn (thinking must lead).
  3. Unsigned readable blocks are SURFACED with signature=None (not
     silently dropped — a silent drop gaps the turn and shifts every later
     block out of its signed position, the same "cannot be modified" 400);
     the assembler then echoes NO thinking for the turn.
  4. ``redacted_thinking`` is captured, because passback is all-or-nothing:
     dropping one reorders the turn.
  5. The five non-Anthropic providers are untouched and stay concrete.
  6. BedrockMantleProvider inherits the whole mechanism (it subclasses
     AnthropicDirectProvider) -- verified structurally, since a gateway
     round-trip cannot be tested offline.
  7. Size/token accounting includes echoed thinking, which is real request
     payload once it is replayed.
"""

import json
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.providers.base import (
    ProviderConfig,
    StreamEnd,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolUseEnd,
    ToolUseStart,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

THINKING_MODEL_CONFIG = {
    "family": "claude",
    "supports_thinking": True,
    "supports_adaptive_thinking": True,
}


@pytest.fixture
def bedrock_provider():
    """BedrockProvider with a mocked boto3 client."""
    with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client") as mock_get:
        mock_get.return_value = MagicMock()
        from app.providers.bedrock import BedrockProvider

        return BedrockProvider(
            model_id="anthropic.claude-sonnet-4-20250514-v1:0",
            model_config=dict(THINKING_MODEL_CONFIG),
            region="us-west-2",
        )


@pytest.fixture
def anthropic_provider():
    """AnthropicDirectProvider with a mocked anthropic module."""
    mock_anthropic = MagicMock()
    mock_anthropic.AsyncAnthropic.return_value = MagicMock()
    with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
        from app.providers.anthropic_direct import AnthropicDirectProvider

        yield AnthropicDirectProvider(
            model_id="claude-sonnet-4-20250514",
            model_config=dict(THINKING_MODEL_CONFIG),
            api_key="sk-test-key",
        )


@pytest.fixture
def basic_config():
    return ProviderConfig(max_output_tokens=8192, temperature=0.5, iteration=0)


# ---------------------------------------------------------------------------
# Wire-format helpers
# ---------------------------------------------------------------------------

def _bedrock_chunk(data: dict) -> dict:
    """Wrap a decoded event the way boto3 delivers it."""
    return {"chunk": {"bytes": json.dumps(data).encode("utf-8")}}


class _SDKEvent:
    """Duck-type of an Anthropic SDK stream event (attribute access, not dict)."""

    def __init__(self, type_: str, **kw):
        self.type = type_
        for k, v in kw.items():
            setattr(self, k, v)


class _SDKBlock:
    def __init__(self, type_: str, **kw):
        self.type = type_
        for k, v in kw.items():
            setattr(self, k, v)


class _SDKDelta:
    def __init__(self, type_: str, **kw):
        self.type = type_
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeAnthropicStream:
    """Async context manager yielding SDK-shaped events, as messages.stream()."""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for ev in self._events:
                yield ev

        return gen()


async def _drain_bedrock(provider, chunks, config):
    events = []
    async for ev in provider._parse_stream({"body": iter(chunks)}, config):
        events.append(ev)
    return events


async def _drain_anthropic(provider, sdk_events):
    provider.client.messages.stream = lambda **kw: _FakeAnthropicStream(sdk_events)
    events = []
    async for ev in provider._do_stream({}):
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# 1. Signature capture — Bedrock raw-JSON path
# ---------------------------------------------------------------------------

class TestBedrockSignatureCapture:
    """signature_delta was previously logged and dropped by design."""

    @pytest.mark.asyncio
    async def test_signed_thinking_block_emitted(self, bedrock_provider, basic_config):
        chunks = [
            _bedrock_chunk({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "thinking"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta",
                                      "thinking": "The toggle endpoint is a stub."}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "signature_delta", "signature": "SIG-ABC"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 0}),
            _bedrock_chunk({"type": "message_stop", "stop_reason": "end_turn"}),
        ]
        events = await _drain_bedrock(bedrock_provider, chunks, basic_config)

        blocks = [e for e in events if isinstance(e, ThinkingBlock)]
        assert len(blocks) == 1, "a signed thinking block must be emitted for passback"
        assert blocks[0].content == "The toggle endpoint is a stub."
        assert blocks[0].signature == "SIG-ABC"
        assert blocks[0].block_type == "thinking"
        assert blocks[0].index == 0

    @pytest.mark.asyncio
    async def test_thinking_delta_still_streams_for_display(self, bedrock_provider, basic_config):
        """Passback must not cannibalise the display channel."""
        chunks = [
            _bedrock_chunk({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "thinking"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": "step one"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "signature_delta", "signature": "S"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 0}),
            _bedrock_chunk({"type": "message_stop", "stop_reason": "end_turn"}),
        ]
        events = await _drain_bedrock(bedrock_provider, chunks, basic_config)

        deltas = [e for e in events if isinstance(e, ThinkingDelta)]
        assert [d.content for d in deltas] == ["step one"]

    @pytest.mark.asyncio
    async def test_multi_delta_accumulates_in_order(self, bedrock_provider, basic_config):
        chunks = [
            _bedrock_chunk({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "thinking"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": "alpha "}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": "beta "}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": "gamma"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "signature_delta", "signature": "S"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 0}),
        ]
        events = await _drain_bedrock(bedrock_provider, chunks, basic_config)
        block = [e for e in events if isinstance(e, ThinkingBlock)][0]
        assert block.content == "alpha beta gamma"

    @pytest.mark.asyncio
    async def test_unsigned_thinking_block_surfaced_not_silently_dropped(
            self, bedrock_provider, basic_config):
        """No signature => surfaced with signature=None, never sent to the API.

        The parser used to drop the block silently, which GAPPED the turn:
        every later block shifted out of its signed position — the same
        "cannot be modified" 400 passback ordering exists to prevent.  It now
        surfaces the block so the assembler (which sees the whole turn) can
        skip ALL thinking passback for the turn instead.
        """
        chunks = [
            _bedrock_chunk({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "thinking"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": "unsigned"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 0}),
            _bedrock_chunk({"type": "message_stop", "stop_reason": "end_turn"}),
        ]
        events = await _drain_bedrock(bedrock_provider, chunks, basic_config)

        blocks = [e for e in events if isinstance(e, ThinkingBlock)]
        assert len(blocks) == 1
        assert blocks[0].signature is None
        # The live contract is enforced at the assembler: an unsigned block
        # means NO thinking is echoed for the turn.
        from app.providers.base import LLMProvider
        content = LLMProvider._ordered_assistant_content(
            "t", [], thinking_blocks=[{
                "type": "thinking", "thinking": blocks[0].content,
                "signature": blocks[0].signature, "_index": blocks[0].index}])
        assert all(b["type"] != "thinking" for b in content)
        # ...and it still reached the display channel.
        assert [e.content for e in events if isinstance(e, ThinkingDelta)] == ["unsigned"]

    @pytest.mark.asyncio
    async def test_redacted_thinking_captured_with_payload(self, bedrock_provider, basic_config):
        """Passback is all-or-nothing: a dropped redacted block reorders the turn."""
        chunks = [
            _bedrock_chunk({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "redacted_thinking",
                                              "data": "OPAQUE-BYTES"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 0}),
            _bedrock_chunk({"type": "message_stop", "stop_reason": "end_turn"}),
        ]
        events = await _drain_bedrock(bedrock_provider, chunks, basic_config)

        blocks = [e for e in events if isinstance(e, ThinkingBlock)]
        assert len(blocks) == 1
        assert blocks[0].block_type == "redacted_thinking"
        assert blocks[0].data == "OPAQUE-BYTES"
        assert blocks[0].signature is None

    @pytest.mark.asyncio
    async def test_signature_without_open_block_is_inert(self, bedrock_provider, basic_config):
        """A stray signature_delta must not fabricate a block or raise."""
        chunks = [
            _bedrock_chunk({"type": "content_block_delta", "index": 7,
                            "delta": {"type": "signature_delta", "signature": "ORPHAN"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 7}),
            _bedrock_chunk({"type": "message_stop", "stop_reason": "end_turn"}),
        ]
        events = await _drain_bedrock(bedrock_provider, chunks, basic_config)
        assert not [e for e in events if isinstance(e, ThinkingBlock)]

    @pytest.mark.asyncio
    async def test_thinking_does_not_disturb_tool_use_parsing(self, bedrock_provider, basic_config):
        """The realistic shape: thinking, then text, then a tool call."""
        chunks = [
            _bedrock_chunk({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "thinking"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": "plan"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "signature_delta", "signature": "S"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 0}),
            _bedrock_chunk({"type": "content_block_start", "index": 1,
                            "content_block": {"type": "text"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 1,
                            "delta": {"type": "text_delta", "text": "Checking."}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 1}),
            _bedrock_chunk({"type": "content_block_start", "index": 2,
                            "content_block": {"type": "tool_use", "id": "t1",
                                              "name": "mcp_run_shell_command"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 2,
                            "delta": {"type": "input_json_delta",
                                      "partial_json": '{"command":"ls"}'}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 2}),
            _bedrock_chunk({"type": "message_stop", "stop_reason": "tool_use"}),
        ]
        events = await _drain_bedrock(bedrock_provider, chunks, basic_config)

        assert len([e for e in events if isinstance(e, ThinkingBlock)]) == 1
        assert [e.content for e in events if isinstance(e, TextDelta)] == ["Checking."]
        ends = [e for e in events if isinstance(e, ToolUseEnd)]
        assert len(ends) == 1
        assert ends[0].id == "t1"
        assert ends[0].input == {"command": "ls"}


# ---------------------------------------------------------------------------
# 2. Signature capture — Anthropic SDK path (inherited by mantle)
# ---------------------------------------------------------------------------

class TestAnthropicSignatureCapture:
    """This path had no signature_delta branch at all before the change."""

    @pytest.mark.asyncio
    async def test_signed_thinking_block_emitted(self, anthropic_provider):
        sdk_events = [
            _SDKEvent("content_block_start", index=0, content_block=_SDKBlock("thinking")),
            _SDKEvent("content_block_delta", index=0,
                      delta=_SDKDelta("thinking_delta", thinking="mirror the favorites approach")),
            _SDKEvent("content_block_delta", index=0,
                      delta=_SDKDelta("signature_delta", signature="SIG-XYZ")),
            _SDKEvent("content_block_stop", index=0),
            _SDKEvent("message_stop"),
        ]
        events = await _drain_anthropic(anthropic_provider, sdk_events)

        blocks = [e for e in events if isinstance(e, ThinkingBlock)]
        assert len(blocks) == 1
        assert blocks[0].content == "mirror the favorites approach"
        assert blocks[0].signature == "SIG-XYZ"

    @pytest.mark.asyncio
    async def test_thinking_delta_still_streams_for_display(self, anthropic_provider):
        sdk_events = [
            _SDKEvent("content_block_start", index=0, content_block=_SDKBlock("thinking")),
            _SDKEvent("content_block_delta", index=0,
                      delta=_SDKDelta("thinking_delta", thinking="visible")),
            _SDKEvent("content_block_delta", index=0,
                      delta=_SDKDelta("signature_delta", signature="S")),
            _SDKEvent("content_block_stop", index=0),
            _SDKEvent("message_stop"),
        ]
        events = await _drain_anthropic(anthropic_provider, sdk_events)
        assert [e.content for e in events if isinstance(e, ThinkingDelta)] == ["visible"]

    @pytest.mark.asyncio
    async def test_unsigned_thinking_block_surfaced_not_silently_dropped(
            self, anthropic_provider):
        """Surfaced with signature=None; the assembler skips the turn's passback."""
        sdk_events = [
            _SDKEvent("content_block_start", index=0, content_block=_SDKBlock("thinking")),
            _SDKEvent("content_block_delta", index=0,
                      delta=_SDKDelta("thinking_delta", thinking="unsigned")),
            _SDKEvent("content_block_stop", index=0),
            _SDKEvent("message_stop"),
        ]
        events = await _drain_anthropic(anthropic_provider, sdk_events)
        blocks = [e for e in events if isinstance(e, ThinkingBlock)]
        assert len(blocks) == 1
        assert blocks[0].signature is None
        from app.providers.base import LLMProvider
        content = LLMProvider._ordered_assistant_content(
            "t", [], thinking_blocks=[{
                "type": "thinking", "thinking": blocks[0].content,
                "signature": blocks[0].signature, "_index": blocks[0].index}])
        assert all(b["type"] != "thinking" for b in content)

    @pytest.mark.asyncio
    async def test_redacted_thinking_captured_with_payload(self, anthropic_provider):
        sdk_events = [
            _SDKEvent("content_block_start", index=0,
                      content_block=_SDKBlock("redacted_thinking", data="ENCRYPTED")),
            _SDKEvent("content_block_stop", index=0),
            _SDKEvent("message_stop"),
        ]
        events = await _drain_anthropic(anthropic_provider, sdk_events)

        blocks = [e for e in events if isinstance(e, ThinkingBlock)]
        assert len(blocks) == 1
        assert blocks[0].block_type == "redacted_thinking"
        assert blocks[0].data == "ENCRYPTED"

    @pytest.mark.asyncio
    async def test_thinking_does_not_disturb_tool_use_parsing(self, anthropic_provider):
        sdk_events = [
            _SDKEvent("content_block_start", index=0, content_block=_SDKBlock("thinking")),
            _SDKEvent("content_block_delta", index=0,
                      delta=_SDKDelta("thinking_delta", thinking="plan")),
            _SDKEvent("content_block_delta", index=0,
                      delta=_SDKDelta("signature_delta", signature="S")),
            _SDKEvent("content_block_stop", index=0),
            _SDKEvent("content_block_start", index=1,
                      content_block=_SDKBlock("tool_use", id="t9", name="mcp_file_read")),
            _SDKEvent("content_block_delta", index=1,
                      delta=_SDKDelta("input_json_delta", partial_json='{"path":"a.py"}')),
            _SDKEvent("content_block_stop", index=1),
            _SDKEvent("message_stop"),
        ]
        events = await _drain_anthropic(anthropic_provider, sdk_events)

        assert len([e for e in events if isinstance(e, ThinkingBlock)]) == 1
        starts = [e for e in events if isinstance(e, ToolUseStart)]
        ends = [e for e in events if isinstance(e, ToolUseEnd)]
        assert len(starts) == 1 and len(ends) == 1
        assert ends[0].input == {"path": "a.py"}


# ---------------------------------------------------------------------------
# 3. Block ORDER in the rebuilt assistant turn
# ---------------------------------------------------------------------------

TB_SIGNED = {"type": "thinking", "thinking": "reasoning", "signature": "SIG"}
TB_REDACTED = {"type": "redacted_thinking", "data": "OPAQUE"}
TOOL_USES = [{"id": "t1", "name": "mcp_run_shell_command", "input": {"command": "ls"}}]


def _providers_supporting_passback():
    """The two implementations that carry Anthropic block format."""
    with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client") as g:
        g.return_value = MagicMock()
        from app.providers.bedrock import BedrockProvider
        bed = BedrockProvider(
            model_id="anthropic.claude-sonnet-4-20250514-v1:0",
            model_config=dict(THINKING_MODEL_CONFIG), region="us-west-2")

    mock_anthropic = MagicMock()
    mock_anthropic.AsyncAnthropic.return_value = MagicMock()
    with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        ant = AnthropicDirectProvider(
            model_id="claude-sonnet-4-20250514",
            model_config=dict(THINKING_MODEL_CONFIG), api_key="sk-test")

    return [pytest.param(bed, id="bedrock"), pytest.param(ant, id="anthropic_direct")]


class TestAssistantMessageBlockOrder:
    """The API requires thinking to PRECEDE text and tool_use, and rejects
    them out of order.  Order is the whole contract here."""

    @pytest.mark.parametrize("provider", _providers_supporting_passback())
    def test_thinking_leads_the_turn(self, provider):
        msg = provider.build_assistant_message(
            "Checking.", TOOL_USES, thinking_blocks=[TB_SIGNED, TB_REDACTED])
        assert [b["type"] for b in msg["content"]] == [
            "thinking", "redacted_thinking", "text", "tool_use"]

    @pytest.mark.parametrize("provider", _providers_supporting_passback())
    def test_thinking_blocks_passed_through_verbatim(self, provider):
        """The signature must survive untouched or the echo is rejected."""
        msg = provider.build_assistant_message("x", [], thinking_blocks=[TB_SIGNED])
        assert msg["content"][0] == TB_SIGNED

    @pytest.mark.parametrize("provider", _providers_supporting_passback())
    def test_omitting_kwarg_reproduces_legacy_shape(self, provider):
        """The path every non-Anthropic provider still takes."""
        msg = provider.build_assistant_message("Checking.", TOOL_USES)
        assert [b["type"] for b in msg["content"]] == ["text", "tool_use"]

    @pytest.mark.parametrize("provider", _providers_supporting_passback())
    def test_empty_list_reproduces_legacy_shape(self, provider):
        msg = provider.build_assistant_message("Checking.", TOOL_USES, thinking_blocks=[])
        assert [b["type"] for b in msg["content"]] == ["text", "tool_use"]

    @pytest.mark.parametrize("provider", _providers_supporting_passback())
    def test_thinking_only_turn_has_no_text_block(self, provider):
        """display='omitted' models can produce reasoning with no visible text."""
        msg = provider.build_assistant_message("", TOOL_USES, thinking_blocks=[TB_SIGNED])
        assert [b["type"] for b in msg["content"]] == ["thinking", "tool_use"]

    @pytest.mark.parametrize("provider", _providers_supporting_passback())
    def test_multiple_thinking_blocks_keep_arrival_order(self, provider):
        a = {"type": "thinking", "thinking": "first", "signature": "S1"}
        b = {"type": "thinking", "thinking": "second", "signature": "S2"}
        msg = provider.build_assistant_message("t", [], thinking_blocks=[a, b])
        assert [blk.get("thinking") for blk in msg["content"][:2]] == ["first", "second"]


# ---------------------------------------------------------------------------
# 4. Capability gating
# ---------------------------------------------------------------------------

class TestPassbackCapabilityFlag:
    @pytest.mark.parametrize("provider", _providers_supporting_passback())
    def test_reported_when_model_thinks(self, provider):
        assert provider.supports_feature("thinking_passback") is True

    def test_not_reported_for_non_thinking_model(self):
        with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client") as g:
            g.return_value = MagicMock()
            from app.providers.bedrock import BedrockProvider
            p = BedrockProvider(
                model_id="anthropic.claude-3-haiku-20240307-v1:0",
                model_config={"family": "claude", "supports_thinking": False,
                              "supports_adaptive_thinking": False},
                region="us-west-2")
        assert p.supports_feature("thinking_passback") is False

    def test_adaptive_only_model_still_reports(self):
        """Adaptive thinking alone is sufficient -- it is the mode that regressed."""
        with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client") as g:
            g.return_value = MagicMock()
            from app.providers.bedrock import BedrockProvider
            p = BedrockProvider(
                model_id="x", region="us-west-2",
                model_config={"supports_thinking": False,
                              "supports_adaptive_thinking": True})
        assert p.supports_feature("thinking_passback") is True

    def test_non_anthropic_providers_do_not_claim_support(self):
        """They would silently drop the kwarg's blocks, so they must not opt in."""
        from app.providers.nova_bedrock import NovaBedrockProvider
        p = NovaBedrockProvider.__new__(NovaBedrockProvider)
        p.model_config = {"supports_thinking": True}
        assert p.supports_feature("thinking_passback") is False


# ---------------------------------------------------------------------------
# 5. The five untouched providers stay concrete
# ---------------------------------------------------------------------------

class TestUntouchedProvidersUnaffected:
    """Adding an OPTIONAL kwarg to the ABC must not force implementers to
    accept it, or five providers become abstract and fail to instantiate."""

    PROVIDERS = [
        ("app.providers.openai_direct", "OpenAIDirectProvider"),
        ("app.providers.openai_bedrock", "OpenAIBedrockProvider"),
        ("app.providers.openai_responses_mantle", "OpenAIResponsesMantleProvider"),
        ("app.providers.google_direct", "GoogleDirectProvider"),
        ("app.providers.nova_bedrock", "NovaBedrockProvider"),
    ]

    @pytest.mark.parametrize("mod,cls_name", PROVIDERS)
    def test_no_abstract_methods_left(self, mod, cls_name):
        cls = getattr(__import__(mod, fromlist=[cls_name]), cls_name)
        assert not getattr(cls, "__abstractmethods__", set()), (
            f"{cls_name} became abstract -- the ABC kwarg must stay optional")

    @pytest.mark.parametrize("mod,cls_name", PROVIDERS)
    def test_two_arg_signature_preserved(self, mod, cls_name):
        import inspect
        cls = getattr(__import__(mod, fromlist=[cls_name]), cls_name)
        params = list(inspect.signature(cls.build_assistant_message).parameters)[1:]
        assert params[:2] == ["text", "tool_uses"]


# ---------------------------------------------------------------------------
# 6. Mantle inherits the mechanism
# ---------------------------------------------------------------------------

class TestMantleInheritsPassback:
    """BedrockMantleProvider subclasses AnthropicDirectProvider and overrides
    only __init__/_estimate_request_tokens/provider_name.  Whether the GATEWAY
    accepts round-tripped signed blocks cannot be tested offline; what is
    testable is that mantle is not silently excluded by an override."""

    def test_inherits_message_builder(self):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        from app.providers.bedrock_mantle import BedrockMantleProvider
        assert (BedrockMantleProvider.build_assistant_message
                is AnthropicDirectProvider.build_assistant_message)

    def test_inherits_stream_parser(self):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        from app.providers.bedrock_mantle import BedrockMantleProvider
        assert BedrockMantleProvider._do_stream is AnthropicDirectProvider._do_stream

    def test_inherits_capability_map(self):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        from app.providers.bedrock_mantle import BedrockMantleProvider
        assert (BedrockMantleProvider.supports_feature
                is AnthropicDirectProvider.supports_feature)

    def test_accepts_thinking_blocks_kwarg(self):
        import inspect
        from app.providers.bedrock_mantle import BedrockMantleProvider
        sig = inspect.signature(BedrockMantleProvider.build_assistant_message)
        assert "thinking_blocks" in sig.parameters


# ---------------------------------------------------------------------------
# 7. Size / token accounting
# ---------------------------------------------------------------------------

class TestEchoedThinkingIsAccounted:
    """Echoed thinking is real request payload on the turn it is replayed in.
    Counting it as zero made a thinking+tool_use turn look free, surfacing as
    unexplained drift in the bucketed estimate-vs-actual diagnostic."""

    @staticmethod
    def _turn(thinking_text="x" * 100):
        return {"role": "assistant", "content": [
            {"type": "thinking", "thinking": thinking_text, "signature": "S"},
            {"type": "tool_use", "id": "t1", "name": "n", "input": {}}]}

    def test_char_counter_includes_thinking(self):
        from app.streaming_tool_executor import StreamingToolExecutor as S
        with_thinking = S._count_conversation_chars([self._turn()])
        without = S._count_conversation_chars([{
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "n", "input": {}}]}])
        assert with_thinking - without == 100

    def test_token_estimator_includes_thinking(self):
        from app.streaming_tool_executor import StreamingToolExecutor as S
        tokens = S._estimate_message_tokens(self._turn(), None, False, "claude")
        assert tokens > 0, "echoed thinking must not estimate as free"
        assert tokens >= 100 // 4

    def test_redacted_thinking_deliberately_not_counted(self):
        """Its payload is opaque ciphertext whose length has no reliable
        relationship to billed tokens -- absent beats confidently wrong.
        This pins the decision so a future change is deliberate."""
        from app.streaming_tool_executor import StreamingToolExecutor as S
        msg = {"role": "assistant", "content": [
            {"type": "redacted_thinking", "data": "z" * 400}]}
        assert S._count_conversation_chars([msg]) == 0
        assert S._estimate_message_tokens(msg, None, False, "claude") == 0

    def test_thinking_only_turn_is_not_treated_as_empty(self):
        """A display='omitted' turn can be thinking + tool_use with no text.
        If judged empty it would be dropped or trigger empty-response recovery."""
        from app.streaming_tool_executor import StreamingToolExecutor as S
        assert S._is_empty_content(self._turn()["content"]) is False

    def test_thinking_survives_older_turn_compaction(self):
        """Refusal-recovery truncates TEXT blocks; it must not corrupt a
        signed thinking block, whose signature must match its content."""
        from app.streaming_tool_executor import StreamingToolExecutor as S
        conv = [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "y" * 5000, "signature": "SIG"},
                {"type": "text", "text": "z" * 5000}]},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "keep1"},
            {"role": "assistant", "content": "keep2"},
        ]
        S._compact_older_assistant_turns(conv)
        thinking_block = conv[0]["content"][0]
        assert thinking_block["thinking"] == "y" * 5000, (
            "truncating a signed thinking block would invalidate its signature")
        assert thinking_block["signature"] == "SIG"


# ---------------------------------------------------------------------------
# 8. Orchestrator wiring — collection and gating
# ---------------------------------------------------------------------------

class _RecordingProvider:
    """Records what build_assistant_message actually received."""

    provider_name = "test_recording"

    def __init__(self, events, supports_passback=True):
        self._events = events
        self._supports = supports_passback
        self.calls: List[Dict[str, Any]] = []

    async def stream_response(self, *a, **kw):
        for ev in self._events:
            yield ev

    def supports_feature(self, name):
        return self._supports if name == "thinking_passback" else False

    def build_assistant_message(self, text, tool_uses, thinking_blocks=None,
                                text_index=None):
        self.calls.append({"text": text, "tool_uses": tool_uses,
                           "thinking_blocks": thinking_blocks,
                           "text_index": text_index})
        content = list(thinking_blocks or [])
        if text.strip():
            content.append({"type": "text", "text": text})
        return {"role": "assistant", "content": content}

    def build_tool_result_message(self, results):
        return {"role": "user", "content": "ok"}


def _make_executor(provider):
    from app.streaming_tool_executor import StreamingToolExecutor

    with patch.object(StreamingToolExecutor, "__init__", lambda self, **kw: None):
        exe = StreamingToolExecutor.__new__(StreamingToolExecutor)
        exe.provider = provider
        exe.model_id = "anthropic.claude-sonnet-4-20250514-v1:0"
        exe.model_config = {
            "family": "claude",
            "supports_thinking": True,
            "supports_adaptive_thinking": True,
            "supports_assistant_prefill": True,
            "max_output_tokens": 8192,
        }
        exe.bedrock = None
        return exe


async def _run_once(exe, conv_id):
    collected = []
    with patch.object(exe, "_load_and_prepare_tools",
                      return_value=([], [], set(), set(), set())):
        async for ev in exe.stream_with_tools(
                [{"role": "user", "content": "test"}], conversation_id=conv_id):
            collected.append(ev)
    return collected


@pytest.mark.timeout(120)
class TestOrchestratorPassbackWiring:

    @pytest.mark.asyncio
    async def test_signed_block_is_echoed_in_assistant_turn(self):
        provider = _RecordingProvider([
            ThinkingBlock(content="the plan", signature="SIG", index=0),
            TextDelta(content="Answer."),
            StreamEnd(stop_reason="end_turn"),
        ])
        await _run_once(_make_executor(provider), "tp-echo")

        assert provider.calls, "build_assistant_message was never called"
        passed = provider.calls[-1]["thinking_blocks"]
        # '_index' is ordering metadata attached by the orchestrator so the
        # provider can rebuild the turn in original block order (interleaved
        # thinking); the provider strips it before the block goes to the API.
        assert passed == [{"type": "thinking", "thinking": "the plan",
                           "signature": "SIG", "_index": 0}]

    @pytest.mark.asyncio
    async def test_redacted_block_shape_is_preserved(self):
        provider = _RecordingProvider([
            ThinkingBlock(block_type="redacted_thinking", data="OPAQUE", index=0),
            TextDelta(content="Answer."),
            StreamEnd(stop_reason="end_turn"),
        ])
        await _run_once(_make_executor(provider), "tp-redacted")

        passed = provider.calls[-1]["thinking_blocks"]
        assert passed == [{"type": "redacted_thinking", "data": "OPAQUE",
                           "_index": 0}]

    @pytest.mark.asyncio
    async def test_arrival_order_preserved(self):
        provider = _RecordingProvider([
            ThinkingBlock(content="first", signature="S1", index=0),
            ThinkingBlock(block_type="redacted_thinking", data="MID", index=1),
            ThinkingBlock(content="last", signature="S2", index=2),
            TextDelta(content="Answer."),
            StreamEnd(stop_reason="end_turn"),
        ])
        await _run_once(_make_executor(provider), "tp-order")

        passed = provider.calls[-1]["thinking_blocks"]
        assert [b["type"] for b in passed] == [
            "thinking", "redacted_thinking", "thinking"]
        assert passed[0]["thinking"] == "first"
        assert passed[2]["thinking"] == "last"

    @pytest.mark.asyncio
    async def test_kwarg_omitted_when_provider_lacks_support(self):
        """A provider whose format cannot carry the blocks must not receive
        them -- it would drop them silently and desync the turn."""
        provider = _RecordingProvider([
            ThinkingBlock(content="the plan", signature="SIG", index=0),
            TextDelta(content="Answer."),
            StreamEnd(stop_reason="end_turn"),
        ], supports_passback=False)
        await _run_once(_make_executor(provider), "tp-unsupported")

        assert provider.calls[-1]["thinking_blocks"] is None

    @pytest.mark.asyncio
    async def test_no_blocks_means_no_kwarg(self):
        """ThinkingDelta alone (no completed block) must not trigger passback."""
        provider = _RecordingProvider([
            ThinkingDelta(content="display only"),
            TextDelta(content="Answer."),
            StreamEnd(stop_reason="end_turn"),
        ])
        await _run_once(_make_executor(provider), "tp-none")

        assert provider.calls[-1]["thinking_blocks"] is None

    @pytest.mark.asyncio
    async def test_kill_switch_disables_passback(self):
        provider = _RecordingProvider([
            ThinkingBlock(content="the plan", signature="SIG", index=0),
            TextDelta(content="Answer."),
            StreamEnd(stop_reason="end_turn"),
        ])
        exe = _make_executor(provider)
        import app.streaming_tool_executor as mod
        _real = mod.ziya_env

        def _fake(name, *a, **kw):
            if name == "ZIYA_DISABLE_THINKING_PASSBACK":
                return "1"
            return _real(name, *a, **kw)

        with patch.object(mod, "ziya_env", _fake):
            await _run_once(exe, "tp-killswitch")

        assert provider.calls[-1]["thinking_blocks"] is None, (
            "ZIYA_DISABLE_THINKING_PASSBACK must fully disable the echo")

    @pytest.mark.asyncio
    async def test_completed_block_is_not_emitted_as_display_text(self):
        """ThinkingDelta already streamed the visible text; ThinkingBlock must
        not double it into the user-facing stream."""
        provider = _RecordingProvider([
            ThinkingDelta(content="visible reasoning"),
            ThinkingBlock(content="visible reasoning", signature="SIG", index=0),
            TextDelta(content="Answer."),
            StreamEnd(stop_reason="end_turn"),
        ])
        collected = await _run_once(_make_executor(provider), "tp-nodouble")

        text = "".join(e.get("content", "") for e in collected
                       if e.get("type") == "text")
        assert "visible reasoning" not in text
        # Count only CONTENT-BEARING thinking events: the stream legitimately
        # also carries a {'type': 'thinking', 'done': True} close marker when
        # the model transitions to answer text (the frontend's collapse
        # signal).  The double-display this test guards against is the
        # completed ThinkingBlock re-emitting its content, so the marker —
        # which carries none — must not be counted.  (The original == 1
        # assertion counted it and failed even at the commit that added it.)
        thinking_content_events = [
            e for e in collected
            if e.get("type") == "thinking" and e.get("content")
        ]
        assert len(thinking_content_events) == 1, (
            "the completed block must not produce a second thinking event")
        assert thinking_content_events[0]["content"] == "visible reasoning"
