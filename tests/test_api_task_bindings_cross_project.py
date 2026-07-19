"""
Cross-project task-binding resolution for global conversations.

Symptom fixed: a task card run in project A and viewed from a global
conversation opened under project B did not render its inline tile,
because the binding file lives only under the chat's HOME project
(the one the card was launched in), while GET /task-bindings was
called with the *viewing* project's id.

GET /task-bindings now falls back to the chat's owning project (via
the shared chat_index) when the viewing project has no bindings, and
stamps each returned binding with ``project_id`` = the owning project
so the client targets follow-up card/run calls correctly.

These tests use real get_ziya_home()/get_project_dir() via ZIYA_HOME
(not a fixed patch) so the two distinct project dirs resolve naturally.
"""

import json
import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.chat import ChatCreate
from app.models.task_card import Block, TaskCardCreate
from app.models.task_run import TaskRunCreate
from app.storage.chats import ChatStorage
from app.storage.task_bindings import TaskBindingStorage
from app.storage.task_cards import TaskCardStorage
from app.storage.task_runs import TaskRunStorage
from app.utils.paths import get_project_dir

OWNER_PID = "proj-owner"
VIEWER_PID = "proj-viewer"


def _seed_project(ziya_home, pid, path):
    pdir = ziya_home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": pid, "path": path,
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))
    return pdir


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Two projects under a real ZIYA_HOME.  The OWNER project holds a
    global chat, its card, a completed run, and the binding.  The VIEWER
    project holds nothing for that chat.  Yields (client, chat_id, card_id,
    run_id)."""
    home = tmp_path / ".ziya"
    home.mkdir()
    monkeypatch.setenv("ZIYA_HOME", str(home))

    # Reset the process-global chat index so a prior test's scan doesn't
    # leak entries / mask the rebuild against this tmp home.
    from app.storage import chat_index
    chat_index.invalidate()

    owner_dir = _seed_project(home, OWNER_PID, "/tmp/owner")
    _seed_project(home, VIEWER_PID, "/tmp/viewer")

    # Global chat lives in the OWNER project.
    chat = ChatStorage(owner_dir).create(ChatCreate(title="Global Chat"))
    card = TaskCardStorage(owner_dir).create(TaskCardCreate(
        name="Owned Card",
        root=Block(block_type="task", name="T", instructions="do x"),
    ))
    run = TaskRunStorage(owner_dir).create(TaskRunCreate(card_id=card.id))
    TaskRunStorage(owner_dir).update_status(run.id, "done")
    TaskBindingStorage(owner_dir).create(
        chat_id=chat.id, card_id=card.id, run_id=run.id,
        anchor_message_id="msg-1",
    )

    from app.api.task_bindings import router
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app), chat.id, card.id, run.id


def _get(client, pid, chat_id):
    return client.get(f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings")


def test_owner_project_lists_binding_directly(env):
    """Sanity: viewed from its home project, the binding lists as before
    and is stamped with the owning project id."""
    client, chat_id, card_id, run_id = env
    res = _get(client, OWNER_PID, chat_id)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 1
    assert body[0]["card_id"] == card_id
    assert body[0]["run_id"] == run_id
    assert body[0]["project_id"] == OWNER_PID
    assert body[0]["run_status"] == "done"


def test_viewer_project_resolves_binding_cross_project(env):
    """The reported bug: viewed from a DIFFERENT project, the binding
    still resolves (via chat_index) and is stamped with the OWNER id so
    the client fetches the card/run from the right place."""
    client, chat_id, card_id, run_id = env
    res = _get(client, VIEWER_PID, chat_id)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 1, "binding should resolve cross-project"
    assert body[0]["card_id"] == card_id
    assert body[0]["run_id"] == run_id
    # Stamped with the OWNING project, not the viewing project.
    assert body[0]["project_id"] == OWNER_PID
    assert body[0]["run_status"] == "done"


def test_viewer_does_not_shadow_own_bindings(env):
    """If the viewing project has its OWN binding for the chat id, that
    wins — the cross-project fallback only fires on an empty local list."""
    client, chat_id, card_id, run_id = env
    viewer_dir = get_project_dir(VIEWER_PID)
    local = TaskBindingStorage(viewer_dir).create(
        chat_id=chat_id, card_id="local-card", run_id=None,
        anchor_message_id="msg-local",
    )
    res = _get(client, VIEWER_PID, chat_id)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 1
    assert body[0]["id"] == local.id
    assert body[0]["card_id"] == "local-card"
    # Stamped with the viewing project (it owns this local binding).
    assert body[0]["project_id"] == VIEWER_PID


def test_unknown_chat_returns_empty(env):
    """A chat id owned by no project resolves to []."""
    client, _chat_id, _card_id, _run_id = env
    res = _get(client, VIEWER_PID, "no-such-chat")
    assert res.status_code == 200
    assert res.json() == []


def test_viewer_unknown_project_still_404s(env):
    """The viewing project must still exist — the fallback doesn't
    paper over a bad project id."""
    client, chat_id, _card_id, _run_id = env
    res = _get(client, "nonexistent-project", chat_id)
    assert res.status_code == 404
