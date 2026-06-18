"""
Tests for embedding-centroid mind-map cross-linking (Option C).

Covers app.memory.maintenance.discover_cross_links_by_embedding and the
shared _same_branch_ids / _node_centroid helpers it relies on.

The tag-overlap cross-linker (discover_cross_links) rarely fired in
practice because bootstrap assigns mostly-distinct tags per domain, so
cross_links stayed at 0.  This pass links nodes whose member-memory
embedding centroids are cosine-similar, regardless of literal tag overlap.

These tests inject a real EmbeddingCache populated with hand-chosen unit
vectors and a non-Noop provider, so no live Bedrock call is made.
"""
import numpy as np
import pytest
from unittest.mock import patch, MagicMock

from app.models.memory import Memory, MindMapNode
from app.storage.memory import MemoryStorage
from app.services.embedding_service import EmbeddingCache, NoopProvider
import app.services.embedding_service as es
from app.memory.maintenance import (
    discover_cross_links_by_embedding,
    _same_branch_ids,
    _node_centroid,
    NODE_CROSS_LINK_MIN_SIMILARITY,
)

DIM = 4


@pytest.fixture
def storage(tmp_path):
    return MemoryStorage(memory_dir=tmp_path / "memory")


@pytest.fixture
def cache(tmp_path):
    return EmbeddingCache(tmp_path / "emb", dim=DIM)


def _unit(vec):
    v = np.array(vec, dtype=np.float32)
    return v / np.linalg.norm(v)


def _add_node(storage, node_id, mem_vecs, cache, tags=None, parent=None, children=None):
    """Create a node with N member memories, each assigned a vector in cache."""
    refs = []
    for i, vec in enumerate(mem_vecs):
        mid = f"m_{node_id}_{i}"
        storage.save(Memory(id=mid, content=f"mem {mid}", tags=tags or [], layer="domain_context"))
        cache.put(mid, _unit(vec))
        refs.append(mid)
    node = MindMapNode(id=node_id, handle=f"Node {node_id}", tags=tags or [],
                       memory_refs=refs, parent=parent, children=children or [])
    storage.save_mindmap_node(node)
    return node


def _patch_embeddings(cache):
    """Patch the source module so the function under test sees our cache and a
    non-Noop provider.  The function imports them from app.services.embedding_service."""
    prov = MagicMock()  # anything that is not a NoopProvider instance
    return patch.multiple(
        "app.services.embedding_service",
        get_embedding_provider=MagicMock(return_value=prov),
        get_embedding_cache=MagicMock(return_value=cache),
    )


# ── _same_branch_ids ──────────────────────────────────────────────────

class TestSameBranchIds:
    def test_self_only_when_isolated(self, storage):
        storage.save_mindmap_node(MindMapNode(id="solo", handle="Solo"))
        assert _same_branch_ids(storage, "solo") == {"solo"}

    def test_includes_ancestors_and_descendants(self, storage):
        storage.save_mindmap_node(MindMapNode(id="root", handle="R", children=["mid"]))
        storage.save_mindmap_node(MindMapNode(id="mid", handle="M", parent="root", children=["leaf"]))
        storage.save_mindmap_node(MindMapNode(id="leaf", handle="L", parent="mid"))
        # From mid: root (ancestor) + leaf (descendant) + self
        assert _same_branch_ids(storage, "mid") == {"root", "mid", "leaf"}

    def test_cycle_safe(self, storage):
        # Pathological parent cycle must not hang
        storage.save_mindmap_node(MindMapNode(id="a", handle="A", parent="b"))
        storage.save_mindmap_node(MindMapNode(id="b", handle="B", parent="a"))
        result = _same_branch_ids(storage, "a")
        assert "a" in result


# ── _node_centroid ────────────────────────────────────────────────────

class TestNodeCentroid:
    def test_none_when_no_refs(self, storage, cache):
        node = MindMapNode(id="empty", handle="Empty", memory_refs=[])
        assert _node_centroid(cache, node) is None

    def test_none_when_no_embeddings(self, storage, cache):
        node = MindMapNode(id="n", handle="N", memory_refs=["missing1", "missing2"])
        assert _node_centroid(cache, node) is None

    def test_centroid_is_unit_length(self, storage, cache):
        cache.put("m1", _unit([1, 0, 0, 0]))
        cache.put("m2", _unit([0, 1, 0, 0]))
        node = MindMapNode(id="n", handle="N", memory_refs=["m1", "m2"])
        c = _node_centroid(cache, node)
        assert c is not None
        assert abs(float(np.linalg.norm(c)) - 1.0) < 1e-5

    def test_skips_missing_member_embeddings(self, storage, cache):
        cache.put("present", _unit([1, 0, 0, 0]))
        node = MindMapNode(id="n", handle="N", memory_refs=["present", "absent"])
        c = _node_centroid(cache, node)
        # Only the present vector contributes → centroid ≈ that vector
        assert c is not None
        assert float(np.dot(c, _unit([1, 0, 0, 0]))) > 0.99


# ── discover_cross_links_by_embedding ─────────────────────────────────

class TestDiscoverCrossLinksByEmbedding:
    def test_links_similar_distinct_branches(self, storage, cache):
        # Two unrelated-by-tag nodes whose centroids are nearly identical.
        _add_node(storage, "alpha", [[1, 0, 0, 0], [0.98, 0.05, 0, 0]], cache, tags=["alpha"])
        _add_node(storage, "beta", [[0.97, 0.1, 0, 0], [1, 0, 0, 0]], cache, tags=["beta"])
        with _patch_embeddings(cache):
            added = discover_cross_links_by_embedding(storage, "alpha")
        assert ("alpha", "beta") in added
        # Bidirectional link persisted
        assert "beta" in storage.get_mindmap_node("alpha").cross_links
        assert "alpha" in storage.get_mindmap_node("beta").cross_links

    def test_no_link_below_threshold(self, storage, cache):
        # Orthogonal centroids → cosine 0, well below default 0.62
        _add_node(storage, "x", [[1, 0, 0, 0]], cache)
        _add_node(storage, "y", [[0, 1, 0, 0]], cache)
        with _patch_embeddings(cache):
            added = discover_cross_links_by_embedding(storage, "x")
        assert added == []
        assert storage.get_mindmap_node("x").cross_links == []

    def test_no_link_within_same_branch(self, storage, cache):
        # Parent/child with identical centroids must NOT cross-link (same branch)
        _add_node(storage, "parent", [[1, 0, 0, 0]], cache, children=["child"])
        _add_node(storage, "child", [[1, 0, 0, 0]], cache, parent="parent")
        with _patch_embeddings(cache):
            added = discover_cross_links_by_embedding(storage, "child")
        assert added == []

    def test_noop_provider_disables(self, storage, cache):
        _add_node(storage, "a", [[1, 0, 0, 0]], cache)
        _add_node(storage, "b", [[1, 0, 0, 0]], cache)
        with patch("app.services.embedding_service.get_embedding_provider",
                   return_value=NoopProvider()):
            added = discover_cross_links_by_embedding(storage, "a")
        assert added == []

    def test_skips_already_linked(self, storage, cache):
        _add_node(storage, "a", [[1, 0, 0, 0]], cache)
        b = _add_node(storage, "b", [[1, 0, 0, 0]], cache)
        a = storage.get_mindmap_node("a")
        a.cross_links = ["b"]
        storage.save_mindmap_node(a)
        with _patch_embeddings(cache):
            added = discover_cross_links_by_embedding(storage, "a")
        assert added == []  # already linked, not re-added

    def test_node_with_no_centroid_is_noop(self, storage, cache):
        # Node whose members have no embeddings → can't compute centroid
        storage.save_mindmap_node(MindMapNode(id="bare", handle="Bare", memory_refs=["ghost"]))
        _add_node(storage, "real", [[1, 0, 0, 0]], cache)
        with _patch_embeddings(cache):
            added = discover_cross_links_by_embedding(storage, "bare")
        assert added == []

    def test_threshold_env_override(self, storage, cache, monkeypatch):
        # Centroids ~0.7 similar: linked at default 0.62, NOT at override 0.9
        _add_node(storage, "p", [[1, 0, 0, 0]], cache)
        _add_node(storage, "q", [[0.7, 0.714, 0, 0]], cache)  # cos≈0.7 with p
        monkeypatch.setenv("ZIYA_NODE_CROSS_LINK_SIMILARITY", "0.9")
        with _patch_embeddings(cache):
            added = discover_cross_links_by_embedding(storage, "p")
        assert added == []  # 0.7 < 0.9 override

    def test_shared_centroid_cache_reused(self, storage, cache):
        # Passing a centroid_cache should populate it and avoid recompute.
        _add_node(storage, "a", [[1, 0, 0, 0]], cache)
        _add_node(storage, "b", [[1, 0, 0, 0]], cache)
        shared: dict = {}
        with _patch_embeddings(cache):
            discover_cross_links_by_embedding(storage, "a", shared)
        assert "a" in shared and "b" in shared
