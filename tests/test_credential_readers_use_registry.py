"""
Enforce that credential READERS consult the registry rather than spelling
their own env-var alias chains.

The bug class: the same "key or fallback key" chain was hand-written in
ModelManager._check_{openai,zai,meta}_credentials, in
app/providers/factory.py, and in app/services/model_resolver.py. They drifted
from app.utils.provider_detection, which is what detect_available_providers
(and therefore /api/endpoints "available") consults. The visible symptom was
ZAI_API_TOKEN -- documented in the README, accepted by detection, rejected by
every reader -- so the modal showed z.ai as available and model init then
failed with "credentials not found".

These tests are source-level guards. They are deliberately structural: a
behavioral test cannot easily prove the ABSENCE of a second lookup path, and
it is the second path that constitutes the bug.
"""
from __future__ import annotations

import inspect
import re

import pytest

import app.utils.provider_detection as pd


# OPENAI_BASE_URL is registered as an openai credential alias (a compatible
# local server needs no key) but is ALSO ordinary endpoint configuration --
# every openai-family call site legitimately reads it to build the client.
# Exempt it from the "no direct alias read" rule, which targets API-KEY
# lookups only; the resolver still accepts it for detection purposes.
CONFIG_ALSO_CREDENTIAL = {"OPENAI_BASE_URL"}

# Every registered non-canonical alias. A reader mentioning one of these
# directly is doing its own resolution instead of asking the registry.
ALIAS_KEYS = sorted({
    k for p in pd.PROVIDER_CREDENTIALS for k in p.keys[1:]
} - CONFIG_ALSO_CREDENTIAL)

# Canonical keys too: a reader should not name these either, since
# resolve_credential() owns the whole lookup.
ALL_KEYS = sorted({
    k for p in pd.PROVIDER_CREDENTIALS for k in p.keys
} - CONFIG_ALSO_CREDENTIAL)


def _source(module_path: str) -> str:
    import importlib
    mod = importlib.import_module(module_path)
    return inspect.getsource(mod)


def _env_reads(src: str) -> set[str]:
    """Env var names read via os.environ.get / os.getenv / os.environ[...]."""
    pats = [
        r'os\.environ\.get\(\s*["\']([A-Z0-9_]+)["\']',
        r'os\.getenv\(\s*["\']([A-Z0-9_]+)["\']',
        r'os\.environ\[\s*["\']([A-Z0-9_]+)["\']\s*\]',
    ]
    found: set[str] = set()
    for p in pats:
        found |= set(re.findall(p, src))
    return found


# Modules that consume credentials and must therefore delegate. The registry
# module itself is excluded -- it is the one place allowed to read these.
READER_MODULES = [
    "app.providers.factory",
    "app.services.model_resolver",
]


@pytest.mark.parametrize("module_path", READER_MODULES)
def test_reader_does_not_read_credential_vars_directly(module_path):
    src = _source(module_path)
    offenders = _env_reads(src) & set(ALL_KEYS)
    assert not offenders, (
        f"{module_path} reads credential vars {sorted(offenders)} directly. "
        f"Use app.utils.provider_detection.resolve_credential(endpoint) so the "
        f"accepted-alias set lives in one place -- a private chain here is how "
        f"ZAI_API_TOKEN came to be accepted by detection but rejected at "
        f"model init."
    )


@pytest.mark.parametrize("module_path", READER_MODULES)
def test_reader_delegates_to_the_registry(module_path):
    """Positive check: the module actually calls the resolver.

    Without this, deleting the lookup entirely would satisfy the negative
    test above.
    """
    src = _source(module_path)
    assert "resolve_credential" in src, (
        f"{module_path} no longer resolves credentials via the registry"
    )


def test_model_manager_credential_checks_delegate():
    """ModelManager's per-endpoint checks must not re-derive alias chains."""
    from app.agents.models import ModelManager
    for name in ("_check_openai_credentials", "_check_zai_credentials",
                 "_check_meta_credentials"):
        fn = getattr(ModelManager, name, None)
        if fn is None:
            pytest.skip(f"{name} not present")
        src = inspect.getsource(fn)
        offenders = _env_reads(src) & set(ALIAS_KEYS)
        assert not offenders, (
            f"ModelManager.{name} reads alias vars {sorted(offenders)} "
            f"directly instead of calling resolve_credential()."
        )
        assert "resolve_credential" in src or "credential_error" in src, (
            f"ModelManager.{name} does not consult the credential registry"
        )


def test_registry_is_the_only_module_naming_aliases():
    """The alias names themselves should appear in exactly one module.

    Base-URL vars (ZAI_BASE_URL, OPENAI_BASE_URL, ...) are NOT covered here:
    they are endpoint configuration, not credentials, and legitimately vary
    per call site.
    """
    src = _source("app.utils.provider_detection")
    for key in ALIAS_KEYS:
        assert key in src, (
            f"{key} is treated as an accepted alias by tests but is absent "
            f"from the registry module"
        )


# ---- The end-to-end property these guards protect ----

def test_readme_alias_reaches_the_provider(monkeypatch):
    """A README-documented alias must produce a usable key at the factory.

    This is the user-visible contract: export the documented variable, and
    the provider gets a key. It closes the loop that the structural tests
    above only approximate.
    """
    for p in pd.PROVIDER_CREDENTIALS:
        for k in p.keys:
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZAI_API_TOKEN", "readme-token")

    captured = {}

    class _Stub:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import app.providers.openai_direct as od
    monkeypatch.setattr(od, "OpenAIDirectProvider", _Stub)

    from app.providers.factory import create_provider
    create_provider(endpoint="zai", model_id="glm-x", model_config={})

    assert captured.get("api_key") == "readme-token", (
        f"factory built the zai provider with api_key="
        f"{captured.get('api_key')!r}; the README-documented ZAI_API_TOKEN "
        f"did not reach the provider"
    )
