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
def proposals(tmp_path):
    """Isolated probationary store.  The proposal endpoints read
    ``get_proposals_store()``, not ``MemoryStorage``; without this patch
    they would operate on the live ~/.ziya/memory/probationary.jsonl."""
    from app.storage.proposals import ProposalsStore
    return ProposalsStore(memory_dir=tmp_path / "memory")


@pytest.fixture
def client(storage, proposals):
    app = FastAPI()
    app.include_router(router)
    with patch("app.storage.memory.get_memory_storage", return_value=storage), \
         patch("app.storage.proposals.get_proposals_store", return_value=proposals):
        yield TestClient(app), storage


class TestMemoryStatus:
    def test_empty_status(self, client):
        tc, _ = client
        resp = tc.get("/api/v1/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["pending_proposals"] == 0

    def test_status_with_data(self, client, proposals):
        tc, store = client
        prob = proposals
        store.save(Memory(content="fact 1", layer="architecture"))
        store.save(Memory(content="fact 2", layer="lexicon"))
        # pending_proposals must count the probationary store, not the
        # legacy proposals.json.  Seed the legacy file with a decoy so a
        # regression to the old source reads 1 instead of 2.
        prob.add(MemoryProposal(content="pending a"))
        prob.add(MemoryProposal(content="pending b"))
        store.add_proposal(MemoryProposal(content="legacy decoy"))
        resp = tc.get("/api/v1/memory")
        data = resp.json()
        assert data["total"] == 2
        assert data["pending_proposals"] == 2


class TestMemoryCRUD:
    def test_save_and_list(self, client):
        tc, _ = client
        resp = tc.post("/api/v1/memory", json={"content": "a test fact for crud", "layer": "decision", "tags": ["test"]})
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
    """The proposal endpoints are backed by the probationary
    ``ProposalsStore`` (event-log projection), not the legacy
    ``MemoryStorage.add_proposal`` queue — seed the same store they read."""

    def test_list_open_proposals(self, client, proposals):
        tc, _ = client
        pid = proposals.add(MemoryProposal(content="proposed fact", layer="lexicon"))
        rows = tc.get("/api/v1/memory/proposals").json()
        assert [r["id"] for r in rows] == [pid]
        assert rows[0]["age"] == 0
        assert rows[0]["would_promote"] is None

    def test_approve_proposal(self, client, proposals):
        tc, store = client
        pid = proposals.add(MemoryProposal(content="proposed fact", layer="lexicon", tags=["test"]))
        resp = tc.post(f"/api/v1/memory/proposals/{pid}/approve")
        assert resp.status_code == 200
        assert resp.json()["content"] == "proposed fact"
        # Should now be in memories, not proposals
        assert len(tc.get("/api/v1/memory/proposals").json()) == 0
        assert len(tc.get("/api/v1/memory/all").json()) == 1
        assert proposals.get(pid)["status"] == "promoted"
        assert store.get(resp.json()["id"]).learned_from == "promoted_from_proposal"

    def test_approve_unknown_proposal_404(self, client):
        tc, _ = client
        assert tc.post("/api/v1/memory/proposals/prop_nope/approve").status_code == 404

    def test_dismiss_proposal(self, client, proposals):
        tc, _ = client
        pid = proposals.add(MemoryProposal(content="rejected"))
        resp = tc.delete(f"/api/v1/memory/proposals/{pid}")
        assert resp.status_code == 200
        assert len(tc.get("/api/v1/memory/proposals").json()) == 0
        assert proposals.get(pid)["status"] == "archived"
        # Dismissing again is a 404: the row is terminal, not open.
        assert tc.delete(f"/api/v1/memory/proposals/{pid}").status_code == 404

    def test_approve_all(self, client, proposals):
        tc, _ = client
        proposals.add(MemoryProposal(content="a"))
        proposals.add(MemoryProposal(content="b"))
        resp = tc.post("/api/v1/memory/proposals/approve-all")
        assert resp.status_code == 200
        assert resp.json()["approved"] == 2
        assert len(tc.get("/api/v1/memory/all").json()) == 2
        assert tc.get("/api/v1/memory/proposals").json() == []
