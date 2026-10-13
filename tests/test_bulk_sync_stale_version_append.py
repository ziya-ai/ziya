"""bulk-sync keeps new messages from a push whose _version is older.

Backend writers (handoff_write, bead tools, context auto-vivify) re-stamp
the chat record with _version=now while the record holds only the messages
the server has seen.  The assistant reply streamed to the browser is not on
disk yet.  The browser then pushes that reply with its older IndexedDB
_version, and the old "incoming_ver < existing_ver -> skipped" branch threw
it away.  The next pull replaced the on-screen conversation with the shorter
server copy, IndexedDB restored it, and the cycle repeated every 30s.  The
reply never reached the server.

merge_messages is additive by construction (it never removes or rewrites a
server message), so an older push can safely contribute messages the
server lacks.  Server-side metadata must not be reverted by the stale copy.
"""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PID = "proj-stale"
CID = "c1"

H1 = {"id": "h1", "role": "human", "content": "first question", "timestamp": 1}
A1 = {"id": "a1", "role": "assistant", "content": "first answer", "timestamp": 2}
H2 = {"id": "h2", "role": "human", "content": "second question", "timestamp": 3}
A2 = {"id": "a2", "role": "assistant", "content": "second answer, only in the browser", "timestamp": 4}


@pytest.fixture
def home(tmp_path):
    h = tmp_path / ".ziya"
    pdir = h / "projects" / PID
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": PID, "name": PID, "path": f"/tmp/{PID}",
        "createdAt": 1, "lastAccessedAt": 1,
    }))
    return h


@contextmanager
def _client(home: Path):
    with patch.dict(os.environ, {"ZIYA_HOME": str(home)}), \
         patch("app.api.chats.get_ziya_home", return_value=home), \
         patch("app.api.chats.get_project_dir",
               side_effect=lambda pid: home / "projects" / pid):
        from app.api.chats import router
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app)


def _storage(home: Path):
    from app.storage.chats import ChatStorage
    return ChatStorage(home / "projects" / PID)


def _seed(home: Path, messages, ver: int, **extra) -> None:
    st = _storage(home)
    st._write_json(st._chat_file(CID), {
        "id": CID, "title": "Real title", "projectId": PID,
        "messages": messages, "createdAt": 1,
        "lastActiveAt": ver, "_version": ver, **extra,
    })


def _read(home: Path) -> dict:
    st = _storage(home)
    return st._read_json(st._chat_file(CID))


def _push(home: Path, messages, ver: int, **extra):
    body = {"chats": [{
        "id": CID, "title": "Real title", "projectId": PID,
        "messages": messages, "createdAt": 1,
        "lastActiveAt": ver, "_version": ver, **extra,
    }]}
    with _client(home) as tc:
        resp = tc.post(f"/api/v1/projects/{PID}/chats/bulk-sync", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_older_push_with_new_reply_is_appended(home):
    # Server was re-stamped by a backend writer AFTER the browser's copy.
    draft = {"document": "## State\nx", "generatedAt": 1500}
    _seed(home, [H1, A1, H2], ver=2000, handoffDraft=draft)
    result = _push(home, [H1, A1, H2, A2], ver=1000)

    rec = _read(home)
    ids = [m["id"] for m in rec["messages"]]
    assert ids == ["h1", "a1", "h2", "a2"], ids
    assert result["updated"] == 1, result
    # Version must move past the server's so every client's next pull
    # converges on the longer record.
    assert rec["_version"] > 2000
    # Backend-owned field from the newer server write survives.
    assert rec.get("handoffDraft", {}).get("document") == "## State\nx"


def test_older_push_does_not_revert_server_metadata(home):
    muted_a1 = dict(A1, muted=True)
    _seed(home, [H1, muted_a1, H2], ver=2000)
    # Stale browser copy still has a1 unmuted.
    _push(home, [H1, dict(A1, muted=False), H2, A2], ver=1000)

    rec = _read(home)
    by_id = {m["id"]: m for m in rec["messages"]}
    assert by_id["a1"].get("muted") is True
    assert "a2" in by_id


def test_older_push_with_nothing_new_is_still_skipped(home):
    _seed(home, [H1, A1, H2], ver=2000)
    result = _push(home, [H1, A1], ver=1000)

    rec = _read(home)
    assert [m["id"] for m in rec["messages"]] == ["h1", "a1", "h2"]
    assert rec["_version"] == 2000
    assert result["skipped"] == 1 and result["updated"] == 0, result


def test_older_push_cannot_resurrect_tombstoned_message(home):
    from app.utils.chat_message_ops import TOMBSTONE_FIELD
    _seed(home, [H1, A1], ver=2000, **{TOMBSTONE_FIELD: {"ids": ["h2", "a2"]}})
    result = _push(home, [H1, A1, H2, A2], ver=1000)

    rec = _read(home)
    assert [m["id"] for m in rec["messages"]] == ["h1", "a1"]
    assert result["skipped"] == 1, result
