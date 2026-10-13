"""Seam test: /api/v1/memory/export/* over the real router.

Asserts on the HTTP surface (status codes, headers, body), not on the
plan object the tests could build themselves.  The 409 and 412 refusals
are the "within bounds" guarantee the UI relies on.
"""
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.memory import router
from app.models.memory import Memory, MemoryScope, MindMapNode
from app.storage.memory import MemoryStorage

ZIYA = "/Users/alice/workspace/ziya"
BILLING = "/Users/alice/workspace/shop-billing"


@pytest.fixture
def storage(tmp_path):
    s = MemoryStorage(memory_dir=tmp_path / "memory")
    s.save_mindmap_node(MindMapNode(id="domain_bench", handle="bench architecture",
                                    tags=["bench"], memory_refs=["m_b1"]))
    s.save_mindmap_node(MindMapNode(id="domain_net", handle="billing fixes",
                                    tags=["billing"], memory_refs=["m_n1"]))
    s.save_many([
        Memory(id="m_b1", content="Bench uses a flat JSON store for runs.",
               layer="architecture", scope=MemoryScope(project_paths=[ZIYA]),
               learned_from_conversation="conv-111"),
        Memory(id="m_n1", content="BILL-793 fix: retry; key AKIAABCDEFGHIJKLMNOP",
               layer="domain_context", scope=MemoryScope(project_paths=[BILLING])),
    ])
    return s


@pytest.fixture
def client(storage, tmp_path):
    from app.storage.proposals import ProposalsStore
    app = FastAPI()
    app.include_router(router)
    events = []

    def _log(event_name, source_tool="unknown", details=None):
        events.append((event_name, source_tool, details or {}))

    with patch("app.storage.memory.get_memory_storage", return_value=storage), \
         patch("app.storage.proposals.get_proposals_store",
               return_value=ProposalsStore(memory_dir=tmp_path / "memory")), \
         patch("app.utils.tool_audit_log.log_security_event", side_effect=_log):
        yield TestClient(app), storage, events


def test_options_route(client):
    c, _, _ = client
    r = c.get("/api/v1/memory/export/options")
    assert r.status_code == 200
    body = r.json()
    assert {d["id"] for d in body["domains"]} == {"domain_bench", "domain_net"}
    assert {p["label"] for p in body["projects"]} == {"ziya", "shop-billing"}


def test_preview_then_export_round_trip(client):
    c, _, events = client
    sel = {"domains": ["domain_bench"]}
    pv = c.post("/api/v1/memory/export/preview", json=sel)
    assert pv.status_code == 200
    preview = pv.json()
    assert preview["memory_ids"] == ["m_b1"]
    assert preview["warnings"] == []
    assert preview["sample"][0]["id"] == "m_b1"
    assert "learned_from_conversation" not in preview["sample"][0]

    r = c.post("/api/v1/memory/export",
               json={"selection": sel, "fingerprint": preview["fingerprint"]})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment; filename=\"ziya-memory-export-" in r.headers["content-disposition"]
    assert r.headers["x-ziya-export-fingerprint"] == preview["fingerprint"]
    assert r.headers["x-ziya-export-count"] == "1"
    bundle = json.loads(r.content)
    assert bundle["format"] == "ziya-memory-export"
    assert [m["id"] for m in bundle["memories"]] == ["m_b1"]
    assert "conv-111" not in r.text and ZIYA not in r.text
    assert [e[0] for e in events] == ["memory_exported"]
    assert events[0][2]["total"] == 1


def test_export_refuses_stale_fingerprint(client):
    c, _, events = client
    r = c.post("/api/v1/memory/export",
               json={"selection": {}, "fingerprint": "deadbeefdeadbeef"})
    assert r.status_code == 409
    assert "preview" in r.json()["detail"].lower()
    assert [e[0] for e in events] == ["memory_export_refused"]


def test_export_refuses_store_change_between_preview_and_export(client):
    c, storage, _ = client
    sel = {"domains": ["domain_net"]}
    fp = c.post("/api/v1/memory/export/preview", json=sel).json()["fingerprint"]
    storage.save(Memory(id="m_n2", content="New fact after preview, in billing domain.",
                        scope=MemoryScope(project_paths=[BILLING], domain_node="domain_net")))
    r = c.post("/api/v1/memory/export",
               json={"selection": sel, "fingerprint": fp, "acknowledge_warnings": True})
    assert r.status_code == 409


def test_export_requires_warning_acknowledgement(client):
    c, _, events = client
    sel = {"domains": ["domain_net"]}
    preview = c.post("/api/v1/memory/export/preview", json=sel).json()
    assert any(w["kind"].startswith("credential:") for w in preview["warnings"])
    r = c.post("/api/v1/memory/export",
               json={"selection": sel, "fingerprint": preview["fingerprint"]})
    assert r.status_code == 412
    assert events == []
    r = c.post("/api/v1/memory/export",
               json={"selection": sel, "fingerprint": preview["fingerprint"],
                     "acknowledge_warnings": True})
    assert r.status_code == 200
    assert events[-1][2]["warnings_acknowledged"] == len(preview["warnings"])


def test_selection_rejects_unknown_fields(client):
    c, _, _ = client
    r = c.post("/api/v1/memory/export/preview", json={"domians": ["x"]})
    assert r.status_code == 422
