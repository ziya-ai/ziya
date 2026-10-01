"""A credential fetch that died on the network is an outage, not expired credentials.

2026-09-28 13:45: a connectivity blip during ada's midway credential refresh
surfaced in chat as "Authentication Required — run mwinit" and paused memory
as ``authentication_error``.  The base AuthProvider.is_auth_error excluded
the network shape, but the enterprise AmazonAuthProvider overrides that
method with a bare indicator list ('credential', 'midway-auth',
'CredentialRetrievalError') and the core trusted its answer.  Pinned here:
the exclusion is applied by the core (``is_auth_failure``) regardless of
what the provider says, ``infra_kind`` reports connection_error, and a real
expired-credential message still classifies as auth (positive control).
"""
from app.utils.infra_errors import (
    ServiceInfraError, infra_kind, is_auth_failure, is_network_failure_text,
)

# Verbatim from ~/.ziya/memory/paused.json after the 13:45 outage.
IIBS_NETWORK = (
    "CredentialRetrievalError: Error when retrieving credentials from custom-process: "
    "2026/09/28 13:45:50 Failed to force refresh the credentials: failed to initialize "
    "iibs client: Request to IDP URL https://midway-auth.amazon.com/SSO/redirect?"
    "redirect_uri=https%3A%2F%2Fiibs-midway.corp.amazon.com%2F&client_id=..."
)
EXPIRED = "CredentialRetrievalError: Error when retrieving credentials from custom-process: midway token expired, please run 'mwinit -o'"


class CredentialRetrievalError(Exception):
    pass


class _EnterpriseLikeProvider:
    """Same shape as site-packages/plugins/amazon_auth.py: no network exclusion."""
    def is_auth_error(self, error_str: str) -> bool:
        return any(ind in error_str for ind in (
            'credential', 'mwinit', 'midway-auth', 'CredentialRetrievalError'))


def test_network_shaped_credential_failure_is_not_auth():
    prov = _EnterpriseLikeProvider()
    assert prov.is_auth_error(IIBS_NETWORK)                      # the plugin alone says auth
    assert is_network_failure_text(IIBS_NETWORK)
    assert is_auth_failure(CredentialRetrievalError(IIBS_NETWORK), prov) is False
    assert infra_kind(CredentialRetrievalError(IIBS_NETWORK)) == "connection_error"
    assert ServiceInfraError(CredentialRetrievalError(IIBS_NETWORK)).infra_kind == "connection_error"


# The exact list that AuthProvider.is_auth_error had built up incident by
# incident before the exclusion moved to infra_errors.  Pinned verbatim so a
# tidy-up of NETWORK_FAILURE_INDICATORS cannot silently drop one of them.
_BATTLE_WON_NETWORK_SHAPES = {
    'no such host', 'dial tcp', 'lookup ', 'i/o timeout',
    'context deadline exceeded', 'temporary failure in name resolution',
    'name or service not known', 'connection refused',
    'network is unreachable', 'could not connect to the endpoint',
    'endpointconnectionerror', 'connection reset', 'connection aborted',
    'unsupported protocol scheme', 'failed to initialize iibs client',
}


def test_network_indicator_list_is_a_superset_of_the_original():
    from app.utils.infra_errors import NETWORK_FAILURE_INDICATORS
    assert _BATTLE_WON_NETWORK_SHAPES <= set(NETWORK_FAILURE_INDICATORS)
    # And each one still does its job through the shared predicate.
    for shape in _BATTLE_WON_NETWORK_SHAPES:
        assert is_network_failure_text(f"CredentialRetrievalError: ... {shape} ..."), shape
    # The specific iibs form the "unsupported protocol scheme" entry was added for.
    assert is_network_failure_text(
        'failed to initialize iibs client: Get "": unsupported protocol scheme ""')


def test_expired_credential_still_auth():
    prov = _EnterpriseLikeProvider()
    assert is_auth_failure(CredentialRetrievalError(EXPIRED), prov) is True
    assert infra_kind(CredentialRetrievalError(EXPIRED)) == "authentication_error"


def test_known_credential_exception_is_auth_without_provider():
    from app.utils.custom_exceptions import KnownCredentialException
    exc = KnownCredentialException("Profile 'x' not found") \
        if callable(getattr(KnownCredentialException, "__init__", None)) else KnownCredentialException()
    assert is_auth_failure(exc, None) is True
    assert is_auth_failure(RuntimeError("some model error"), None) is False


def test_base_provider_interface_uses_shared_exclusion():
    from app.plugins.interfaces import AuthProvider
    # Abstract methods stubbed so the base is_auth_error can be exercised.
    class P(AuthProvider):
        pass
    for name in getattr(AuthProvider, "__abstractmethods__", ()):
        setattr(P, name, lambda self, *a, **k: None)
    P.__abstractmethods__ = frozenset()
    p = P()
    assert p.is_auth_error(IIBS_NETWORK) is False
    assert p.is_auth_error(EXPIRED) is True
