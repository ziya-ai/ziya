"""The provider credential registry is the single source of truth.

History (the bug class this prevents): the env var names identifying each
provider's credentials were restated in six places —
ModelManager._check_{openai,zai,meta,google}_credentials, providers/
factory.py, services/model_resolver.py, provider_detection._PROVIDER_ENV,
the prose list in build_setup_help(), and the README quick-start. They had
already drifted: build_setup_help() listed only anthropic/openai/google
(omitting zai and meta entirely), and the README told users to export
ZAI_API_TOKEN while every reader looked up ZAI_API_KEY — so a user
following the documented instructions got "credentials not found".

These tests pin the registry to the endpoints that actually exist and to
the code that consumes credentials, so a new provider cannot be added
without being detectable and documented.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.utils import provider_detection as pd


def _config_endpoints() -> set[str]:
    from app.config import models_config as c
    return set(c.MODEL_CONFIGS)


# ── The core contract ─────────────────────────────────────────────

def test_every_endpoint_has_a_credential_declaration():
    """Each endpoint in MODEL_CONFIGS is either registered or bedrock.

    bedrock is exempt because its check is file/profile-based rather than a
    single env var (_has_bedrock_credentials).
    """
    declared = {p.endpoint for p in pd.PROVIDER_CREDENTIALS} | {"bedrock"}
    missing = _config_endpoints() - declared
    assert not missing, (
        f"Endpoints {sorted(missing)} have models in MODEL_CONFIGS but no "
        f"ProviderCredential entry, so detect_available_providers() cannot "
        f"see them and /api/endpoints would report them as always-available. "
        f"Add them to PROVIDER_CREDENTIALS."
    )


def test_no_registered_endpoint_is_unknown_to_config():
    extra = {p.endpoint for p in pd.PROVIDER_CREDENTIALS} - _config_endpoints()
    assert not extra, (
        f"PROVIDER_CREDENTIALS declares {sorted(extra)} but MODEL_CONFIGS has "
        f"no such endpoint — the entry is dead or the name is misspelled."
    )


def test_detect_covers_every_configured_endpoint():
    result = pd.detect_available_providers()
    missing = _config_endpoints() - set(result)
    assert not missing, f"detect_available_providers() omits {sorted(missing)}"


# ── Registry consistency ──────────────────────────────────────────

def test_keys_are_nonempty_and_unique_per_provider():
    seen: dict[str, str] = {}
    for p in pd.PROVIDER_CREDENTIALS:
        assert p.keys, f"{p.endpoint} declares no credential keys"
        for k in p.keys:
            # MODEL_API_KEY is Meta's own generic name; any genuine sharing
            # of one var between two providers would make detection ambiguous.
            assert k not in seen or k == "MODEL_API_KEY", (
                f"{k} is claimed by both {seen[k]} and {p.endpoint}"
            )
            seen.setdefault(k, p.endpoint)


def test_canonical_key_is_first():
    for p in pd.PROVIDER_CREDENTIALS:
        assert p.canonical_key == p.keys[0]


# ── The specific drift that motivated this ────────────────────────

def test_setup_help_mentions_every_provider():
    """The generated help can't omit a provider the way the old list did."""
    help_text = pd.build_setup_help()
    for p in pd.PROVIDER_CREDENTIALS:
        assert p.canonical_key in help_text, (
            f"build_setup_help() never mentions {p.canonical_key} "
            f"({p.endpoint}) — a user of that provider gets no guidance."
        )


def test_readme_documented_zai_token_is_accepted(monkeypatch):
    """ZAI_API_TOKEN (the README's name) must satisfy the zai endpoint."""
    for v in ("ZAI_API_KEY", "ZHIPUAI_API_KEY", "ZAI_API_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(pd, "_has_bedrock_credentials", lambda: False)
    monkeypatch.setenv("ZAI_API_TOKEN", "t")
    assert pd.detect_available_providers()["zai"] is True


def test_whitespace_only_key_is_not_a_credential(monkeypatch):
    monkeypatch.setattr(pd, "_has_bedrock_credentials", lambda: False)
    monkeypatch.setenv("GOOGLE_API_KEY", "   ")
    assert pd.detect_available_providers()["google"] is False


# ── Auto-select safety ────────────────────────────────────────────

def test_meta_is_never_autoselected(monkeypatch):
    """Meta's contributor tier trains on submitted prompts; Ziya sends source
    code, so meta must only ever be chosen by explicit name."""
    monkeypatch.setattr(pd, "_has_bedrock_credentials", lambda: False)
    for p in pd.PROVIDER_CREDENTIALS:
        for k in p.keys:
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("META_API_KEY", "k")
    assert pd.maybe_autoselect_endpoint("bedrock", False) is None


def test_single_autoselectable_provider_is_chosen(monkeypatch):
    monkeypatch.setattr(pd, "_has_bedrock_credentials", lambda: False)
    for p in pd.PROVIDER_CREDENTIALS:
        for k in p.keys:
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZAI_API_KEY", "k")
    assert pd.maybe_autoselect_endpoint("bedrock", False) == "zai"


# ── Availability cache ────────────────────────────────────────────

def test_availability_cache_is_stable_until_refreshed(monkeypatch):
    monkeypatch.setattr(pd, "_has_bedrock_credentials", lambda: False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    pd.refresh_availability()
    assert pd.get_availability()["google"] is False
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert pd.get_availability()["google"] is False, "snapshot should be cached"
    assert pd.refresh_availability()["google"] is True
