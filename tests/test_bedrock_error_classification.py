"""Exhaustive characterization of BedrockProvider._classify_error.

Locks the full error-classification contract for the bedrock path, and in
particular guards the Tier-1 alignment fix: httpx-style mid-stream drops
(RemoteProtocolError / ChunkedEncodingError) must be recognized
CASE-INSENSITIVELY and classified as a retriable transient, matching
AnthropicDirectProvider.

Background: the bedrock connection-drop family was matched case-SENSITIVELY
against the raw error string and lacked the two httpx phrases
("peer closed connection", "incomplete chunked read"). bedrock normally rides
botocore/urllib3 so those exact phrases don't arise there, but a shared or
wrapped httpx transport can surface them; without the fix they fell through to
UNKNOWN (non-retryable) and ended the stream on a transient drop.

The retryable set (see bedrock.py ErrorEvent wiring) is exactly
{THROTTLE, READ_TIMEOUT, OVERLOADED}. SERVER_ERROR, CONTEXT_LIMIT and UNKNOWN
are intentionally NOT retryable.
"""
import pytest

from app.providers.bedrock import BedrockProvider
from app.providers.base import ErrorType

# The exact set the orchestrator retries (mirrors the retryable= expression
# in bedrock.py's ErrorEvent construction).
_RETRYABLE = (ErrorType.THROTTLE, ErrorType.READ_TIMEOUT, ErrorType.OVERLOADED)


def _classify(s: str) -> ErrorType:
    return BedrockProvider._classify_error(s)


# ---------------------------------------------------------------------------
# Tier-1 fix: httpx mid-stream drop family, matched case-insensitively.
# ---------------------------------------------------------------------------

def test_incomplete_chunked_read_exact_live_string():
    """The exact string observed live on bedrock-mantle/fable-5."""
    err = ("peer closed connection without sending complete message body "
           "(incomplete chunked read)")
    et = _classify(err)
    assert et == ErrorType.READ_TIMEOUT
    assert et in _RETRYABLE


@pytest.mark.parametrize("err", [
    "peer closed connection without sending complete message body (incomplete chunked read)",
    "PEER CLOSED CONNECTION",
    "Peer Closed Connection without sending complete message body",
    "incomplete chunked read",
    "Incomplete Chunked Read",
    "httpx.RemoteProtocolError: server disconnected without sending a response",
    "RemoteProtocolError",
    "remoteprotocolerror: peer closed connection",
    "Server disconnected without sending a response",
    "SERVER DISCONNECTED",
])
def test_httpx_drop_family_case_insensitive_retryable(err):
    """Every httpx-style drop phrase, in any case, must be retriable."""
    assert _classify(err) == ErrorType.READ_TIMEOUT
    assert _classify(err) in _RETRYABLE


# ---------------------------------------------------------------------------
# Pre-existing connection-quality family (case-sensitive botocore/urllib3
# phrasing) must still classify as retriable READ_TIMEOUT.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("err", [
    "UNEXPECTED_EOF_WHILE_READING",
    "EOF occurred in violation of protocol",
    "SSL validation failed",
    "SSLError: bad handshake",
    "Connection reset by peer",
    "ConnectionResetError: [Errno 54]",
    "Connection aborted",
    "Connection broken: IncompleteRead",
    "EndpointConnectionError: Could not connect",
    "ConnectionClosedError: Connection was closed",
])
def test_botocore_connection_family_retryable(err):
    assert _classify(err) == ErrorType.READ_TIMEOUT
    assert _classify(err) in _RETRYABLE


# ---------------------------------------------------------------------------
# Throttle family.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("err", [
    "ThrottlingException",
    "Too many tokens, please wait",
    "Too many requests",
    "rate limit exceeded",
    "RATE LIMIT reached",
])
def test_throttle_family(err):
    assert _classify(err) == ErrorType.THROTTLE
    assert _classify(err) in _RETRYABLE


# ---------------------------------------------------------------------------
# Overloaded family.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("err", [
    "model overloaded",
    "OVERLOADED",
    "error 529",
    "ServiceUnavailableException",
])
def test_overloaded_family(err):
    assert _classify(err) == ErrorType.OVERLOADED
    assert _classify(err) in _RETRYABLE


# ---------------------------------------------------------------------------
# Timeout family (distinct wording from the connection-drop families).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("err", [
    "Read timed out",
    "ReadTimeoutError",
    "request timeout after 60s",
    "TIMEOUT",
])
def test_timeout_family(err):
    assert _classify(err) == ErrorType.READ_TIMEOUT
    assert _classify(err) in _RETRYABLE


# ---------------------------------------------------------------------------
# Non-retryable classes must stay non-retryable.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("err,expected", [
    ("Input is too long", ErrorType.CONTEXT_LIMIT),
    ("prompt is too long for this model", ErrorType.CONTEXT_LIMIT),
    ("payload too large", ErrorType.CONTEXT_LIMIT),
    ("InternalServerException", ErrorType.SERVER_ERROR),
    ("ValidationException: malformed request", ErrorType.UNKNOWN),
    ("AccessDeniedException", ErrorType.UNKNOWN),
    ("some entirely unrecognized failure", ErrorType.UNKNOWN),
])
def test_non_retryable_classes(err, expected):
    et = _classify(err)
    assert et == expected
    assert et not in _RETRYABLE


def test_precedence_throttle_beats_connection_words():
    """A throttle string that also mentions a connection keeps THROTTLE
    (throttle is checked first and is the actionable class)."""
    assert _classify("ThrottlingException: connection reset by peer") == ErrorType.THROTTLE

def test_empty_string_is_unknown():
    assert _classify("") == ErrorType.UNKNOWN



# ---------------------------------------------------------------------------
# CredentialRetrievalError network-vs-auth split (ada/iibs client-init
# failures, e.g. "failed to initialize iibs client: Get \"\": unsupported
# protocol scheme \"\"" from a corp-network midway redirect returning empty).
# These occur before any auth exchange, so they are transient network
# failures and must be retried, never treated as expired credentials.
# ---------------------------------------------------------------------------

_ADA_IIBS_ERROR = (
    'Error when retrieving credentials from custom-process: '
    '2026/07/25 02:28:27 Failed to force refresh the credentials: '
    'failed to initialize iibs client: Get "": unsupported protocol scheme ""'
)


def test_ada_iibs_client_init_failure_is_retryable():
    assert _classify(_ADA_IIBS_ERROR) == ErrorType.READ_TIMEOUT
    assert _classify(_ADA_IIBS_ERROR) in _RETRYABLE


@pytest.mark.parametrize("network_phrase", [
    "no such host",
    "dial tcp",
    "i/o timeout",
    "context deadline exceeded",
    "connection refused",
    "unsupported protocol scheme",
    "failed to initialize iibs client",
])
def test_credential_retrieval_error_network_variants_retryable(network_phrase):
    err = f"CredentialRetrievalError: something about {network_phrase} happened"
    assert _classify(err) == ErrorType.READ_TIMEOUT
    assert _classify(err) in _RETRYABLE


def test_credential_retrieval_error_without_network_signature_is_auth():
    """A CredentialRetrievalError with no network signature (e.g. a genuine
    expired/invalid token surfaced via the credential_process) is a real
    auth failure and must NOT be retried automatically."""
    err = "CredentialRetrievalError: the security token included in the request is expired"
    assert _classify(err) == ErrorType.AUTH
    assert _classify(err) not in _RETRYABLE
