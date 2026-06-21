"""
Tests for app.api.beads — focused on the fork-from-bead (Mode-1 branch)
endpoint.  See design/bead-branching.md.

The endpoint is the backend mechanism for "split from here": it truncates a
conversation at a chosen bead's message_index seam into a new branched
conversation, carrying the inherited beads and stamping lineage metadata,
leaving the source fully intact.
"""
import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.storage.chats import ChatStorage


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
        # beads.py imports get_project_dir inside the handler, so the lookup
        # resolves app.utils.paths.get_project_dir at call time.
        with patch("app.utils.paths.get_project_dir", return_value=proj_path):
            from fastapi import FastAPI
            from app.api.beads import router
            app = FastAPI()
            app.include_router(router)
            yield TestClient(app), project_dir, proj_path


def _msg(i, role):
    return {"id": f"m{i}", "role": role, "content": f"msg {i}", "timestamp": 1000 + i}


def _write_source(proj_path, chat_id, messages, beads):
    storage = ChatStorage(proj_path)
    now = int(time.time() * 1000)
    storage._write_json(storage._chat_file(chat_id), {
        "id": chat_id, "title": "Source conversation", "messages": messages,
        "createdAt": now, "lastActiveAt": now, "_version": now,
        "folderId": "folder-1", "_beads": beads,
    })


def test_fork_creates_truncated_branch(client):
    tc, pid, proj_path = client
    messages = [_msg(0, "human"), _msg(1, "assistant"), _msg(2, "human"),
                _msg(3, "assistant"), _msg(4, "human"), _msg(5, "assistant")]
    beads = [
        {"id": "root", "content": "root task", "status": "parked", "message_index": 1, "parent_id": None},
        {"id": "mid", "content": "microburst drops", "status": "parked", "message_index": 3, "parent_id": "root"},
        {"id": "late", "content": "later thread", "status": "active", "message_index": 5, "parent_id": "root"},
    ]
    _write_source(proj_path, "src-chat", messages, beads)

    res = tc.post(f"/api/v1/projects/{pid}/chats/src-chat/beads/fork", json={"bead_id": "mid"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["branchedFrom"] == "src-chat"
    assert data["branchedAtMessageIndex"] == 3
    assert data["branchedFromLabel"] == "microburst drops"
    assert data["message_count"] == 3
    assert data["inherited_bead_count"] == 2

    storage = ChatStorage(proj_path)
    new_raw = storage._read_json(storage._chat_file(data["new_chat_id"]))
    assert [m["id"] for m in new_raw["messages"]] == ["m0", "m1", "m2"]
    assert new_raw["branchedFrom"] == "src-chat"
    assert new_raw["branchedAtMessageIndex"] == 3
    assert new_raw["branchedFromLabel"] == "microburst drops"
    assert new_raw["folderId"] == "folder-1"          # inherits placement
    # Inherited beads carry fresh ids + an origin backlink; "late" (mi 5 > seam
    # 3) is dropped.  Identify the carried-over threads by origin_bead_id.
    assert {b["origin_bead_id"] for b in new_raw["_beads"]} == {"root", "mid"}
    assert all(b["id"] not in ("root", "mid") for b in new_raw["_beads"])
    assert all(b["origin_conversation_id"] == "src-chat" for b in new_raw["_beads"])
    mid_new = next(b for b in new_raw["_beads"] if b["origin_bead_id"] == "mid")
    root_new = next(b for b in new_raw["_beads"] if b["origin_bead_id"] == "root")
    assert mid_new["status"] == "active"
    # parent chain remapped to the fresh ids, not the stale source ids
    assert mid_new["parent_id"] == root_new["id"]


def test_fork_is_non_destructive(client):
    tc, pid, proj_path = client
    messages = [_msg(0, "human"), _msg(1, "assistant"), _msg(2, "human")]
    beads = [{"id": "mid", "content": "thread", "status": "parked", "message_index": 2, "parent_id": None}]
    _write_source(proj_path, "src-chat", messages, beads)

    res = tc.post(f"/api/v1/projects/{pid}/chats/src-chat/beads/fork", json={"bead_id": "mid"})
    assert res.status_code == 200

    # Source untouched: full messages, no lineage stamped, bead still parked.
    src = ChatStorage(proj_path)._read_json(ChatStorage(proj_path)._chat_file("src-chat"))
    assert len(src["messages"]) == 3
    assert src.get("branchedFrom") is None
    assert next(b for b in src["_beads"] if b["id"] == "mid")["status"] == "parked"


def test_fork_missing_source_404(client):
    tc, pid, _ = client
    res = tc.post(f"/api/v1/projects/{pid}/chats/nope/beads/fork", json={"bead_id": "x"})
    assert res.status_code == 404


def test_fork_bead_without_seam_400(client):
    tc, pid, proj_path = client
    beads = [{"id": "noidx", "content": "legacy", "status": "parked", "message_index": None, "parent_id": None}]
    _write_source(proj_path, "src-chat", [_msg(0, "human")], beads)
    res = tc.post(f"/api/v1/projects/{pid}/chats/src-chat/beads/fork", json={"bead_id": "noidx"})
    assert res.status_code == 400


def test_fork_unknown_bead_400(client):
    tc, pid, proj_path = client
    _write_source(proj_path, "src-chat", [_msg(0, "human")], [])
    res = tc.post(f"/api/v1/projects/{pid}/chats/src-chat/beads/fork", json={"bead_id": "ghost"})
    assert res.status_code == 400
