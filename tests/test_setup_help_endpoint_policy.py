"""First-run credential guidance must respect the enterprise endpoint allowlist.

A build restricted to Bedrock (app.plugins.get_allowed_endpoints() ->
['bedrock']) previously still printed the full provider menu when no
credentials were found, telling the user to set ANTHROPIC_API_KEY /
OPENAI_API_KEY / etc. -- endpoints app.main then refuses with exit(1). It
could also silently auto-select one of them.

Covers app/utils/provider_detection.py: _permitted_endpoints,
build_setup_help, maybe_autoselect_endpoint.
"""
import pytest

import app.plugins as plugins
import app.utils.provider_detection as pd


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No provider credentials, no escape hatch, no AWS profiles."""
    for key in [k for p in pd.PROVIDER_CREDENTIALS for k in p.keys]:
        monkeypatch.delenv(key, raising=False)
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN", "ZIYA_ALLOW_ALL_ENDPOINTS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(pd, "available_aws_profiles", lambda: [])
    monkeypatch.setattr(pd, "_has_bedrock_credentials", lambda: False)
    pd.refresh_availability()
    yield


def _policy(monkeypatch, allowed):
    """Pretend a config provider declares ``allowed`` (None = unrestricted)."""
    monkeypatch.setattr(plugins, "get_allowed_endpoints", lambda: allowed)


# --- build_setup_help -----------------------------------------------------

def test_unrestricted_build_still_lists_every_provider(monkeypatch):
    """Community edition must be untouched by the filtering."""
    _policy(monkeypatch, None)
    msg = pd.build_setup_help()
    for provider in pd.PROVIDER_CREDENTIALS:
        assert provider.canonical_key in msg
    assert "aws configure" in msg


def test_bedrock_only_policy_hides_other_providers(monkeypatch):
    _policy(monkeypatch, ["bedrock"])
    msg = pd.build_setup_help()
    for provider in pd.PROVIDER_CREDENTIALS:
        assert provider.canonical_key not in msg, (
            f"{provider.canonical_key} advertised despite bedrock-only policy"
        )
        assert f"--endpoint {provider.endpoint}" not in msg
    # Still tells the user how to fix the actual problem.
    assert "aws configure" in msg
    assert "--profile" in msg


def test_partial_policy_lists_only_permitted(monkeypatch):
    _policy(monkeypatch, ["bedrock", "anthropic"])
    msg = pd.build_setup_help()
    assert "ANTHROPIC_API_KEY" in msg
    assert "OPENAI_API_KEY" not in msg
    assert "META_API_KEY" not in msg


def test_empty_policy_reports_misconfiguration(monkeypatch):
    """An allowlist permitting nothing must not emit a header with no body."""
    _policy(monkeypatch, [])
    msg = pd.build_setup_help()
    assert "allowed_endpoints" in msg
    assert "aws configure" not in msg


def test_allow_all_endpoints_escape_hatch(monkeypatch):
    _policy(monkeypatch, ["bedrock"])
    monkeypatch.setenv("ZIYA_ALLOW_ALL_ENDPOINTS", "1")
    msg = pd.build_setup_help()
    assert "OPENAI_API_KEY" in msg and "META_API_KEY" in msg


def test_policy_lookup_failure_falls_back_to_full_menu(monkeypatch):
    """A broken config provider must not suppress all guidance."""
    def boom():
        raise RuntimeError("config provider exploded")
    monkeypatch.setattr(plugins, "get_allowed_endpoints", boom)
    assert "OPENAI_API_KEY" in pd.build_setup_help()


# --- maybe_autoselect_endpoint --------------------------------------------

def test_no_autoselect_of_forbidden_provider(monkeypatch):
    """Bedrock-only build must not switch to a provider it will then refuse."""
    _policy(monkeypatch, ["bedrock"])
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert pd.maybe_autoselect_endpoint("bedrock", False) is None


def test_autoselect_of_permitted_provider_still_works(monkeypatch):
    _policy(monkeypatch, ["bedrock", "google"])
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert pd.maybe_autoselect_endpoint("bedrock", False) == "google"


def test_autoselect_unrestricted_unchanged(monkeypatch):
    _policy(monkeypatch, None)
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert pd.maybe_autoselect_endpoint("bedrock", False) == "google"
