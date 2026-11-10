"""
bulk-sync must carry forward EVERY omitted client-authored lineage field,
not only lineageKind.

Observed 2026-10-20 on continuation d88d9994: the handoff commit endpoint
wrote branchedFrom / lineageRootId / lineageKind; a browser window that did
not yet hold the record minted a fresh one on the first send (no lineage at
all) and pushed it with a newer _version.  The endpoint restored lineageKind
(guarded) but nulled branchedFrom and lineageRootId (unguarded), so the
sidebar could neither nest the continuation under its source nor number it.
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
    pid = "proj-lineage"
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


def _read(proj, chat_id):
    return json.loads((proj / "chats" / f"{chat_id}.json").read_text())


LINEAGE = {
    "branchedFrom": "src-1",
    "lineageRootId": "src-1",
    "lineageKind": "handoff",
    "branchedAtMessageIndex": 7,
    "branchedFromLabel": "seam",
}


def _seed(tc, pid, proj, version):
    chat = {"id": "cont-1", "title": "Cont", "messages": [],
            "createdAt": version, "lastActiveAt": version, "_version": version, **LINEAGE}
    r = tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync", json={"chats": [chat]})
    assert r.status_code == 200
    on_disk = _read(proj, "cont-1")
    for k, v in LINEAGE.items():
        assert on_disk[k] == v, k


def test_omitted_lineage_fields_are_carried_forward_from_disk(client):
    tc, pid, proj = client
    v0 = int(time.time() * 1000)
    _seed(tc, pid, proj, v0)

    # A newer push from a window that minted the record fresh: no lineage keys at all.
    fresh = {"id": "cont-1", "title": "Cont", "createdAt": v0, "lastActiveAt": v0 + 10,
             "_version": v0 + 10,
             "messages": [{"id": "m1", "role": "human", "content": "hi", "timestamp": v0 + 10}]}
    r = tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync", json={"chats": [fresh]})
    assert r.status_code == 200

    on_disk = _read(proj, "cont-1")
    assert len(on_disk["messages"]) == 1, "the newer content was accepted"
    for k, v in LINEAGE.items():
        assert on_disk[k] == v, f"{k} was nulled by an omitting push"


def test_explicit_null_still_clears(client):
    """A client that SENDS null is clearing the field; only omission is guarded."""
    tc, pid, proj = client
    v0 = int(time.time() * 1000)
    _seed(tc, pid, proj, v0)
    clearing = {"id": "cont-1", "title": "Cont", "messages": [], "createdAt": v0,
                "lastActiveAt": v0 + 10, "_version": v0 + 10,
                "branchedFrom": None, "lineageRootId": None}
    r = tc.post(f"/api/v1/projects/{pid}/chats/bulk-sync", json={"chats": [clearing]})
    assert r.status_code == 200
    on_disk = _read(proj, "cont-1")
    assert on_disk["branchedFrom"] is None
    assert on_disk["lineageRootId"] is None
    # Omitted ones are still kept.
    assert on_disk["lineageKind"] == "handoff"
    assert on_disk["branchedAtMessageIndex"] == 7
