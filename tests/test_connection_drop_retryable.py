"""Characterization tests for transient connection-drop classification.

Regression guard for the bedrock-mantle/fable-5 failure where a mid-stream
network drop was surfaced as a fatal, non-retryable error:

    ERROR_CHUNK: 'peer closed connection without sending complete message
                  body (incomplete chunked read)'
    NON_RETRYABLE_END: provider error already surfaced, ending stream ...

httpx raises RemoteProtocolError for these mid-stream socket drops. The
string matched none of the substrings in AnthropicDirectProvider._classify_error
and fell through to ErrorType.UNKNOWN (retryable=False), so the orchestrator
ended the stream instead of retrying. These are transient and must classify
into the retryable set (mapped to READ_TIMEOUT).
"""
import pytest

from app.providers.anthropic_direct import AnthropicDirectProvider
from app.providers.base import ErrorType

# The set every provider treats as retryable (see StreamEnd/ErrorEvent wiring).
_RETRYABLE = (ErrorType.THROTTLE, ErrorType.READ_TIMEOUT, ErrorType.OVERLOADED)


def _classify(s: str) -> ErrorType:
    return AnthropicDirectProvider._classify_error(s)


def test_incomplete_chunked_read_is_retryable():
    """The exact observed live failure string must be retryable."""
    err = ("peer closed connection without sending complete message body "
           "(incomplete chunked read)")
    et = _classify(err)
    assert et == ErrorType.READ_TIMEOUT
    assert et in _RETRYABLE


@pytest.mark.parametrize("err", [
    "peer closed connection without sending complete message body (incomplete chunked read)",
    "httpx.RemoteProtocolError: peer closed connection",
    "Connection reset by peer",
    "Connection aborted",
    "Server disconnected without sending a response",
])
def test_connection_drop_family_retryable(err):
    assert _classify(err) in _RETRYABLE


def test_throttle_still_classified():
    assert _classify("Error 429: too many requests") == ErrorType.THROTTLE


def test_overloaded_still_classified():
    assert _classify("service overloaded (529)") == ErrorType.OVERLOADED


def test_context_limit_still_classified():
    assert _classify("prompt is too long for this model") == ErrorType.CONTEXT_LIMIT


def test_genuine_unknown_still_non_retryable():
    """A real fatal error (bad request) must remain non-retryable."""
    et = _classify("ValidationException: malformed request payload")
    assert et == ErrorType.UNKNOWN
    assert et not in _RETRYABLE
