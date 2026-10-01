"""
Authorization gates on the bench (design/capabilities-hub.md, "Gates").

A rug-pull quarantine must show as HELD -- connected, tools listed, tokens
not counted -- never as "not connected"; and re-authorizing from the drawer
must go through the manager's human-initiated path, refusals included.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import bench as svc
from app.services.token_service import TokenService
from app.storage.skills import SkillStorage

PID = "proj1"


class _Mgr:
    def __init__(self):
        self.server_configs = {"pippin": {}, "jira": {}}
        self.is_initialized = True
        self._quarantined_servers = {"pippin"}
        tool = SimpleNamespace(name="mcp_scan", description="Scan a thing",
                               inputSchema={"type": "object", "properties": {"x": {"type": "string"}}})
        self.clients = {"pippin": SimpleNamespace(tools=[tool])}
        self.refuse = False
        self.calls = []

    def get_all_tools(self):   # the manager withholds a quarantined server's tools
        return [SimpleNamespace(name="mcp_j", _server_name="jira", description="", inputSchema={})]

    def reauthorize_server(self, name, force=False):
        self.calls.append((name, force))
        if self.refuse and not force:
            return {"success": False, "message": "refused", "can_force": True,
                    "blocking_tools": {"mcp_scan": ["injection pattern"]}, "advisory_tools": {}}
        self._quarantined_servers.discard(name)
        return {"success": True, "message": f"Server '{name}' re-authorized"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    storage = SkillStorage(tmp_path / "projects" / PID, TokenService())
    monkeypatch.setattr(svc, "_skill_storage", lambda pid: storage)
    monkeypatch.setattr(svc, "_builtin_items", lambda: [])
    mgr = _Mgr()
    import app.mcp.manager as mm
    monkeypatch.setattr(mm, "get_mcp_manager", lambda: mgr)
    import app.mcp.permissions as pm
    monkeypatch.setattr(pm, "get_permissions_manager",
                        lambda: SimpleNamespace(get_permissions=lambda: {"defaults": {"tool": "enabled"}, "servers": {}}))
    return mgr


def _vm(cid=None):
    from app.api.bench import _view_model
    items = svc.collect_items(PID)
    return _view_model(items, svc.resolve_bench(PID, cid, items), cid)


def test_quarantined_server_is_held_not_disconnected(env):
    card = next(i for i in _vm()["items"] if i["key"] == "mcp:pippin")
    assert card["health"]["connected"] is True and card["health"]["quarantined"] is True
    assert card["health"]["gate"]["state"] == "quarantined"
    assert card["held"] is True and card["health"]["tool_count"] == 1
    assert card["tokens"] > 0, "the card shows what re-authorizing would send"
    assert card["effective"] == "always", "the placement is untouched; the gate is separate"
    # ...but a held item weighs nothing: the request path is not sending it
    jira = next(i for i in _vm()["items"] if i["key"] == "mcp:jira")
    assert _vm()["weight"]["always"] == jira["tokens"] and jira["held"] is False
    d = svc.item_detail(PID, "mcp:pippin")
    assert d["connected"] is True and d["gate"]["state"] == "quarantined" and d["tools"] == ["mcp_scan"]
    assert svc.item_detail(PID, "mcp:jira")["gate"] is None


def test_reauthorize_route_lifts_the_gate_and_recosts(env):
    from app.api.bench import ReauthorizeRequest, post_reauthorize
    from fastapi import HTTPException
    env.refuse = True
    r = asyncio.run(post_reauthorize(PID, ReauthorizeRequest(server="pippin"), None))
    assert r["result"]["success"] is False and r["result"]["can_force"] is True
    assert "mcp_scan" in r["result"]["blocking_tools"]
    assert r["detail"]["gate"] is not None, "a refusal leaves the gate in place"
    r = asyncio.run(post_reauthorize(PID, ReauthorizeRequest(server="pippin", force=True), None))
    assert r["result"]["success"] is True and env.calls == [("pippin", False), ("pippin", True)]
    card = next(i for i in r["view"]["items"] if i["key"] == "mcp:pippin")
    assert card["held"] is False and r["detail"]["gate"] is None
    assert r["view"]["weight"]["always"] == sum(i["tokens"] for i in r["view"]["items"] if i["kind"] == "mcp")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(post_reauthorize(PID, ReauthorizeRequest(server="nope"), None))
    assert ei.value.status_code == 404 and len(env.calls) == 2       # 404 before acting
