"""
Regression tests: credential-retrieval network failures must not be
misclassified as expired/invalid credentials.

Covers the ada/iibs client-init failure signature observed live:
  "Error when retrieving credentials from custom-process: ...
   failed to initialize iibs client: Get \"\": unsupported protocol scheme \"\""

This occurs while a configured credential_process (ada) tries to reach
Midway's IIBS endpoint and gets an empty redirect back -- before any real
auth exchange happens. It is a transient network/client-init glitch, not
an expired token, and must not trigger a misleading "run mwinit" prompt.
"""
import pytest

from app.plugins.interfaces import AuthProvider


class _ConcreteAuthProvider(AuthProvider):
    """Minimal concrete subclass so we can exercise the base is_auth_error
    implementation directly (AuthProvider itself is abstract)."""

    def check_credentials(self, profile_name=None, region=None):
        return True, "ok"

    def get_credential_help_message(self, error_context=None):
        return "help"


@pytest.fixture
def provider():
    return _ConcreteAuthProvider()


ADA_IIBS_ERROR = (
    'Error when retrieving credentials from custom-process: '
    '2026/07/25 02:28:27 Failed to force refresh the credentials: '
    'failed to initialize iibs client: Get "": unsupported protocol scheme ""'
)


def test_ada_iibs_client_init_failure_is_not_an_auth_error(provider):
    assert provider.is_auth_error(ADA_IIBS_ERROR) is False


@pytest.mark.parametrize("phrase", [
    "unsupported protocol scheme",
    "failed to initialize iibs client",
])
def test_network_phrases_are_not_auth_errors(provider, phrase):
    err = f"CredentialRetrievalError: something about {phrase} happened, credentials unavailable"
    assert provider.is_auth_error(err) is False


def test_existing_network_indicators_still_excluded(provider):
    """Pre-existing network-outage phrases must remain excluded (no
    regression from adding the new ones)."""
    for phrase in (
        "no such host", "dial tcp", "i/o timeout", "context deadline exceeded",
        "connection refused", "network is unreachable", "connection reset",
    ):
        err = f"CredentialRetrievalError: {phrase} while retrieving credentials"
        assert provider.is_auth_error(err) is False, phrase


def test_genuine_expired_credentials_still_detected_as_auth_error(provider):
    """A real expired/invalid token (no network signature) must still be
    classified as an auth error so the mwinit prompt fires when it should."""
    assert provider.is_auth_error("ExpiredToken: the security token has expired") is True
    assert provider.is_auth_error("CredentialRetrievalError: invalid credentials") is True
