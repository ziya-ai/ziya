"""
Tests for token counting routes.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch


@pytest.fixture
def mock_app():
    """Create a mock FastAPI app with model_manager."""
    from fastapi import FastAPI
    from app.routes.token_routes import router
    
    app = FastAPI()
    app.include_router(router)
    
    # Mock model_manager
    mock_manager = Mock()
    mock_manager.count_tokens = Mock(return_value=150)
    
    app.state.model_manager = mock_manager
    
    return app


@pytest.fixture
def client(mock_app):
    """Create test client."""
    return TestClient(mock_app)


def test_token_count(client, mock_app):
    """Test token counting."""
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"}
    ]
    
    response = client.post("/api/token-count", json={"messages": messages})
    assert response.status_code == 200
    data = response.json()
    assert data["token_count"] == 150
    
    # Verify count_tokens was called
    mock_app.state.model_manager.count_tokens.assert_called_once()


@pytest.mark.skip(reason="get_accurate_token_counts not yet implemented")
@patch('app.utils.token_counter.get_accurate_token_counts')
def test_accurate_token_count(mock_get_counts, client):
    """Test accurate token counting for files."""
    mock_get_counts.return_value = {
        "file1.py": 100,
        "file2.py": 200
    }
    
    response = client.post("/api/accurate-token-count", json={
        "files": ["file1.py", "file2.py"]
    })
    assert response.status_code == 200
    data = response.json()
    assert data["file1.py"] == 100
    assert data["file2.py"] == 200
