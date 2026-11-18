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


# ── the note reaches the model ─────────────────────────────────────────
#
# The chat endpoint builds history from human and assistant entries only,
# so a system-role note was dropped before the prompt was built: the record
# held the move, the model was never told.  fold_project_change_notes puts
# the text into the turn beside it.

from app.utils.project_change_note import (
    NOTE_PREFIX, fold_project_change_notes, is_project_change_note,
)


def _note_text(kind="moved"):
    return build_project_change_message(
        kind, {"id": SRC, "name": "Alpha", "path": SRC_PATH},
        {"id": DST, "name": "Beta", "path": DST_PATH})["content"]


@pytest.mark.parametrize("kind", ["moved", "global_on", "global_off"])
def test_every_note_kind_is_recognised_by_its_text(kind):
    assert _note_text(kind).startswith(NOTE_PREFIX)
    assert is_project_change_note(_note_text(kind))
    assert not is_project_change_note("Model changed from a to b")
    assert not is_project_change_note(None)


def test_fold_appends_the_note_to_the_turn_before_it():
    note = _note_text()
    history = [["human", "look at src/a.py", "", "1"],
               ["assistant", "done", "", "2"],
               ["system", note, "", "3"],
               ["human", "now what", "", "4"]]
    out = fold_project_change_notes(history)
    assert [m[0] for m in out] == ["human", "assistant", "human"]     # roles still alternate
    assert out[1][1] == "done\n\n" + note
    assert SRC_PATH in out[1][1] and DST_PATH in out[1][1]
    assert out[1][2:] == ["", "2"] and out[2] == history[3]
    assert history[1][1] == "done"                                    # input untouched


def test_fold_skips_over_other_system_entries_and_keeps_them():
    note = _note_text()
    history = [["assistant", "a"], ["system", "Model changed from x to y"], ["system", note]]
    out = fold_project_change_notes(history)
    assert out == [["assistant", "a\n\n" + note], ["system", "Model changed from x to y"]]


def test_fold_prepends_to_the_next_turn_when_nothing_precedes():
    note = _note_text()
    out = fold_project_change_notes([{"role": "system", "content": note},
                                     {"role": "human", "content": "hi", "images": None}])
    assert out == [{"role": "human", "content": note + "\n\nhi", "images": None}]
    # Nothing to attach to: dropped, not sent as a turn of its own.
    assert fold_project_change_notes([["system", note]]) == []


def test_fold_leaves_history_without_notes_alone():
    history = [["human", "q"], ["system", "Model changed"], ["assistant", "a"]]
    assert fold_project_change_notes(history) == history
    assert fold_project_change_notes(None) is None


def test_chat_endpoint_folds_before_it_filters_by_role():
    """The seam: server.py calls the fold on the list its role filter walks,
    ahead of that loop."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "server.py").read_text()
    call = src.find("messages_to_process = fold_project_change_notes(messages_to_process)")
    assert call > -1
    loop = src.find("for msg in messages_to_process:")
    assert loop > call


def test_move_and_toggle_endpoints_build_the_note():
    """The seam the first landing missed: the builder existed, nothing called it."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    api = (root / "app" / "api" / "chats.py").read_text()
    assert api.count("build_project_change_message(") >= 2
    storage = (root / "app" / "storage" / "chats.py").read_text()
    move_to = storage[storage.index("def move_to("):storage.index("def add_message(")]
    assert 'raw.setdefault("messages", []).append(note)' in move_to
