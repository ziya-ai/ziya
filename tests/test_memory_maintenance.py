"""
Tests for Phase 2 memory maintenance — auto-organization and upkeep.

Covers:
  - Auto-placement on save (via run_post_save_maintenance)
  - Cell division when node exceeds threshold
  - Cell division skipped when cluster = entire node
  - Cell division idempotent (no duplicate children)
  - Cross-link discovery between non-ancestor nodes
  - Cross-links not created within same branch
  - Staleness detection
  - Review summary (stale + large nodes + orphans)
  - End-to-end: save triggers placement + division + cross-links
"""
import time
from unittest.mock import patch

import pytest

from app.models.memory import Memory, MindMapNode
from app.storage.memory import MemoryStorage
from app.utils.memory_maintenance import (
    run_post_save_maintenance,
    maybe_divide_node,
    discover_cross_links,
    find_stale_memories,
    get_review_summary,
    CELL_DIVISION_THRESHOLD,
    CELL_DIVISION_MIN_CLUSTER,
)


@pytest.fixture
def storage(tmp_path):
    return MemoryStorage(memory_dir=tmp_path / "memory")


@pytest.fixture
def patch_storage(storage):
    with patch("app.storage.memory.get_memory_storage", return_value=storage):
        with patch("app.utils.memory_maintenance.get_memory_storage", return_value=storage):
            yield storage


def _populate_node_with_memories(storage, node_id: str, count: int, common_tag: str, extra_tag: str = None):
    """Helper: create a node and fill it with tagged memories."""
    node = storage.get_mindmap_node(node_id)
    if not node:
        node = MindMapNode(id=node_id, handle=f"Node {node_id}", tags=["broad"])
        storage.save_mindmap_node(node)

    for i in range(count):
        tags = [common_tag]
        if extra_tag and i % 2 == 0:
            tags.append(extra_tag)
        mem = Memory(
            id=f"m_{node_id}_{i}",
            content=f"Memory {i} about {common_tag}",
            tags=tags,
            layer="domain_context",
        )
        storage.save(mem)
        if mem.id not in node.memory_refs:
            node.memory_refs.append(mem.id)

    storage.save_mindmap_node(node)
    return node


# ── Cell Division ──────────────────────────────────────────────────

class TestCellDivision:

    def test_no_split_under_threshold(self, storage):
        _populate_node_with_memories(storage, "small", 5, "topic-a")
        result = maybe_divide_node(storage, "small")
        assert result == []

    def test_splits_when_cluster_found(self, storage):
        """Node with 15 memories, 8 sharing 'specific-tag' → split."""
        node = MindMapNode(id="big", handle="Big node", tags=["broad"])
        storage.save_mindmap_node(node)

        refs = []
        for i in range(15):
            tags = ["broad"]
            if i < 8:
                tags.append("specific-tag")
            else:
                tags.append("other-tag")
            mem = Memory(id=f"m_big_{i}", content=f"Mem {i}", tags=tags)
            storage.save(mem)
            refs.append(mem.id)

        node.memory_refs = refs
        storage.save_mindmap_node(node)

        result = maybe_divide_node(storage, "big")
        assert len(result) == 1
        child_id = result[0]
        assert "specific-tag" in child_id

        # Child should have the clustered memories
        child = storage.get_mindmap_node(child_id)
        assert len(child.memory_refs) == 8

        # Parent should have the remainder
        parent = storage.get_mindmap_node("big")
        assert len(parent.memory_refs) == 7
        assert child_id in parent.children

    def test_no_split_when_cluster_is_entire_node(self, storage):
        """If ALL memories share the same non-parent tag, don't split (nothing would remain)."""
        node = MindMapNode(id="uniform", handle="Uniform node", tags=["broad"])
        storage.save_mindmap_node(node)

        refs = []
        for i in range(15):
            mem = Memory(id=f"m_uni_{i}", content=f"Mem {i}", tags=["broad", "same-tag"])
            storage.save(mem)
            refs.append(mem.id)
        node.memory_refs = refs
        storage.save_mindmap_node(node)

        result = maybe_divide_node(storage, "uniform")
        assert result == []  # "same-tag" covers all 15; nothing would remain

    def test_idempotent(self, storage):
        """Running division twice should not create duplicate children."""
        node = MindMapNode(id="idem", handle="Idempotent", tags=["broad"])
        storage.save_mindmap_node(node)
        refs = []
        for i in range(15):
            tags = ["broad", "cluster-tag"] if i < 8 else ["broad", "other"]
            mem = Memory(id=f"m_idem_{i}", content=f"Mem {i}", tags=tags)
            storage.save(mem)
            refs.append(mem.id)
        node.memory_refs = refs
        storage.save_mindmap_node(node)

        first = maybe_divide_node(storage, "idem")
        assert len(first) == 1
        second = maybe_divide_node(storage, "idem")
        assert second == []  # Already split


# ── Cross-Links ────────────────────────────────────────────────────

class TestCrossLinks:

    def test_discovers_cross_links(self, storage):
        """Two root-level nodes sharing 2+ tags should get cross-linked."""
        a = MindMapNode(id="domain-a", handle="Domain A", tags=["networking", "protocols", "tcp"])
        b = MindMapNode(id="domain-b", handle="Domain B", tags=["networking", "protocols", "http"])
        storage.save_mindmap_node(a)
        storage.save_mindmap_node(b)

        links = discover_cross_links(storage, "domain-a")
        assert len(links) == 1
        assert links[0] == ("domain-a", "domain-b")

        # Bidirectional
        a_node = storage.get_mindmap_node("domain-a")
        b_node = storage.get_mindmap_node("domain-b")
        assert "domain-b" in a_node.cross_links
        assert "domain-a" in b_node.cross_links

    def test_no_cross_link_within_branch(self, storage):
        """Parent and child should not get cross-linked even if tags overlap."""
        parent = MindMapNode(id="parent", handle="Parent", tags=["networking", "protocols"], children=["child"])
        child = MindMapNode(id="child", handle="Child", parent="parent", tags=["networking", "protocols", "tcp"])
        storage.save_mindmap_node(parent)
        storage.save_mindmap_node(child)

        links = discover_cross_links(storage, "child")
        assert links == []

    def test_no_cross_link_below_threshold(self, storage):
        """Only 1 shared tag (below CROSS_LINK_MIN_OVERLAP=2) → no link."""
        a = MindMapNode(id="x", handle="X", tags=["networking", "unique-a"])
        b = MindMapNode(id="y", handle="Y", tags=["networking", "unique-b"])
        storage.save_mindmap_node(a)
        storage.save_mindmap_node(b)

        links = discover_cross_links(storage, "x")
        assert links == []


# ── Staleness ──────────────────────────────────────────────────────

class TestStaleness:

    def test_finds_stale_memories(self, storage):
        old = Memory(id="m_old", content="Old fact", last_accessed="2024-01-01")
        recent = Memory(id="m_new", content="New fact", last_accessed=time.strftime("%Y-%m-%d"))
        storage.save(old)
        storage.save(recent)

        stale = find_stale_memories(storage, days=90)
        ids = [s["id"] for s in stale]
        assert "m_old" in ids
        assert "m_new" not in ids


# ── Review Summary ─────────────────────────────────────────────────

class TestReviewSummary:

    def test_review_finds_orphans(self, storage):
        """Memories not referenced by any node are orphans."""
        storage.save(Memory(id="m_orphan", content="Orphan"))
        node = MindMapNode(id="n1", handle="Node", memory_refs=["m_attached"])
        storage.save_mindmap_node(node)
        storage.save(Memory(id="m_attached", content="Attached"))

        review = get_review_summary(storage)
        orphan_ids = [o["id"] for o in review["orphan_memories"]]
        assert "m_orphan" in orphan_ids
        assert "m_attached" not in orphan_ids

    def test_review_empty_store(self, storage):
        review = get_review_summary(storage)
        assert review["total_memories"] == 0
        assert review["stale_count"] == 0
        assert review["orphan_count"] == 0


# ── End-to-End ─────────────────────────────────────────────────────

class TestEndToEnd:

    def test_save_triggers_placement(self, patch_storage):
        storage = patch_storage
        node = MindMapNode(id="domain", handle="Domain", tags=["satellite", "leo"])
        storage.save_mindmap_node(node)

        mem = Memory(id="m_e2e", content="LEO fact", tags=["satellite", "leo"])
        storage.save(mem)

        results = run_post_save_maintenance("m_e2e")
        assert results["placed"] == "domain"
        # Memory should be in the node's refs
        updated_node = storage.get_mindmap_node("domain")
        assert "m_e2e" in updated_node.memory_refs
