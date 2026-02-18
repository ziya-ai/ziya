"""
Tests for app.routes.conversation_routes — model-initiated conversation/folder management.

Covers:
  - Create folder (root and nested)
  - Create conversation in a folder
  - Move conversation between folders
  - List folders and conversations
  - Folder configuration (system instructions, global context/model flags)
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def data_dir(tmp_path):
    """Create a temporary data directory for conversation storage."""
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    folders_dir = conv_dir / "folders"
    folders_dir.mkdir()
    # Also create .ziya dir that the move endpoint reads from
    ziya_dir = tmp_path / ".ziya"
    ziya_dir.mkdir()
    return str(tmp_path)


@pytest.fixture
def client(data_dir):
    """Create a test client with the conversation routes."""
    with patch.dict(os.environ, {"ZIYA_USER_CODEBASE_DIR": data_dir}):
        from fastapi import FastAPI
        from app.routes.conversation_routes import router

        app = FastAPI()
        app.include_router(router)
        yield TestClient(app), data_dir


# ── Folder CRUD ────────────────────────────────────────────────────

class TestFolderCRUD:

    def test_create_root_folder(self, client):
        tc, _ = client
        resp = tc.post("/api/conversations/folders", json={
            "name": "Architecture",
        })
        assert resp.status_code == 200
        folder = resp.json()
        assert folder["name"] == "Architecture"
        assert folder["id"]
        assert folder["parent_id"] is None

    def test_create_nested_folder(self, client):
        tc, _ = client
        parent = tc.post("/api/conversations/folders", json={
            "name": "Parent",
        }).json()

        child = tc.post("/api/conversations/folders", json={
            "name": "Child",
            "parent_id": parent["id"],
        }).json()

        assert child["parent_id"] == parent["id"]

    def test_create_folder_with_config(self, client):
        tc, _ = client
        resp = tc.post("/api/conversations/folders", json={
            "name": "Custom Config",
            "system_instructions": "Always use TypeScript",
            "use_global_context": False,
            "use_global_model": False,
        })
        folder = resp.json()
        assert folder["system_instructions"] == "Always use TypeScript"
        assert folder["use_global_context"] is False
        assert folder["use_global_model"] is False

    def test_create_multiple_folders(self, client):
        tc, _ = client
        resp_a = tc.post("/api/conversations/folders", json={"name": "Folder A"})
        resp_b = tc.post("/api/conversations/folders", json={"name": "Folder B"})
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        assert resp_a.json()["id"] != resp_b.json()["id"]


# ── Conversation CRUD ──────────────────────────────────────────────

class TestConversationCRUD:

    def test_create_conversation_in_folder(self, client):
        tc, _ = client
        folder = tc.post("/api/conversations/folders", json={
            "name": "Debug",
        }).json()

        resp = tc.post("/api/conversations/conversations", json={
            "title": "Fix bug #123",
            "folder_id": folder["id"],
        })
        assert resp.status_code == 200
        conv = resp.json()
        assert conv["title"] == "Fix bug #123"
        assert conv["folder_id"] == folder["id"]

    def test_create_conversation_at_root(self, client):
        tc, _ = client
        resp = tc.post("/api/conversations/conversations", json={
            "title": "Root conversation",
        })
        assert resp.status_code == 200
        conv = resp.json()
        assert conv["folder_id"] is None

    def test_create_conversation_with_context_files(self, client):
        tc, _ = client
        resp = tc.post("/api/conversations/conversations", json={
            "title": "With context",
            "context_files": ["src/main.py", "README.md"],
        })
        conv = resp.json()
        assert conv.get("id")


# ── Move Conversation ──────────────────────────────────────────────

class TestMoveConversation:

    def _seed_conversations_file(self, data_dir, conversations):
        """Write a conversations.json that the move endpoint can find."""
        import json, os
        ziya_dir = os.path.join(data_dir, ".ziya")
        os.makedirs(ziya_dir, exist_ok=True)
        with open(os.path.join(ziya_dir, "conversations.json"), "w") as f:
            json.dump(conversations, f)

    def test_move_conversation_to_folder(self, client):
        tc, dd = client
        folder = tc.post("/api/conversations/folders", json={
            "name": "Target",
        }).json()

        conv = tc.post("/api/conversations/conversations", json={
            "title": "Movable",
        }).json()

        self._seed_conversations_file(dd, [
            {"id": conv["id"], "title": "Movable", "folderId": None}
        ])

        resp = tc.post("/api/conversations/conversations/move", json={
            "conversation_id": conv["id"],
            "target_folder_id": folder["id"],
        })
        assert resp.status_code == 200

    def test_move_conversation_to_root(self, client):
        tc, dd = client
        folder = tc.post("/api/conversations/folders", json={
            "name": "Source",
        }).json()

        conv = tc.post("/api/conversations/conversations", json={
            "title": "To be moved",
            "folder_id": folder["id"],
        }).json()

        self._seed_conversations_file(dd, [
            {"id": conv["id"], "title": "To be moved", "folderId": folder["id"]}
        ])

        resp = tc.post("/api/conversations/conversations/move", json={
            "conversation_id": conv["id"],
            "target_folder_id": None,
        })
        assert resp.status_code == 200

    def test_move_nonexistent_conversation_404(self, client):
        tc, dd = client
        self._seed_conversations_file(dd, [])
        resp = tc.post("/api/conversations/conversations/move", json={
            "conversation_id": "nonexistent",
            "target_folder_id": None,
        })
        assert resp.status_code == 404
