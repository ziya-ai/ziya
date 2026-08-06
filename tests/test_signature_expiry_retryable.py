"""SigV4 signature-expiry vs token-expiry classification.

Regression guard for the bedrock-mantle failure where an expired request
signature was surfaced as a fatal, non-retryable error:

    ERROR_CHUNK: "Error code: 401 - {... 'error': {'type':
                  'authentication_error', 'message': 'Signature expired:
                  20260801T183402Z is now earlier than 20260801T183916Z
                  (20260801T184416Z - 5 min.)'}}"
    NON_RETRYABLE_END: provider error already surfaced, ending stream ...

_AsyncSigV4Transport signs with wall clock at signing time. When more than
AWS's 5-minute window elapses before the server evaluates the request, it
401s. The string matched nothing in _classify_error and fell through to
UNKNOWN (retryable=False).

It is transient AND self-healing: signing happens per-attempt inside the
transport, so a retry stamps a fresh X-Amz-Date.

The central distinction pinned here is that two adjacent wordings have
OPPOSITE correct outcomes:
  - expired SIGNATURE -> retryable (re-signing repairs it)
  - expired TOKEN     -> AUTH, fatal (no retry can mint credentials)
Collapsing them either way produces a wrong answer: retrying a dead token
delays the honest message, and failing a stale signature aborts a run that
would have succeeded on the next attempt.
"""
import pytest

from app.providers.anthropic_direct import AnthropicDirectProvider
from app.providers.openai_responses_mantle import OpenAIResponsesMantleProvider
from app.providers.base import ErrorType

_RETRYABLE = (ErrorType.THROTTLE, ErrorType.READ_TIMEOUT, ErrorType.OVERLOADED)

# The verbatim observed failure, request_id included — that is what the
# classifier actually receives.
_OBSERVED = (
    "Error code: 401 - {'type': 'error', 'request_id': "
    "'req_fd7uushnpeyhohlgyt65xgi2w6ydnejgkfw6kpt6ibpdrv3ygliq', 'error': "
    "{'type': 'authentication_error', 'message': 'Signature expired: "
    "20260801T183402Z is now earlier than 20260801T183916Z "
    "(20260801T184416Z - 5 min.)'}}"
)

# Both providers share _AsyncSigV4Transport, so both see both faults.
_CLASSIFIERS = [
    pytest.param(AnthropicDirectProvider._classify_error, id="anthropic"),
    pytest.param(OpenAIResponsesMantleProvider._classify_error, id="openai-mantle"),
]


@pytest.mark.parametrize("classify", _CLASSIFIERS)
def test_observed_signature_expiry_is_retryable(classify):
    assert classify(_OBSERVED) == ErrorType.READ_TIMEOUT
    assert classify(_OBSERVED) in _RETRYABLE


@pytest.mark.parametrize("classify", _CLASSIFIERS)
def test_signature_expiry_is_not_auth(classify):
    """AUTH would abort AND rewrite the message as a credential problem.

    streaming_tool_executor.py replaces an AUTH message with the
    credential-help template, so misclassifying here tells the user to run
    mwinit while their credentials are valid.
    """
    assert classify(_OBSERVED) != ErrorType.AUTH


@pytest.mark.parametrize("classify", _CLASSIFIERS)
@pytest.mark.parametrize("err", [
    "Signature expired: 20260801T183402Z is now earlier than 20260801T183916Z",
    "RequestExpired: Request has expired.",
    "RequestTimeTooSkewed: The difference between the request time and the "
    "current time is too large.",
])
def test_signature_expiry_family_retryable(classify, err):
    assert classify(err) in _RETRYABLE


@pytest.mark.parametrize("classify", _CLASSIFIERS)
@pytest.mark.parametrize("err", [
    "ExpiredToken: The security token included in the request is expired",
    "An error occurred (ExpiredTokenException) when calling the operation",
    "InvalidClientTokenId: The security token included in the request is invalid",
])
def test_expired_token_is_fatal_auth(classify, err):
    """A dead token is NOT repairable by re-signing — must not be retried."""
    et = classify(err)
    assert et == ErrorType.AUTH
    assert et not in _RETRYABLE


@pytest.mark.parametrize("classify", _CLASSIFIERS)
def test_wrong_secret_key_stays_fatal(classify):
    """SignatureDoesNotMatch is a bad key — no retry can repair it."""
    err = ("SignatureDoesNotMatch: The request signature we calculated does "
           "not match the signature you provided.")
    assert classify(err) not in _RETRYABLE


@pytest.mark.parametrize("classify", _CLASSIFIERS)
def test_throttle_still_classified(classify):
    assert classify("Error 429: too many requests") == ErrorType.THROTTLE


@pytest.mark.parametrize("classify", _CLASSIFIERS)
def test_overloaded_still_classified(classify):
    assert classify("service overloaded (529)") == ErrorType.OVERLOADED


def test_anthropic_context_limit_still_classified():
    c = AnthropicDirectProvider._classify_error
    assert c("prompt is too long for this model") == ErrorType.CONTEXT_LIMIT


def test_anthropic_connection_drop_still_retryable():
    """Guard against the new branches shadowing the existing drop family."""
    c = AnthropicDirectProvider._classify_error
    assert c("peer closed connection (incomplete chunked read)") in _RETRYABLE


def test_anthropic_api_error_500_still_retryable():
    c = AnthropicDirectProvider._classify_error
    assert c("Error code: 500 - {'type': 'api_error'}") in _RETRYABLE


@pytest.mark.parametrize("classify", _CLASSIFIERS)
def test_genuine_unknown_still_non_retryable(classify):
    et = classify("ValidationException: malformed request payload")
    assert et not in _RETRYABLE
