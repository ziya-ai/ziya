"""
Tests for model configuration routes.

These tests verify the HTTP contract of model_routes.py by mocking
ModelManager at its import location within the route module.

The route handlers call ModelManager directly — they do NOT delegate to
app.server helper functions — so all patches must target
'app.routes.model_routes.ModelManager'.
"""
import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch


@pytest.fixture
def client():
    """Create test client with model routes."""
    from fastapi import FastAPI
    from app.routes.model_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# The route handler uses ModelManager directly (not via app.server).
MM_PATCH = 'app.routes.model_routes.ModelManager'


# ---- Read-only endpoints (straightforward mocks) ----

def test_get_current_model(client):
    """GET /current-model returns model_alias and endpoint."""
    mock_mm = MagicMock()
    mock_mm.get_model_alias.return_value = "sonnet3.5"
    mock_mm.get_model_id.return_value = "anthropic.claude-3-5-sonnet-v2"
    mock_mm.get_model_settings.return_value = {
        "temperature": 0.3, "top_k": 15,
        "max_output_tokens": 8192, "max_input_tokens": 200000,
    }
    mock_mm.get_model_config.return_value = {
        "model_id": "anthropic.claude-3-5-sonnet-v2",
        "token_limit": 200000,
        "max_output_tokens": 8192,
    }
    mock_mm._state = {"aws_region": "us-west-2"}

    with patch(MM_PATCH, mock_mm), \
         patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "AWS_REGION": "us-west-2"}):
        response = client.get("/api/current-model")

    assert response.status_code == 200
    data = response.json()
    assert data["model_alias"] == "sonnet3.5"
    assert data["endpoint"] == "bedrock"


def test_get_model_id(client):
    """GET /model-id returns the model alias."""
    mock_mm = MagicMock()
    mock_mm.get_model_alias.return_value = "sonnet3.5"

    with patch(MM_PATCH, mock_mm):
        response = client.get("/api/model-id")

    assert response.status_code == 200
    assert response.json()["model_id"] == "sonnet3.5"


def test_get_available_models(client):
    """GET /available-models returns model list for current endpoint."""
    mock_mm = MagicMock()
    mock_mm.MODEL_CONFIGS = {
        "bedrock": {
            "model1": {"model_id": "anthropic.model1-v1"},
            "model2": {"model_id": "anthropic.model2-v1"},
        },
    }

    with patch(MM_PATCH, mock_mm), \
         patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock"}):
        response = client.get("/api/available-models")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    aliases = {m["alias"] for m in data}
    assert aliases == {"model1", "model2"}


def test_get_model_capabilities(client):
    """GET /model-capabilities returns capability flags."""
    mock_mm = MagicMock()
    mock_mm.MODEL_CONFIGS = {
        "bedrock": {
            "sonnet3.5": {"model_id": "anthropic.claude-3-5-sonnet-v2"},
        },
    }
    mock_mm.get_model_config.return_value = {
        "model_id": "anthropic.claude-3-5-sonnet-v2",
        "supports_thinking": False,
        "supports_vision": True,
        "token_limit": 200000,
        "max_output_tokens": 8192,
        "family": "claude",
    }
    mock_mm.get_model_settings.return_value = {
        "temperature": 0.3, "thinking_mode": False,
        "max_output_tokens": 8192,
    }

    with patch(MM_PATCH, mock_mm), \
         patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "sonnet3.5"}):
        response = client.get("/api/model-capabilities")

    assert response.status_code == 200
    data = response.json()
    assert data["supports_vision"] is True
    assert "max_output_tokens" in data


# ---- Mutating endpoints ----
# set_model and update_model_settings are deeply integrated with
# ModelManager internals (initialize_model, agent chain creation, etc.).
# Rather than mocking 15+ call sites, we test boundary behaviour:
# input validation, error paths, and the contract shape.

def test_set_model_empty_id_returns_error(client):
    """POST /set-model with empty model_id returns an error status code."""
    # The route raises HTTPException(400) for empty model_id, but an outer
    # try/except re-wraps as 500.  Either way the body contains the reason.
    response = client.post("/api/set-model", json={"model_id": ""})
    assert response.status_code in (400, 500)
    assert "required" in response.text.lower() or "model" in response.text.lower()


def test_set_model_unknown_model_returns_error(client):
    """POST /set-model with an unrecognised model returns an error."""
    mock_mm = MagicMock()
    mock_mm.MODEL_CONFIGS = {"bedrock": {}}
    mock_mm._state = {"aws_region": "us-west-2", "current_model_id": None}
    mock_mm.get_model_alias.return_value = "old-model"

    with patch(MM_PATCH, mock_mm), \
         patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "old-model"}):
        response = client.post("/api/set-model", json={"model_id": "nonexistent-model"})

    # Should fail — model not in MODEL_CONFIGS
    assert response.status_code in (400, 404, 500)


def test_update_model_settings_returns_settings(client):
    """POST /model-settings returns applied settings on success."""
    mock_mm = MagicMock()
    mock_mm.get_model_alias.return_value = "sonnet3.5"
    mock_mm.get_model_config.return_value = {
        "model_id": "anthropic.claude-3-5-sonnet-v2",
        "supports_thinking": False,
        "family": "claude",
    }
    mock_mm.filter_model_kwargs.return_value = {"temperature": 0.5, "max_tokens": 8192}
    # initialize_model returns a mock model object with needed attrs
    mock_model = MagicMock()
    mock_model.model.model_kwargs = {"temperature": 0.5, "max_tokens": 8192}
    mock_model.max_tokens = 8192
    mock_mm.initialize_model.return_value = mock_model
    mock_mm._state = {"aws_region": "us-west-2"}

    with patch(MM_PATCH, mock_mm), \
         patch('app.agents.agent.model', mock_model), \
         patch.dict(os.environ, {
             "ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "sonnet3.5",
             "ZIYA_MAX_OUTPUT_TOKENS": "8192",
         }):
        response = client.post("/api/model-settings", json={
            "temperature": 0.5, "max_output_tokens": 8192,
        })

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "settings" in data
