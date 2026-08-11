"""
Tests for GET /api/endpoints (app/routes/model_routes.py).

This route exists because the frontend cannot know two things the backend
owns: the enterprise endpoint allowlist, and which endpoints actually have
credentials.  The model config modal's endpoint pulldown is built entirely
from this response, so three properties are load-bearing:

  1. POLICY CLAMP -- a policy-forbidden endpoint must never be offered as a
     selectable choice.  This mirrors the clamp on /api/available-models,
     /api/model-tiers and /api/set-model; the bug class being guarded is
     "one route forgot the clamp" (see tests/routes/
     test_model_tiers_endpoint_clamp.py for the same guard on that route).
  2. AVAILABILITY -- credential state is read from the startup snapshot in
     app.utils.provider_detection, NOT re-probed per request.  The modal
     polls, and probing walks ~/.aws + enumerates botocore profiles on the
     event loop, so a regression to per-request probing is a latency bug.
  3. UNKNOWN != UNAVAILABLE -- an endpoint missing from the snapshot must
     report available=True.  Reporting unknown as False would grey out a
     working endpoint and lock the user out of it.

default_model is asserted because model aliases do not carry across
endpoints: on an endpoint switch the UI has nothing else to select.
"""
import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.routes.model_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _get(client, **env):
    """GET /api/endpoints with ZIYA_ALLOW_ALL_ENDPOINTS removed by default.

    The bypass var leaking in from the developer's shell would silently
    disable the clamp under test and make these assertions vacuous.
    """
    with patch.dict(os.environ, env, clear=False):
        if "ZIYA_ALLOW_ALL_ENDPOINTS" not in env:
            os.environ.pop("ZIYA_ALLOW_ALL_ENDPOINTS", None)
        return client.get("/api/endpoints")


# ---- Shape ----

def test_returns_active_and_endpoint_list(client):
    resp = _get(client, ZIYA_ENDPOINT="bedrock")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] == "bedrock"
    assert isinstance(data["endpoints"], list) and data["endpoints"]
    for ep in data["endpoints"]:
        assert set(ep) >= {"id", "default_model", "model_count", "available", "hint"}


def test_every_listed_endpoint_is_real(client):
    """Every id must exist in MODEL_CONFIGS -- the UI switches to these."""
    from app.agents.models import ModelManager
    resp = _get(client, ZIYA_ENDPOINT="bedrock")
    ids = [e["id"] for e in resp.json()["endpoints"]]
    assert ids, "no endpoints returned"
    for ep_id in ids:
        assert ep_id in ModelManager.MODEL_CONFIGS


def test_default_model_is_valid_for_its_endpoint(client):
    """default_model must exist on that endpoint.

    The modal selects it verbatim after an endpoint switch, so a stale or
    cross-endpoint value would immediately 'Unknown model ID' the
    capabilities fetch.
    """
    from app.agents.models import ModelManager
    resp = _get(client, ZIYA_ENDPOINT="bedrock")
    for ep in resp.json()["endpoints"]:
        default = ep["default_model"]
        if default is None:
            continue
        assert default in ModelManager.MODEL_CONFIGS[ep["id"]], (
            f"default_model {default!r} for endpoint {ep['id']!r} is not a "
            f"model on that endpoint"
        )


def test_model_count_matches_config(client):
    from app.agents.models import ModelManager
    resp = _get(client, ZIYA_ENDPOINT="bedrock")
    for ep in resp.json()["endpoints"]:
        assert ep["model_count"] == len(ModelManager.MODEL_CONFIGS[ep["id"]])


# ---- Policy clamp ----

def test_forbidden_endpoints_are_not_listed(client):
    """A policy-forbidden endpoint must not appear as a selectable choice."""
    with patch("app.plugins.get_allowed_endpoints", return_value=["bedrock"]):
        resp = _get(client, ZIYA_ENDPOINT="bedrock")
    ids = [e["id"] for e in resp.json()["endpoints"]]
    assert ids == ["bedrock"]


def test_no_restriction_lists_everything(client):
    """Community build: get_allowed_endpoints() -> None means no clamp."""
    from app.agents.models import ModelManager
    with patch("app.plugins.get_allowed_endpoints", return_value=None):
        resp = _get(client, ZIYA_ENDPOINT="bedrock")
    ids = {e["id"] for e in resp.json()["endpoints"]}
    assert ids == set(ModelManager.MODEL_CONFIGS)


def test_allow_all_endpoints_bypasses_clamp(client):
    """ZIYA_ALLOW_ALL_ENDPOINTS=1 overrides the policy (dev/testing)."""
    from app.agents.models import ModelManager
    with patch("app.plugins.get_allowed_endpoints", return_value=["bedrock"]):
        resp = _get(client, ZIYA_ENDPOINT="bedrock",
                    ZIYA_ALLOW_ALL_ENDPOINTS="1")
    ids = {e["id"] for e in resp.json()["endpoints"]}
    assert ids == set(ModelManager.MODEL_CONFIGS)


def test_plugin_failure_does_not_break_the_route(client):
    """The modal must still populate when the plugin system is unavailable."""
    with patch("app.plugins.get_allowed_endpoints",
               side_effect=ImportError("no plugins")):
        resp = _get(client, ZIYA_ENDPOINT="bedrock")
    assert resp.status_code == 200
    assert resp.json()["endpoints"]


# ---- Credential availability ----

def test_unavailable_endpoint_is_listed_with_a_hint(client):
    """Missing credentials => available=False plus an actionable hint.

    Unavailable endpoints are deliberately LISTED rather than hidden, so the
    user can see the provider exists and what it needs.
    """
    import app.routes.model_routes as mr
    with patch("app.utils.provider_detection.get_availability",
               return_value={"google": False}), \
         patch("app.utils.provider_detection.missing_credential_hint",
               return_value="Set GOOGLE_API_KEY"), \
         patch("app.plugins.get_allowed_endpoints", return_value=None):
        resp = _get(client, ZIYA_ENDPOINT="bedrock")
    google = next(e for e in resp.json()["endpoints"] if e["id"] == "google")
    assert google["available"] is False
    assert google["hint"] == "Set GOOGLE_API_KEY"


def test_available_endpoint_has_no_hint(client):
    with patch("app.utils.provider_detection.get_availability",
               return_value={"google": True}), \
         patch("app.plugins.get_allowed_endpoints", return_value=None):
        resp = _get(client, ZIYA_ENDPOINT="bedrock")
    google = next(e for e in resp.json()["endpoints"] if e["id"] == "google")
    assert google["available"] is True
    assert google["hint"] is None


def test_endpoint_absent_from_snapshot_is_available(client):
    """Unknown must NOT be reported as unavailable.

    An empty/partial snapshot (probe skipped, new endpoint added after
    startup) would otherwise grey out every working endpoint.
    """
    with patch("app.utils.provider_detection.get_availability",
               return_value={}), \
         patch("app.plugins.get_allowed_endpoints", return_value=None):
        resp = _get(client, ZIYA_ENDPOINT="bedrock")
    assert all(e["available"] is True for e in resp.json()["endpoints"])
    assert all(e["hint"] is None for e in resp.json()["endpoints"])


def test_detection_failure_degrades_to_available(client):
    """If provider_detection cannot be consulted, fail OPEN.

    Availability is advisory UI metadata; the real gate is /api/set-model.
    Failing closed here would make the modal unusable on an install where
    the detection module errors.
    """
    with patch("app.utils.provider_detection.get_availability",
               side_effect=RuntimeError("boom")), \
         patch("app.plugins.get_allowed_endpoints", return_value=None):
        resp = _get(client, ZIYA_ENDPOINT="bedrock")
    assert resp.status_code == 200
    assert all(e["available"] is True for e in resp.json()["endpoints"])


def test_availability_is_read_from_snapshot_not_reprobed(client):
    """The route must consult the cached snapshot, never detect directly.

    Regression guard: the modal polls this route, and
    detect_available_providers() walks ~/.aws and enumerates botocore
    profiles synchronously on the event loop.
    """
    with patch("app.utils.provider_detection.detect_available_providers") as probe, \
         patch("app.plugins.get_allowed_endpoints", return_value=None):
        # Prime the cache so get_availability() has no reason to probe.
        from app.utils.provider_detection import refresh_availability
        probe.return_value = {"bedrock": True}
        refresh_availability()
        probe.reset_mock()
        resp = _get(client, ZIYA_ENDPOINT="bedrock")
    assert resp.status_code == 200
    assert probe.call_count == 0, (
        "GET /api/endpoints re-probed credentials; it must read the cached "
        "startup snapshot via get_availability()"
    )


# ---- Consistency with the routes the UI calls next ----

def test_active_endpoint_is_listed_when_permitted(client):
    """The running endpoint must appear, or the modal shows a dropdown that
    excludes the user's own current selection."""
    with patch("app.plugins.get_allowed_endpoints", return_value=None):
        resp = _get(client, ZIYA_ENDPOINT="google")
    data = resp.json()
    assert data["active"] == "google"
    assert "google" in {e["id"] for e in data["endpoints"]}


def test_listed_endpoints_serve_models_via_available_models(client):
    """Every listed endpoint must return models from /api/available-models.

    These two routes are used back-to-back by the modal: pick an endpoint,
    then populate its model list.  An endpoint listed here that yields no
    models there is a dead choice in the UI.
    """
    with patch("app.plugins.get_allowed_endpoints", return_value=None):
        resp = _get(client, ZIYA_ENDPOINT="bedrock")
        for ep in resp.json()["endpoints"]:
            models = client.get(f"/api/available-models?endpoint={ep['id']}")
            assert models.status_code == 200, ep["id"]
            assert models.json(), f"endpoint {ep['id']} returned no models"
