"""
Tests for model configuration routes.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch


@pytest.fixture
def client():
    """Create test client with model routes."""
    from fastapi import FastAPI
    from app.routes.model_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@patch('app.routes.model_routes.ModelManager')
def test_get_current_model(mock_mm, client):
    """GET /current-model returns model_alias and endpoint."""
    with patch('app.server.get_current_model', return_value={
        'model_id': 'sonnet3.5', 'model_alias': 'sonnet3.5',
        'endpoint': 'bedrock', 'display_model_id': 'anthropic.claude-3-5-sonnet-v2',
    }):
        response = client.get("/api/current-model")
        assert response.status_code == 200
        data = response.json()
        assert data["model_alias"] == "sonnet3.5"
        assert data["endpoint"] == "bedrock"


@patch('app.routes.model_routes.ModelManager')
def test_get_model_id(mock_mm, client):
    """GET /model-id returns the model alias."""
    mock_mm.get_model_alias.return_value = "sonnet3.5"
    response = client.get("/api/model-id")
    assert response.status_code == 200
    assert response.json()["model_id"] == "sonnet3.5"


def test_set_model_success(client):
    """POST /set-model delegates to server.set_model."""
    with patch('app.server.set_model', return_value={
        'success': True, 'model': 'new-model', 'endpoint': 'google',
    }):
        response = client.post("/api/set-model", json={
            "model": "new-model", "endpoint": "google",
        })
        assert response.status_code == 200
        assert response.json()["success"] is True


def test_set_model_missing_params(client):
    """POST /set-model with missing endpoint returns error."""
    with patch('app.server.set_model', return_value={
        'success': False, 'error': 'Model and endpoint are required',
    }):
        response = client.post("/api/set-model", json={"model": "new-model"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "required" in data["error"].lower()


def test_update_model_settings(client):
    """POST /model-settings delegates to server.update_model_settings."""
    with patch('app.server.update_model_settings', return_value={
        'success': True,
        'settings': {'temperature': 0.5, 'top_p': 0.95, 'top_k': 100, 'max_output_tokens': 8192},
    }):
        response = client.post("/api/model-settings", json={
            "temperature": 0.5, "top_p": 0.95, "top_k": 100, "max_output_tokens": 8192,
        })
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["settings"]["temperature"] == 0.5


def test_get_available_models(client):
    """GET /available-models returns model list by endpoint."""
    with patch('app.server.get_available_models', return_value={
        "bedrock": ["model1", "model2"], "google": ["model3"],
    }):
        response = client.get("/api/available-models")
        assert response.status_code == 200
        data = response.json()
        assert "bedrock" in data
        assert len(data["bedrock"]) == 2


@patch('app.routes.model_routes.ModelManager')
def test_get_model_capabilities(mock_mm, client):
    """GET /model-capabilities returns capability flags."""
    mock_mm.get_model_alias.return_value = "sonnet3.5"
    with patch('app.server.get_model_capabilities', return_value={
        "supports_streaming": True, "max_tokens": 4096,
    }):
        response = client.get("/api/model-capabilities")
        assert response.status_code == 200
        data = response.json()
        assert data["supports_streaming"] is True
        assert data["max_tokens"] == 4096
