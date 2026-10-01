"""One definition of "the infrastructure failed, not the work".

Shared by the service-model resolver, the embedding provider, the task
system and the memory replay harness, so all four agree on what counts as
an outage.  Until 2026-09-27 each had its own list or none: the memory
subsystem treated an expired credential like any other exception and
failed open (contradiction -> AGREES, comparison -> ADD, embedding ->
None), the replay cache stored outages as answers, and the task system had
its own ``infra_kind`` vocabulary.

``ServiceInfraError`` is a BaseException on purpose.  ``app/memory`` wraps
its 12 model-call sites in 34 ``except Exception`` handlers that guess an
answer when the call fails; a BaseException passes every one of them and
reaches the subsystem entry point, which pauses memory instead
(app/utils/memory_pause.py).  It carries ``infra_kind`` so the task
executor's hold logic recognises it too.
"""
from __future__ import annotations

import re
from typing import Optional

INFRA_TYPES = frozenset({
    "CredentialRetrievalError", "NoCredentialsError", "PartialCredentialsError",
    "TokenRetrievalError", "SSOTokenLoadError", "UnauthorizedSSOTokenError",
    "ExpiredTokenException", "ExpiredToken", "UnrecognizedClientException",
    "EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError",
    "ConnectionClosedError", "ProxyConnectionError", "ConnectionError",
    "ConnectionResetError", "ConnectionRefusedError", "TimeoutError",
    "ClientConnectorError", "ServerDisconnectedError", "ThrottlingException",
    "ServiceUnavailableException", "ModelNotReadyException",
    "InternalServerException", "TaskInfraError", "ServiceInfraError",
})
INFRA_MESSAGE = re.compile(
    r"credential|expired token|token (has )?expired|security token.*invalid|"
    r"could not connect|connection (was )?(closed|reset|refused|aborted)|"
    r"timed out|read timeout|throttl|too many requests|service unavailable|"
    r"temporarily unavailable|name resolution|network is unreachable|"
    r"endpoint url|run 'mwinit'|failed to initialize iibs client|"
    r"request to idp url|no such host|dial tcp", re.I)

# A credential *retrieval* that failed for a network reason.  botocore wraps
# a credential_process failure in CredentialRetrievalError, whose message
# contains "credentials" and, for the Amazon provider, "midway-auth"; the
# ada/iibs client reports a dropped network as "failed to initialize iibs
# client: Request to IDP URL https://midway-auth...".  Every auth classifier
# that keys on those words (the enterprise AmazonAuthProvider.is_auth_error
# does exactly that) calls this an expired credential and tells the user to
# run mwinit, when the fix is to wait for the network.  Observed 2026-09-28
# 13:45: a connectivity blip surfaced as "Authentication Required" in chat
# and paused memory as ``authentication_error``.
#
# Checked BEFORE any credential/token pattern, here and in
# ``is_auth_failure``, so it is authoritative regardless of what an auth
# provider plugin's own ``is_auth_error`` says.
#
# The first fifteen entries were accumulated one incident at a time in
# AuthProvider.is_auth_error (app/plugins/interfaces.py) before being moved
# here; tests/test_infra_errors_credential_network.py pins that set so a
# later edit cannot drop one.  Provenance worth keeping:
#   - "unsupported protocol scheme" / "failed to initialize iibs client":
#     ada's iibs client failing to even form a request when a corp-network
#     midway redirect returned an empty body -- observed as
#     'failed to initialize iibs client: Get "": unsupported protocol scheme ""'.
#     Happens before any auth exchange, so it is a client-init glitch.
#   - "request to idp url": the same client dying mid-redirect on a dropped
#     network (2026-09-28 13:45), whose message also carries "midway-auth".
#   - botocore connection classes are matched by lowercased type name
#     ("endpointconnectionerror", "connecttimeouterror", "readtimeouterror")
#     because infra_kind() feeds the type name into the text it checks.
NETWORK_FAILURE_INDICATORS = (
    "no such host", "dial tcp", "lookup ", "i/o timeout",
    "context deadline exceeded", "temporary failure in name resolution",
    "name or service not known", "connection refused", "connection reset",
    "connection aborted", "network is unreachable",
    "could not connect to the endpoint", "endpointconnectionerror",
    "connecttimeouterror", "readtimeouterror", "unsupported protocol scheme",
    "failed to initialize iibs client", "request to idp url",
    "tls handshake timeout", "no route to host",
)


def is_network_failure_text(text: Optional[str]) -> bool:
    """True when ``text`` describes a dropped/unreachable network, whatever
    else (credential words included) it also says."""
    lowered = (text or "").lower()
    return any(ind in lowered for ind in NETWORK_FAILURE_INDICATORS)


def is_auth_failure(error: BaseException, auth_provider: Any = None) -> bool:
    """The one decision every chat/stream error path should make: is this
    an authentication failure the user must fix (re-authenticate), as
    opposed to an outage to wait out?

    A ``KnownCredentialException`` is authoritative.  Otherwise a network-
    shaped message is never auth, and only then is the active auth
    provider's own ``is_auth_error`` consulted.  Centralised because the
    core previously trusted the provider's answer directly, and the
    enterprise provider's override had no network exclusion.
    """
    error_str = str(error)
    try:
        from app.utils.custom_exceptions import KnownCredentialException
        if isinstance(error, KnownCredentialException):
            return True
    except ImportError:  # pragma: no cover
        pass
    if is_network_failure_text(error_str):
        return False
    return bool(auth_provider and auth_provider.is_auth_error(error_str))


# Kinds understood by app.utils.infra_gate / TaskInfraError.
_KIND_BY_PATTERN = (
    (re.compile(r"credential|token|mwinit|UnrecognizedClient", re.I), "authentication_error"),
    (re.compile(r"throttl|too many requests", re.I), "throttling"),
    (re.compile(r"timed out|timeout", re.I), "timeout"),
)


def infra_kind(exc: Optional[BaseException]) -> str:
    """The task-system ``infra_kind`` for an outage, else ``""``."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        k = getattr(exc, "infra_kind", "")
        if k:
            return k
        text = f"{type(exc).__name__}: {exc}"
        if type(exc).__name__ in INFRA_TYPES or INFRA_MESSAGE.search(str(exc) or ""):
            # A credential fetch that died on the network is a connection
            # outage, not an expired credential, even though its message
            # says "credentials".
            if is_network_failure_text(text):
                return "connection_error"
            for pat, kind in _KIND_BY_PATTERN:
                if pat.search(text):
                    return kind
            return "connection_error"
        exc = exc.__cause__ or exc.__context__
    return ""


def is_infra_error(exc: Optional[BaseException]) -> bool:
    """True when ``exc`` (or anything in its cause chain) is an outage."""
    return bool(infra_kind(exc))


class ServiceInfraError(BaseException):
    """A service-model or embedding call failed for an infrastructure reason.

    Raised by ``call_service_model`` and ``BedrockTitanProvider.embed_text``
    in place of the underlying botocore/HTTP error, so callers can tell an
    outage from a model answer without their own lists.
    """

    def __init__(self, cause: BaseException, where: str = ""):
        self.cause = cause
        self.where = where
        self.infra_kind = infra_kind(cause) or "connection_error"
        super().__init__(f"{type(cause).__name__}: {str(cause)[:400]}")
        self.__cause__ = cause
