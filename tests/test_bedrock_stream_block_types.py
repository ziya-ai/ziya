"""
Regression guard: the Bedrock stream parser must not warn about block
types it handles by design.

An adaptive-thinking response opens a ``thinking`` block at index 0,
closes it with a ``signature_delta``, then opens a ``text`` block at
index 1.  Neither the signature nor the text-block start requires any
action -- text arrives via ``text_delta``, and thinking blocks are never
echoed back upstream, so the signature has no consumer.  Both
nevertheless fell through to a WARNING, several times per turn, on every
tool-calling iteration.

That is worse than merely noisy: the same WARNING channel is the only
signal for a genuinely unknown block/delta type, so a channel that cries
wolf on every request is a channel nobody reads.  These tests pin the
distinction -- known-inert types log at DEBUG, unknown ones still WARN.
"""

import json
import logging
from contextlib import contextmanager

import pytest


def _chunk(payload: dict) -> dict:
    """Wrap a payload in the boto3 event envelope the parser expects."""
    return {"chunk": {"bytes": json.dumps(payload).encode("utf-8")}}


# The exact sequence an adaptive-thinking turn produces.
ADAPTIVE_THINKING_EVENTS = [
    _chunk({"type": "content_block_start", "index": 0,
            "content_block": {"type": "thinking", "thinking": ""}}),
    _chunk({"type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "pondering"}}),
    _chunk({"type": "content_block_delta", "index": 0,
            "delta": {"type": "signature_delta", "signature": "abc123"}}),
    _chunk({"type": "content_block_stop", "index": 0}),
    _chunk({"type": "content_block_start", "index": 1,
            "content_block": {"type": "text", "text": ""}}),
    _chunk({"type": "content_block_delta", "index": 1,
            "delta": {"type": "text_delta", "text": "hello"}}),
    _chunk({"type": "content_block_stop", "index": 1}),
    _chunk({"type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 5}}),
    _chunk({"type": "message_stop"}),
]


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextmanager
def capture_bedrock_logs():
    """Capture records from the bedrock provider's own logger.

    pytest's ``caplog`` fixture CANNOT see these: ModeAwareLogger sets
    ``propagate = False`` on the logger it wraps, so records never reach
    the root handler caplog installs -- they land only on the logger's own
    stderr handler.  A caplog-based assertion therefore fails while
    pytest's own "Captured stderr" section displays the very warning the
    assertion claims is absent.
    """
    log = logging.getLogger("app.providers.bedrock")
    # Force ModeAwareLogger's lazy configuration to run BEFORE we attach:
    # ``_ensure_configured()`` calls ``handlers.clear()`` on its first
    # invocation, which would otherwise silently discard our handler.
    from app.providers.bedrock import logger as provider_logger
    provider_logger.debug("test: priming logger configuration")

    handler = _Collect()
    prev_level = log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        log.removeHandler(handler)
        log.setLevel(prev_level)


def _provider():
    from app.providers.bedrock import BedrockProvider
    # __new__ bypasses the boto3 client construction in __init__; the
    # parser touches only these three attributes.
    p = BedrockProvider.__new__(BedrockProvider)
    p.model_config = {}
    p.model_id = "test-model"
    p._region = "us-west-2"
    return p


def _config():
    # A real config is required: _parse_stream dereferences
    # ``config.thinking`` unconditionally to size its stall timeout.
    from app.providers.base import ProviderConfig, ThinkingConfig
    return ProviderConfig(thinking=ThinkingConfig(enabled=True, mode="adaptive"))


async def _drain(events):
    """Run the parser over ``events``; return (yielded, warnings, debugs).

    ``response`` is the boto3 response DICT, so the event list goes under
    a "body" key -- not passed as a bare iterable.
    """
    provider = _provider()
    out = []
    with capture_bedrock_logs() as records:
        async for ev in provider._parse_stream({"body": events}, _config()):
            out.append(ev)
        warnings = [r.getMessage() for r in records
                    if r.levelno >= logging.WARNING]
        debugs = [r.getMessage() for r in records
                  if r.levelno == logging.DEBUG]
    return out, warnings, debugs


class TestHarnessActuallyCaptures:
    """If this fails, every no-warning assertion below is vacuous.

    Not defensive padding: the first version of this file used caplog and
    every ``assert warnings == []`` passed for the wrong reason -- the
    harness saw nothing at all.
    """

    @pytest.mark.asyncio
    async def test_capture_sees_a_known_warning(self):
        _, warnings, _ = await _drain([
            _chunk({"type": "content_block_start", "index": 0,
                    "content_block": {"type": "server_tool_use"}}),
        ])
        assert warnings, "harness captured nothing -- assertions are vacuous"


class TestKnownInertBlockTypesDoNotWarn:
    @pytest.mark.asyncio
    async def test_adaptive_thinking_turn_produces_no_warnings(self):
        _, warnings, _ = await _drain(ADAPTIVE_THINKING_EVENTS)
        assert warnings == [], (
            f"an ordinary adaptive-thinking turn must be warning-free; "
            f"got: {warnings}"
        )

    @pytest.mark.asyncio
    async def test_inert_types_are_logged_at_debug_not_dropped(self):
        # Downgraded, not deleted: a genuinely surprising stream is still
        # reconstructable from a DEBUG-level run.
        _, _, debugs = await _drain(ADAPTIVE_THINKING_EVENTS)
        assert any("signature_delta" in d for d in debugs)
        assert any("content_block_start type='text'" in d for d in debugs)

    @pytest.mark.asyncio
    async def test_signature_delta_yields_nothing(self):
        # It must be DROPPED, not forwarded as text -- a signature leaking
        # into the visible stream would render as gibberish.
        events, _, _ = await _drain(ADAPTIVE_THINKING_EVENTS)
        from app.providers import TextDelta
        text = "".join(e.content for e in events if isinstance(e, TextDelta))
        assert "abc123" not in text
        assert text == "hello"

    @pytest.mark.asyncio
    async def test_thinking_content_still_reaches_the_caller(self):
        # Guards against "silence the warning" being implemented by
        # swallowing the whole thinking block.
        events, _, _ = await _drain(ADAPTIVE_THINKING_EVENTS)
        from app.providers import ThinkingDelta
        thinking = "".join(e.content for e in events
                           if isinstance(e, ThinkingDelta))
        assert thinking == "pondering"

    @pytest.mark.asyncio
    async def test_redacted_thinking_start_does_not_warn(self):
        _, warnings, _ = await _drain([
            _chunk({"type": "content_block_start", "index": 0,
                    "content_block": {"type": "redacted_thinking",
                                      "data": "opaque"}}),
        ])
        assert warnings == []


class TestUnknownTypesStillWarn:
    @pytest.mark.asyncio
    async def test_unknown_block_start_warns(self):
        _, warnings, _ = await _drain([
            _chunk({"type": "content_block_start", "index": 0,
                    "content_block": {"type": "server_tool_use"}}),
        ])
        assert any("UNHANDLED content_block_start" in w for w in warnings), (
            "the warning channel must still fire for a type we do not "
            "handle -- silencing it wholesale is the opposite fix"
        )

    @pytest.mark.asyncio
    async def test_unknown_delta_warns(self):
        _, warnings, _ = await _drain([
            _chunk({"type": "content_block_delta", "index": 0,
                    "delta": {"type": "citations_delta", "citation": {}}}),
        ])
        assert any("UNHANDLED content_block_delta" in w for w in warnings)
