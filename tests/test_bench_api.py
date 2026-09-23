"""
Bench API (design/capabilities-hub.md, slice 3).

Item assembly is stubbed — the skill catalog and MCP manager are slice-2
inputs, not what this suite is about.  Storage is REAL: user and project
layers go through JsonStateStore under a tmp ZIYA_HOME, and the conversation
layer through a minimal chat-storage double with the same get/update shape.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import bench as bench_api
from app.models.activation import ItemSpec
from app.models.chat import Chat, ChatUpdate
from app.services import bench as svc

PID = "proj1"
CID = "chat1"


def _items():
    return [
        svc.BenchItem(ItemSpec(key="skill:disc", kind="skill", discoverable=True), "disc", "project", tokens=100),
        svc.BenchItem(ItemSpec(key="skill:pick", kind="skill"), "pick", "user", tokens=50),
        svc.BenchItem(ItemSpec(key="mcp:jira", kind="mcp"), "jira", "user", tokens=41000, health={"enabled": True}),
        svc.BenchItem(ItemSpec(key="mcp:shell", kind="mcp", tier="environment"), "shell", "builtin", tokens=900),
    ]


class _Chats:
    def __init__(self):
        self.chats = {CID: Chat(id=CID, title="t", createdAt=1, lastActiveAt=1)}

    def get(self, cid):
        return self.chats.get(cid)

    def update(self, cid, data: ChatUpdate):
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(self.chats[cid], k, v)
        return self.chats[cid]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    monkeypatch.setattr(svc, "collect_items", lambda pid: _items())
    chats = _Chats()
    monkeypatch.setattr(svc, "_chat_storage", lambda pid: chats)

    async def _no_mcp(name, enabled):
        _no_mcp.calls.append((name, enabled))
    _no_mcp.calls = []
    monkeypatch.setattr(bench_api, "_sync_mcp_enabled", _no_mcp)

    app = FastAPI(); app.include_router(bench_api.router)
    c = TestClient(app)
    c.chats, c.mcp_calls = chats, _no_mcp.calls
    return c


def _get(c, cid=None):
    q = f"?conversation_id={cid}" if cid else ""
    r = c.get(f"/api/v1/projects/{PID}/bench{q}")
    assert r.status_code == 200, r.text
    return r.json()


def _put(c, key, layer, state, cid=None):
    return c.put(f"/api/v1/projects/{PID}/bench/place",
                 json={"key": key, "layer": layer, "state": state, "conversation_id": cid})


def _item(body, key):
    return next(i for i in body["items"] if i["key"] == key)


def test_get_defaults_and_shape(client):
    body = _get(client)
    assert _item(body, "skill:disc")["effective"] == "ondemand"
    assert _item(body, "skill:pick")["effective"] == "off"
    assert _item(body, "mcp:jira") | {"effective": "always", "origin": "default"} == _item(body, "mcp:jira")
    shell = _item(body, "mcp:shell")
    assert shell["tier"] == "environment" and shell["origin"] == "environment"
    assert shell["provenance"] == "builtin" and shell["tokens"] == 900
    assert body["lenses"] == ["project", "user"], "no conversation lens without a conversation"
    assert body["weight"] == {"always": 41000, "ondemand": 100, "environment": 900}
    assert body["deltas"] == {"conversation": 0, "project": 0, "user": 0}


@pytest.mark.parametrize("layer", ["user", "project", "conversation"])
def test_get_reflects_put_at_each_layer(client, layer):
    r = _put(client, "skill:disc", layer, "always", cid=CID)
    assert r.status_code == 200, r.text
    body = _get(client, CID)
    item = _item(body, "skill:disc")
    assert item["effective"] == "always" and item["origin"] == layer
    assert item["placements"][layer] == "always"
    assert body["deltas"][layer] == 1
    if layer == "conversation":
        assert client.chats.get(CID).placements == {"skill:disc": "always"}
        assert "conversation" in body["lenses"]

    # clearing removes the pin and falls back to the default
    assert _put(client, "skill:disc", layer, None, cid=CID).status_code == 200
    item = _item(_get(client, CID), "skill:disc")
    assert item["effective"] == "ondemand" and item["origin"] == "default"
    assert item["placements"][layer] is None


def test_layers_are_independent_files_and_precedence_holds(client, tmp_path):
    _put(client, "skill:pick", "user", "always")
    _put(client, "skill:pick", "project", "off")
    assert (tmp_path / "state" / "bench_placements.json").exists()
    assert (tmp_path / "projects" / PID / "state" / "bench_placements.json").exists()
    item = _item(_get(client), "skill:pick")
    assert item["effective"] == "off" and item["origin"] == "project"
    assert item["placements"] == {"conversation": None, "project": "off", "user": "always"}


def test_environment_item_409_on_put(client):
    r = _put(client, "mcp:shell", "user", "off")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "environment_not_placeable"


def test_mcp_ondemand_409_and_leaves_no_trace(client):
    r = _put(client, "mcp:jira", "project", "ondemand")
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "mcp_ondemand_unsupported"
    assert _item(_get(client), "mcp:jira")["placements"]["project"] is None
    assert client.mcp_calls == []


def test_unknown_key_and_bad_layer_409(client):
    assert _put(client, "skill:ghost", "project", "always").status_code == 409
    assert _put(client, "skill:disc", "default", "always").status_code == 409


def test_conversation_layer_needs_a_conversation(client):
    assert _put(client, "skill:disc", "conversation", "always").status_code == 400
    assert _put(client, "skill:disc", "conversation", "always", cid="nope").status_code == 404


def test_user_mcp_off_syncs_enable_switch(client):
    assert _put(client, "mcp:jira", "user", "off").status_code == 200
    assert _put(client, "mcp:jira", "user", None).status_code == 200
    assert client.mcp_calls == [("jira", False), ("jira", True)]


def test_prompt_builder_always_set_equals_get_effective_always_set(client):
    _put(client, "skill:disc", "project", "always")
    _put(client, "skill:pick", "conversation", "always", cid=CID)
    _put(client, "mcp:jira", "user", "off")
    body = _get(client, CID)
    via_get = sorted(i["key"] for i in body["items"] if i["effective"] == "always")
    via_prompt = sorted(svc.resolve_bench(PID, CID).always_set())
    assert via_get == via_prompt == ["mcp:shell", "skill:disc", "skill:pick"]
    assert sorted(svc.always_skill_names(PID, CID)) == ["disc", "pick"]


def test_route_order_guard():
    paths = {(r.path, tuple(sorted(r.methods))) for r in bench_api.router.routes}
    assert ("/api/v1/projects/{project_id}/bench", ("GET",)) in paths
    assert ("/api/v1/projects/{project_id}/bench/place", ("PUT",)) in paths
    assert not any("{" in r.path.split("/bench", 1)[1] for r in bench_api.router.routes), \
        "no /{id} route may follow /place"
