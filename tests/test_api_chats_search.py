"""
Tests for the GET /api/v1/projects/{pid}/chats/search endpoint.

Critical regression guard: the /chats/search route MUST resolve to the
search handler and NOT be captured by /chats/{chat_id} (which would 404 or
try to load a chat literally named "search").
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def ziya_home(tmp_path):
    home = tmp_path / ".ziya"
    home.mkdir()
    return home


@pytest.fixture
def project_id(ziya_home):
    pid = "test-project-001"
    proj_dir = ziya_home / "projects" / pid
    (proj_dir / "chats").mkdir(parents=True)
    (proj_dir / "project.json").write_text(json.dumps({
        "id": pid, "name": "Test", "path": "/x",
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000),
        "lastAccessedAt": int(time.time() * 1000),
    }))
    return pid


def _write_chat(ziya_home, pid, chat_id, content, title="Untitled"):
    chats = ziya_home / "projects" / pid / "chats"
    chats.mkdir(parents=True, exist_ok=True)
    (chats / f"{chat_id}.json").write_text(json.dumps({
        "id": chat_id, "title": title,
        "messages": [{"id": "m1", "role": "human", "content": content}],
        "createdAt": 1000, "lastActiveAt": 1000,
    }))


@pytest.fixture
def client(ziya_home, project_id):
    with patch.dict(os.environ, {"ZIYA_HOME": str(ziya_home)}):
        with patch("app.api.chats.get_ziya_home", return_value=ziya_home):
            with patch("app.api.chats.get_project_dir",
                       return_value=ziya_home / "projects" / project_id):
                from app.api.chats import router
                app = FastAPI()
                app.include_router(router)
                yield TestClient(app), project_id


def test_search_route_not_captured_by_chat_id(client, ziya_home):
    """GET /chats/search must hit the search handler, not /chats/{chat_id}."""
    tc, pid = client
    _write_chat(ziya_home, pid, "c1", "find the outlook reference")
    res = tc.get(f"/api/v1/projects/{pid}/chats/search?q=outlook")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["conversationId"] == "c1"


def test_search_missing_query_is_422(client):
    tc, pid = client
    res = tc.get(f"/api/v1/projects/{pid}/chats/search")
    assert res.status_code == 422  # q is required (min_length=1)


def test_search_no_results(client, ziya_home):
    tc, pid = client
    _write_chat(ziya_home, pid, "c1", "nothing relevant")
    res = tc.get(f"/api/v1/projects/{pid}/chats/search?q=outlook")
    assert res.status_code == 200
    assert res.json() == []


def test_search_unknown_project_404(client):
    tc, _pid = client
    res = tc.get("/api/v1/projects/does-not-exist/chats/search?q=outlook")
    assert res.status_code == 404


def test_search_local_scope_default(client, ziya_home):
    """Default (all_projects unset) searches strictly this project."""
    tc, pid = client
    _write_chat(ziya_home, pid, "c1", "outlook local")
    # A chat in another project should not appear unless all_projects=true.
    _write_chat(ziya_home, "other-project", "c2", "outlook elsewhere")
    res = tc.get(f"/api/v1/projects/{pid}/chats/search?q=outlook")
    assert res.status_code == 200
    assert {r["conversationId"] for r in res.json()} == {"c1"}


def test_search_all_projects(client, ziya_home):
    tc, pid = client
    _write_chat(ziya_home, pid, "c1", "outlook local")
    _write_chat(ziya_home, "other-project", "c2", "outlook elsewhere")
    res = tc.get(f"/api/v1/projects/{pid}/chats/search?q=outlook&all_projects=true")
    assert res.status_code == 200
    assert {r["conversationId"] for r in res.json()} == {"c1", "c2"}


# ---------------------------------------------------------------------------
# sort= parameter.  These cover the HTTP seam specifically: a `sort` query
# param that is declared on the endpoint but never forwarded to search_chats
# would still return 200 with plausible-looking results, so the assertions
# below compare ORDER across modes rather than just checking the status code.
# Ranking semantics themselves live in tests/test_chat_search_ranking.py.
# ---------------------------------------------------------------------------

def _write_aged_chat(ziya_home, pid, chat_id, content, title, last_active):
    chats = ziya_home / "projects" / pid / "chats"
    chats.mkdir(parents=True, exist_ok=True)
    (chats / f"{chat_id}.json").write_text(json.dumps({
        "id": chat_id, "title": title,
        "messages": [{"id": "m1", "role": "human", "content": content}],
        "createdAt": 1000, "lastActiveAt": last_active,
    }))


@pytest.fixture
def sortable(client, ziya_home):
    """Two chats where relevance order and newest order are opposites.

    strong: term in the title AND the opening message, but oldest.
    weak:   term in the opening message only, but newest.
    """
    tc, pid = client
    _write_aged_chat(ziya_home, pid, "strong", "regulator detail",
                     title="regulator teardown", last_active=100)
    _write_aged_chat(ziya_home, pid, "weak", "regulator mention",
                     title="misc notes", last_active=9_000)
    return tc, pid


def test_search_sort_defaults_to_relevance(sortable):
    tc, pid = sortable
    res = tc.get(f"/api/v1/projects/{pid}/chats/search?q=regulator")
    assert res.status_code == 200
    assert [r["conversationId"] for r in res.json()] == ["strong", "weak"]


def test_search_sort_newest_reaches_the_search_layer(sortable):
    """sort=newest must invert the default order, proving it is forwarded."""
    tc, pid = sortable
    res = tc.get(f"/api/v1/projects/{pid}/chats/search?q=regulator&sort=newest")
    assert res.status_code == 200
    assert [r["conversationId"] for r in res.json()] == ["weak", "strong"]


def test_search_sort_oldest_reaches_the_search_layer(sortable):
    tc, pid = sortable
    res = tc.get(f"/api/v1/projects/{pid}/chats/search?q=regulator&sort=oldest")
    assert res.status_code == 200
    assert [r["conversationId"] for r in res.json()] == ["strong", "weak"]


def test_search_sort_rejects_unknown_mode(sortable):
    """An unrecognised sort value is a 422 at the HTTP boundary.

    search_chats() itself tolerates junk (a stale client must not break), but
    the endpoint is strict so a typo in a new client surfaces immediately.
    """
    tc, pid = sortable
    res = tc.get(f"/api/v1/projects/{pid}/chats/search?q=regulator&sort=sideways")
    assert res.status_code == 422


def test_search_response_carries_ranking_fields(sortable):
    """The fields the frontend comparator reads must be on the wire."""
    tc, pid = sortable
    res = tc.get(f"/api/v1/projects/{pid}/chats/search?q=regulator")
    assert res.status_code == 200
    for row in res.json():
        assert isinstance(row["relevanceScore"], (int, float))
        assert isinstance(row["lastActivityAt"], int)
