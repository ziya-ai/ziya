"""bulk-sync never replaces a real title with a placeholder.

Backend writers (context-tool auto-vivify, bead/handoff writes) create or
re-stamp a chat record with "New Conversation" and a fresh _version.  A
browser copy that missed the derived title then pushed the placeholder
back with a newer version, and the update branch took it wholesale, so the
server record lost its title and every other window adopted the loss.
These run through the real route; the helper alone fixes nothing.
"""

import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.utils.chat_title import PLACEHOLDER_TITLES, merge_title

PID = "proj-title"


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


def _seed(home: Path, title: str, ver: int) -> None:
    from app.storage.chats import ChatStorage
    st = ChatStorage(home / "projects" / PID)
    st._write_json(st._chat_file("c1"), {
        "id": "c1", "title": title, "projectId": PID,
        "messages": [{"id": "m1", "role": "human", "content": "why stuck", "timestamp": 1}],
        "createdAt": 1, "lastActiveAt": ver, "_version": ver,
    })


def _read(home: Path) -> dict:
    from app.storage.chats import ChatStorage
    st = ChatStorage(home / "projects" / PID)
    return st._read_json(st._chat_file("c1"))


def _push(home: Path, title: str, ver: int):
    body = {"chats": [{
        "id": "c1", "title": title, "projectId": PID,
        "messages": [{"id": "m1", "role": "human", "content": "why stuck", "timestamp": 1}],
        "createdAt": 1, "lastActiveAt": ver, "_version": ver,
    }]}
    with _client(home) as tc:
        resp = tc.post(f"/api/v1/projects/{PID}/chats/bulk-sync", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 1, resp.json()
    return resp


class TestMergeTitle:
    def test_placeholder_never_replaces_real(self):
        assert merge_title("New Conversation", "real") == "real"
        assert merge_title("", "real") == "real"
        assert merge_title(None, "real") == "real"

    def test_real_incoming_wins(self):
        assert merge_title("renamed", "old") == "renamed"
        assert merge_title("renamed", "New Conversation") == "renamed"

    def test_both_placeholders(self):
        assert merge_title("New Conversation", "Loading...") == "New Conversation"


class TestBulkSyncRoute:
    def test_newer_placeholder_push_keeps_real_title(self, home):
        now = int(time.time() * 1000)
        _seed(home, "why stuck", now - 5000)
        resp = _push(home, "New Conversation", now)
        # Positive: the update branch ran (not skipped as older).
        assert resp.status_code == 200
        assert resp.json()["updated"] == 1
        rec = _read(home)
        assert rec["title"] == "why stuck"
        assert rec["_version"] >= now

    def test_rename_still_lands(self, home):
        now = int(time.time() * 1000)
        _seed(home, "why stuck", now - 5000)
        resp = _push(home, "Renamed elsewhere", now)
        assert resp.json()["updated"] == 1
        assert _read(home)["title"] == "Renamed elsewhere"

    def test_real_title_heals_a_placeholder_record(self, home):
        # The auto-vivified record: placeholder on disk, real title pushed.
        now = int(time.time() * 1000)
        _seed(home, "New Conversation", now - 5000)
        _push(home, "why stuck", now)
        assert _read(home)["title"] == "why stuck"


def test_placeholder_set_matches_frontend():
    src = (Path(__file__).resolve().parents[1]
           / "frontend/src/utils/conversationTitle.ts").read_text()
    m = re.search(r"PLACEHOLDER_TITLES = new Set\(\[(.*?)\]\)", src, re.S)
    assert m, "PLACEHOLDER_TITLES not found in conversationTitle.ts"
    fe = set(re.findall(r"'([^']*)'", m.group(1)))
    assert fe == set(PLACEHOLDER_TITLES)
