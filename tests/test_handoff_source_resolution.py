"""
Handoff endpoints resolve the SOURCE conversation's real home.

Incident (2026-10-02): conversation be643308 ("meta fixes") was GLOBAL,
homed in project bc5a9c78 with 60 messages, and was being worked from a
window open on project e0fba134.  That project held a stale same-id copy
with 0 messages (a shadow left by cross-project sync).  The handoff client
routed by the WINDOW's project, so commit_handoff:

  - wrote the continuation into e0fba134, where the rest of the client
    looked for it under bc5a9c78 (SHELL_GUARD recovery 404 -> the whole
    first turn of the continuation was held in memory and lost);
  - stamped the document's sourceMessageCount as 0;
  - set handedOffTo on the shadow, leaving the real source unlinked;
  - made a non-global child, invisible from the window in use.

The client fix routes by the conversation's own projectId; these tests pin
the server-side defence: when the copy under the requested project has no
messages, the endpoints use the fullest sibling copy instead.

DIRECTION: against the unpatched router every assertion naming project B
fails (the child lands in A with sourceMessageCount 0).
"""
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.storage.chats import ChatStorage
from app.utils.handoff_prelude import clear_pressure_cache

A = "proj_window"   # the project the window had open (holds the shadow)
B = "proj_home"     # the conversation's real home
SRC = "src-conv"


def _msgs(n):
    return [{"id": f"m{i}", "role": "human" if i % 2 == 0 else "assistant",
             "content": f"message {i}", "timestamp": i} for i in range(n)]


def _write(storage, chat_id, **extra):
    now = int(time.time() * 1000)
    rec = {"id": chat_id, "title": extra.pop("title", f"chat {chat_id}"),
           "messages": extra.pop("messages", []), "createdAt": now,
           "lastActiveAt": now, "_version": now, **extra}
    storage._write_json(storage._chat_file(chat_id), rec)
    return rec


def _read(storage, chat_id):
    return storage._read_json(storage._chat_file(chat_id))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    root = tmp_path / "projects"
    stores = {}
    for pid in (A, B):
        (root / pid / "chats").mkdir(parents=True)
        stores[pid] = ChatStorage(root / pid)
    # A third project with no copy, and a stray non-project directory, so the
    # sibling scan is exercised against things it must ignore.
    (root / "unrelated" / "chats").mkdir(parents=True)
    (root / "_path_index.json").write_text("{}")
    clear_pressure_cache()

    def project_dir(pid: str) -> Path:
        return root / pid

    with patch("app.api.handoff.get_project_dir", side_effect=project_dir):
        from app.api.handoff import router
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app), stores, root


def _commit_url(pid, chat_id=SRC):
    return f"/api/v1/projects/{pid}/chats/{chat_id}/handoff/commit"


def _seed_incident(stores):
    _write(stores[B], SRC, title="meta fixes", messages=_msgs(6),
           projectId=B, isGlobal=True)
    _write(stores[A], SRC, title="New Conversation", messages=[])


def test_commit_against_shadow_lands_in_the_sources_home(env):
    client, stores, root = env
    _seed_incident(stores)

    r = client.post(_commit_url(A), json={"document": "## Objective\nx"})
    assert r.status_code == 200, r.text
    body = r.json()
    child_id = body["handedOffTo"]

    # Outermost surface: the response and the record agree on B.
    assert body["child"]["projectId"] == B
    child = _read(stores[B], child_id)
    assert child is not None, "continuation must be written in the source's home"
    assert not (root / A / "chats" / f"{child_id}.json").exists()
    assert child["handoff"]["sourceMessageCount"] == 6
    assert child["title"] == "meta fixes (2)"
    assert child["isGlobal"] is True


def test_commit_links_the_real_source_not_the_shadow(env):
    client, stores, _ = env
    _seed_incident(stores)

    child_id = client.post(_commit_url(A), json={"document": "d"}).json()["handedOffTo"]

    assert _read(stores[B], SRC)["handedOffTo"] == child_id
    assert not _read(stores[A], SRC).get("handedOffTo")
    # The real source keeps its messages.
    assert len(_read(stores[B], SRC)["messages"]) == 6


def test_get_state_reports_the_real_message_count(env):
    client, stores, _ = env
    _seed_incident(stores)
    r = client.get(f"/api/v1/projects/{A}/chats/{SRC}/handoff")
    assert r.status_code == 200
    assert r.json()["messageCount"] == 6
    assert r.json()["title"] == "meta fixes"


def test_nonempty_local_copy_is_used_even_if_a_fuller_one_exists(env):
    """Only an EMPTY local copy is treated as a possible shadow."""
    client, stores, root = env
    _write(stores[A], SRC, title="here", messages=_msgs(2), projectId=A)
    _write(stores[B], SRC, title="elsewhere", messages=_msgs(10), projectId=B)

    body = client.post(_commit_url(A), json={"document": "d"}).json()
    assert body["child"]["projectId"] == A
    assert (root / A / "chats" / f"{body['handedOffTo']}.json").exists()
    assert _read(stores[A], SRC)["handedOffTo"] == body["handedOffTo"]
    assert not _read(stores[B], SRC).get("handedOffTo")


def test_genuinely_empty_source_still_commits_in_place(env):
    """No regression for a source with no copy anywhere else."""
    client, stores, root = env
    _write(stores[A], SRC, title="fresh", messages=[], projectId=A)

    body = client.post(_commit_url(A), json={"document": "d"}).json()
    assert body["child"]["projectId"] == A
    assert (root / A / "chats" / f"{body['handedOffTo']}.json").exists()
    assert body["child"].get("isGlobal") is False


def test_continuation_loads_from_a_window_on_another_project(env):
    """A continuation is empty by construction.  Loading or editing it
    from a window whose project does not hold it must still find it
    (TokenCountDisplay and HandoffBanner call these on every child)."""
    client, stores, _ = env
    _write(stores[B], "child", title="meta fixes (2)", messages=[],
           projectId=B, lineageKind="handoff", branchedFrom=SRC,
           handoff={"document": "old", "generatedAt": 1})

    r = client.get(f"/api/v1/projects/{A}/chats/child/handoff")
    assert r.status_code == 200, r.text
    assert r.json()["handoff"]["document"] == "old"

    r = client.patch(f"/api/v1/projects/{A}/chats/child/handoff",
                     json={"document": "new"})
    assert r.status_code == 200, r.text
    assert _read(stores[B], "child")["handoff"]["document"] == "new"
    assert _read(stores[A], "child") is None


def test_missing_everywhere_is_404(env):
    client, _, _ = env
    r = client.post(_commit_url(A, "nope"), json={"document": "d"})
    assert r.status_code == 404
