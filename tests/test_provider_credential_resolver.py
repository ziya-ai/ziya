"""
Tests for the credential RESOLVER on the provider registry
(resolve_credential / credential_error in app/utils/provider_detection.py).

The registry made credential *detection* single-sourced. The resolver makes
credential *reading* single-sourced too, which is the half that actually
bit users:

  ModelManager._check_zai_credentials, providers/factory.py and
  services/model_resolver.py each spelled their own
  ``os.environ.get("ZAI_API_KEY") or os.environ.get("ZHIPUAI_API_KEY")``
  chain. None of them accepted ZAI_API_TOKEN -- the variable the README
  told users to export. So a user who followed the documented instructions
  had a key set, saw the endpoint reported as AVAILABLE by
  detect_available_providers (which does consult the registry), and then
  got "credentials not found" at model init. The two halves disagreed.

These tests pin the resolver's contract: alias precedence, blank handling,
and that every registered alias actually resolves.
"""
import pytest

import app.utils.provider_detection as pd


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    """Clear every registered credential var, driven off the registry."""
    for p in pd.PROVIDER_CREDENTIALS:
        for k in p.keys:
            monkeypatch.delenv(k, raising=False)
    yield


# ---- resolve_credential ----

def test_returns_none_when_nothing_set():
    for p in pd.PROVIDER_CREDENTIALS:
        assert pd.resolve_credential(p.endpoint) is None


def test_canonical_key_resolves(monkeypatch):
    for p in pd.PROVIDER_CREDENTIALS:
        monkeypatch.setenv(p.canonical_key, f"val-{p.endpoint}")
        assert pd.resolve_credential(p.endpoint) == f"val-{p.endpoint}"
        monkeypatch.delenv(p.canonical_key)


def test_every_registered_alias_resolves(monkeypatch):
    """Each accepted alias must satisfy its endpoint on its own.

    This is the assertion that would have caught the ZAI_API_TOKEN gap: the
    alias is registered, so reading it must work.
    """
    for p in pd.PROVIDER_CREDENTIALS:
        for key in p.keys:
            monkeypatch.setenv(key, f"via-{key}")
            got = pd.resolve_credential(p.endpoint)
            assert got == f"via-{key}", (
                f"{key} is registered for endpoint {p.endpoint!r} but "
                f"resolve_credential returned {got!r}"
            )
            monkeypatch.delenv(key)


def test_readme_documented_zai_token_resolves(monkeypatch):
    """The specific drift: ZAI_API_TOKEN must produce a usable key."""
    monkeypatch.setenv("ZAI_API_TOKEN", "tok")
    assert pd.resolve_credential("zai") == "tok"


def test_canonical_key_wins_over_alias(monkeypatch):
    """Declaration order is precedence -- canonical first."""
    monkeypatch.setenv("ZAI_API_KEY", "canonical")
    monkeypatch.setenv("ZHIPUAI_API_KEY", "alias")
    monkeypatch.setenv("ZAI_API_TOKEN", "readme-alias")
    assert pd.resolve_credential("zai") == "canonical"


def test_earlier_alias_wins_over_later(monkeypatch):
    monkeypatch.setenv("ZHIPUAI_API_KEY", "second")
    monkeypatch.setenv("ZAI_API_TOKEN", "third")
    assert pd.resolve_credential("zai") == "second"


def test_blank_value_is_not_a_credential(monkeypatch):
    """A whitespace-only key must be treated as unset, matching detection.

    Otherwise an empty export makes the endpoint look configured and the
    provider fails later with an opaque auth error.
    """
    monkeypatch.setenv("GOOGLE_API_KEY", "   ")
    assert pd.resolve_credential("google") is None


def test_blank_canonical_falls_through_to_alias(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "  ")
    monkeypatch.setenv("ZHIPUAI_API_KEY", "real")
    assert pd.resolve_credential("zai") == "real"


def test_value_is_stripped(monkeypatch):
    """Trailing newlines from \`export KEY=$(cat file)\` must not reach the SDK."""
    monkeypatch.setenv("GOOGLE_API_KEY", "  key-with-space\n")
    assert pd.resolve_credential("google") == "key-with-space"


def test_bedrock_has_no_single_env_credential():
    """Bedrock resolves via profiles/files, not one var -- must be None."""
    assert pd.resolve_credential("bedrock") is None


def test_unknown_endpoint_is_none():
    assert pd.resolve_credential("no-such-endpoint") is None


# ---- Agreement with detection ----

def test_resolver_agrees_with_detection(monkeypatch):
    """detect_available_providers and resolve_credential must never disagree.

    This is the invariant whose violation produced the original bug:
    availability said yes while the reader said "credentials not found".
    """
    monkeypatch.setattr(pd, "_has_bedrock_credentials", lambda: False)
    for p in pd.PROVIDER_CREDENTIALS:
        for key in p.keys:
            monkeypatch.setenv(key, "x")
            available = pd.detect_available_providers()[p.endpoint]
            resolved = pd.resolve_credential(p.endpoint) is not None
            assert available == resolved, (
                f"with {key} set, detection says available={available} but "
                f"resolve_credential says {resolved}"
            )
            monkeypatch.delenv(key)


# ---- credential_error ----

def test_error_names_the_canonical_key():
    for p in pd.PROVIDER_CREDENTIALS:
        msg = pd.credential_error(p.endpoint)
        assert p.canonical_key in msg
        assert p.label in msg


def test_error_lists_accepted_aliases():
    """A user who set an alias shouldn't be told it is unsupported."""
    msg = pd.credential_error("zai")
    assert "ZHIPUAI_API_KEY" in msg
    assert "ZAI_API_TOKEN" in msg


def test_error_includes_the_note_when_present():
    assert "gcloud auth" in pd.credential_error("google")


def test_error_for_unknown_endpoint_is_not_an_exception():
    msg = pd.credential_error("no-such-endpoint")
    assert isinstance(msg, str) and msg
