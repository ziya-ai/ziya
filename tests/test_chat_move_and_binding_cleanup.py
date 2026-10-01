"""
Server-side chat move and task-binding lifecycle across it.

Two defects fixed together because they are the two halves of one seam:

1. The client moved a conversation between projects as bulk-sync-to-target
   then DELETE-from-source.  Both legs were best-effort, bulk-sync silently
   drops shell (partially loaded) conversations, and bulk-sync never updates
   the cross-project chat_index — so the "move" could delete the only copy,
   and the index lost the chat entirely.  A single server endpoint,
   POST /chats/{chat_id}/move, now does the move atomically from the
   client's point of view and keeps the index current.

2. ChatStorage.delete left chats/{chat_id}.bindings.json behind, so a
   deleted conversation kept its task bindings (which also kept its
   conversation-only card from being pruned).  It now removes the file —
   UNLESS the same chat id still exists in a sibling project, which is what
   a move-in-progress looks like from the source side.  The bindings, the
   conversation-only card and the run all stay where the card was launched;
   GET /task-bindings resolves them from there (see
   test_api_task_bindings_cross_project.py::test_moved_chat_keeps_its_binding).
"""

import json
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

SRC = "proj-src"
DST = "proj-dst"


def _seed_project(ziya_home, pid):
    pdir = ziya_home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": pid, "path": f"/tmp/{pid}",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))
    return pdir


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / ".ziya"
    home.mkdir()
    monkeypatch.setenv("ZIYA_HOME", str(home))
    from app.storage import chat_index
    chat_index.invalidate()

    src_dir = _seed_project(home, SRC)
    _seed_project(home, DST)

    chat = ChatStorage(src_dir).create(ChatCreate(title="Moving Chat"))
    # Give it a folder so the cross-project move can be seen clearing it.
    raw = json.loads((src_dir / "chats" / f"{chat.id}.json").read_text())
    raw["groupId"] = "folder-in-src"
    raw["folderId"] = "folder-in-src"
    raw["isGlobal"] = True
    ChatStorage(src_dir)._write_json(src_dir / "chats" / f"{chat.id}.json", raw)

    card = TaskCardStorage(src_dir).create(TaskCardCreate(
        name="Card", draft=True,
        root=Block(block_type="task", name="T", instructions="do x"),
    ))
    run = TaskRunStorage(src_dir).create(TaskRunCreate(card_id=card.id))
    TaskRunStorage(src_dir).update_status(run.id, "running")
    TaskBindingStorage(src_dir).create(
        chat_id=chat.id, card_id=card.id, run_id=run.id, anchor_message_id="m1",
    )

    from app.api.chats import router as chats_router
    from app.api.task_bindings import router as bindings_router
    app = FastAPI()
    app.include_router(chats_router)
    app.include_router(bindings_router)
    yield TestClient(app), chat.id, card.id, run.id


def _move(client, src, chat_id, dst):
    return client.post(f"/api/v1/projects/{src}/chats/{chat_id}/move",
                       json={"targetProjectId": dst})


# ── POST /move ────────────────────────────────────────────────────────

def test_move_relocates_chat_file_and_rewrites_ownership_fields(env):
    client, chat_id, _card, _run = env
    src_dir, dst_dir = get_project_dir(SRC), get_project_dir(DST)

    res = _move(client, SRC, chat_id, DST)
    assert res.status_code == 200, res.text
    assert res.json() == {"moved": True, "chatId": chat_id, "projectId": DST}

    assert not (src_dir / "chats" / f"{chat_id}.json").exists()
    moved = json.loads((dst_dir / "chats" / f"{chat_id}.json").read_text())
    assert moved["id"] == chat_id
    assert moved["title"] == "Moving Chat"
    assert moved["projectId"] == DST
    # Global visibility and folder membership are per-project facts; a
    # folder id from SRC would dangle in DST (copy already clears it).
    assert moved["isGlobal"] is False
    assert moved["groupId"] is None
    assert moved["folderId"] is None


def test_move_updates_chat_index(env):
    client, chat_id, _card, _run = env
    from app.storage import chat_index
    from app.utils.paths import get_ziya_home
    assert chat_index.lookup(get_ziya_home(), chat_id)[0] == SRC
    assert _move(client, SRC, chat_id, DST).status_code == 200
    assert chat_index.lookup(get_ziya_home(), chat_id)[0] == DST


def test_move_keeps_task_binding_where_the_run_lives_and_tile_still_resolves(env):
    """The seam: after the move, the run is still executing under SRC, its
    binding file is still under SRC, and the conversation — now viewed from
    DST — still lists the binding, stamped with SRC so the tile's card/run
    calls target the right project."""
    client, chat_id, card_id, run_id = env
    src_dir, dst_dir = get_project_dir(SRC), get_project_dir(DST)
    assert _move(client, SRC, chat_id, DST).status_code == 200

    assert (src_dir / "chats" / f"{chat_id}.bindings.json").exists()
    assert not (dst_dir / "chats" / f"{chat_id}.bindings.json").exists()
    assert TaskCardStorage(src_dir).get(card_id) is not None
    assert TaskRunStorage(src_dir).get(run_id) is not None

    res = client.get(f"/api/v1/projects/{DST}/chats/{chat_id}/task-bindings")
    assert res.status_code == 200, res.text
    body = res.json()
    assert [b["run_id"] for b in body] == [run_id]
    assert body[0]["run_status"] == "running"
    assert body[0]["project_id"] == SRC


def test_move_rejects_same_project_and_missing_targets(env):
    client, chat_id, _card, _run = env
    assert _move(client, SRC, chat_id, SRC).status_code == 400
    assert _move(client, SRC, chat_id, "no-such-project").status_code == 404
    assert _move(client, SRC, "no-such-chat", DST).status_code == 404
    assert _move(client, "no-such-project", chat_id, DST).status_code == 404
    # Nothing moved on the failed attempts.
    assert (get_project_dir(SRC) / "chats" / f"{chat_id}.json").exists()


# ── ChatStorage.delete and the bindings file ──────────────────────────

def test_delete_removes_orphaned_bindings_file(env):
    """A real delete takes the bindings with it; the card, no longer bound,
    becomes prunable once stale."""
    _client, chat_id, card_id, _run = env
    src_dir = get_project_dir(SRC)
    bindings = src_dir / "chats" / f"{chat_id}.bindings.json"
    assert bindings.exists()
    assert ChatStorage(src_dir).delete(chat_id)
    assert not bindings.exists()
    assert card_id not in TaskBindingStorage(src_dir).all_bound_card_ids()


def test_delete_keeps_bindings_when_chat_exists_in_sibling_project(env):
    """The legacy client move (bulk-sync to target, then DELETE from source)
    must not destroy the binding it just orphaned: from the source side a
    move-in-progress is a delete of a chat that already exists elsewhere."""
    _client, chat_id, _card, _run = env
    src_dir, dst_dir = get_project_dir(SRC), get_project_dir(DST)
    raw = json.loads((src_dir / "chats" / f"{chat_id}.json").read_text())
    ChatStorage(dst_dir)._write_json(dst_dir / "chats" / f"{chat_id}.json", raw)

    assert ChatStorage(src_dir).delete(chat_id)
    assert (src_dir / "chats" / f"{chat_id}.bindings.json").exists()


def test_delete_route_removes_bindings(env):
    _client, chat_id, _card, _run = env
    client = _client
    src_dir = get_project_dir(SRC)
    res = client.delete(f"/api/v1/projects/{SRC}/chats/{chat_id}")
    assert res.status_code == 200, res.text
    assert not (src_dir / "chats" / f"{chat_id}.bindings.json").exists()


def test_purge_expired_removes_bindings(env):
    _client, chat_id, _card, _run = env
    src_dir = get_project_dir(SRC)
    storage = ChatStorage(src_dir)
    storage._is_chat_expired = lambda _chat: True  # type: ignore[method-assign]
    assert storage.purge_expired() == 1
    assert not (src_dir / "chats" / f"{chat_id}.json").exists()
    assert not (src_dir / "chats" / f"{chat_id}.bindings.json").exists()
