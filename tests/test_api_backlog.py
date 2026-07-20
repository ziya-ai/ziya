"""
Tests for app.api.backlog -- the abandon/restore status endpoint
(design/bead-backlog-browser.md).
"""
import os
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.storage.chats import ChatStorage
from app.storage.backlog import _extract_cache


@pytest.fixture(autouse=True)
def _clear_extract_cache():
    _extract_cache.clear()
    yield
    _extract_cache.clear()


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    project_id = "test-project-001"
    (ziya_home / "projects" / project_id / "chats").mkdir(parents=True)
    return project_id


@pytest.fixture
def client(ziya_home, project_dir):
    proj_path = ziya_home / "projects" / project_dir
    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
        # Both app.api.backlog and app.storage.backlog resolve
        # get_project_dir at call time (imported inside the handler /
        # module-level import resolved against app.utils.paths), so patching
        # the underlying app.utils.paths symbol covers both call sites.
        with patch("app.utils.paths.get_project_dir", return_value=proj_path):
            from fastapi import FastAPI
            from app.api.backlog import router
            app = FastAPI()
            app.include_router(router)
            yield TestClient(app), project_dir, proj_path


def _msg(i, role):
    return {"id": f"m{i}", "role": role, "content": f"msg {i}", "timestamp": 1000 + i}


def _bead(id, status, parent_id=None, message_index=None):
    return {
        "id": id,
        "content": f"content-{id}",
        "status": status,
        "parent_id": parent_id,
        "message_index": message_index,
        "created_at": int(time.time() * 1000),
    }


def _write_chat(proj_path, chat_id, messages, beads):
    storage = ChatStorage(proj_path)
    now = int(time.time() * 1000)
    storage._write_json(storage._chat_file(chat_id), {
        "id": chat_id, "title": "Source conversation", "messages": messages,
        "createdAt": now, "lastActiveAt": now, "_version": now,
        "folderId": "folder-1", "_beads": beads,
    })
    return storage


def test_parked_to_abandoned_succeeds(client):
    tc, pid, proj_path = client
    _write_chat(proj_path, "chat-1", [_msg(0, "human")],
                [_bead("b1", "parked")])

    res = tc.post(
        f"/api/v1/projects/{pid}/chats/chat-1/beads/b1/status",
        json={"status": "abandoned"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["bead"]["status"] == "abandoned"

    storage = ChatStorage(proj_path)
    raw = storage._read_json(storage._chat_file("chat-1"))
    assert next(b for b in raw["_beads"] if b["id"] == "b1")["status"] == "abandoned"


def test_abandoned_to_parked_succeeds(client):
    tc, pid, proj_path = client
    _write_chat(proj_path, "chat-1", [_msg(0, "human")],
                [_bead("b1", "abandoned")])

    res = tc.post(
        f"/api/v1/projects/{pid}/chats/chat-1/beads/b1/status",
        json={"status": "parked"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["bead"]["status"] == "parked"


def test_set_active_returns_400(client):
    tc, pid, proj_path = client
    _write_chat(proj_path, "chat-1", [_msg(0, "human")],
                [_bead("b1", "parked")])

    res = tc.post(
        f"/api/v1/projects/{pid}/chats/chat-1/beads/b1/status",
        json={"status": "active"},
    )
    assert res.status_code == 400


def test_set_completed_returns_400(client):
    tc, pid, proj_path = client
    _write_chat(proj_path, "chat-1", [_msg(0, "human")],
                [_bead("b1", "parked")])

    res = tc.post(
        f"/api/v1/projects/{pid}/chats/chat-1/beads/b1/status",
        json={"status": "completed"},
    )
    assert res.status_code == 400


def test_unknown_bead_returns_404(client):
    tc, pid, proj_path = client
    _write_chat(proj_path, "chat-1", [_msg(0, "human")], [])

    res = tc.post(
        f"/api/v1/projects/{pid}/chats/chat-1/beads/ghost/status",
        json={"status": "abandoned"},
    )
    assert res.status_code == 404


def test_unknown_chat_returns_404(client):
    tc, pid, _ = client

    res = tc.post(
        f"/api/v1/projects/{pid}/chats/nope/beads/b1/status",
        json={"status": "abandoned"},
    )
    assert res.status_code == 404


def test_abandoning_already_abandoned_bead_returns_400(client):
    tc, pid, proj_path = client
    _write_chat(proj_path, "chat-1", [_msg(0, "human")],
                [_bead("b1", "abandoned")])

    res = tc.post(
        f"/api/v1/projects/{pid}/chats/chat-1/beads/b1/status",
        json={"status": "abandoned"},
    )
    assert res.status_code == 400


def test_abandoning_active_bead_returns_400(client):
    """The current status must be the opposite pair member (parked) --
    an active bead cannot be abandoned directly from the backlog."""
    tc, pid, proj_path = client
    _write_chat(proj_path, "chat-1", [_msg(0, "human")],
                [_bead("b1", "active")])

    res = tc.post(
        f"/api/v1/projects/{pid}/chats/chat-1/beads/b1/status",
        json={"status": "abandoned"},
    )
    assert res.status_code == 400
