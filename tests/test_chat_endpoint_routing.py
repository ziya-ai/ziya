"""
Regression guard for streaming-endpoint routability — the provider factory
is the single source of truth.

History (the bug class this prevents): the web ``chat_endpoint`` in
app/server.py used to keep its OWN model-routing decision — a chain of
``is_*`` predicates feeding ``use_direct_streaming`` — SEPARATE from
``app.providers.factory.create_provider``.  Every new endpoint had to be added
in BOTH places; forgetting the chat_endpoint half silently dropped the model
(fable5/mythos5 → ``200 null`` pre-0.7.3.0; then zai → 500; then openrouter →
500, all the identical class).

The generalization removed the duplication entirely: ``create_provider`` now
exposes ``is_endpoint_supported(endpoint, model_config)`` backed by a single
``_SUPPORTED_ENDPOINTS`` set, and ``chat_endpoint`` calls it instead of
re-deriving anything.  There is now ONE list.

This test pins the two halves of that one seam to each other so they can never
drift: every endpoint ``create_provider`` actually dispatches on must be in
``_SUPPORTED_ENDPOINTS`` (and vice-versa).  Adding an endpoint to the factory
without registering it — or registering one the factory can't build — fails
here.  It also asserts ``chat_endpoint`` consults the factory rather than
reintroducing a local predicate list (guarding against regressing the
generalization itself).
"""

from __future__ import annotations

import inspect
import re

import pytest


def _factory_dispatch_endpoints() -> set[str]:
    """Endpoints create_provider actually branches on (``endpoint == "x"``)."""
    from app.providers import factory
    src = inspect.getsource(factory.create_provider)
    return set(re.findall(r'endpoint\s*==\s*[\'"]([a-z_]+)[\'"]', src))


# ── The core contract: the registry matches the dispatch ────────

def test_supported_set_matches_factory_dispatch():
    """``_SUPPORTED_ENDPOINTS`` must equal exactly the endpoints
    create_provider dispatches on — no more, no less.

    This is the single invariant that makes the whole bug class impossible:
    the routability registry and the actual provider construction can't drift,
    because they're asserted equal here.  Adding a branch to create_provider
    without adding it to the set (or vice-versa) fails immediately.
    """
    from app.providers.factory import _SUPPORTED_ENDPOINTS
    dispatch = _factory_dispatch_endpoints()
    registry = set(_SUPPORTED_ENDPOINTS)
    missing_from_registry = dispatch - registry
    extra_in_registry = registry - dispatch
    assert not missing_from_registry, (
        f"create_provider dispatches on {sorted(missing_from_registry)} but they "
        f"are not in _SUPPORTED_ENDPOINTS — is_endpoint_supported would wrongly "
        f"reject them (→ 500 on the web path). Add them to _SUPPORTED_ENDPOINTS."
    )
    assert not extra_in_registry, (
        f"_SUPPORTED_ENDPOINTS lists {sorted(extra_in_registry)} but create_provider "
        f"has no branch for them — is_endpoint_supported would wrongly accept them "
        f"(→ ValueError when the provider is built). Remove them or add a factory branch."
    )


def test_is_endpoint_supported_true_for_every_dispatch_endpoint():
    """The public predicate returns True for every endpoint the factory builds."""
    from app.providers.factory import is_endpoint_supported
    for ep in _factory_dispatch_endpoints():
        assert is_endpoint_supported(ep), f"is_endpoint_supported({ep!r}) should be True"


def test_is_endpoint_supported_false_for_unknown():
    from app.providers.factory import is_endpoint_supported
    assert is_endpoint_supported("nonexistent_endpoint_xyz") is False
    assert is_endpoint_supported("") is False


# ── Specific pins for the endpoints that regressed ──────────────

def test_zai_supported():
    """Explicit pin for the reported breakage: glm-5.2 on the zai endpoint."""
    from app.providers.factory import is_endpoint_supported
    assert is_endpoint_supported("zai") is True


def test_openrouter_supported():
    """openrouter was the second latent gap found alongside zai."""
    from app.providers.factory import is_endpoint_supported
    assert is_endpoint_supported("openrouter") is True


def test_core_endpoints_supported():
    """The always-present endpoints must remain routable (no regression)."""
    from app.providers.factory import is_endpoint_supported
    for ep in ("bedrock", "anthropic", "openai", "google"):
        assert is_endpoint_supported(ep) is True


# ── Guard the generalization itself ─────────────────────────────

def test_chat_endpoint_delegates_to_factory_not_local_predicates():
    """chat_endpoint must consult the factory's routability check, NOT
    re-derive its own per-endpoint predicate list.

    This guards against regressing the generalization back into the
    drift-prone dual-list shape that caused the original bug.  We require:
      (1) chat_endpoint references is_endpoint_supported, and
      (2) it no longer contains a hand-rolled `is_<x>_direct` endpoint-equality
          predicate chain.
    """
    from app import server
    src = inspect.getsource(server.chat_endpoint)
    assert "is_endpoint_supported" in src, (
        "chat_endpoint should call factory.is_endpoint_supported for routing; "
        "re-deriving routability locally is what caused the zai/openrouter 500s."
    )
    # The old smell: per-endpoint equality predicates assigned to is_*_direct.
    local_equality_predicates = re.findall(
        r'is_[a-z_]+_direct\s*=\s*ziya_env\(\s*[\'"]ZIYA_ENDPOINT[\'"]\s*\)\s*==',
        src,
    )
    assert not local_equality_predicates, (
        f"chat_endpoint reintroduced local endpoint-equality predicates "
        f"({len(local_equality_predicates)} found) — route via "
        f"is_endpoint_supported instead so the routable set lives in one place."
    )
