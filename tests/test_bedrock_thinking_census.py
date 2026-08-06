"""
Thinking-visibility census: is reasoning we are BILLED for actually sent?

``output_tokens_details.thinking_tokens`` is the API's own count of
reasoning it charged for.  Nothing in the codebase read it, so it reached
a human only incidentally, inside the raw ``MESSAGE_DELTA`` dump.  That
left one question unanswerable from the logs: a turn showing 283 thinking
tokens but only 145 characters of assistant text could mean either

  (a) Bedrock streamed thinking and Ziya dropped it -- a bug here, or
  (b) Bedrock billed for reasoning it never sent  -- normal API behaviour,

and the two call for opposite responses.  The census puts both numbers on
one line so they can be compared directly.

Tests pin THREE things, in order of importance:
  1. The census discriminates (a) from (b) -- ``visible=NO`` with a
     non-zero ``billed`` is the smoking gun for (b).
  2. It is silent when there is no thinking, so an ordinary turn gains no
     log noise.
  3. It is purely OBSERVATIONAL -- the yielded event sequence is
     unchanged with and without it.  A diagnostic that perturbs the thing
     it measures is worse than none.
"""

import json
import logging
from contextlib import contextmanager

import pytest


def _chunk(payload: dict) -> dict:
    return {"chunk": {"bytes": json.dumps(payload).encode("utf-8")}}


def _thinking_turn(billed, thinking_text,
                   text: str = "hello", stop: str = "end_turn",
                   out_tokens: int = 480) -> list:
    """A turn that optionally streams thinking and optionally reports a
    billed thinking-token count.  ``billed=None`` omits the field
    entirely, as a non-thinking model does.  ``out_tokens`` is settable
    because the census derives chars/token from the VISIBLE portion of the
    response, so the ratio depends on it."""
    events = []
    if thinking_text is not None:
        events += [
            _chunk({"type": "content_block_start", "index": 0,
                    "content_block": {"type": "thinking"}}),
            _chunk({"type": "content_block_delta", "index": 0,
                    "delta": {"type": "thinking_delta",
                              "thinking": thinking_text}}),
            _chunk({"type": "content_block_delta", "index": 0,
                    "delta": {"type": "signature_delta", "signature": "sig"}}),
            _chunk({"type": "content_block_stop", "index": 0}),
        ]
    events += [
        _chunk({"type": "content_block_start", "index": 1,
                "content_block": {"type": "text"}}),
        _chunk({"type": "content_block_delta", "index": 1,
                "delta": {"type": "text_delta", "text": text}}),
        _chunk({"type": "content_block_stop", "index": 1}),
    ]
    usage = {"output_tokens": out_tokens}
    if billed is not None:
        usage["output_tokens_details"] = {"thinking_tokens": billed}
    events += [
        _chunk({"type": "message_delta", "delta": {"stop_reason": stop},
                "usage": usage}),
        _chunk({"type": "message_stop"}),
    ]
    return events


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextmanager
def capture_bedrock_logs():
    """See tests/test_bedrock_stream_block_types.py for why caplog cannot
    be used here: ModeAwareLogger sets propagate=False, so records never
    reach the root handler caplog installs."""
    from app.providers.bedrock import logger as provider_logger
    provider_logger.debug("test: priming logger configuration")
    log = logging.getLogger("app.providers.bedrock")
    handler = _Collect()
    prev = log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        log.removeHandler(handler)
        log.setLevel(prev)


def _provider():
    from app.providers.bedrock import BedrockProvider
    p = BedrockProvider.__new__(BedrockProvider)
    p.model_config = {}
    p.model_id = "sonnet4.6"
    p._region = "us-west-2"
    return p


def _config():
    from app.providers.base import ProviderConfig, ThinkingConfig
    return ProviderConfig(thinking=ThinkingConfig(enabled=True, mode="adaptive"))


async def _drain(events):
    """Return (yielded_reprs, census_lines)."""
    out = []
    with capture_bedrock_logs() as records:
        async for ev in _provider()._parse_stream({"body": events}, _config()):
            out.append(repr(ev))
        census = [r.getMessage() for r in records
                  if "THINKING_CENSUS" in r.getMessage()]
    return out, census


class TestCensusDiscriminatesTheTwoDiagnoses:
    """The whole reason the census exists."""

    @pytest.mark.asyncio
    async def test_billed_and_streamed_reports_visible_yes(self):
        _, census = await _drain(_thinking_turn(billed=100,
                                               thinking_text="x" * 350))
        assert len(census) == 1
        line = census[0]
        assert "visible=yes" in line
        assert "billed_thinking_tok=100" in line
        assert "streamed_deltas=1" in line
        assert "streamed_chars=350" in line

    @pytest.mark.asyncio
    async def test_billed_but_never_streamed_reports_visible_NO(self):
        # The suspected real-world case: 283 billed thinking tokens with
        # no thinking_delta on the wire.  This is what tells a reader the
        # API withheld it rather than Ziya losing it.
        _, census = await _drain(_thinking_turn(billed=283,
                                               thinking_text=None,
                                               stop="tool_use"))
        assert len(census) == 1
        line = census[0]
        assert "visible=NO" in line
        assert "billed_thinking_tok=283" in line
        assert "streamed_deltas=0" in line

    @pytest.mark.asyncio
    async def test_streamed_without_a_billed_figure_still_reports(self):
        # Either signal alone must trigger the census; requiring both
        # would hide exactly the asymmetric cases of interest.
        _, census = await _drain(_thinking_turn(billed=None,
                                               thinking_text="reasoning"))
        assert len(census) == 1
        assert "visible=yes" in census[0]
        assert "billed_thinking_tok=?" in census[0]

    @pytest.mark.asyncio
    async def test_ratio_quantifies_a_partial_shortfall(self):
        # est/billed near 1.0 means we saw what we paid for; near 0 means we
        # did not.  A bare boolean could not show a PARTIAL withholding
        # (summarised thinking), which is the case worth detecting.
        #
        # The ratio is SELF-CALIBRATING: chars/token comes from this
        # response's own visible text, not a constant.  Live measurement on
        # sonnet4.6 gave 1.58 / 2.27 / 3.63 chars-per-token across three
        # requests, so a fixed divisor mis-scores most responses -- the old
        # 3.5 rated fully-delivered streams at 0.25-0.72, which a reader
        # would take as the API withholding reasoning it had in fact sent.
        #
        # Here: 700 visible chars over (2000-1000)=1000 visible tokens = 0.7
        # chars/token, so 350 thinking chars ~ 500 tokens against 1000
        # billed = 0.50.
        _, census = await _drain(_thinking_turn(
            billed=1000, thinking_text="x" * 350,
            text="y" * 700, out_tokens=2000))
        assert "est/billed=0.50" in census[0], census[0]

    @pytest.mark.asyncio
    async def test_full_delivery_scores_near_one(self):
        # Regression guard for the fixed-3.5 defect.  Measured live on
        # sonnet4.6 (509 thinking chars, 644 text chars, out=514,
        # billed=232) and the stream WAS fully delivered -- but a 3.5
        # divisor scored it 0.62.
        _, census = await _drain(_thinking_turn(
            billed=232, thinking_text="t" * 509,
            text="x" * 644, out_tokens=514))
        line = census[0]
        assert "visible=yes" in line
        ratio = float(line.split("est/billed=")[1].split()[0])
        assert 0.85 <= ratio <= 1.15, (
            f"a fully-delivered stream must score near 1.0, got {ratio}"
        )

    @pytest.mark.asyncio
    async def test_tiny_samples_report_na_rather_than_a_precise_lie(self):
        # Measured live: 19 thinking chars against 20 billed tokens.
        # Quantization dominates at that size, so any 2-decimal ratio would
        # be noise presented as signal.
        _, census = await _drain(_thinking_turn(
            billed=20, thinking_text="t" * 19,
            text="x" * 351, out_tokens=102))
        assert "est/billed=n/a" in census[0], census[0]


class TestCensusIsSilentWhenIrrelevant:
    @pytest.mark.asyncio
    async def test_no_thinking_at_all_emits_nothing(self):
        # An ordinary non-thinking turn must gain no log noise --
        # otherwise this diagnostic repeats the mistake it was written to
        # fix (a channel so chatty nobody reads it).
        _, census = await _drain(_thinking_turn(billed=None,
                                               thinking_text=None))
        assert census == []

    @pytest.mark.asyncio
    async def test_zero_billed_and_zero_streamed_emits_nothing(self):
        _, census = await _drain(_thinking_turn(billed=0,
                                               thinking_text=None))
        assert census == []

    @pytest.mark.asyncio
    async def test_emits_exactly_once_per_response(self):
        # Guards against the census landing inside the delta loop, where
        # it would fire per chunk.
        _, census = await _drain(_thinking_turn(billed=50,
                                               thinking_text="a"))
        assert len(census) == 1


class TestCensusIsPurelyObservational:
    """A diagnostic that changes the stream is worse than no diagnostic."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("events,label", [
        (_thinking_turn(billed=100, thinking_text="x" * 350), "thinking"),
        (_thinking_turn(billed=283, thinking_text=None), "billed-only"),
        (_thinking_turn(billed=None, thinking_text=None), "plain"),
    ])
    async def test_text_still_reaches_the_caller(self, events, label):
        out, _ = await _drain(events)
        # Counting must not consume: the channel still yields.
        assert any("TextDelta" in r for r in out), label

    @pytest.mark.asyncio
    async def test_counting_does_not_swallow_thinking_deltas(self):
        out, _ = await _drain(_thinking_turn(billed=10,
                                            thinking_text="pondering"))
        assert any("ThinkingDelta" in r and "pondering" in r for r in out)

    @pytest.mark.asyncio
    async def test_counting_does_not_swallow_text_deltas(self):
        out, _ = await _drain(_thinking_turn(billed=10,
                                            thinking_text="p",
                                            text="visible answer"))
        assert any("visible answer" in r for r in out)

    @pytest.mark.asyncio
    async def test_empty_stream_still_yields_nothing(self):
        # The census must not resurrect the empty-stream case, which is
        # detected one level up in stream_response.
        out, census = await _drain([])
        assert out == []
        assert census == []
