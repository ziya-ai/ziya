"""Seam test: /api/v1/memory/import/* over the real router.

Asserts on the HTTP surface and on the store afterwards.  The 409 on
either fingerprint is the "exactly what you previewed" guarantee.
"""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.memory import router
from app.models.memory import Memory, MindMapNode
from app.storage.memory import MemoryStorage


def bundle(memories, mindmap=None):
    return {"format": "ziya-memory-export", "version": 1, "exported_by": "alice",
            "exported_at": "2026-10-07T00:00:00Z", "memories": memories, "mindmap": mindmap or []}


@pytest.fixture
def client(tmp_path):
    from app.storage.proposals import ProposalsStore
    storage = MemoryStorage(memory_dir=tmp_path / "memory")
    storage.save_mindmap_node(MindMapNode(id="domain_net", handle="billing", memory_refs=["m_n1"]))
    storage.save_many([Memory(id="m_n1", content="Existing billing fact."),
                       Memory(id="m_mine", content="My own fact.")])
    app = FastAPI()
    app.include_router(router)
    events = []
    with patch("app.storage.memory.get_memory_storage", return_value=storage), \
         patch("app.storage.proposals.get_proposals_store",
               return_value=ProposalsStore(memory_dir=tmp_path / "memory")), \
         patch("app.utils.tool_audit_log.log_security_event",
               side_effect=lambda n, s="?", d=None: events.append((n, d or {}))):
        yield TestClient(app), storage, events


def test_preview_then_import_round_trip(client):
    c, storage, events = client
    b = bundle([{"id": "m_new", "content": "Alice's new fact.", "layer": "decision"},
                {"id": "m_n1", "content": "Existing billing fact."},
                {"id": "m_mine", "content": "Alice's fact under my id."}],
               mindmap=[{"id": "domain_net", "handle": "alice billing", "memory_refs": ["m_new", "m_n1"]}])
    pv = c.post("/api/v1/memory/import/preview", json={"bundle": b})
    assert pv.status_code == 200
    p = pv.json()
    assert p["total"] == 3 and p["exported_by"] == "alice"
    assert p["counts"]["new"] == 1 and p["counts"]["identical"] == 1
    assert p["counts"]["collision_renamed"] == 1 and p["counts"]["to_write"] == 2
    assert p["nodes_merged"] == ["domain_net"]
    assert "dispositions" not in p and "id_map" not in p
    assert {r["id"]: r["disposition"] for r in p["sample"]} == {
        "m_new": "new", "m_n1": "identical", "m_mine": "collision_renamed"}
    assert storage.get("m_new") is None                       # preview wrote nothing

    r = c.post("/api/v1/memory/import", json={
        "bundle": b, "bundle_fingerprint": p["bundle_fingerprint"],
        "store_fingerprint": p["store_fingerprint"], "source": "alice.json",
        "options": {"source_label": "alice"}})
    assert r.status_code == 200
    assert r.json()["imported"] == 2
    new = storage.get("m_new")
    assert new and new.learned_from == "imported" and "from:alice" in new.tags
    assert storage.get("m_mine").content == "My own fact."   # renamed, not overwritten
    assert len(storage.list_memories()) == 4
    assert set(storage.get_mindmap_node("domain_net").memory_refs) == {"m_n1", "m_new"}
    assert [e[0] for e in events] == ["memory_imported"]
    assert events[0][1]["imported"] == 2 and events[0][1]["source"] == "alice.json"


def test_import_refuses_stale_fingerprints(client):
    c, storage, events = client
    b = bundle([{"id": "m_new", "content": "Fresh."}])
    p = c.post("/api/v1/memory/import/preview", json={"bundle": b}).json()
    bad = c.post("/api/v1/memory/import", json={
        "bundle": b, "bundle_fingerprint": "deadbeefdeadbeef",
        "store_fingerprint": p["store_fingerprint"]})
    assert bad.status_code == 409
    storage.save(Memory(id="m_late", content="Arrived after preview."))
    bad = c.post("/api/v1/memory/import", json={
        "bundle": b, "bundle_fingerprint": p["bundle_fingerprint"],
        "store_fingerprint": p["store_fingerprint"]})
    assert bad.status_code == 409 and "preview" in bad.json()["detail"].lower()
    assert storage.get("m_new") is None
    assert [e[0] for e in events] == ["memory_import_refused", "memory_import_refused"]


def test_import_rejects_malformed_bundle_and_options(client):
    c, _, _ = client
    r = c.post("/api/v1/memory/import/preview", json={"bundle": {"format": "nope", "memories": []}})
    assert r.status_code == 422 and "ziya-memory-export" in r.json()["detail"]
    r = c.post("/api/v1/memory/import/preview",
               json={"bundle": bundle([]), "options": {"on_collision": "explode"}})
    assert r.status_code == 422
