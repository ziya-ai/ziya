"""Contract pinned from LIVE probes of extended-thinking passback.

Every assertion here encodes a fact measured against a real endpoint by
``scripts/probe_thinking_passback.py`` (see .ziya/probe_thinking_passback/ for
the raw captures), not a fact inferred from documentation. These are offline
tests -- they make no network calls -- but the SHAPES they assert are the ones
the live API accepted or rejected:

  Measured 2026-08-15 on all THREE Claude paths -- opus5 via bedrock-runtime
  (us-west-2), claude-fable-5 via bedrock-mantle (us-east-1), and
  claude-opus-5 via the Anthropic API directly:

    assistant[thinking, text, tool_use] + user[tool_result]
        -> ACCEPTED on all three paths (stop_reason=end_turn)
    thinking block with real signature but EMPTY thinking text
        -> ACCEPTED  (the display="omitted" shape is safe to echo)
    thinking block with a TAMPERED signature
        -> REJECTED  'Invalid `signature` in `thinking` block'
    redacted_thinking carrying server-issued opaque bytes in `data`
        -> ACCEPTED
    redacted_thinking with the payload under any name other than `data`
        -> REJECTED  'redacted_thinking.data: Field required'

The last two settle the field-name question that could not be answered from
code: ``data`` is correct, and the API validates its CONTENTS as well as its
presence -- an invented value is refused ('Invalid `data` in
`redacted_thinking` block'), so a synthesised redacted block is not a legal
substitute for a captured one.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


MODEL_CONFIG = {
    "family": "claude",
    "supports_thinking": True,
    "supports_adaptive_thinking": True,
}

TOOL_USES = [{"id": "tu_1", "name": "read_shelf_count", "input": {"shelf": "primary"}}]

# A real captured signature is ~912 chars of base64; length is not semantically
# meaningful to our code, only presence is, so a short stand-in is honest here.
SIG = "CAISgQMKcAgQ" + "x" * 40


@pytest.fixture
def bedrock_provider():
    with patch("app.providers.bedrock_client_cache.get_persistent_bedrock_client") as m:
        m.return_value = MagicMock()
        from app.providers.bedrock import BedrockProvider
        return BedrockProvider(
            model_id="us.anthropic.claude-opus-5",
            model_config=copy.deepcopy(MODEL_CONFIG),
            aws_profile="test", region="us-west-2",
        )


@pytest.fixture
def anthropic_provider():
    import sys
    mock_anthropic = MagicMock()
    with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
        from app.providers.anthropic_direct import AnthropicDirectProvider
        return AnthropicDirectProvider(
            model_id="claude-opus-5",
            model_config=copy.deepcopy(MODEL_CONFIG),
            api_key="sk-test",
        )


def _types(msg: Dict[str, Any]) -> List[str]:
    return [b.get("type") for b in msg["content"]]


class TestAcceptedWireShape:
    """The exact assistant-turn shape both live endpoints accepted."""

    @pytest.mark.parametrize("which", ["bedrock", "anthropic"])
    def test_thinking_leads_then_text_then_tool_use(
            self, which, bedrock_provider, anthropic_provider):
        # Live MSG_STRUCT on the accepted request read:
        #   assistant[thinking,text,tool_use] | user[tool_result]
        provider = bedrock_provider if which == "bedrock" else anthropic_provider
        blocks = [{"type": "thinking", "thinking": "Comparing shipping costs...",
                   "signature": SIG}]
        msg = provider.build_assistant_message(
            "Let me check the shelf.", TOOL_USES, thinking_blocks=blocks)
        assert _types(msg) == ["thinking", "text", "tool_use"]

    @pytest.mark.parametrize("which", ["bedrock", "anthropic"])
    def test_signature_travels_verbatim(
            self, which, bedrock_provider, anthropic_provider):
        # A tampered signature was REJECTED live ('Invalid `signature`'), so any
        # mutation of the captured value breaks the turn -- pin byte equality.
        provider = bedrock_provider if which == "bedrock" else anthropic_provider
        blocks = [{"type": "thinking", "thinking": "reasoning", "signature": SIG}]
        msg = provider.build_assistant_message("t", TOOL_USES, thinking_blocks=blocks)
        assert msg["content"][0]["signature"] == SIG
        assert msg["content"][0]["thinking"] == "reasoning"

    @pytest.mark.parametrize("which", ["bedrock", "anthropic"])
    def test_omitting_kwarg_reproduces_pre_change_shape(
            self, which, bedrock_provider, anthropic_provider):
        provider = bedrock_provider if which == "bedrock" else anthropic_provider
        assert _types(provider.build_assistant_message("t", TOOL_USES)) == \
            ["text", "tool_use"]


class TestSignedButEmptyBlockIsSafeToEcho:
    """Q2, settled live: ACCEPTED.

    On display="omitted" models the stream carries only signature_delta, so the
    captured block has a signature and no text.  Sending exactly that shape was
    accepted (stop_reason=end_turn), which is why the capture code emits it on
    the strength of the signature alone rather than also requiring text.
    """

    @pytest.mark.parametrize("which", ["bedrock", "anthropic"])
    def test_empty_text_with_signature_is_emitted(
            self, which, bedrock_provider, anthropic_provider):
        provider = bedrock_provider if which == "bedrock" else anthropic_provider
        blocks = [{"type": "thinking", "thinking": "", "signature": SIG}]
        msg = provider.build_assistant_message("t", TOOL_USES, thinking_blocks=blocks)
        assert _types(msg) == ["thinking", "text", "tool_use"]
        assert msg["content"][0]["thinking"] == ""
        assert msg["content"][0]["signature"] == SIG

    def test_capture_keeps_signed_empty_block_bedrock(self, bedrock_provider):
        # Guards against "tighten" edits that would add `and _tb["thinking"]`
        # to the emit condition: live evidence says that would DISCARD a block
        # the API accepts, silently disabling passback on omitted-display models.
        import asyncio, json
        from app.providers.base import ThinkingBlock, ProviderConfig

        def chunk(d):
            return {"chunk": {"bytes": json.dumps(d).encode()}}

        chunks = [
            chunk({"type": "content_block_start", "index": 0,
                   "content_block": {"type": "thinking"}}),
            # No thinking_delta at all -- only the closing signature.
            chunk({"type": "content_block_delta", "index": 0,
                   "delta": {"type": "signature_delta", "signature": SIG}}),
            chunk({"type": "content_block_stop", "index": 0}),
            chunk({"type": "message_stop", "stop_reason": "end_turn"}),
        ]

        async def run():
            return [e async for e in bedrock_provider._parse_stream(
                {"body": iter(chunks)}, ProviderConfig())]

        blocks = [e for e in asyncio.run(run()) if isinstance(e, ThinkingBlock)]
        assert len(blocks) == 1, "signed-but-empty block must still be emitted"
        assert blocks[0].signature == SIG
        assert blocks[0].content == ""


class TestRedactedThinkingFieldName:
    """Q3, settled live: the payload field is ``data``.

    Renaming it produced 'redacted_thinking.data: Field required'; keeping it
    with server-issued opaque bytes was accepted.  The API also validates the
    VALUE ('Invalid `data`'), so a fabricated redacted block cannot stand in
    for a captured one -- which is exactly why the capture path forwards the
    bytes untouched instead of synthesising anything.
    """

    @pytest.mark.parametrize("which", ["bedrock", "anthropic"])
    def test_payload_key_is_data(self, which, bedrock_provider, anthropic_provider):
        provider = bedrock_provider if which == "bedrock" else anthropic_provider
        blocks = [{"type": "redacted_thinking", "data": "OPAQUE_SERVER_BYTES"}]
        msg = provider.build_assistant_message("t", TOOL_USES, thinking_blocks=blocks)
        assert _types(msg) == ["redacted_thinking", "text", "tool_use"]
        assert "data" in msg["content"][0], "payload MUST be under 'data'"
        assert msg["content"][0]["data"] == "OPAQUE_SERVER_BYTES"

    def test_capture_forwards_data_unmodified_bedrock(self, bedrock_provider):
        import asyncio, json
        from app.providers.base import ThinkingBlock, ProviderConfig

        def chunk(d):
            return {"chunk": {"bytes": json.dumps(d).encode()}}

        chunks = [
            chunk({"type": "content_block_start", "index": 0,
                   "content_block": {"type": "redacted_thinking",
                                     "data": "SERVER_ISSUED"}}),
            chunk({"type": "content_block_stop", "index": 0}),
            chunk({"type": "message_stop", "stop_reason": "end_turn"}),
        ]

        async def run():
            return [e async for e in bedrock_provider._parse_stream(
                {"body": iter(chunks)}, ProviderConfig())]

        blocks = [e for e in asyncio.run(run()) if isinstance(e, ThinkingBlock)]
        assert len(blocks) == 1
        assert blocks[0].block_type == "redacted_thinking"
        assert blocks[0].data == "SERVER_ISSUED", \
            "opaque bytes must survive verbatim; the API rejects altered data"


class TestAnthropicDirectSdkCapture:
    """The anthropic-direct path parses SDK event OBJECTS, not raw JSON.

    Its ``signature_delta`` branch did not exist before this feature, so it is
    the one capture path with no prior live exposure.  Probed 2026-08-15 against
    claude-opus-5 on the Anthropic API: a 744-byte signature was captured off
    the SDK stream and accepted back (stop_reason=end_turn), and the same four
    mutations rejected there as on Bedrock -- tampered signature, invented
    ``data``, and a renamed payload key all 400.

    These tests pin the ATTRIBUTE NAMES the SDK branch reads.  A rename in the
    SDK (``delta.signature`` -> anything else) would otherwise degrade silently:
    ``getattr(delta, "signature", "")`` returns "", the block is dropped as
    unsigned, and passback quietly stops working with no error anywhere.
    """

    @staticmethod
    def _events(*evs):
        class _Stream:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *a):
                return False

            def __aiter__(self_inner):
                async def gen():
                    for e in evs:
                        yield e
                return gen()
        return _Stream()

    def _run(self, provider, *evs):
        import asyncio
        from app.providers.base import ThinkingBlock

        provider.client.messages.stream = lambda **kw: self._events(*evs)

        async def run():
            return [e async for e in provider._do_stream({"model": "m", "messages": []})]

        return [e for e in asyncio.run(run()) if isinstance(e, ThinkingBlock)]

    def test_signature_delta_attribute_is_read(self, anthropic_provider):
        from types import SimpleNamespace as NS

        blocks = self._run(
            anthropic_provider,
            NS(type="content_block_start", index=0,
               content_block=NS(type="thinking")),
            NS(type="content_block_delta", index=0,
               delta=NS(type="thinking_delta", thinking="plan")),
            NS(type="content_block_delta", index=0,
               delta=NS(type="signature_delta", signature=SIG)),
            NS(type="content_block_stop", index=0),
            NS(type="message_stop"),
        )
        assert len(blocks) == 1, "SDK signature_delta must close a thinking block"
        assert blocks[0].signature == SIG
        assert blocks[0].content == "plan"

    def test_redacted_data_attribute_is_read(self, anthropic_provider):
        from types import SimpleNamespace as NS

        blocks = self._run(
            anthropic_provider,
            NS(type="content_block_start", index=0,
               content_block=NS(type="redacted_thinking", data="SERVER_ISSUED")),
            NS(type="content_block_stop", index=0),
            NS(type="message_stop"),
        )
        assert len(blocks) == 1
        assert blocks[0].block_type == "redacted_thinking"
        assert blocks[0].data == "SERVER_ISSUED"

    def test_unsigned_block_never_reaches_the_api(self, anthropic_provider):
        # A thinking block whose signature never arrived cannot be echoed: the
        # live API answers a bad/missing signature with
        # 'Invalid `signature` in `thinking` block'.  The parser SURFACES the
        # block with signature=None (silently dropping it gapped the turn and
        # shifted every later block out of its signed position — the same
        # "cannot be modified" 400); the assembler enforces the live contract
        # by echoing NO thinking for the turn.
        from types import SimpleNamespace as NS

        blocks = self._run(
            anthropic_provider,
            NS(type="content_block_start", index=0,
               content_block=NS(type="thinking")),
            NS(type="content_block_delta", index=0,
               delta=NS(type="thinking_delta", thinking="partial")),
            NS(type="content_block_stop", index=0),
            NS(type="message_stop"),
        )
        assert len(blocks) == 1
        assert blocks[0].signature is None
        from app.providers.base import LLMProvider
        content = LLMProvider._ordered_assistant_content(
            "t", [], thinking_blocks=[{
                "type": "thinking", "thinking": blocks[0].content,
                "signature": blocks[0].signature, "_index": blocks[0].index}])
        assert all(b["type"] != "thinking" for b in content), \
            "unsigned thinking must never be echoed back"


class TestKillSwitchIsUsableUnderTheAcceptedContract:
    """Passback is live on both Bedrock paths, so its off-switch must work.

    ziya_env raises KeyError for undeclared variables, and the executor consults
    this switch on every iteration that produced thinking -- an unregistered
    name takes the whole feature down rather than merely leaving it enabled.
    """

    def test_declared_and_defaults_off(self):
        from app.config.env_registry import REGISTRY, ziya_env
        assert "ZIYA_DISABLE_THINKING_PASSBACK" in REGISTRY
        assert ziya_env("ZIYA_DISABLE_THINKING_PASSBACK") is False

    def test_guard_expression_cannot_raise(self):
        from app.config.env_registry import ziya_env

        class _P:
            def supports_feature(self, f):
                return True

        blocks = [{"type": "thinking", "thinking": "x", "signature": SIG}]
        provider = _P()
        passed = (
            blocks
            if (blocks
                and provider.supports_feature("thinking_passback")
                and not ziya_env("ZIYA_DISABLE_THINKING_PASSBACK"))
            else None
        )
        assert passed == blocks
