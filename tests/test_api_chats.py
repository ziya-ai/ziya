"""
Tests for app.api.chats — Chat and group REST API endpoints.

Covers:
  - bulk-sync: creates, updates, skips based on _version
  - list chats with/without messages
  - Single chat CRUD
  - Chat group CRUD
  - Version conflict resolution
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.models.chat import Chat, Message
from app.models.project import Project, ProjectSettings


@pytest.fixture
def ziya_home(tmp_path):
    """Set up a fake ZIYA_HOME with a project."""
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    """Create a project directory structure."""
    project_id = "test-project-001"
    proj_dir = ziya_home / "projects" / project_id
    proj_dir.mkdir(parents=True)
    (proj_dir / "chats").mkdir()

    # Write project.json
    project_data = {
        "id": project_id,
        "name": "Test Project",
        "path": "/some/test/path",
        "settings": {
            "defaultContextIds": [],
            "defaultSkillIds": [],
        },
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }
    (proj_dir / "project.json").write_text(json.dumps(project_data))
    return project_id


@pytest.fixture
def client(ziya_home, project_dir):
    """Create a FastAPI test client with mocked ZIYA_HOME."""
    # Patch paths before importing the router
    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
        with patch("app.api.chats.get_ziya_home", return_value=ziya_home):
            with patch("app.api.chats.get_project_dir", return_value=ziya_home / "projects" / project_dir):
                from fastapi import FastAPI
                from app.api.chats import router

                app = FastAPI()
                app.include_router(router)
                yield TestClient(app), project_dir


def _make_chat(chat_id: str, title: str = "Test", version: int = None, messages=None) -> dict:
    """Helper to create a chat dict for bulk-sync."""
    now = int(time.time() * 1000)
    data = {
        "id": chat_id,
        "title": title,
        "messages": messages or [],
        "createdAt": now,
        "lastActiveAt": now,
    }
    if version is not None:
        data["_version"] = version
    return data


def _make_message(content: str = "hello", role: str = "human") -> dict:
    return {
        "id": f"msg-{time.time()}",
        "role": role,
        "content": content,
        "timestamp": int(time.time() * 1000),
    }


# ── Bulk Sync ──────────────────────────────────────────────────────

class TestBulkSync:

    def test_create_new_chats(self, client):
        tc, pid = client
        chats = [
            _make_chat("chat-1", "First"),
            _make_chat("chat-2", "Second"),
        ]
        resp = tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync", json={"chats": chats})
        assert resp.status_code == 200
        result = resp.json()
        assert result["created"] == 2
        assert result["updated"] == 0
        assert result["skipped"] == 0

    def test_update_existing_chat_newer_version(self, client):
        tc, pid = client
        # First create
        chats = [_make_chat("chat-1", "Original", version=1000)]
        tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync", json={"chats": chats})

        # Then update with newer version
        updated = [_make_chat("chat-1", "Updated", version=2000)]
        resp = tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync", json={"chats": updated})
        result = resp.json()
        assert result["updated"] == 1
        assert result["created"] == 0

        # Verify the update was applied
        get_resp = tc.get(f"/api/v1/projects/{pid}/chats/chat-1")
        assert get_resp.json()["title"] == "Updated"

    def test_skip_older_version(self, client):
        tc, pid = client
        # Create with version 2000
        chats = [_make_chat("chat-1", "Newer", version=2000)]
        tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync", json={"chats": chats})

        # Try to sync with older version
        old = [_make_chat("chat-1", "Older", version=1000)]
        resp = tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync", json={"chats": old})
        result = resp.json()
        assert result["skipped"] == 1

        # Verify original is preserved
        get_resp = tc.get(f"/api/v1/projects/{pid}/chats/chat-1")
        assert get_resp.json()["title"] == "Newer"

    def test_mixed_create_update_skip(self, client):
        tc, pid = client
        # Pre-populate one chat
        tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync",
                json={"chats": [_make_chat("existing", "Old", version=1000)]})

        # Sync: 1 new, 1 update (newer), 1 skip (older)
        chats = [
            _make_chat("brand-new", "New Chat"),
            _make_chat("existing", "Updated", version=2000),
        ]
        resp = tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync", json={"chats": chats})
        result = resp.json()
        assert result["created"] == 1
        assert result["updated"] == 1

    def test_empty_bulk_sync(self, client):
        tc, pid = client
        resp = tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync", json={"chats": []})
        result = resp.json()
        assert result["created"] == 0
        assert result["updated"] == 0


# ── List Chats ─────────────────────────────────────────────────────

class TestListChats:

    def test_list_without_messages(self, client):
        tc, pid = client
        # Create chats
        tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync",
                json={"chats": [_make_chat("c1", "Chat 1", messages=[_make_message()])]})

        resp = tc.get(f"/api/v1/projects/{pid}/chats?include_messages=false")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["messageCount"] == 1
        # Summaries should NOT have full messages list
        assert "messages" not in items[0] or items[0].get("messages") is None

    def test_list_with_messages(self, client):
        tc, pid = client
        tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync",
                json={"chats": [_make_chat("c1", "Chat 1", messages=[_make_message()])]})

        resp = tc.get(f"/api/v1/projects/{pid}/chats?include_messages=true")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert len(items[0]["messages"]) == 1


# ── Single Chat CRUD ───────────────────────────────────────────────

class TestSingleChatCRUD:

    def test_get_chat(self, client):
        tc, pid = client
        tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync",
                json={"chats": [_make_chat("c1", "My Chat")]})

        resp = tc.get(f"/api/v1/projects/{pid}/chats/c1")
        assert resp.status_code == 200
        assert resp.json()["title"] == "My Chat"

    def test_get_nonexistent_chat_404(self, client):
        tc, pid = client
        resp = tc.get(f"/api/v1/projects/{pid}/chats/nonexistent")
        assert resp.status_code == 404

    def test_delete_chat(self, client):
        tc, pid = client
        tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync",
                json={"chats": [_make_chat("c1", "Doomed")]})

        resp = tc.delete(f"/api/v1/projects/{pid}/chats/c1")
        assert resp.status_code == 200

        resp = tc.get(f"/api/v1/projects/{pid}/chats/c1")
        assert resp.status_code == 404

    def test_delete_nonexistent_404(self, client):
        tc, pid = client
        resp = tc.delete(f"/api/v1/projects/{pid}/chats/nonexistent")
        assert resp.status_code == 404

    def test_add_message_to_chat(self, client):
        tc, pid = client
        tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync",
                json={"chats": [_make_chat("c1", "Chat")]})

        msg = _make_message("Hello from test")
        resp = tc.post(f"/api/v1/projects/{pid}/chats/c1/messages", json=msg)
        assert resp.status_code == 200
        assert len(resp.json()["messages"]) == 1

    def test_add_message_to_nonexistent_404(self, client):
        tc, pid = client
        msg = _make_message("orphan message")
        resp = tc.post(f"/api/v1/projects/{pid}/chats/nonexistent/messages", json=msg)
        assert resp.status_code == 404


# ── Chat Groups ────────────────────────────────────────────────────

class TestChatGroups:

    def test_create_and_list_groups(self, client):
        tc, pid = client
        resp = tc.post(f"/api/v1/projects/{pid}/chat-groups",
                       json={"name": "My Group"})
        assert resp.status_code == 200
        group = resp.json()
        assert group["name"] == "My Group"

        list_resp = tc.get(f"/api/v1/projects/{pid}/chat-groups")
        assert len(list_resp.json()) == 1

    def test_delete_group_ungroups_chats(self, client):
        tc, pid = client
        # Create group
        grp_resp = tc.post(f"/api/v1/projects/{pid}/chat-groups",
                          json={"name": "Temp Group"})
        group_id = grp_resp.json()["id"]

        # Create a chat in that group
        tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync",
                json={"chats": [_make_chat("c1", "Grouped Chat")]})
        tc.put(f"/api/v1/projects/{pid}/chats/c1",
               json={"groupId": group_id})

        # Delete the group
        del_resp = tc.delete(f"/api/v1/projects/{pid}/chat-groups/{group_id}")
        assert del_resp.status_code == 200

        # Chat should now be ungrouped (groupId == null)
        chat = tc.get(f"/api/v1/projects/{pid}/chats/c1").json()
        assert chat["groupId"] is None


# ── Error handling ─────────────────────────────────────────────────

class TestErrorHandling:

    def test_nonexistent_project_404(self, ziya_home):
        """Accessing a non-existent project should return 404."""
        with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
            with patch("app.api.chats.get_ziya_home", return_value=ziya_home):
                from fastapi import FastAPI
                from app.api.chats import router

                app = FastAPI()
                app.include_router(router)
                tc = TestClient(app)

                resp = tc.get("/api/v1/projects/nonexistent/chats")
                assert resp.status_code == 404
