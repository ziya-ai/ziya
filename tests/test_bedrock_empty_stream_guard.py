"""
Regression guard: a Bedrock stream that closes WITHOUT a terminal event must
surface as a retryable ErrorEvent, not as a silent success.

Observed live (claude-fable-5, 2026-09-03): iteration 46 of a long tool
loop returned HTTP 200 with zero events and was correctly retried
(TRANSIENT_RETRY).  Two iterations later the same service-side failure
returned ONE non-content event and then closed.  The guard at the time
counted raw events (``_parsed_events == 0``), so that stream passed as a
success and reached the executor as text='' / tools=False /
stop_reason=None -- which the no-tool decider treated as a clean end_turn
and, with the per-stream grace budget already spent, ended the turn with
nothing visible in the log above DEBUG.

The invariant is termination, not count: every successful parse ends in a
StreamEnd (from message_stop) or an ErrorEvent.  Anything else is an
empty completion and must be retried like the zero-event case.

These tests drive the REAL ``stream_response`` (request build stubbed, a
fake boto3 client returning a canned response dict) through the real
``_parse_stream`` and the real guard.  Nothing about the guard is
re-implemented here.
"""

import json
import logging
from contextlib import contextmanager

import pytest

from app.providers.base import (
    ErrorEvent, ErrorType, ProviderConfig, StreamEnd, ThinkingConfig,
    UsageEvent,
)


def _chunk(payload: dict) -> dict:
    return {"chunk": {"bytes": json.dumps(payload).encode("utf-8")}}


# A metrics-only chunk: parsed into a UsageEvent, but carries no content and
# no stop_reason.  This is the shape that defeated the event-count guard.
METRICS_ONLY = [
    _chunk({"amazon-bedrock-invocationMetrics": {
        "inputTokenCount": 10, "outputTokenCount": 0,
        "cacheReadInputTokenCount": 0, "cacheWriteInputTokenCount": 0}}),
]

COMPLETE_TURN = [
    _chunk({"type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}}),
    _chunk({"type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "hello"}}),
    _chunk({"type": "content_block_stop", "index": 0}),
    _chunk({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 1}}),
    _chunk({"type": "message_stop"}),
]


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextmanager
def _capture_logs():
    # See test_bedrock_stream_block_types.py: ModeAwareLogger disables
    # propagation, so caplog cannot see these records.
    log = logging.getLogger("app.providers.bedrock")
    from app.providers.bedrock import logger as provider_logger
    provider_logger.debug("test: priming logger configuration")
    handler = _Collect()
    prev = log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        log.removeHandler(handler)
        log.setLevel(prev)


class _FakeRouter:
    enabled = False
    successes = 0

    def report_success(self, region):
        self.successes += 1


class _FakeBedrockClient:
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def invoke_model_with_response_stream(self, **kwargs):
        self.calls += 1
        return self._response


def _provider(events, request_id="req-123"):
    from app.providers.bedrock import BedrockProvider
    p = BedrockProvider.__new__(BedrockProvider)
    p.model_config = {}
    p.model_id = "test-model"
    p._region = "us-west-2"
    p._region_router = _FakeRouter()
    p.bedrock = _FakeBedrockClient(
        {"body": events, "ResponseMetadata": {"RequestId": request_id}})
    # The request body is irrelevant to the guard; skip the real builder,
    # which needs a fully-populated model_config.
    p._build_request_body = lambda *a, **k: {}
    return p


def _config():
    return ProviderConfig(thinking=ThinkingConfig(enabled=False, mode="adaptive"))


async def _run(events, request_id="req-123"):
    provider = _provider(events, request_id)
    out = []
    with _capture_logs() as records:
        async for ev in provider.stream_response([], None, [], _config()):
            out.append(ev)
        warnings = [r.getMessage() for r in records
                    if r.levelno >= logging.WARNING]
    assert provider.bedrock.calls == 1, "harness precondition: real stream_response reached the client"
    return provider, out, warnings


class TestEmptyStreamGuard:
    @pytest.mark.asyncio
    async def test_zero_events_yields_retryable_error(self):
        _, out, warnings = await _run([])
        errs = [e for e in out if isinstance(e, ErrorEvent)]
        assert len(errs) == 1
        assert errs[0].retryable is True
        assert "req-123" in errs[0].message
        assert any("without message_stop" in w and "0 non-terminal" in w
                   for w in warnings), warnings

    @pytest.mark.asyncio
    async def test_metrics_only_stream_yields_retryable_error(self):
        """The live failure: one non-content event, then the stream closes.
        Under the old ``_parsed_events == 0`` guard this produced NO
        ErrorEvent and reached the executor as stop_reason=None."""
        provider, out, warnings = await _run(METRICS_ONLY)
        assert any(isinstance(e, UsageEvent) for e in out), \
            "harness precondition: the metrics chunk must parse to an event"
        errs = [e for e in out if isinstance(e, ErrorEvent)]
        assert len(errs) == 1, f"expected retryable ErrorEvent, got {out}"
        assert errs[0].retryable is True
        assert errs[0].error_type == ErrorType.OVERLOADED
        assert any("1 non-terminal" in w for w in warnings), warnings
        # An empty stream is a failure: the serving region must not be rewarded.
        assert provider._region_router.successes == 0

    @pytest.mark.asyncio
    async def test_complete_turn_is_not_flagged(self):
        """Positive control: a stream that ends in StreamEnd emits no error
        and no warning, so the guard is not simply firing on everything."""
        provider, out, warnings = await _run(COMPLETE_TURN)
        assert isinstance(out[-1], StreamEnd)
        assert out[-1].stop_reason == "end_turn"
        assert not any(isinstance(e, ErrorEvent) for e in out)
        assert not any("without message_stop" in w for w in warnings), warnings

    @pytest.mark.asyncio
    async def test_parser_error_event_counts_as_termination(self):
        """A stream that already ended in an ErrorEvent (e.g. a stall) must
        not be double-reported as an empty response."""
        provider = _provider([])

        async def _fake_parse(response, config):
            yield ErrorEvent(message="Stream stalled",
                             error_type=ErrorType.READ_TIMEOUT, retryable=True)

        provider._parse_stream = _fake_parse
        out = []
        with _capture_logs() as records:
            async for ev in provider.stream_response([], None, [], _config()):
                out.append(ev)
            warnings = [r.getMessage() for r in records if r.levelno >= logging.WARNING]
        assert len([e for e in out if isinstance(e, ErrorEvent)]) == 1
        assert out[0].message == "Stream stalled"
        assert not any("without message_stop" in w for w in warnings), warnings
