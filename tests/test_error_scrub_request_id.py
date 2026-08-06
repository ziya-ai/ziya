"""The request_id must not participate in substring classification.

Every provider's _classify_error is a ladder of `in` tests against the repr
of the whole error envelope, which embeds a server-generated request_id of
opaque random alphanumerics. That id can contain the very tokens the ladder
matches on, so a request_id containing "429" silently reclassifies ANY
error as THROTTLE — then retries it with escalating backoff up to 80s
before failing anyway, and unreproducibly, because the id differs every
request.

These tests pin both halves of the contract: the id cannot cause a false
match, and scrubbing it cannot suppress a genuine one.
"""
import pytest

from app.providers.anthropic_direct import AnthropicDirectProvider
from app.providers.openai_responses_mantle import OpenAIResponsesMantleProvider
from app.providers.base import ErrorType
from app.providers.error_scrub import scrub_request_id

_CLASSIFIERS = [
    pytest.param(AnthropicDirectProvider._classify_error, id="anthropic"),
    pytest.param(OpenAIResponsesMantleProvider._classify_error, id="openai-mantle"),
]


def _envelope(request_id: str, message: str, code: int = 400) -> str:
    """The shape the SDKs actually stringify."""
    return (
        f"Error code: {code} - {{'type': 'error', 'request_id': "
        f"'{request_id}', 'error': {{'type': 'invalid_request_error', "
        f"'message': '{message}'}}}}"
    )


class TestScrubber:
    def test_removes_the_field(self):
        out = scrub_request_id(_envelope("req_abc123", "bad shape"))
        assert "req_abc123" not in out

    def test_preserves_the_real_message(self):
        out = scrub_request_id(_envelope("req_abc123", "bad shape"))
        assert "bad shape" in out

    def test_preserves_a_genuine_status_code(self):
        """The scrub is field-scoped; it must not eat the status position."""
        out = scrub_request_id(_envelope("req_zzz", "rate limit", code=429))
        assert "429" in out
        assert "rate limit" in out

    @pytest.mark.parametrize("raw", [
        '"requestId": "req_429x"',
        "request-id='req_429x'",
        "requestid: 'req_429x'",
        "'request_id': 'req_429x'",
    ])
    def test_spelling_variants(self, raw):
        assert "429" not in scrub_request_id(raw)

    def test_empty_and_absent_tolerated(self):
        assert scrub_request_id("") == ""
        assert scrub_request_id("plain error") == "plain error"

    def test_idempotent(self):
        once = scrub_request_id(_envelope("req_abc", "boom"))
        assert scrub_request_id(once) == once


class TestNoFalseThrottle:
    """A request_id embedding throttle tokens must not force THROTTLE."""

    @pytest.mark.parametrize("classify", _CLASSIFIERS)
    @pytest.mark.parametrize("rid", [
        "req_429abcdef",            # matches "429" in error_str
        "req_ratelimitxyz",         # matches "rate" in lowered
        "req_xx429xx",
    ])
    def test_fatal_error_not_reclassified(self, classify, rid):
        err = _envelope(rid, "malformed request payload")
        assert classify(err) != ErrorType.THROTTLE

    @pytest.mark.parametrize("classify", _CLASSIFIERS)
    def test_signature_expiry_survives_a_429_request_id(self, classify):
        """The observed failure, with the worst-case id — still retryable."""
        err = _envelope(
            "req_429ratexyz",
            "Signature expired: 20260801T183402Z is now earlier than "
            "20260801T183916Z",
            code=401,
        )
        assert classify(err) == ErrorType.READ_TIMEOUT

    @pytest.mark.parametrize("classify", _CLASSIFIERS)
    def test_expired_token_survives_a_429_request_id(self, classify):
        err = _envelope(
            "req_429ratexyz",
            "ExpiredToken: The security token included in the request is "
            "expired",
            code=403,
        )
        assert classify(err) == ErrorType.AUTH


class TestGenuineThrottleStillWorks:
    @pytest.mark.parametrize("classify", _CLASSIFIERS)
    def test_status_429_classifies(self, classify):
        err = _envelope("req_plainid", "Too many requests", code=429)
        assert classify(err) == ErrorType.THROTTLE

    @pytest.mark.parametrize("classify", _CLASSIFIERS)
    def test_rate_limit_message_classifies(self, classify):
        err = _envelope("req_plainid", "rate limit exceeded", code=400)
        assert classify(err) == ErrorType.THROTTLE
