"""
Tests for the /api/model-tiers route (app/routes/model_routes.py).

Unlike most model routes, /api/model-tiers reads the real
app.config.models_config (tier tags + resolve_tier_model) directly rather
than going through ModelManager, so these tests exercise the actual tier
resolution wired into the HTTP contract — no ModelManager mock needed.
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


def test_get_model_tiers_bedrock(client):
    """All five portable rungs are returned with resolutions and exact flags."""
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock"}):
        response = client.get("/api/model-tiers?endpoint=bedrock")

    assert response.status_code == 200
    data = response.json()
    assert data["endpoint"] == "bedrock"
    tiers = {t["tier"]: t for t in data["tiers"]}
    assert set(tiers) == {"xsmall", "small", "medium", "large", "frontier"}

    # Exactly-tagged rungs resolve to their tagged model and are flagged exact.
    # 'medium' is the center rung = default model (sonnet5 on Bedrock).
    assert tiers["xsmall"]["resolved_model"] == "nova-micro"
    assert tiers["xsmall"]["exact"] is True
    assert tiers["small"]["resolved_model"] == "nova-lite"
    assert tiers["small"]["exact"] is True
    assert tiers["medium"]["resolved_model"] == "sonnet5"
    assert tiers["medium"]["exact"] is True
    assert tiers["large"]["resolved_model"] == "opus4.8"
    assert tiers["large"]["exact"] is True
    assert tiers["frontier"]["resolved_model"] == "fable5"
    assert tiers["frontier"]["exact"] is True


def test_get_model_tiers_defaults_to_env_endpoint(client):
    """Omitting ?endpoint falls back to ZIYA_ENDPOINT."""
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "openai"}):
        response = client.get("/api/model-tiers")
    assert response.status_code == 200
    data = response.json()
    assert data["endpoint"] == "openai"
    tiers = {t["tier"]: t for t in data["tiers"]}
    assert tiers["medium"]["resolved_model"] == "gpt-5.5"
    assert tiers["medium"]["exact"] is True


def test_get_model_tiers_every_endpoint_has_center(client):
    """Every endpoint's center rung 'medium' (the default model) resolves
    exactly — the invariant the picker relies on for a sane default."""
    for ep in ("bedrock", "google", "openai", "anthropic", "zai"):
        with patch.dict(os.environ, {"ZIYA_ENDPOINT": ep}):
            response = client.get(f"/api/model-tiers?endpoint={ep}")
        assert response.status_code == 200
        tiers = {t["tier"]: t for t in response.json()["tiers"]}
        assert tiers["medium"]["exact"] is True, f"{ep} medium not exactly tagged"
