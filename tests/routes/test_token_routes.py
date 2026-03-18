"""
Tests for token counting routes.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch


@pytest.fixture
def client():
    """Create test client."""
    from fastapi import FastAPI
    from app.routes.token_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_token_count(client):
    """POST /token-count accepts text and returns token_count."""
    with patch('app.agents.agent.estimate_token_count', return_value=150):
        response = client.post("/api/token-count", json={"text": "Hello, how are you?"})
        assert response.status_code == 200
        data = response.json()
        assert data["token_count"] == 150


def test_token_count_missing_text(client):
    """POST /token-count without required text field returns 422."""
    response = client.post("/api/token-count", json={"messages": [{"role": "user"}]})
    assert response.status_code == 422


@pytest.mark.skip(reason="get_accurate_token_counts not yet implemented")
def test_accurate_token_count(client):
    """Test accurate token counting for files."""
    with patch('app.utils.directory_util.get_accurate_token_count', return_value={
        "file1.py": 100, "file2.py": 200,
    }):
        response = client.post("/api/accurate-token-count", json={
            "file_paths": ["file1.py", "file2.py"]
        })
        assert response.status_code == 200
        data = response.json()
        assert data["file1.py"] == 100
        assert data["file2.py"] == 200
