"""Characterization: transient Anthropic HTTP 500 api_error is retryable.

Regression guard for a bedrock-mantle/fable-5 failure observed live TWICE:

    ERROR_CHUNK: "Error code: 500 - {...'type': 'api_error', 'message':
                  'The server had an error while processing your request...'}"
    ERROR_CHUNK: "{...'type': 'api_error', 'message': 'Internal server error'}"
    NON_RETRYABLE_END: provider error already surfaced, ending stream ...

Anthropic documents 500 api_error as a transient server fault to be retried
with backoff. It matched none of the substrings in
AnthropicDirectProvider._classify_error and fell through to ErrorType.UNKNOWN
(retryable=False), so the orchestrator ended the stream instead of retrying.

The fix classifies it as READ_TIMEOUT (already in the retryable set) rather
than SERVER_ERROR, which is intentionally reserved as NON-retryable for
Bedrock's persistent InternalServerException. This test locks both halves of
that contract: the 500 api_error family is retryable, and the persistent
SERVER_ERROR / genuine UNKNOWN classes stay fatal.
"""
import pytest

from app.providers.anthropic_direct import AnthropicDirectProvider
from app.providers.base import ErrorType

# Exactly the set the orchestrator retries.
_RETRYABLE = (ErrorType.THROTTLE, ErrorType.READ_TIMEOUT, ErrorType.OVERLOADED)


def _classify(s: str) -> ErrorType:
    return AnthropicDirectProvider._classify_error(s)


# ---------------------------------------------------------------------------
# The two exact live strings must be retryable.
# ---------------------------------------------------------------------------

def test_exact_live_string_the_server_had_an_error():
    err = ("Error code: 500 - {'type': 'error', 'request_id': 'req_wfm3nq', "
           "'error': {'type': 'api_error', 'message': 'The server had an "
           "error while processing your request. Sorry about that!'}}")
    et = _classify(err)
    assert et == ErrorType.READ_TIMEOUT
    assert et in _RETRYABLE


def test_exact_live_string_internal_server_error():
    err = ("{'type': 'error', 'error': {'details': None, 'type': 'api_error', "
           "'message': 'Internal server error'}, 'request_id': 'req_e2bdck'}")
    et = _classify(err)
    assert et == ErrorType.READ_TIMEOUT
    assert et in _RETRYABLE


# ---------------------------------------------------------------------------
# Phrasing / case variants of the same transient family.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("err", [
    "api_error",
    "'type': 'api_error'",
    "API_ERROR",
    "Internal server error",
    "INTERNAL SERVER ERROR",
    "The server had an error while processing your request",
    "the server had an error",
])
def test_500_api_error_family_retryable(err):
    assert _classify(err) == ErrorType.READ_TIMEOUT
    assert _classify(err) in _RETRYABLE


# ---------------------------------------------------------------------------
# Ordering hazards: the new branch must NOT swallow other classes.
# ---------------------------------------------------------------------------

def test_throttle_still_wins_over_api_error_wording():
    # A 429 that also mentions api_error stays THROTTLE (checked first).
    assert _classify("429 rate limit; type api_error") == ErrorType.THROTTLE


def test_context_limit_not_shadowed():
    # Anthropic returns context overflow as invalid_request_error (400), not
    # api_error, so it must still classify as CONTEXT_LIMIT.
    assert _classify("prompt is too long: 250000 tokens > 200000") == ErrorType.CONTEXT_LIMIT


# ---------------------------------------------------------------------------
# The other half of the contract: persistent server error / genuine unknown
# must stay NON-retryable.
# ---------------------------------------------------------------------------

def test_bedrock_internalserverexception_not_matched_here():
    # Bedrock's persistent InternalServerException lowercases to
    # "internalserverexception" (no spaces) and must NOT be caught by the
    # spaced "internal server error" phrase -> stays UNKNOWN (non-retryable)
    # on THIS provider's classifier.
    et = _classify("InternalServerException: model failed")
    assert et == ErrorType.UNKNOWN
    assert et not in _RETRYABLE


@pytest.mark.parametrize("err", [
    "ValidationException: malformed request payload",
    "invalid_request_error: bad field",
    "AccessDeniedException",
    "some entirely unrecognized failure",
])
def test_genuine_fatal_errors_stay_non_retryable(err):
    et = _classify(err)
    assert et == ErrorType.UNKNOWN
    assert et not in _RETRYABLE


def test_connection_drop_family_still_retryable():
    # Guard the prior-session fix is untouched by this change.
    err = ("peer closed connection without sending complete message body "
           "(incomplete chunked read)")
    assert _classify(err) == ErrorType.READ_TIMEOUT
