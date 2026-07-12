"""
Regression coverage for PenPal #123 [CWE-200]: /api/model-tiers accepted a
caller-supplied ?endpoint= parameter but — unlike /api/available-models and
/api/set-model — did NOT clamp it to the enterprise endpoint allowlist
(get_allowed_endpoints). A caller could therefore enumerate the tier->model
resolution for a policy-forbidden endpoint.

Fix: get_model_tiers now applies the same clamp get_available_models uses —
if the requested endpoint isn't in the allowlist, it falls back to the first
allowed endpoint. Community builds return None (no restriction) so this is a
no-op there; ZIYA_ALLOW_ALL_ENDPOINTS=1 bypasses for dev/testing.
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


def test_forbidden_endpoint_is_clamped_to_allowlist(client):
    """A caller-supplied endpoint outside the enterprise allowlist must be
    clamped to the first allowed endpoint, not honored."""
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock"}, clear=False):
        os.environ.pop("ZIYA_ALLOW_ALL_ENDPOINTS", None)
        with patch("app.plugins.get_allowed_endpoints", return_value=["bedrock"]):
            resp = client.get("/api/model-tiers?endpoint=google")
    assert resp.status_code == 200
    # Clamped back to the only allowed endpoint — the forbidden one is NOT echoed.
    assert resp.json()["endpoint"] == "bedrock"


def test_allowed_endpoint_passes_through(client):
    """An endpoint that IS in the allowlist is honored unchanged."""
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock"}, clear=False):
        os.environ.pop("ZIYA_ALLOW_ALL_ENDPOINTS", None)
        with patch("app.plugins.get_allowed_endpoints", return_value=["bedrock", "google"]):
            resp = client.get("/api/model-tiers?endpoint=google")
    assert resp.status_code == 200
    assert resp.json()["endpoint"] == "google"


def test_no_restriction_is_noop(client):
    """Community build: get_allowed_endpoints() -> None means no clamp, any
    endpoint is honored (backward compatible)."""
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock"}, clear=False):
        os.environ.pop("ZIYA_ALLOW_ALL_ENDPOINTS", None)
        with patch("app.plugins.get_allowed_endpoints", return_value=None):
            resp = client.get("/api/model-tiers?endpoint=google")
    assert resp.status_code == 200
    assert resp.json()["endpoint"] == "google"


def test_allow_all_endpoints_bypass(client):
    """ZIYA_ALLOW_ALL_ENDPOINTS=1 bypasses the clamp even under a restriction."""
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_ALLOW_ALL_ENDPOINTS": "1"}, clear=False):
        with patch("app.plugins.get_allowed_endpoints", return_value=["bedrock"]):
            resp = client.get("/api/model-tiers?endpoint=google")
    assert resp.status_code == 200
    assert resp.json()["endpoint"] == "google"


def test_available_models_parity(client):
    """Sanity parity: /api/available-models already clamps the same way, so the
    two endpoint-taking read routes now behave consistently under a restriction.
    """
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock"}, clear=False):
        os.environ.pop("ZIYA_ALLOW_ALL_ENDPOINTS", None)
        with patch("app.plugins.get_allowed_endpoints", return_value=["bedrock"]):
            tiers = client.get("/api/model-tiers?endpoint=google")
            models = client.get("/api/available-models?endpoint=google")
    # Both clamp: tiers echoes bedrock; available-models returns bedrock's models
    # (never raises / never serves the forbidden endpoint's set).
    assert tiers.json()["endpoint"] == "bedrock"
    assert models.status_code == 200
