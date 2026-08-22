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


def test_token_count_missing_text_does_not_hit_error_path(client):
    """Omitting text must not raise inside the handler.

    Both `text` and `messages` are Optional on TokenCountRequest, so a body
    without `text` is valid (NOT 422 -- the previous expectation here was
    wrong).  The real defect it was masking: the debug log called
    len(request.text) on None, raising TypeError *after* a correct count, and
    the handler's blanket `except` then discarded that count and returned a
    fabricated {"token_count": 0}.  A swallowed crash is indistinguishable
    from a genuine zero, so assert the error path was never taken.
    """
    import app.routes.token_routes as token_routes

    errors = []
    original_error = token_routes.logger.error
    token_routes.logger.error = lambda msg, *a, **k: errors.append(str(msg))
    try:
        response = client.post("/api/token-count", json={})
    finally:
        token_routes.logger.error = original_error

    assert response.status_code == 200
    assert response.json()["token_count"] == 0
    assert not errors, (
        f"handler took the exception path instead of counting cleanly: {errors}"
    )


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
