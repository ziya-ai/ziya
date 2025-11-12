"""
Tests for model configuration routes.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch


@pytest.fixture
def mock_app():
    """Create a mock FastAPI app with model_manager."""
    from fastapi import FastAPI
    from app.routes.model_routes import router
    
    app = FastAPI()
    app.include_router(router)
    
    # Mock model_manager
    mock_manager = Mock()
    mock_manager.model_name = "test-model"
    mock_manager.endpoint = "bedrock"
    mock_manager.model_id = "test-model-id"
    mock_manager.temperature = 0.7
    mock_manager.top_p = 0.9
    mock_manager.top_k = 50
    mock_manager.max_tokens = 4096
    
    app.state.model_manager = mock_manager
    
    return app


@pytest.fixture
def client(mock_app):
    """Create test client."""
    return TestClient(mock_app)


def test_get_current_model(client):
    """Test getting current model configuration."""
    response = client.get("/api/current-model")
    assert response.status_code == 200
    data = response.json()
    assert data["model"] == "test-model"
    assert data["endpoint"] == "bedrock"


def test_get_model_id(client):
    """Test getting model ID."""
    response = client.get("/api/model-id")
    assert response.status_code == 200
    data = response.json()
    assert data["model_id"] == "test-model-id"


def test_set_model_success(client, mock_app):
    """Test setting model successfully."""
    response = client.post("/api/set-model", json={
        "model": "new-model",
        "endpoint": "google"
    })
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["model"] == "new-model"
    assert data["endpoint"] == "google"
    
    # Verify set_model was called
    mock_app.state.model_manager.set_model.assert_called_once_with("new-model", "google")


def test_set_model_missing_params(client):
    """Test setting model with missing parameters."""
    response = client.post("/api/set-model", json={"model": "new-model"})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "required" in data["error"].lower()


def test_update_model_settings(client, mock_app):
    """Test updating model settings."""
    response = client.post("/api/model-settings", json={
        "temperature": 0.5,
        "top_p": 0.95,
        "top_k": 100,
        "max_tokens": 8192
    })
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["settings"]["temperature"] == 0.5
    assert data["settings"]["top_p"] == 0.95
    assert data["settings"]["top_k"] == 100
    assert data["settings"]["max_tokens"] == 8192


@patch('app.server.get_available_models')
def test_get_available_models(mock_get_models, client):
    """Test getting available models."""
    mock_get_models.return_value = {
        "bedrock": ["model1", "model2"],
        "google": ["model3"]
    }
    
    response = client.get("/api/available-models")
    assert response.status_code == 200
    data = response.json()
    assert "bedrock" in data
    assert len(data["bedrock"]) == 2


@patch('app.server.get_model_capabilities')
def test_get_model_capabilities(mock_get_caps, client):
    """Test getting model capabilities."""
    mock_get_caps.return_value = {
        "supports_streaming": True,
        "max_tokens": 4096
    }
    
    response = client.get("/api/model-capabilities")
    assert response.status_code == 200
    data = response.json()
    assert data["supports_streaming"] is True
    assert data["max_tokens"] == 4096
