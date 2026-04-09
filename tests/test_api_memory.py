"""
Tests for app.api.memory — Memory REST API endpoints.

Covers:
  - GET /api/v1/memory — status overview
  - GET /api/v1/memory/all — list all
  - POST /api/v1/memory — save
  - PUT /api/v1/memory/{id} — edit
  - DELETE /api/v1/memory/{id} — delete
  - Proposals: list, approve, dismiss
  - Mind-map: list, expand
  - Review and maintenance
"""

import pytest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.memory import router
from app.storage.memory import MemoryStorage
from app.models.memory import Memory, MemoryProposal


@pytest.fixture
def storage(tmp_path):
    return MemoryStorage(memory_dir=tmp_path / "memory")


@pytest.fixture
def client(storage):
    app = FastAPI()
    app.include_router(router)
    with patch("app.storage.memory.get_memory_storage", return_value=storage):
        yield TestClient(app), storage


class TestMemoryStatus:
    def test_empty_status(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["pending_proposals"] == 0

    def test_status_with_data(self, client):
        tc, store = client
        store.save(Memory(content="fact 1", layer="architecture"))
        store.save(Memory(content="fact 2", layer="lexicon"))
        store.add_proposal(MemoryProposal(content="pending"))
        resp = tc.get("/api/v1/memory")
        data = resp.json()
        assert data["total"] == 2
        assert data["pending_proposals"] == 1


class TestMemoryCRUD:
    def test_save_and_list(self, client):
        tc, _ = client
        resp = tc.post("/api/v1/memory", json={"content": "test fact", "layer": "decision", "tags": ["test"]})
        assert resp.status_code == 200
        mem_id = resp.json()["id"]

        resp = tc.get("/api/v1/memory/all")
        assert resp.status_code == 200
        assert any(m["id"] == mem_id for m in resp.json())

    def test_update(self, client):
        tc, store = client
        mem = store.save(Memory(content="original"))
        resp = tc.put(f"/api/v1/memory/{mem.id}", json={"content": "updated"})
        assert resp.status_code == 200
        assert resp.json()["content"] == "updated"

    def test_delete(self, client):
        tc, store = client
        mem = store.save(Memory(content="doomed"))
        resp = tc.delete(f"/api/v1/memory/{mem.id}")
        assert resp.status_code == 200
        assert tc.get("/api/v1/memory/all").json() == []

    def test_delete_nonexistent_404(self, client):
        tc, _ = client
        assert tc.delete("/api/v1/memory/nonexistent").status_code == 404


class TestProposals:
    def test_approve_proposal(self, client):
        tc, store = client
        p = MemoryProposal(content="proposed fact", layer="lexicon", tags=["test"])
        store.add_proposal(p)
        resp = tc.post(f"/api/v1/memory/proposals/{p.id}/approve")
        assert resp.status_code == 200
        # Should now be in memories, not proposals
        assert len(tc.get("/api/v1/memory/proposals").json()) == 0
        assert len(tc.get("/api/v1/memory/all").json()) == 1

    def test_dismiss_proposal(self, client):
        tc, store = client
        p = MemoryProposal(content="rejected")
        store.add_proposal(p)
        resp = tc.delete(f"/api/v1/memory/proposals/{p.id}")
        assert resp.status_code == 200
        assert len(tc.get("/api/v1/memory/proposals").json()) == 0

    def test_approve_all(self, client):
        tc, store = client
        store.add_proposal(MemoryProposal(content="a"))
        store.add_proposal(MemoryProposal(content="b"))
        resp = tc.post("/api/v1/memory/proposals/approve-all")
        assert resp.status_code == 200
        assert resp.json()["approved"] == 2
        assert len(tc.get("/api/v1/memory/all").json()) == 2
