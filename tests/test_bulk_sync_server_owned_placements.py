"""
bulk-sync must not clobber the server-owned bench fields on a chat record.

``placements`` (bench conversation layer, written by bench.place) and
``loadedMcp`` (written by mcp_ondemand.write_record) have no frontend writer.
The browser's IndexedDB copy either lacks them or holds a stale mirror, and
every end-of-turn bulk-sync pushed that copy with a newer _version.  The
merge took ``chat_data.model_dump()`` wholesale, so ``placements`` became
None: a skill moved to "on hand" in the conversation lens fell back to its
default ("shelved") a few turns later, with no user action.
"""
import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    home = tmp_path / ".ziya"
    pid = "proj-placements"
    proj = home / "projects" / pid
    (proj / "chats").mkdir(parents=True)
    (proj / "project.json").write_text(json.dumps({
        "id": pid, "name": "P", "path": "/x",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": 1, "lastAccessedAt": 1,
    }))
    with patch.dict(os.environ, {"ZIYA_HOME": str(home)}):
        with patch("app.api.chats.get_ziya_home", return_value=home), \
             patch("app.api.chats.get_project_dir", return_value=proj):
            from fastapi import FastAPI
            from app.api.chats import router
            app = FastAPI()
            app.include_router(router)
            yield TestClient(app), pid, proj


def _path(proj, chat_id):
    return proj / "chats" / f"{chat_id}.json"


def _read(proj, chat_id):
    return json.loads(_path(proj, chat_id).read_text())


PLACEMENTS = {"skill:tests-for-everything": "always"}
LOADED = {"jira-mcp": ["get_jira_issue"]}


def _seed(tc, pid, proj, v0):
    chat = {"id": "c1", "title": "T", "messages": [],
            "createdAt": v0, "lastActiveAt": v0, "_version": v0}
    assert tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync",
                   json={"chats": [chat]}).status_code == 200
    # Simulate the server-side writers (bench.place / mcp_ondemand) landing
    # on the record after the browser's last pull.
    raw = _read(proj, "c1")
    raw["placements"] = dict(PLACEMENTS)
    raw["loadedMcp"] = dict(LOADED)
    _path(proj, "c1").write_text(json.dumps(raw))


def _push(tc, pid, v, **extra):
    chat = {"id": "c1", "title": "T", "createdAt": v, "lastActiveAt": v, "_version": v,
            "messages": [{"id": "m1", "role": "human", "content": "next turn", "timestamp": v}],
            **extra}
    r = tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync", json={"chats": [chat]})
    assert r.status_code == 200
    return r.json()


def test_omitting_push_keeps_placements_and_loaded_mcp(client):
    tc, pid, proj = client
    v0 = int(time.time() * 1000)
    _seed(tc, pid, proj, v0)

    out = _push(tc, pid, v0 + 10)   # newer, carries neither field
    assert out["updated"] == 1, "the newer push must actually be applied"

    on_disk = _read(proj, "c1")
    assert [m["id"] for m in on_disk["messages"]] == ["m1"], "push content landed"
    assert on_disk["placements"] == PLACEMENTS, "on-hand placement wiped by bulk-sync"
    assert on_disk["loadedMcp"] == LOADED, "loaded MCP servers wiped by bulk-sync"


def test_stale_mirror_does_not_revert_placements(client):
    """A browser holding an older mirror must not restore it over the record."""
    tc, pid, proj = client
    v0 = int(time.time() * 1000)
    _seed(tc, pid, proj, v0)

    _push(tc, pid, v0 + 10,
          placements={"skill:tests-for-everything": "off"},
          loadedMcp={})

    on_disk = _read(proj, "c1")
    assert on_disk["placements"] == PLACEMENTS
    assert on_disk["loadedMcp"] == LOADED


def test_new_chat_via_bulk_sync_keeps_its_placements(client):
    """A fork is created through bulk-sync with no record on disk; its copied
    placements / loadedMcp are the only source and must be written."""
    tc, pid, proj = client
    v0 = int(time.time() * 1000)
    chat = {"id": "fork1", "title": "F", "messages": [], "createdAt": v0,
            "lastActiveAt": v0, "_version": v0,
            "placements": dict(PLACEMENTS), "loadedMcp": dict(LOADED)}
    assert tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync",
                   json={"chats": [chat]}).status_code == 200
    on_disk = _read(proj, "fork1")
    assert on_disk["placements"] == PLACEMENTS
    assert on_disk["loadedMcp"] == LOADED
