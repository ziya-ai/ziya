"""
Consent API carries the path-scope rung and exposes standing grants
(design/scope-consent.md D1, D4).  Seam test: the panel's POST body reaches
the gate's answer record, and the grants chip can list and revoke.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.utils import consent_gate as cg


@pytest.fixture
def client(tmp_path, monkeypatch):
    cg.reset_for_tests(tmp_path / "consents")
    root = str(tmp_path / "consents" / "project")
    from app.api import consent as api
    monkeypatch.setattr(api, "_grant_root", lambda: root)
    app = FastAPI()
    app.include_router(api.router)
    yield TestClient(app), root
    cg.reset_for_tests()


def test_path_scope_rides_the_approval_into_the_ledger(client):
    c, _ = client
    cg.ledger().open("conv:c1:t1", {"kind": "tool_call", "tool": "file_write", "op": "*"},
                     owner_id="c1")
    r = c.post("/api/consent/conv:c1:t1",
               json={"decision": "approve", "scope": "conversation", "path_scope": "frontend/src"})
    assert r.status_code == 200
    assert r.json()["answer"]["path_scope"] == "frontend/src"
    assert r.json()["answer"]["scope"] == "conversation"


def test_a_reject_carries_no_path_scope(client):
    c, _ = client
    cg.ledger().open("conv:c1:t2", {"kind": "tool_call", "tool": "file_write", "op": "*"},
                     owner_id="c1")
    r = c.post("/api/consent/conv:c1:t2",
               json={"decision": "reject", "scope": "project", "path_scope": ""})
    assert r.status_code == 200
    assert "path_scope" not in r.json()["answer"]
    assert r.json()["answer"]["scope"] == "once"


def test_grants_are_listed_per_conversation_and_revocable(client):
    c, root = client
    g = cg.grants()
    (pg,) = g.remember_paths("project", "file_write", "c1", root, ["a.py"], rung="", rule="proj")
    g.remember_paths("conversation", "file_write", "c1", root, ["b.py"], rule="conv1")
    g.remember_paths("conversation", "file_write", "c2", root, ["b.py"], rule="conv2")

    both = c.get("/api/consent/grants", params={"conversation_id": "c1"}).json()
    assert sorted(x["rule"] for x in both) == ["conv1", "proj"]
    only_project = c.get("/api/consent/grants").json()
    assert [x["rule"] for x in only_project] == ["proj"]
    assert both[0]["kind"] in ("project", "file") and "path_display" in both[0]

    assert c.delete(f"/api/consent/grants/{pg.id}").status_code == 200
    assert c.delete(f"/api/consent/grants/{pg.id}").status_code == 404
    assert [x["rule"] for x in c.get("/api/consent/grants", params={"conversation_id": "c1"}).json()] == ["conv1"]


def test_grants_route_is_not_shadowed_by_the_request_id_catch_all(client):
    """``GET /{request_id:path}`` would otherwise swallow ``/grants`` as a
    consent lookup and 404."""
    c, _ = client
    assert c.get("/api/consent/grants").status_code == 200
