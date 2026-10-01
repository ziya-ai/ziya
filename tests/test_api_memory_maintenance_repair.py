"""
The Health tab's "Run Maintenance" button (POST /api/v1/memory/maintenance)
must reach ``MemoryStorage.repair_mindmap``.

Before this, the endpoint only ran cell division and cross-linking; repair
(dangling refs, duplicate roots, stray placement, and the provenance/score
rebalance that drains a gravity-well root) ran only as a side effect of
saving a memory or of an LLM organize run.  There was no user-facing way to
apply the rebalance to an existing store.

The anchor test stages a misplaced memory exactly as the placement seam
tests do and asserts on the outermost surface: the HTTP response reports
the move and the mind-map on disk reflects it.  Confirmed to FAIL against
the endpoint that does not call repair (no ``repaired`` key; memory stays
in the fat root).
"""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.memory import router
from app.models.memory import Memory, MemoryScope, MindMapNode
from app.storage.memory import MemoryStorage


@pytest.fixture(autouse=True)
def _disable_embeddings(monkeypatch):
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    import app.services.embedding_service as _es
    monkeypatch.setattr(_es, "_provider", None)
    monkeypatch.setattr(_es, "_cache", None)


@pytest.fixture
def storage(tmp_path):
    return MemoryStorage(memory_dir=tmp_path / "memory")


@pytest.fixture
def client(storage, tmp_path):
    from app.storage.proposals import ProposalsStore
    app = FastAPI()
    app.include_router(router)
    with patch("app.storage.memory.get_memory_storage", return_value=storage), \
         patch("app.storage.proposals.get_proposals_store",
               return_value=ProposalsStore(memory_dir=tmp_path / "memory")):
        yield TestClient(app)


X, Y = "/w/project-x", "/w/project-y"


def _mem(storage, content, tags, project):
    m = Memory(content=content, tags=list(tags),
               scope=MemoryScope(project_paths=[project]))
    storage.save(m)
    return m


def test_maintenance_endpoint_runs_repair_and_rebalances(client, storage):
    # Fat root of X (500 tags) holding a Y-memory it won by mass; a small
    # Y node that shares the memory's one tag.
    fat_refs = [_mem(storage, f"x{i}", [f"xt{i}"], X).id for i in range(5)]
    stuck = _mem(storage, "y stuck", ["shared"], Y)
    stuck.scope.domain_node = "fat"
    storage.save(stuck)
    storage.save_mindmap_node(MindMapNode(
        id="fat", handle="Fat root",
        tags=["shared"] + [f"fat{i}" for i in range(499)],
        memory_refs=fat_refs + [stuck.id]))
    small_refs = [_mem(storage, f"y{i}", [f"yt{i}"], Y).id for i in range(2)]
    storage.save_mindmap_node(MindMapNode(
        id="small", handle="Small node", tags=["shared", "a", "b", "c"],
        memory_refs=small_refs))

    resp = client.post("/api/v1/memory/maintenance")
    assert resp.status_code == 200
    body = resp.json()

    # Seam: the endpoint reports the repair it ran ...
    assert "repaired" in body, body
    assert body["repaired"]["rebalanced"] == 1
    # ... and the store shows the move.
    assert stuck.id in storage.get_mindmap_node("small").memory_refs
    assert stuck.id not in storage.get_mindmap_node("fat").memory_refs
    assert storage.get(stuck.id).scope.domain_node == "small"
    # Existing keys are still produced (positive control that the rest of
    # the pass ran, not just repair).
    assert "divided" in body and "cross_linked" in body
