"""
Tests for Phase 1 mind-map: tree structure, progressive loading, auto-placement.

Covers:
  - Node CRUD (create, get, list, delete with reparenting)
  - Root/children navigation
  - get_node_with_context (handle + children handles)
  - expand_node (recursive memory collection)
  - place_memory_in_mindmap (tag-based auto-placement)
  - Progressive prompt loading (mind-map present → handles only)
  - Circular reference protection in expand_node
"""
from unittest.mock import patch

import pytest

from app.models.memory import Memory, MindMapNode
from app.storage.memory import MemoryStorage


@pytest.fixture
def storage(tmp_path):
    return MemoryStorage(memory_dir=tmp_path / "memory")


@pytest.fixture
def populated_tree(storage):
    """Build a small mind-map tree with memories attached."""
    # Root domain
    root = MindMapNode(
        id="leo-network",
        handle="LEO broadband constellation — architecture, protocols, ground segment",
        tags=["leo", "satellite", "network"],
        children=["leo-arch", "leo-fc"],
    )
    arch = MindMapNode(
        id="leo-arch",
        handle="System architecture — Gen 1 link chain, orbital shells, OBP",
        parent="leo-network",
        tags=["architecture", "obp", "link-chain"],
        memory_refs=["m_001", "m_002"],
    )
    fc = MindMapNode(
        id="leo-fc",
        handle="Flow control — credit-based, NACK rejected",
        parent="leo-network",
        tags=["flow-control", "return-link"],
        memory_refs=["m_003"],
        children=["leo-fc-rejected"],
    )
    rejected = MindMapNode(
        id="leo-fc-rejected",
        handle="Rejected approaches: static allocation, NACK-based",
        parent="leo-fc",
        tags=["flow-control", "rejected"],
        memory_refs=["m_004"],
    )
    for node in [root, arch, fc, rejected]:
        storage.save_mindmap_node(node)

    # Store the referenced memories
    storage.save(Memory(id="m_001", content="OBP has 512MB RAM budget", layer="architecture", tags=["obp"]))
    storage.save(Memory(id="m_002", content="Three orbital shells", layer="architecture", tags=["shells"]))
    storage.save(Memory(id="m_003", content="Credit-based flow control chosen", layer="decision", tags=["flow-control"]))
    storage.save(Memory(id="m_004", content="Static allocation wastes 85% capacity", layer="negative_constraint", tags=["flow-control", "rejected"]))

    return storage


# ── Node CRUD ──────────────────────────────────────────────────────

class TestNodeCRUD:

    def test_save_and_get(self, storage):
        node = MindMapNode(id="test-node", handle="Test handle", tags=["test"])
        storage.save_mindmap_node(node)
        retrieved = storage.get_mindmap_node("test-node")
        assert retrieved is not None
        assert retrieved.handle == "Test handle"

    def test_get_nonexistent(self, storage):
        assert storage.get_mindmap_node("nonexistent") is None

    def test_list_all(self, populated_tree):
        nodes = populated_tree.list_mindmap_nodes()
        assert len(nodes) == 4

    def test_delete_reparents_children(self, populated_tree):
        """Deleting leo-fc should reparent leo-fc-rejected to leo-network."""
        assert populated_tree.delete_mindmap_node("leo-fc")
        # Child should now point to grandparent
        child = populated_tree.get_mindmap_node("leo-fc-rejected")
        assert child.parent == "leo-network"
        # Parent should list the reparented child
        parent = populated_tree.get_mindmap_node("leo-network")
        assert "leo-fc-rejected" in parent.children
        assert "leo-fc" not in parent.children

    def test_delete_nonexistent(self, storage):
        assert not storage.delete_mindmap_node("nonexistent")


# ── Navigation ─────────────────────────────────────────────────────

class TestNavigation:

    def test_get_root_nodes(self, populated_tree):
        roots = populated_tree.get_root_nodes()
        assert len(roots) == 1
        assert roots[0].id == "leo-network"

    def test_get_children(self, populated_tree):
        children = populated_tree.get_children("leo-network")
        ids = {c.id for c in children}
        assert ids == {"leo-arch", "leo-fc"}

    def test_get_children_leaf(self, populated_tree):
        children = populated_tree.get_children("leo-arch")
        assert children == []

    def test_node_with_context(self, populated_tree):
        ctx = populated_tree.get_node_with_context("leo-network")
        assert ctx["node"]["id"] == "leo-network"
        assert len(ctx["children"]) == 2
        child_ids = {c["id"] for c in ctx["children"]}
        assert child_ids == {"leo-arch", "leo-fc"}

    def test_node_with_context_updates_access(self, populated_tree):
        before = populated_tree.get_mindmap_node("leo-network").access_count
        populated_tree.get_node_with_context("leo-network")
        after = populated_tree.get_mindmap_node("leo-network").access_count
        assert after == before + 1


# ── Expand ─────────────────────────────────────────────────────────

class TestExpand:

    def test_expand_leaf(self, populated_tree):
        memories = populated_tree.expand_node("leo-arch")
        assert len(memories) == 2
        ids = {m.id for m in memories}
        assert ids == {"m_001", "m_002"}

    def test_expand_recursive(self, populated_tree):
        """Expanding leo-fc should include its own memories AND leo-fc-rejected's."""
        memories = populated_tree.expand_node("leo-fc")
        assert len(memories) == 2
        ids = {m.id for m in memories}
        assert ids == {"m_003", "m_004"}

    def test_expand_root_gets_everything(self, populated_tree):
        memories = populated_tree.expand_node("leo-network")
        assert len(memories) == 4

    def test_expand_nonexistent(self, populated_tree):
        memories = populated_tree.expand_node("nonexistent")
        assert memories == []

    def test_expand_circular_reference_safe(self, storage):
        """Circular parent references should not cause infinite recursion."""
        a = MindMapNode(id="a", handle="A", children=["b"])
        b = MindMapNode(id="b", handle="B", parent="a", children=["a"])
        storage.save_mindmap_node(a)
        storage.save_mindmap_node(b)
        # Should terminate without stack overflow
        memories = storage.expand_node("a")
        assert isinstance(memories, list)


# ── Auto-Placement ─────────────────────────────────────────────────

class TestAutoPlacement:

    def test_place_by_tag_match(self, populated_tree):
        mem = Memory(id="m_new", content="New OBP constraint", tags=["obp", "constraints"])
        populated_tree.save(mem)
        placed_id = populated_tree.place_memory_in_mindmap(mem)
        assert placed_id == "leo-arch"  # Best tag overlap: "obp"
        # Memory should be in the node's refs
        node = populated_tree.get_mindmap_node("leo-arch")
        assert "m_new" in node.memory_refs

    def test_place_no_tags_returns_none(self, populated_tree):
        mem = Memory(id="m_orphan", content="No tags")
        assert populated_tree.place_memory_in_mindmap(mem) is None

    def test_place_no_match_returns_none(self, populated_tree):
        mem = Memory(id="m_unrelated", content="Genealogy fact", tags=["dna", "genealogy"])
        assert populated_tree.place_memory_in_mindmap(mem) is None


# ── Progressive Prompt ─────────────────────────────────────────────

class TestProgressivePrompt:

    def test_mindmap_triggers_handles_only(self, populated_tree):
        with patch("app.storage.memory.get_memory_storage", return_value=populated_tree):
            from app.utils.memory_prompt import get_memory_prompt_section
            section = get_memory_prompt_section()
            # Should show Level 0 handles, not individual memory content
            assert "LEO broadband constellation" in section
            assert "memory_context" in section
            # Should NOT dump individual memories
            assert "OBP has 512MB RAM" not in section
            assert "4 total memories" in section

    def test_no_mindmap_falls_back_to_dump(self, tmp_path):
        """Without a mind-map, all memories are dumped directly (Phase 0 behavior)."""
        storage = MemoryStorage(memory_dir=tmp_path / "mem")
        storage.save(Memory(content="A fact", layer="domain_context"))
        with patch("app.storage.memory.get_memory_storage", return_value=storage):
            from app.utils.memory_prompt import get_memory_prompt_section
            section = get_memory_prompt_section()
            assert "A fact" in section
            assert "memory_context" not in section  # No handles-only mode
