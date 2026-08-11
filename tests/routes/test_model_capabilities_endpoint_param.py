"""
Tests for the ``?endpoint=`` parameter on GET /api/model-capabilities
(app/routes/model_routes.py).

Why the parameter exists: the model config modal has an endpoint pulldown,
so it must show a target model's limits BEFORE any switch is applied.  The
route previously resolved every model against the RUNNING endpoint only, so
selecting a different endpoint returned ``{"error": "Unknown model ID"}``
and the sliders never populated.

Two properties are load-bearing:

  1. RESOLUTION -- a model is resolved against the requested endpoint, and
     the running endpoint remains the default when the param is absent
     (backward compatibility for every existing caller).
  2. POLICY CLAMP -- a caller-supplied endpoint is clamped to the enterprise
     allowlist, exactly as /api/available-models, /api/model-tiers and
     /api/set-model clamp it.  Without the clamp this read route discloses
     the model catalog and limits of a policy-forbidden endpoint (the same
     CWE-200 issue fixed for /api/model-tiers; see
     tests/routes/test_model_tiers_endpoint_clamp.py).
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


def _first_model(endpoint: str):
    from app.agents.models import ModelManager
    models = ModelManager.MODEL_CONFIGS.get(endpoint, {})
    return next(iter(models), None)


def _get(client, url, **env):
    """GET with the allow-all bypass cleared unless explicitly provided."""
    with patch.dict(os.environ, env, clear=False):
        if "ZIYA_ALLOW_ALL_ENDPOINTS" not in env:
            os.environ.pop("ZIYA_ALLOW_ALL_ENDPOINTS", None)
        return client.get(url)


# ---- Cross-endpoint resolution ----

def test_model_on_another_endpoint_resolves(client):
    """A google model must resolve when ?endpoint=google, while bedrock runs.

    This is the exact case that failed before the parameter existed: the
    modal asks about the endpoint the user just picked, not the running one.
    """
    google = _first_model("google")
    if google is None:
        pytest.skip("no google models configured")
    with patch("app.plugins.get_allowed_endpoints", return_value=None):
        resp = _get(client,
                    f"/api/model-capabilities?model={google}&endpoint=google",
                    ZIYA_ENDPOINT="bedrock")
    assert resp.status_code == 200
    data = resp.json()
    assert "error" not in data, data
    # The sliders the modal drives off must be present.
    assert "max_output_tokens_range" in data
    assert "max_input_tokens_range" in data


def test_same_model_without_endpoint_param_still_works(client):
    """Omitting ?endpoint= must keep resolving against the running endpoint."""
    model = _first_model("bedrock")
    assert model is not None
    with patch("app.plugins.get_allowed_endpoints", return_value=None):
        resp = _get(client, f"/api/model-capabilities?model={model}",
                    ZIYA_ENDPOINT="bedrock")
    assert resp.status_code == 200
    assert "error" not in resp.json()


def test_cross_endpoint_model_without_param_is_an_error(client):
    """Control for the test above: the param is what makes it resolve.

    A google model asked for while bedrock is running -- with NO endpoint
    param -- must not resolve.  If this passed, the cross-endpoint test
    would be proving nothing.
    """
    google = _first_model("google")
    if google is None:
        pytest.skip("no google models configured")
    with patch("app.plugins.get_allowed_endpoints", return_value=None):
        resp = _get(client, f"/api/model-capabilities?model={google}",
                    ZIYA_ENDPOINT="bedrock")
    assert resp.status_code == 200
    assert "error" in resp.json(), (
        "a google model resolved against the bedrock endpoint -- the "
        "?endpoint= test is not actually exercising cross-endpoint lookup"
    )


def test_unknown_endpoint_returns_error_not_exception(client):
    """A bogus endpoint must produce a clean error, not a 500.

    MODEL_CONFIGS[endpoint] would raise KeyError without the guard.
    """
    with patch("app.plugins.get_allowed_endpoints", return_value=None):
        resp = _get(client,
                    "/api/model-capabilities?model=x&endpoint=no-such-endpoint",
                    ZIYA_ENDPOINT="bedrock")
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_unknown_model_on_valid_endpoint_returns_error(client):
    with patch("app.plugins.get_allowed_endpoints", return_value=None):
        resp = _get(client,
                    "/api/model-capabilities?model=definitely-not-a-model&endpoint=bedrock",
                    ZIYA_ENDPOINT="bedrock")
    assert resp.status_code == 200
    assert "error" in resp.json()


# ---- Policy clamp (CWE-200) ----

def test_forbidden_endpoint_is_clamped(client):
    """Asking about a forbidden endpoint must not disclose its catalog.

    The clamp falls back to the first allowed endpoint, so a model that
    exists ONLY on the forbidden endpoint must fail to resolve.
    """
    google = _first_model("google")
    if google is None:
        pytest.skip("no google models configured")
    with patch("app.plugins.get_allowed_endpoints", return_value=["bedrock"]):
        resp = _get(client,
                    f"/api/model-capabilities?model={google}&endpoint=google",
                    ZIYA_ENDPOINT="bedrock")
    assert resp.status_code == 200
    assert "error" in resp.json(), (
        "capabilities for a policy-forbidden endpoint's model were returned; "
        "the endpoint param bypassed the allowlist clamp"
    )


def test_allowed_endpoint_passes_through(client):
    google = _first_model("google")
    if google is None:
        pytest.skip("no google models configured")
    with patch("app.plugins.get_allowed_endpoints",
               return_value=["bedrock", "google"]):
        resp = _get(client,
                    f"/api/model-capabilities?model={google}&endpoint=google",
                    ZIYA_ENDPOINT="bedrock")
    assert resp.status_code == 200
    assert "error" not in resp.json()


def test_allow_all_endpoints_bypasses_clamp(client):
    google = _first_model("google")
    if google is None:
        pytest.skip("no google models configured")
    with patch("app.plugins.get_allowed_endpoints", return_value=["bedrock"]):
        resp = _get(client,
                    f"/api/model-capabilities?model={google}&endpoint=google",
                    ZIYA_ENDPOINT="bedrock", ZIYA_ALLOW_ALL_ENDPOINTS="1")
    assert resp.status_code == 200
    assert "error" not in resp.json()


def test_plugin_failure_does_not_break_the_route(client):
    model = _first_model("bedrock")
    with patch("app.plugins.get_allowed_endpoints",
               side_effect=ImportError("no plugins")):
        resp = _get(client,
                    f"/api/model-capabilities?model={model}&endpoint=bedrock",
                    ZIYA_ENDPOINT="bedrock")
    assert resp.status_code == 200
    assert "error" not in resp.json()


# ---- Consistency with /api/endpoints ----

def test_every_endpoint_default_model_has_capabilities(client):
    """The modal's post-switch sequence must not dead-end.

    On an endpoint switch the UI selects that endpoint's default_model and
    immediately fetches its capabilities.  Every such pair must resolve, or
    the user lands on an endpoint whose sliders are blank.
    """
    with patch("app.plugins.get_allowed_endpoints", return_value=None):
        eps = _get(client, "/api/endpoints", ZIYA_ENDPOINT="bedrock").json()
        for ep in eps["endpoints"]:
            default = ep["default_model"]
            if not default:
                continue
            resp = _get(
                client,
                f"/api/model-capabilities?model={default}&endpoint={ep['id']}",
                ZIYA_ENDPOINT="bedrock",
            )
            assert resp.status_code == 200, ep["id"]
            assert "error" not in resp.json(), (
                f"default model {default!r} on endpoint {ep['id']!r} has no "
                f"capabilities: {resp.json()}"
            )
