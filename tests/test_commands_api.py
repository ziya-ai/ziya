"""
Tests for app/api/commands.py — the shared command dispatch endpoint.

These tests exercise the goal command flow: synthesis → create →
launch → bind, using mocked storage to avoid filesystem side effects.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def mock_project_context():
    """Set up minimal project context mocks."""
    with patch("app.api.commands.get_project_root_or_none", return_value="/tmp/test-project"), \
         patch("app.api.commands.get_ziya_home", return_value="/tmp/ziya-home"), \
         patch("app.api.commands.get_project_dir", return_value="/tmp/ziya-home/projects/proj1"):
        yield


@pytest.fixture
def mock_project_storage(mock_project_context):
    """Mock ProjectStorage to return a project."""
    mock_project = MagicMock()
    mock_project.id = "proj1"

    mock_ps = MagicMock()
    mock_ps.get_by_path.return_value = mock_project

    with patch("app.api.commands.ProjectStorage", return_value=mock_ps):
        yield mock_project


@pytest.fixture
def client():
    """Create a test client with the commands router."""
    from fastapi import FastAPI
    from app.api.commands import router

    test_app = FastAPI()
    test_app.include_router(router)
    return TestClient(test_app)


class TestCommandDispatch:
    """Test the dispatch routing."""

    def test_unknown_command_returns_400(self, client):
        """Unknown commands are rejected."""
        resp = client.post("/api/v1/commands", json={
            "command": "foobar",
            "args": "something",
        })
        assert resp.status_code == 400
        assert "Unknown command" in resp.json()["detail"]

    def test_empty_goal_args_returns_usage(self, client, mock_project_context):
        """Goal with no args returns usage info."""
        resp = client.post("/api/v1/commands", json={
            "command": "goal",
            "args": "",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "error"
        assert "Usage" in data["message"]


class TestGoalCreate:
    """Test goal creation flow."""

    def test_creates_card_and_launches(self, client, mock_project_storage):
        """Goal create synthesizes a card, saves it, and launches."""
        mock_card = MagicMock()
        mock_card.id = "card_123"
        mock_card.source = "goal"

        mock_card_storage = MagicMock()
        mock_card_storage.create.return_value = mock_card

        mock_run = MagicMock()
        mock_run.id = "run_456"

        mock_binding = MagicMock()
        mock_binding.id = "bind_789"

        mock_binding_storage = MagicMock()
        mock_binding_storage.create.return_value = mock_binding

        # `_launch_run_for_card` is imported inside `_goal_create`, so we
        # patch it at the source module rather than at app.api.commands.
        with patch("app.api.commands.TaskCardStorage", return_value=mock_card_storage), \
             patch("app.api.commands.TaskBindingStorage", return_value=mock_binding_storage), \
             patch("app.api.task_cards._launch_run_for_card", new_callable=AsyncMock, return_value=mock_run):

            resp = client.post("/api/v1/commands", json={
                "command": "goal",
                "args": "fix all lint errors",
                "conversation_id": "conv_abc",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "goal_launched"
        assert data["data"]["card_id"] == "card_123"
        assert data["data"]["run_id"] == "run_456"
        assert data["data"]["binding_id"] == "bind_789"
        assert "fix all lint errors" in data["message"]

        # Verify card was created with goal source
        mock_card_storage.create.assert_called_once()
        create_arg = mock_card_storage.create.call_args[0][0]
        assert "Goal:" in create_arg.name
        assert "goal" in create_arg.tags

    def test_goal_without_conversation_id_skips_binding(self, client, mock_project_storage):
        """Goal without conversation_id still creates and launches."""
        mock_card = MagicMock()
        mock_card.id = "card_123"

        mock_card_storage = MagicMock()
        mock_card_storage.create.return_value = mock_card

        mock_run = MagicMock()
        mock_run.id = "run_456"

        with patch("app.api.commands.TaskCardStorage", return_value=mock_card_storage), \
             patch("app.api.task_cards._launch_run_for_card", new_callable=AsyncMock, return_value=mock_run):

            resp = client.post("/api/v1/commands", json={
                "command": "goal",
                "args": "deploy the service",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "goal_launched"
        assert data["data"]["binding_id"] is None


class TestGoalSubcommands:
    """Test goal status/pause/resume/clear."""

    def test_status_no_active_goal(self, client, mock_project_storage):
        """Status when no goal returns inactive."""
        with patch("app.api.commands._find_active_goal_binding", new_callable=AsyncMock, return_value=None):
            resp = client.post("/api/v1/commands", json={
                "command": "goal",
                "args": "status",
                "conversation_id": "conv_123",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "goal_status"
        assert data["data"]["active"] is False

    def test_pause_no_active_goal(self, client, mock_project_storage):
        """Pause when no goal returns appropriate message."""
        with patch("app.api.commands._find_active_goal_binding", new_callable=AsyncMock, return_value=None):
            resp = client.post("/api/v1/commands", json={
                "command": "goal",
                "args": "pause",
                "conversation_id": "conv_123",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "goal_pause"
        assert "No active goal" in data["message"]

    def test_clear_cancels_and_unbinds(self, client, mock_project_storage):
        """Clear cancels the run and removes the binding."""
        mock_binding_info = {
            "card_id": "card_1",
            "run_id": "run_1",
            "binding_id": "bind_1",
            "goal_text": "fix errors",
        }

        mock_run = MagicMock()
        mock_run.status = "running"

        mock_run_storage = MagicMock()
        mock_run_storage.get.return_value = mock_run

        mock_binding_storage = MagicMock()

        with patch("app.api.commands._find_active_goal_binding", new_callable=AsyncMock, return_value=mock_binding_info), \
             patch("app.api.commands.TaskRunStorage", return_value=mock_run_storage), \
             patch("app.api.commands.TaskBindingStorage", return_value=mock_binding_storage):

            resp = client.post("/api/v1/commands", json={
                "command": "goal",
                "args": "clear",
                "conversation_id": "conv_123",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "goal_cleared"
        mock_run_storage.request_cancel.assert_called_once_with("run_1")
        mock_binding_storage.delete.assert_called_once_with("conv_123", "bind_1")
