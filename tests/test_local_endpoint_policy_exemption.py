"""The ``local`` endpoint is exempt from the enterprise allowlist on loopback.

The allowlist (ConfigProvider.get_allowed_endpoints) exists to control where
source code is sent. A local inference server on the loopback interface
sends it nowhere, so ``app.plugins.get_allowed_endpoints()`` adds ``local``
to a restricted list when ZIYA_LOCAL_MODEL_URL points at this machine --
unless an active provider returns False from allows_loopback_local(). A
local server on another host is a remote destination and stays restricted.

These drive the REAL resolver with a registered fake provider rather than
monkeypatching get_allowed_endpoints, so the code under test actually runs.
Every other caller (app.main, /api/endpoints, setup help, auto-select) goes
through this one function.
"""
import pytest

import app.plugins as plugins
from app.plugins.interfaces import ConfigProvider
from app.utils import local_models as lm


class _Policy(ConfigProvider):
    provider_id = "test-policy"

    def __init__(self, allowed, loopback_ok=True):
        self._allowed = allowed
        self._loopback_ok = loopback_ok

    def get_defaults(self):
        return {}

    def get_allowed_endpoints(self):
        return self._allowed

    def allows_loopback_local(self):
        return self._loopback_ok


@pytest.fixture
def providers(monkeypatch):
    """Isolate the provider registry; yield a function to install policies."""
    saved = list(plugins._config_providers)
    plugins._config_providers.clear()
    monkeypatch.delenv("ZIYA_LOCAL_MODEL_URL", raising=False)
    monkeypatch.setattr(lm, "KNOWN_LOCAL_PORTS", ())   # no real servers
    lm.reset_discovery_cache_for_tests()

    def _install(*ps):
        plugins._config_providers.clear()
        plugins._config_providers.extend(ps)

    yield _install
    plugins._config_providers.clear()
    plugins._config_providers.extend(saved)


# ── is_loopback_url ────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://localhost:11434", "http://localhost:8000/v1", "http://127.0.0.1:8000",
    "http://127.5.6.7/v1", "http://[::1]:8000/v1", "http://LOCALHOST:1234",
])
def test_loopback_urls(url):
    assert lm.is_loopback_url(url) is True


@pytest.mark.parametrize("url", [
    "http://192.168.1.20:8000/v1",      # LAN ds4 box
    "http://ds4.corp.example/v1",
    "http://0.0.0.0:8000",              # bind-all is not loopback
    "http://10.0.0.1",
    "",                                 # unparseable -> fail closed
    "not a url",
    "http:///v1",                       # hostless
])
def test_non_loopback_urls_fail_closed(url):
    assert lm.is_loopback_url(url) is False


def test_default_local_url_is_loopback(monkeypatch):
    monkeypatch.delenv("ZIYA_LOCAL_MODEL_URL", raising=False)
    assert lm.local_server_is_loopback() is True


# ── resolver behaviour ─────────────────────────────────────────────────

def test_no_policy_is_unchanged(providers):
    providers()
    assert plugins.get_allowed_endpoints() is None


def test_unrestricted_provider_is_unchanged(providers):
    providers(_Policy(None))
    assert plugins.get_allowed_endpoints() is None


def test_bedrock_only_policy_still_offers_local_on_loopback(providers):
    providers(_Policy(["bedrock"]))
    assert plugins.get_allowed_endpoints() == ["bedrock", "local"]


def test_remote_local_url_stays_restricted(providers, monkeypatch):
    monkeypatch.setenv("ZIYA_LOCAL_MODEL_URL", "http://192.168.1.20:8000")
    providers(_Policy(["bedrock"]))
    assert plugins.get_allowed_endpoints() == ["bedrock"]


def test_provider_can_shut_loopback_exemption_off(providers):
    providers(_Policy(["bedrock"], loopback_ok=False))
    assert plugins.get_allowed_endpoints() == ["bedrock"]


def test_any_provider_saying_no_wins(providers):
    providers(_Policy(["bedrock", "local"], loopback_ok=True),
              _Policy(["bedrock"], loopback_ok=False))
    # Intersection drops local; the second provider blocks the exemption.
    assert plugins.get_allowed_endpoints() == ["bedrock"]


def test_explicitly_listed_local_survives_remote_url(providers, monkeypatch):
    """Naming local in the allowlist is how an enterprise permits a LAN server."""
    monkeypatch.setenv("ZIYA_LOCAL_MODEL_URL", "http://192.168.1.20:8000")
    providers(_Policy(["bedrock", "local"]))
    assert plugins.get_allowed_endpoints() == ["bedrock", "local"]


def test_default_interface_permits_loopback():
    """A provider that never heard of the hook (older plugin) permits it."""
    class _Old(ConfigProvider):
        def get_defaults(self):
            return {}
    assert _Old().allows_loopback_local() is True


def test_hook_error_fails_closed(providers):
    class _Broken(_Policy):
        def allows_loopback_local(self):
            raise RuntimeError("boom")
    providers(_Broken(["bedrock"]))
    assert plugins.get_allowed_endpoints() == ["bedrock"]


# ── seam: the consumers see the exemption ──────────────────────────────

def test_setup_help_and_autoselect_see_local_under_bedrock_policy(providers, monkeypatch):
    import app.utils.provider_detection as pd
    for key in [k for p in pd.PROVIDER_CREDENTIALS for k in p.keys]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("ZIYA_ALLOW_ALL_ENDPOINTS", raising=False)
    monkeypatch.setattr(pd, "_has_bedrock_credentials", lambda: False)
    monkeypatch.setattr(pd, "available_aws_profiles", lambda: [])
    providers(_Policy(["bedrock"]))
    # Positive: local is offered in the setup menu under a bedrock-only policy.
    assert "--endpoint local" in pd.build_setup_help()
    # And with only the local server "credential" present, auto-select lands on it.
    monkeypatch.setenv("ZIYA_LOCAL_MODEL_URL", "http://localhost:11434")
    assert pd.maybe_autoselect_endpoint("bedrock", False) == "local"
