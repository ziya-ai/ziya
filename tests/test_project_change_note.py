"""A project move or global toggle leaves a scope note in the transcript.

The model works against the project root; when a conversation changes
project (or becomes global, so it can be driven from another project's
root) nothing in the history said so, and relative paths from before the
change were read as if they still resolved.  The server now appends one
``system`` message at the change (``app.utils.project_change_note``):
``content`` carries both directories for the model; ``projectChange``
carries the from/to projects for the user-facing row.
"""

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.chat import ChatCreate
from app.storage.chats import ChatStorage
from app.utils.paths import get_project_dir
from app.utils.project_change_note import build_project_change_message

SRC, DST = "proj-src", "proj-dst"
SRC_PATH, DST_PATH = "/work/alpha", "/work/beta"


def _seed_project(ziya_home, pid, name, path):
    pdir = ziya_home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": name, "path": path,
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
    src_dir = _seed_project(home, SRC, "Alpha", SRC_PATH)
    _seed_project(home, DST, "Beta", DST_PATH)
    chat = ChatStorage(src_dir).create(ChatCreate(title="Scoped Chat"))
    from app.api.chats import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), chat.id


def _read(pid, chat_id):
    return json.loads((get_project_dir(pid) / "chats" / f"{chat_id}.json").read_text())


# ── the note itself ────────────────────────────────────────────────────

def test_moved_note_names_both_projects_and_both_directories():
    msg = build_project_change_message(
        "moved", {"id": SRC, "name": "Alpha", "path": SRC_PATH},
        {"id": DST, "name": "Beta", "path": DST_PATH}, now_ms=1_700_000_000_000)
    assert msg["role"] == "system"
    assert not msg.get("muted")          # must reach the model: send paths drop muted
    assert msg["content"].startswith("[Ziya:")
    for needle in ("Alpha", "Beta", SRC_PATH, DST_PATH, "previously", "now"):
        assert needle in msg["content"]
    assert msg["projectChange"] == {
        "kind": "moved",
        "from": {"id": SRC, "name": "Alpha", "path": SRC_PATH},
        "to": {"id": DST, "name": "Beta", "path": DST_PATH},
    }
    assert msg["timestamp"] == 1_700_000_000_000 and msg["id"]


def test_global_notes_keep_owner_and_say_where_turns_run():
    on = build_project_change_message("global_on", {"id": SRC, "name": "Alpha", "path": SRC_PATH})
    off = build_project_change_message("global_off", {"id": SRC, "name": "Alpha", "path": SRC_PATH})
    assert on["projectChange"]["kind"] == "global_on" and on["projectChange"]["to"]["id"] == SRC
    assert "any project" in on["content"] and SRC_PATH in on["content"]
    assert off["projectChange"]["kind"] == "global_off"
    assert "no longer global" in off["content"] and SRC_PATH in off["content"]


def test_unknown_project_degrades_without_raising():
    msg = build_project_change_message("moved", None, None)
    assert "unknown" in msg["content"]
    assert msg["projectChange"]["from"] == {"id": None, "name": None, "path": None}


# ── POST /move appends it and returns it ───────────────────────────────

def test_move_appends_note_to_record_and_returns_it(env):
    client, chat_id = env
    res = client.post(f"/api/v1/projects/{SRC}/chats/{chat_id}/move", json={"targetProjectId": DST})
    assert res.status_code == 200, res.text
    body = res.json()
    note = body["note"]
    assert note["role"] == "system" and note["projectChange"]["kind"] == "moved"
    assert SRC_PATH in note["content"] and DST_PATH in note["content"]
    assert "Alpha" in note["content"] and "Beta" in note["content"]

    moved = _read(DST, chat_id)
    assert moved["messages"][-1]["id"] == note["id"]
    assert moved["messages"][-1]["projectChange"]["from"]["path"] == SRC_PATH
    assert moved["messages"][-1]["projectChange"]["to"]["path"] == DST_PATH
    # The record still loads through the model after the append.
    assert client.get(f"/api/v1/projects/{DST}/chats/{chat_id}").status_code == 200


def test_move_note_is_appended_after_existing_messages(env):
    client, chat_id = env
    src_dir = get_project_dir(SRC)
    raw = _read(SRC, chat_id)
    raw["messages"] = [
        {"id": "m1", "role": "human", "content": "look at src/a.py", "timestamp": 1},
        {"id": "m2", "role": "assistant", "content": "ok", "timestamp": 2},
    ]
    ChatStorage(src_dir)._write_json(src_dir / "chats" / f"{chat_id}.json", raw)
    assert client.post(f"/api/v1/projects/{SRC}/chats/{chat_id}/move",
                       json={"targetProjectId": DST}).status_code == 200
    msgs = _read(DST, chat_id)["messages"]
    assert [m["id"] for m in msgs[:2]] == ["m1", "m2"]
    assert msgs[2]["projectChange"]["kind"] == "moved"


def test_failed_move_appends_nothing(env):
    client, chat_id = env
    client.post(f"/api/v1/projects/{SRC}/chats/{chat_id}/move", json={"targetProjectId": "nope"})
    assert _read(SRC, chat_id)["messages"] == []


# ── POST /global appends on a real flip only ───────────────────────────

def test_global_toggle_appends_once_per_flip(env):
    client, chat_id = env
    url = f"/api/v1/projects/{SRC}/chats/{chat_id}/global"
    r1 = client.post(url, json={"isGlobal": True})
    assert r1.status_code == 200, r1.text
    msgs = r1.json()["messages"]
    assert len(msgs) == 1 and msgs[0]["projectChange"]["kind"] == "global_on"
    assert SRC_PATH in msgs[0]["content"] and "Alpha" in msgs[0]["content"]

    # Same value again: no second note.
    r2 = client.post(url, json={"isGlobal": True})
    assert len(r2.json()["messages"]) == 1

    r3 = client.post(url, json={"isGlobal": False})
    msgs = r3.json()["messages"]
    assert [m["projectChange"]["kind"] for m in msgs] == ["global_on", "global_off"]
    assert _read(SRC, chat_id)["messages"][-1]["projectChange"]["kind"] == "global_off"
