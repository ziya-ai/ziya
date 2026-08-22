"""
Tests for extended-thinking passback — echoing signed thinking blocks back
inside a tool chain so the model resumes its reasoning instead of re-deriving
it from the tool_result on every iteration.

Covers the contract at three levels:

  1. Capture — the stream parsers must accumulate a thinking block AND its
     closing ``signature_delta``, then emit a ``ThinkingBlock`` event.  Two
     wire formats, so two parsers: Bedrock's raw JSON chunks and the Anthropic
     SDK's event objects.  ``BedrockMantleProvider`` inherits the latter.
  2. Emission — ``build_assistant_message`` must place thinking blocks BEFORE
     text and tool_use, because the API rejects them out of order.
  3. Accounting — echoed thinking is real request payload, so the size and
     token estimators must count it.

The negative cases carry the weight here: an UNSIGNED thinking block must be
dropped (the API rejects a block whose signature is missing), and providers
that do not advertise ``thinking_passback`` must never be handed the kwarg.
"""

import json
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.providers.base import (
    ProviderConfig,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolUseEnd,
    ToolUseStart,
    StreamEnd,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def bedrock_provider():
    """BedrockProvider with a mocked boto3 client."""
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
    """AnthropicDirectProvider built without touching the anthropic SDK.

    Constructed via __new__ so the test does not depend on the real client;
    _do_stream only needs ``self.client.messages.stream``, which each test
    supplies.
    """
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


@pytest.fixture
def basic_config():
    return ProviderConfig(max_output_tokens=8192, temperature=0.5, iteration=0)


# ---------------------------------------------------------------------------
# Wire-format helpers
# ---------------------------------------------------------------------------

def _bedrock_chunk(data: dict) -> dict:
    """Wrap a decoded chunk the way boto3 delivers it."""
    return {"chunk": {"bytes": json.dumps(data).encode("utf-8")}}


class _FakeAnthropicStream:
    """Async context manager + async iterable, mirroring the SDK's stream()."""

    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def _gen():
            for ev in self._events:
                yield ev
        return _gen()


def _attach_stream(provider, events):
    provider.client = SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kw: _FakeAnthropicStream(events))
    )


# Anthropic SDK event shapes (attribute access, not dict access)
def _a_cb_start(idx, block):
    return SimpleNamespace(type="content_block_start", index=idx, content_block=block)


def _a_delta(idx, delta):
    return SimpleNamespace(type="content_block_delta", index=idx, delta=delta)


def _a_cb_stop(idx):
    return SimpleNamespace(type="content_block_stop", index=idx)


def _a_msg_stop():
    return SimpleNamespace(type="message_stop")


# ---------------------------------------------------------------------------
# 1. Capture — Bedrock raw-JSON parser
# ---------------------------------------------------------------------------

class TestBedrockThinkingCapture:
    """_parse_stream must turn thinking + signature_delta into a ThinkingBlock."""

    @pytest.mark.asyncio
    async def test_signed_thinking_block_emitted(self, bedrock_provider, basic_config):
        chunks = [
            _bedrock_chunk({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "thinking"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": "plan: "}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": "fix the stub"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "signature_delta", "signature": "SIG-abc"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 0}),
            _bedrock_chunk({"type": "message_stop"}),
        ]
        events = [e async for e in bedrock_provider._parse_stream({"body": iter(chunks)}, basic_config)]

        blocks = [e for e in events if isinstance(e, ThinkingBlock)]
        assert len(blocks) == 1
        assert blocks[0].content == "plan: fix the stub"
        assert blocks[0].signature == "SIG-abc"
        assert blocks[0].block_type == "thinking"
        assert blocks[0].index == 0

    @pytest.mark.asyncio
    async def test_thinking_deltas_still_stream_for_display(self, bedrock_provider, basic_config):
        """Capture must not consume the display channel."""
        chunks = [
            _bedrock_chunk({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "thinking"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": "visible"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "signature_delta", "signature": "S"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 0}),
            _bedrock_chunk({"type": "message_stop"}),
        ]
        events = [e async for e in bedrock_provider._parse_stream({"body": iter(chunks)}, basic_config)]

        deltas = [e for e in events if isinstance(e, ThinkingDelta)]
        assert [d.content for d in deltas] == ["visible"]

    @pytest.mark.asyncio
    async def test_unsigned_thinking_block_dropped(self, bedrock_provider, basic_config):
        """No signature_delta means the block cannot be echoed back."""
        chunks = [
            _bedrock_chunk({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "thinking"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": "unsigned"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 0}),
            _bedrock_chunk({"type": "message_stop"}),
        ]
        events = [e async for e in bedrock_provider._parse_stream({"body": iter(chunks)}, basic_config)]

        assert [e for e in events if isinstance(e, ThinkingBlock)] == []
        # ...but the text still reached the UI.
        assert any(isinstance(e, ThinkingDelta) for e in events)

    @pytest.mark.asyncio
    async def test_redacted_thinking_captured(self, bedrock_provider, basic_config):
        """Passback is all-or-nothing: a dropped redacted block reorders the turn."""
        chunks = [
            _bedrock_chunk({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "redacted_thinking", "data": "OPAQUE"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 0}),
            _bedrock_chunk({"type": "message_stop"}),
        ]
        events = [e async for e in bedrock_provider._parse_stream({"body": iter(chunks)}, basic_config)]

        blocks = [e for e in events if isinstance(e, ThinkingBlock)]
        assert len(blocks) == 1
        assert blocks[0].block_type == "redacted_thinking"
        assert blocks[0].data == "OPAQUE"

    @pytest.mark.asyncio
    async def test_multiple_blocks_keep_arrival_order(self, bedrock_provider, basic_config):
        chunks = [
            _bedrock_chunk({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "thinking"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "thinking_delta", "thinking": "first"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "signature_delta", "signature": "S0"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 0}),
            _bedrock_chunk({"type": "content_block_start", "index": 1,
                            "content_block": {"type": "thinking"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 1,
                            "delta": {"type": "thinking_delta", "thinking": "second"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 1,
                            "delta": {"type": "signature_delta", "signature": "S1"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 1}),
            _bedrock_chunk({"type": "message_stop"}),
        ]
        events = [e async for e in bedrock_provider._parse_stream({"body": iter(chunks)}, basic_config)]

        blocks = [e for e in events if isinstance(e, ThinkingBlock)]
        assert [(b.content, b.signature) for b in blocks] == [("first", "S0"), ("second", "S1")]

    @pytest.mark.asyncio
    async def test_tool_use_unaffected_by_capture(self, bedrock_provider, basic_config):
        """A thinking block sharing the stream with a tool must not disturb it."""
        chunks = [
            _bedrock_chunk({"type": "content_block_start", "index": 0,
                            "content_block": {"type": "thinking"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 0,
                            "delta": {"type": "signature_delta", "signature": "S"}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 0}),
            _bedrock_chunk({"type": "content_block_start", "index": 1,
                            "content_block": {"type": "tool_use", "id": "t1", "name": "read_file"}}),
            _bedrock_chunk({"type": "content_block_delta", "index": 1,
                            "delta": {"type": "input_json_delta", "partial_json": '{"path":"/x"}'}}),
            _bedrock_chunk({"type": "content_block_stop", "index": 1}),
            _bedrock_chunk({"type": "message_stop"}),
        ]
        events = [e async for e in bedrock_provider._parse_stream({"body": iter(chunks)}, basic_config)]

        ends = [e for e in events if isinstance(e, ToolUseEnd)]
        assert len(ends) == 1
        assert ends[0].id == "t1"
        assert ends[0].input == {"path": "/x"}
        assert any(isinstance(e, ToolUseStart) for e in events)


# ---------------------------------------------------------------------------
# 2. Capture — Anthropic SDK parser (inherited by BedrockMantleProvider)
# ---------------------------------------------------------------------------

class TestAnthropicThinkingCapture:
    """_do_stream had no signature_delta branch at all before this change."""

    @pytest.mark.asyncio
    async def test_signed_thinking_block_emitted(self, anthropic_provider):
        _attach_stream(anthropic_provider, [
            _a_cb_start(0, SimpleNamespace(type="thinking")),
            _a_delta(0, SimpleNamespace(type="thinking_delta", thinking="reason ")),
            _a_delta(0, SimpleNamespace(type="thinking_delta", thinking="here")),
            _a_delta(0, SimpleNamespace(type="signature_delta", signature="SIG-xyz")),
            _a_cb_stop(0),
            _a_msg_stop(),
        ])
        events = [e async for e in anthropic_provider._do_stream({})]

        blocks = [e for e in events if isinstance(e, ThinkingBlock)]
        assert len(blocks) == 1
        assert blocks[0].content == "reason here"
        assert blocks[0].signature == "SIG-xyz"

    @pytest.mark.asyncio
    async def test_thinking_deltas_still_stream_for_display(self, anthropic_provider):
        _attach_stream(anthropic_provider, [
            _a_cb_start(0, SimpleNamespace(type="thinking")),
            _a_delta(0, SimpleNamespace(type="thinking_delta", thinking="shown")),
            _a_delta(0, SimpleNamespace(type="signature_delta", signature="S")),
            _a_cb_stop(0),
            _a_msg_stop(),
        ])
        events = [e async for e in anthropic_provider._do_stream({})]

        assert [e.content for e in events if isinstance(e, ThinkingDelta)] == ["shown"]

    @pytest.mark.asyncio
    async def test_unsigned_thinking_block_dropped(self, anthropic_provider):
        _attach_stream(anthropic_provider, [
            _a_cb_start(0, SimpleNamespace(type="thinking")),
            _a_delta(0, SimpleNamespace(type="thinking_delta", thinking="unsigned")),
            _a_cb_stop(0),
            _a_msg_stop(),
        ])
        events = [e async for e in anthropic_provider._do_stream({})]

        assert [e for e in events if isinstance(e, ThinkingBlock)] == []

    @pytest.mark.asyncio
    async def test_redacted_thinking_captured(self, anthropic_provider):
        _attach_stream(anthropic_provider, [
            _a_cb_start(0, SimpleNamespace(type="redacted_thinking", data="OPAQUE")),
            _a_cb_stop(0),
            _a_msg_stop(),
        ])
        events = [e async for e in anthropic_provider._do_stream({})]

        blocks = [e for e in events if isinstance(e, ThinkingBlock)]
        assert len(blocks) == 1
        assert blocks[0].block_type == "redacted_thinking"
        assert blocks[0].data == "OPAQUE"

    @pytest.mark.asyncio
    async def test_tool_use_unaffected_by_capture(self, anthropic_provider):
        _attach_stream(anthropic_provider, [
            _a_cb_start(0, SimpleNamespace(type="thinking")),
            _a_delta(0, SimpleNamespace(type="signature_delta", signature="S")),
            _a_cb_stop(0),
            _a_cb_start(1, SimpleNamespace(type="tool_use", id="t1", name="read_file")),
            _a_delta(1, SimpleNamespace(type="input_json_delta", partial_json='{"p":1}')),
            _a_cb_stop(1),
            _a_msg_stop(),
        ])
        events = [e async for e in anthropic_provider._do_stream({})]

        ends = [e for e in events if isinstance(e, ToolUseEnd)]
        assert len(ends) == 1
        assert ends[0].input == {"p": 1}
        assert any(isinstance(e, StreamEnd) for e in events)


# ---------------------------------------------------------------------------
# 3. Emission — block ordering in the assistant turn
# ---------------------------------------------------------------------------

_TB = [
    {"type": "thinking", "thinking": "plan", "signature": "SIG"},
    {"type": "redacted_thinking", "data": "OPAQUE"},
]
_TU = [{"id": "t1", "name": "mcp_run_shell_command", "input": {"command": "ls"}}]


class TestAssistantMessageOrdering:
    """Thinking must lead the turn; the API rejects it out of order."""

    @pytest.mark.parametrize("provider_fixture", ["bedrock_provider", "anthropic_provider"])
    def test_thinking_precedes_text_and_tool_use(self, provider_fixture, request):
        provider = request.getfixturevalue(provider_fixture)
        msg = provider.build_assistant_message("Checking.", _TU, thinking_blocks=_TB)

        assert msg["role"] == "assistant"
        assert [b["type"] for b in msg["content"]] == [
            "thinking", "redacted_thinking", "text", "tool_use",
        ]

    @pytest.mark.parametrize("provider_fixture", ["bedrock_provider", "anthropic_provider"])
    def test_blocks_passed_through_verbatim(self, provider_fixture, request):
        """The signature must survive untouched — a mutated one is rejected."""
        provider = request.getfixturevalue(provider_fixture)
        msg = provider.build_assistant_message("t", _TU, thinking_blocks=_TB)

        assert msg["content"][0] == {"type": "thinking", "thinking": "plan", "signature": "SIG"}
        assert msg["content"][1] == {"type": "redacted_thinking", "data": "OPAQUE"}

    @pytest.mark.parametrize("provider_fixture", ["bedrock_provider", "anthropic_provider"])
    def test_omitted_kwarg_leaves_turn_unchanged(self, provider_fixture, request):
        """The pre-change shape, still used by every other provider."""
        provider = request.getfixturevalue(provider_fixture)
        msg = provider.build_assistant_message("Checking.", _TU)

        assert [b["type"] for b in msg["content"]] == ["text", "tool_use"]

    @pytest.mark.parametrize("provider_fixture", ["bedrock_provider", "anthropic_provider"])
    def test_empty_list_leaves_turn_unchanged(self, provider_fixture, request):
        provider = request.getfixturevalue(provider_fixture)
        msg = provider.build_assistant_message("Checking.", _TU, thinking_blocks=[])

        assert [b["type"] for b in msg["content"]] == ["text", "tool_use"]

    @pytest.mark.parametrize("provider_fixture", ["bedrock_provider", "anthropic_provider"])
    def test_thinking_leads_even_with_no_text(self, provider_fixture, request):
        """display="omitted" turns can be thinking + tool_use with no text."""
        provider = request.getfixturevalue(provider_fixture)
        msg = provider.build_assistant_message("", _TU, thinking_blocks=_TB[:1])

        assert [b["type"] for b in msg["content"]] == ["thinking", "tool_use"]


# ---------------------------------------------------------------------------
# 4. Capability gating
# ---------------------------------------------------------------------------

class TestFeatureGating:

    @pytest.mark.parametrize("provider_fixture", ["bedrock_provider", "anthropic_provider"])
    def test_advertised_when_model_thinks(self, provider_fixture, request):
        provider = request.getfixturevalue(provider_fixture)
        assert provider.supports_feature("thinking_passback") is True

    def test_not_advertised_when_model_does_not_think(self, bedrock_provider):
        bedrock_provider.model_config = {"family": "claude"}
        assert bedrock_provider.supports_feature("thinking_passback") is False

    def test_advertised_on_adaptive_only_model(self, anthropic_provider):
        anthropic_provider.model_config = {"supports_adaptive_thinking": True}
        assert anthropic_provider.supports_feature("thinking_passback") is True

    @pytest.mark.parametrize("module,cls", [
        ("app.providers.openai_direct", "OpenAIDirectProvider"),
        ("app.providers.openai_bedrock", "OpenAIBedrockProvider"),
        ("app.providers.openai_responses_mantle", "OpenAIResponsesMantleProvider"),
        ("app.providers.google_direct", "GoogleDirectProvider"),
        ("app.providers.nova_bedrock", "NovaBedrockProvider"),
    ])
    def test_other_providers_do_not_advertise(self, module, cls):
        """Their reasoning formats differ; they must never be handed the kwarg."""
        klass = getattr(__import__(module, fromlist=[cls]), cls)
        p = klass.__new__(klass)
        p.model_config = {"supports_thinking": True, "supports_adaptive_thinking": True}
        assert p.supports_feature("thinking_passback") is False

    @pytest.mark.parametrize("module,cls", [
        ("app.providers.openai_direct", "OpenAIDirectProvider"),
        ("app.providers.openai_bedrock", "OpenAIBedrockProvider"),
        ("app.providers.openai_responses_mantle", "OpenAIResponsesMantleProvider"),
        ("app.providers.google_direct", "GoogleDirectProvider"),
        ("app.providers.nova_bedrock", "NovaBedrockProvider"),
    ])
    def test_other_providers_remain_concrete(self, module, cls):
        """An optional kwarg on the ABC must not re-abstract implementers."""
        klass = getattr(__import__(module, fromlist=[cls]), cls)
        assert not getattr(klass, "__abstractmethods__", set())


class TestMantleInheritance:
    """Mantle reuses the Anthropic request/stream path, so it is covered
    without any mantle-specific code.  That is only true while it does not
    override these three methods."""

    def test_inherits_all_three_touchpoints(self):
        from app.providers.bedrock_mantle import BedrockMantleProvider as M
        assert M.build_assistant_message.__qualname__ == \
            "AnthropicDirectProvider.build_assistant_message"
        assert M._do_stream.__qualname__ == "AnthropicDirectProvider._do_stream"
        assert M.supports_feature.__qualname__ == "AnthropicDirectProvider.supports_feature"

    def test_accepts_thinking_blocks_kwarg(self):
        import inspect
        from app.providers.bedrock_mantle import BedrockMantleProvider as M
        params = inspect.signature(M.build_assistant_message).parameters
        assert "thinking_blocks" in params


# ---------------------------------------------------------------------------
# 5. Accounting — echoed thinking is billed input
# ---------------------------------------------------------------------------

class TestConversationAccounting:

    @staticmethod
    def _turn(thinking_len=100):
        return {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "x" * thinking_len, "signature": "S"},
            {"type": "tool_use", "id": "t1", "name": "n", "input": {}},
        ]}

    def test_char_counter_counts_thinking(self):
        from app.streaming_tool_executor import StreamingToolExecutor as S
        with_thinking = S._count_conversation_chars([self._turn(100)])
        without = S._count_conversation_chars([{
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "n", "input": {}}],
        }])
        assert with_thinking - without == 100

    def test_token_estimator_counts_thinking(self):
        """A thinking+tool_use turn estimating at ~0 would surface as
        unexplained drift in the bucketed estimate-vs-actual diagnostic."""
        from app.streaming_tool_executor import StreamingToolExecutor as S
        tokens = S._estimate_message_tokens(self._turn(400), None, False, "claude")
        assert tokens >= 100  # 400 chars // 4

    def test_thinking_only_turn_is_not_empty(self):
        """display="omitted" can yield thinking + tool_use and no text; that
        must not be mistaken for an empty completion."""
        from app.streaming_tool_executor import StreamingToolExecutor as S
        assert S._is_empty_content(self._turn()["content"]) is False


# ---------------------------------------------------------------------------
# 6. The kill switch must be usable
# ---------------------------------------------------------------------------

class TestKillSwitchRegistration:
    """``ziya_env`` raises KeyError for any var absent from the registry, and
    the executor's passback guard calls it on every iteration that produced
    thinking blocks.  An unregistered name therefore does not merely disable
    the switch — it raises inside the guard and takes the whole feature down
    in its DEFAULT state."""

    def test_declared_in_registry(self):
        from app.config.env_registry import REGISTRY
        assert "ZIYA_DISABLE_THINKING_PASSBACK" in REGISTRY

    def test_readable_and_defaults_off(self):
        from app.config.env_registry import ziya_env
        assert ziya_env("ZIYA_DISABLE_THINKING_PASSBACK") is False

    def test_guard_expression_does_not_raise(self, bedrock_provider):
        """Mirrors the executor's guard: non-empty blocks + supporting
        provider is exactly the combination that reaches ziya_env()."""
        from app.config.env_registry import ziya_env
        blocks = [{"type": "thinking", "thinking": "x", "signature": "S"}]
        passed = (
            blocks
            if (blocks
                and bedrock_provider.supports_feature("thinking_passback")
                and not ziya_env("ZIYA_DISABLE_THINKING_PASSBACK"))
            else None
        )
        assert passed == blocks

    def test_switch_disables_passback(self, bedrock_provider):
        import os
        from app.config.env_registry import ziya_env
        blocks = [{"type": "thinking", "thinking": "x", "signature": "S"}]
        with patch.dict(os.environ, {"ZIYA_DISABLE_THINKING_PASSBACK": "1"}):
            passed = (
                blocks
                if (blocks
                    and bedrock_provider.supports_feature("thinking_passback")
                    and not ziya_env("ZIYA_DISABLE_THINKING_PASSBACK"))
                else None
            )
        assert passed is None
