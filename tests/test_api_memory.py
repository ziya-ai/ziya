"""
Tests for app.api.memory — Memory REST API endpoints.

Covers:
  - GET /api/v1/memory (status overview)
  - GET /api/v1/memory/search
  - POST /api/v1/memory (direct save)
  - PUT /api/v1/memory/{id} (edit)
  - DELETE /api/v1/memory/{id}
  - Proposal lifecycle: list, approve, approve-all, dismiss
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.storage.memory import MemoryStorage


@pytest.fixture
def memory_storage(tmp_path):
    """Create an isolated MemoryStorage for tests."""
    return MemoryStorage(memory_dir=tmp_path / "memory")


@pytest.fixture
def client(memory_storage):
    """Create a test client with patched storage singleton."""
    with patch("app.storage.memory.get_memory_storage", return_value=memory_storage):
        from app.api.memory import router
        app = FastAPI()
        app.include_router(router)
        yield TestClient(app)


# ── Status ─────────────────────────────────────────────────────────

class TestStatus:

    def test_empty_store_status(self, client):
        resp = client.get("/api/v1/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["pending_proposals"] == 0

    def test_status_reflects_counts(self, client):
        client.post("/api/v1/memory", json={"content": "fact 1", "layer": "decision"})
        client.post("/api/v1/memory", json={"content": "fact 2", "layer": "lexicon"})
        resp = client.get("/api/v1/memory")
        data = resp.json()
        assert data["total"] == 2
        assert data["by_layer"]["decision"] == 1


# ── Search ─────────────────────────────────────────────────────────

class TestSearch:

    def test_search_by_keyword(self, client):
        client.post("/api/v1/memory", json={"content": "CCSDS framing protocol"})
        client.post("/api/v1/memory", json={"content": "IP rejected"})
        resp = client.get("/api/v1/memory/search?q=CCSDS")
        results = resp.json()
        assert len(results) == 1
        assert "CCSDS" in results[0]["content"]

    def test_search_empty_query_returns_all(self, client):
        client.post("/api/v1/memory", json={"content": "fact A"})
        client.post("/api/v1/memory", json={"content": "fact B"})
        resp = client.get("/api/v1/memory/search")
        results = resp.json()
        assert len(results) == 2


# ── CRUD ───────────────────────────────────────────────────────────

class TestCRUD:

    def test_save_and_list(self, client):
        resp = client.post("/api/v1/memory", json={
            "content": "test memory",
            "layer": "architecture",
            "tags": ["test"],
        })
        assert resp.status_code == 200
        memory_id = resp.json()["id"]

        all_resp = client.get("/api/v1/memory/all")
        assert len(all_resp.json()) == 1
        assert all_resp.json()[0]["id"] == memory_id

    def test_update_memory(self, client):
        resp = client.post("/api/v1/memory", json={"content": "original"})
        mid = resp.json()["id"]

        update_resp = client.put(f"/api/v1/memory/{mid}", json={"content": "updated"})
        assert update_resp.status_code == 200
        assert update_resp.json()["content"] == "updated"

    def test_update_nonexistent_404(self, client):
        resp = client.put("/api/v1/memory/nonexistent", json={"content": "nope"})
        assert resp.status_code == 404

    def test_delete_memory(self, client):
        resp = client.post("/api/v1/memory", json={"content": "doomed"})
        mid = resp.json()["id"]
        del_resp = client.delete(f"/api/v1/memory/{mid}")
        assert del_resp.status_code == 200

        all_resp = client.get("/api/v1/memory/all")
        assert len(all_resp.json()) == 0

    def test_delete_nonexistent_404(self, client):
        resp = client.delete("/api/v1/memory/nonexistent")
        assert resp.status_code == 404


# ── Proposals ──────────────────────────────────────────────────────

class TestProposals:

    def _create_proposal(self, client, content="proposed fact"):
        """Helper: create a proposal via the storage layer (no POST endpoint for proposals from frontend)."""
        from app.models.memory import MemoryProposal
        from app.storage.memory import get_memory_storage
        store = get_memory_storage()
        p = MemoryProposal(content=content, tags=["test"], layer="lexicon")
        store.add_proposal(p)
        return p.id

    def test_list_proposals(self, client):
        pid = self._create_proposal(client)
        resp = client.get("/api/v1/memory/proposals")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_approve_proposal(self, client):
        pid = self._create_proposal(client)
        resp = client.post(f"/api/v1/memory/proposals/{pid}/approve")
        assert resp.status_code == 200
        assert resp.json()["content"] == "proposed fact"

        # Now in flat store
        all_resp = client.get("/api/v1/memory/all")
        assert len(all_resp.json()) == 1
        # Proposal gone
        prop_resp = client.get("/api/v1/memory/proposals")
        assert len(prop_resp.json()) == 0

    def test_approve_all(self, client):
        self._create_proposal(client, "fact 1")
        self._create_proposal(client, "fact 2")
        resp = client.post("/api/v1/memory/proposals/approve-all")
        assert resp.json()["approved"] == 2

    def test_dismiss_proposal(self, client):
        pid = self._create_proposal(client)
        resp = client.delete(f"/api/v1/memory/proposals/{pid}")
        assert resp.status_code == 200
        assert len(client.get("/api/v1/memory/proposals").json()) == 0

    def test_dismiss_nonexistent_404(self, client):
        resp = client.delete("/api/v1/memory/proposals/nonexistent")
        assert resp.status_code == 404
