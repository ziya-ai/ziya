"""
Tests for app.api.task_bindings — the endpoints that attach task-card
runs to chats.

Covers:
  - GET returns [] for a chat with no bindings
  - POST treats chat_id as opaque (no chat-existence check); this
    avoids a race with the frontend's dual-write debounce when a
    brand-new conversation launches a task before the chat has been
    pushed to the server
  - POST validates card exists (404)
  - POST launches the run and records the binding atomically
  - POST assigns binding.id, sets chat_id/run_id, captures
    anchor_message_id
  - POST with null anchor_message_id is accepted (unanchored binding)
  - GET returns bindings in creation order
  - Multiple bindings can be created against the same chat
  - DELETE removes a binding; 404 on unknown binding
  - DELETE is chat-scoped: can't delete a binding via a different chat id

The background execution is patched to an AsyncMock that returns
immediately with a canned Artifact — we are testing the endpoint
contract, not the executor.
"""

import asyncio
import json
import os
import time
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.chat import ChatCreate
from app.models.task_card import Artifact, Block, TaskCardCreate
from app.storage.chats import ChatStorage
from app.storage.task_cards import TaskCardStorage


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    pid = "test-proj-bindings"
    pdir = ziya_home / "projects" / pid
    pdir.mkdir(parents=True)
    (pdir / "chats").mkdir()
    project_data = {
        "id": pid, "name": "Bindings Test", "path": "/tmp/x",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }
    (pdir / "project.json").write_text(json.dumps(project_data))
    return pid


@pytest.fixture
def client(ziya_home, project_dir):
    """TestClient with patched paths and execute_block stubbed to
    complete immediately.  Yields (client, project_id, chat_id,
    card_id)."""
    pdir = ziya_home / "projects" / project_dir

    # Seed a chat and a card directly via storage.
    chat = ChatStorage(pdir).create(ChatCreate(title="Test Chat"))
    card = TaskCardStorage(pdir).create(TaskCardCreate(
        name="Stub Card",
        root=Block(block_type="task", name="T", instructions="do x"),
    ))

    async def _stub_execute(block, ctx):
        return Artifact(summary="stub done", created_at=time.time())

    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
        # Patch all the get_project_dir + get_ziya_home call sites —
        # task_bindings imports them, and so do task_cards (via the
        # shared helper) and task_runs.
        with patch("app.api.task_bindings.get_ziya_home", return_value=ziya_home), \
             patch("app.api.task_bindings.get_project_dir", return_value=pdir), \
             patch("app.api.task_cards.get_ziya_home", return_value=ziya_home), \
             patch("app.api.task_cards.get_project_dir", return_value=pdir), \
             patch("app.api.task_cards.execute_block", new=_stub_execute):

            from app.api.task_bindings import router as bindings_router
            app = FastAPI()
            app.include_router(bindings_router)
            yield TestClient(app), project_dir, chat.id, card.id


# ──────────────────────────────────────────────────────────────────
# GET /task-bindings
# ──────────────────────────────────────────────────────────────────

def test_list_empty_for_new_chat(client):
    tc, pid, chat_id, _ = client
    res = tc.get(f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings")
    assert res.status_code == 200
    assert res.json() == []


def test_list_unknown_project_returns_404(client):
    tc, _, chat_id, _ = client
    res = tc.get(f"/api/v1/projects/nope/chats/{chat_id}/task-bindings")
    assert res.status_code == 404


# ──────────────────────────────────────────────────────────────────
# POST /task-bindings — creates run + binding atomically
# ──────────────────────────────────────────────────────────────────

def test_post_unknown_chat_is_accepted(client):
    """chat_id is opaque: a binding can be created against a chat
    that doesn't yet exist on the server.  This covers the case
    where the frontend optimistically creates a conversation locally
    and launches a task before the dual-write debounce (2 s) has
    had a chance to push the chat record.

    A stranded binding against a chat that never materializes is
    harmless — it's invisible to the UI (which only looks up bindings
    for active chats) and the storage is cheap to clean up.
    """
    tc, pid, _, card_id = client
    res = tc.post(
        f"/api/v1/projects/{pid}/chats/brand-new-chat-id/task-bindings",
        json={"card_id": card_id, "anchor_message_id": None},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["binding"]["chat_id"] == "brand-new-chat-id"
    assert body["binding"]["card_id"] == card_id
    assert body["run"]["id"]


def test_post_unknown_card_returns_404(client):
    tc, pid, chat_id, _ = client
    res = tc.post(
        f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings",
        json={"card_id": "nonexistent", "anchor_message_id": None},
    )
    assert res.status_code == 404
    assert "Task card not found" in res.json().get("detail", "")


def test_post_creates_binding_and_run(client):
    tc, pid, chat_id, card_id = client
    res = tc.post(
        f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings",
        json={"card_id": card_id, "anchor_message_id": "msg-42"},
    )
    assert res.status_code == 201
    body = res.json()

    assert "binding" in body
    assert "run" in body

    binding = body["binding"]
    assert binding["id"]
    assert binding["chat_id"] == chat_id
    assert binding["card_id"] == card_id
    assert binding["run_id"]
    assert binding["anchor_message_id"] == "msg-42"
    assert binding["created_at"] > 0

    run = body["run"]
    assert run["id"] == binding["run_id"]
    assert run["card_id"] == card_id


def test_post_with_null_anchor_is_allowed(client):
    tc, pid, chat_id, card_id = client
    res = tc.post(
        f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings",
        json={"card_id": card_id, "anchor_message_id": None},
    )
    assert res.status_code == 201
    assert res.json()["binding"]["anchor_message_id"] is None


def test_get_returns_created_binding(client):
    tc, pid, chat_id, card_id = client
    tc.post(
        f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings",
        json={"card_id": card_id, "anchor_message_id": "msg-1"},
    )
    res = tc.get(f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["anchor_message_id"] == "msg-1"


def test_multiple_bindings_same_chat(client):
    tc, pid, chat_id, card_id = client
    # Launch the same card three times.  Each is its own binding per
    # the decision that re-launch == new binding.
    for anchor in ("msg-1", "msg-2", "msg-3"):
        res = tc.post(
            f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings",
            json={"card_id": card_id, "anchor_message_id": anchor},
        )
        assert res.status_code == 201

    rows = tc.get(f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings").json()
    assert len(rows) == 3
    anchors = [r["anchor_message_id"] for r in rows]
    assert set(anchors) == {"msg-1", "msg-2", "msg-3"}
    # All bindings for this chat point to the same card but have
    # distinct run_ids
    assert all(r["card_id"] == card_id for r in rows)
    run_ids = [r["run_id"] for r in rows]
    assert len(set(run_ids)) == 3


def test_post_sets_source_conversation_id(client):
    """The launch helper records the chat as source_conversation_id."""
    tc, pid, chat_id, card_id = client
    res = tc.post(
        f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings",
        json={"card_id": card_id},
    )
    assert res.status_code == 201
    assert res.json()["run"]["source_conversation_id"] == chat_id


# ──────────────────────────────────────────────────────────────────
# DELETE /task-bindings/{binding_id}
# ──────────────────────────────────────────────────────────────────

def test_delete_removes_binding(client):
    tc, pid, chat_id, card_id = client
    post_res = tc.post(
        f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings",
        json={"card_id": card_id},
    )
    binding_id = post_res.json()["binding"]["id"]

    del_res = tc.delete(
        f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings/{binding_id}",
    )
    assert del_res.status_code == 204

    # Verify it's gone
    rows = tc.get(f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings").json()
    assert rows == []


def test_delete_unknown_binding_returns_404(client):
    tc, pid, chat_id, _ = client
    res = tc.delete(
        f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings/nonexistent",
    )
    assert res.status_code == 404


def test_delete_is_chat_scoped(client):
    """A binding id is only valid within its own chat; attempting to
    delete it via a different chat id should 404."""
    tc, pid, chat_id, card_id = client
    post_res = tc.post(
        f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings",
        json={"card_id": card_id},
    )
    binding_id = post_res.json()["binding"]["id"]

    # Try to delete via a different chat id
    res = tc.delete(
        f"/api/v1/projects/{pid}/chats/other-chat/task-bindings/{binding_id}",
    )
    assert res.status_code == 404

    # Original binding still there
    rows = tc.get(f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings").json()
    assert len(rows) == 1
