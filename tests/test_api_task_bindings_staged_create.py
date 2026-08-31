"""
Tests for staged binding creation — copying a task card into a
conversation WITHOUT running it.

POST /task-bindings has always launched.  The staged shape (binding with
run_id=None, rendered by TaskCardInlineTile as a Run/Discard tile) only
existed for cards synthesized by /goal, which creates the binding
directly through storage.  These cover the ``staged: true`` request flag
that exposes the same shape to any persisted card.

The seam that matters is the pair: a staged create must mint NO run, and
the existing /{binding_id}/launch must then be able to launch it.  Either
half alone is useless — a staged binding nothing can start, or a launch
endpoint nothing reaches.

Fixtures mirror tests/test_api_task_bindings.py: execute_block is stubbed
so a launching create returns immediately.
"""

import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.chat import ChatCreate
from app.models.task_card import Artifact, Block, TaskCardCreate
from app.storage.chats import ChatStorage
from app.storage.task_bindings import TaskBindingStorage
from app.storage.task_cards import TaskCardStorage
from app.storage.task_runs import TaskRunStorage


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(ziya_home):
    pid = "test-proj-staged-bindings"
    pdir = ziya_home / "projects" / pid
    pdir.mkdir(parents=True)
    (pdir / "chats").mkdir()
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": "Staged Bindings Test", "path": "/tmp/x",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))
    return pid


@pytest.fixture
def client(ziya_home, project_dir):
    """Yields (client, project_id, chat_id, card_id, project_path)."""
    pdir = ziya_home / "projects" / project_dir

    chat = ChatStorage(pdir).create(ChatCreate(title="Staged Chat"))
    card = TaskCardStorage(pdir).create(TaskCardCreate(
        name="Stub Card",
        root=Block(block_type="task", name="T", instructions="do x"),
    ))

    async def _stub_execute(block, ctx):
        return Artifact(summary="stub done", created_at=time.time())

    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
        with patch("app.api.task_bindings.get_ziya_home", return_value=ziya_home), \
             patch("app.api.task_bindings.get_project_dir", return_value=pdir), \
             patch("app.api.task_cards.get_ziya_home", return_value=ziya_home), \
             patch("app.api.task_cards.get_project_dir", return_value=pdir), \
             patch("app.api.task_cards.execute_block", new=_stub_execute):

            from app.api.task_bindings import router as bindings_router
            app = FastAPI()
            app.include_router(bindings_router)
            yield TestClient(app), project_dir, chat.id, card.id, pdir


def _url(pid, chat_id):
    return f"/api/v1/projects/{pid}/chats/{chat_id}/task-bindings"


# ──────────────────────────────────────────────────────────────────
# staged: true — bind without launching
# ──────────────────────────────────────────────────────────────────

def test_staged_create_mints_no_run(client):
    tc, pid, chat_id, card_id, pdir = client
    res = tc.post(_url(pid, chat_id), json={
        "card_id": card_id, "anchor_message_id": "msg-7", "staged": True,
    })
    assert res.status_code == 201, res.text
    body = res.json()

    # The response must not offer a run to poll ...
    assert body["run"] is None, body["run"]
    # ... and the binding must carry the staged shape the tile keys on.
    assert body["binding"]["run_id"] is None
    assert body["binding"]["card_id"] == card_id
    assert body["binding"]["anchor_message_id"] == "msg-7"

    # Nothing was scheduled: a staged copy that quietly started a run is
    # the exact failure this flag exists to prevent, and the response
    # shape alone would not reveal it.
    assert TaskRunStorage(pdir).list() == []


def test_staged_create_persists_staged_binding(client):
    """The staged shape must survive the round trip to storage — a
    run_id that is None in the response but absent/garbage on disk would
    leave the tile unable to decide which variant to render."""
    tc, pid, chat_id, card_id, pdir = client
    res = tc.post(_url(pid, chat_id), json={"card_id": card_id, "staged": True})
    assert res.status_code == 201, res.text
    binding_id = res.json()["binding"]["id"]

    stored = TaskBindingStorage(pdir).get(chat_id, binding_id)
    assert stored is not None
    assert stored.run_id is None

    listed = tc.get(_url(pid, chat_id)).json()
    assert [b["id"] for b in listed] == [binding_id]
    assert listed[0]["run_id"] is None
    # No run to enrich from, so no status is claimed for it.
    assert not listed[0].get("run_status")


def test_staged_create_unknown_card_still_404s(client):
    """Staging must not become a back door around card validation."""
    tc, pid, chat_id, _card_id, _pdir = client
    res = tc.post(_url(pid, chat_id), json={
        "card_id": "nonexistent", "staged": True,
    })
    assert res.status_code == 404
    assert "Task card not found" in res.json().get("detail", "")


# ──────────────────────────────────────────────────────────────────
# The seam: staged create → launch
# ──────────────────────────────────────────────────────────────────

def test_staged_binding_can_be_launched_afterwards(client):
    tc, pid, chat_id, card_id, pdir = client
    created = tc.post(_url(pid, chat_id), json={"card_id": card_id, "staged": True})
    assert created.status_code == 201, created.text
    binding_id = created.json()["binding"]["id"]

    launched = tc.post(f"{_url(pid, chat_id)}/{binding_id}/launch")
    assert launched.status_code == 200, launched.text
    run_id = launched.json()["id"]
    assert run_id

    # The binding now points at that run — the same binding, not a second.
    stored = TaskBindingStorage(pdir).get(chat_id, binding_id)
    assert stored.run_id == run_id
    assert len(TaskBindingStorage(pdir).list_for_chat(chat_id)) == 1

    # And a second launch is refused rather than minting a duplicate run.
    again = tc.post(f"{_url(pid, chat_id)}/{binding_id}/launch")
    assert again.status_code == 409


# ──────────────────────────────────────────────────────────────────
# Positive control: the default is still to launch
# ──────────────────────────────────────────────────────────────────

def test_default_create_still_launches(client):
    """Guards against the staged path becoming the default by accident —
    the assertions above are all "no run", so without this the flag
    could be inverted and the suite would stay green."""
    tc, pid, chat_id, card_id, _pdir = client
    res = tc.post(_url(pid, chat_id), json={"card_id": card_id})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["run"] is not None
    assert body["run"]["id"]
    assert body["binding"]["run_id"] == body["run"]["id"]


def test_explicit_staged_false_launches(client):
    tc, pid, chat_id, card_id, _pdir = client
    res = tc.post(_url(pid, chat_id), json={"card_id": card_id, "staged": False})
    assert res.status_code == 201, res.text
    assert res.json()["binding"]["run_id"]
