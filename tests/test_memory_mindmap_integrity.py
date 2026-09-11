"""
Seam tests for mind-map referential integrity and search allow-listing.

These assert the connections between subsystems that the live store showed
broken (Sep 2026 audit: 728 of 816 mind-map refs dangling, system prompt
advertising 298 memories in a 98-memory store, embedding cache 97% non-active
ids):

  - MemoryStorage.delete()  ->  mindmap.json refs and scope.domain_node
  - mindmap refs            ->  counts rendered by get_memory_prompt_section
  - MemoryStorage.search()  ->  semantic leg restricted to active memory ids
  - repair_mindmap()        ->  store invariant  refs ⊆ active ids
"""
from unittest.mock import patch

import pytest

from app.models.memory import Memory, MindMapNode
from app.storage.memory import MemoryStorage


@pytest.fixture(autouse=True)
def _disable_embeddings(monkeypatch):
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    import app.services.embedding_service as _es
    monkeypatch.setattr(_es, "_provider", None)
    monkeypatch.setattr(_es, "_cache", None)


@pytest.fixture(autouse=True)
def _enable_memory(monkeypatch):
    monkeypatch.setenv("ZIYA_ENABLE_MEMORY", "true")
    from app.mcp.builtin_tools import invalidate_category_cache
    invalidate_category_cache()
    yield
    invalidate_category_cache()


@pytest.fixture
def storage(tmp_path):
    return MemoryStorage(memory_dir=tmp_path / "memory")


def _placed_memory(storage, node_id="dom", tags=("qos",)):
    node = MindMapNode(id=node_id, handle="Domain", tags=list(tags))
    storage.save_mindmap_node(node)
    mem = Memory(content="Fact about qos", tags=list(tags))
    storage.save(mem)
    assert storage.place_memory_in_mindmap(mem) == node_id
    return mem


# ── delete() → mind-map ───────────────────────────────────────────────


class TestDeleteCleansMindMap:

    def test_delete_removes_ref_from_node(self, storage):
        mem = _placed_memory(storage)
        # Positive: the seam exists before we test its teardown.
        assert mem.id in storage.get_mindmap_node("dom").memory_refs

        assert storage.delete(mem.id) is True

        assert mem.id not in storage.get_mindmap_node("dom").memory_refs

    def test_delete_removes_ref_from_every_node(self, storage):
        """A memory can be multi-placed (166 such refs in the live store)."""
        mem = _placed_memory(storage, node_id="a")
        other = MindMapNode(id="b", handle="Other", memory_refs=[mem.id])
        storage.save_mindmap_node(other)

        storage.delete(mem.id)

        for nid in ("a", "b"):
            assert mem.id not in storage.get_mindmap_node(nid).memory_refs


# ── mind-map refs → prompt counts ─────────────────────────────────────


class TestPromptRendersLiveCounts:

    def test_prompt_count_excludes_dangling_refs(self, storage):
        live = Memory(content="Live fact", tags=["x"])
        storage.save(live)
        storage.save_mindmap_node(MindMapNode(
            id="root", handle="Root Domain",
            memory_refs=[live.id, "m_ghost1", "m_ghost2"],
        ))
        with patch("app.storage.memory.get_memory_storage", return_value=storage):
            from app.memory.prompt import get_memory_prompt_section
            section = get_memory_prompt_section()
        assert "Root Domain" in section
        assert "(1 memories" in section
        assert "(3 memories" not in section

    def test_prompt_count_excludes_archived_refs(self, storage):
        live = Memory(content="Live fact")
        gone = Memory(content="Archived fact", status="archived")
        storage.save(live)
        storage.save(gone)
        storage.save_mindmap_node(MindMapNode(
            id="root", handle="Root Domain", memory_refs=[live.id, gone.id],
        ))
        with patch("app.storage.memory.get_memory_storage", return_value=storage):
            from app.memory.prompt import get_memory_prompt_section
            section = get_memory_prompt_section()
        assert "(1 memories" in section

    def test_prompt_count_tracks_delete(self, storage):
        """End-to-end: delete a placed memory, the advertised count drops."""
        mem = _placed_memory(storage)
        with patch("app.storage.memory.get_memory_storage", return_value=storage):
            from app.memory.prompt import get_memory_prompt_section
            assert "(1 memories" in get_memory_prompt_section()
            storage.delete(mem.id)
            assert "(0 memories" in get_memory_prompt_section()


# ── repair_mindmap() ──────────────────────────────────────────────────


def _norm(h: str) -> str:
    return " ".join(h.lower().split())


class TestDuplicateRootMerge:
    """Two root nodes with the same normalized handle are one domain.

    The live store held ``domain_music_notation`` and
    ``domain_music_notation_1`` — identical handle, identical refs — because
    the organizer treated an existing node with the same derived id as an
    id collision to suffix around rather than as the same domain.
    """

    def _two_roots(self, storage):
        for i in range(3):
            storage.save(Memory(id=f"m_{i}", content=f"fact {i}", tags=["music"]))
        storage.save_mindmap_node(MindMapNode(
            id="domain_music_notation", handle="music notation",
            tags=["music-notation"], memory_refs=["m_0", "m_1"],
            access_count=4))
        storage.save_mindmap_node(MindMapNode(
            id="domain_music_notation_1", handle="Music  Notation",
            tags=["pitch-integrity"], memory_refs=["m_1", "m_2"],
            children=["dmn1_child"]))
        storage.save_mindmap_node(MindMapNode(
            id="dmn1_child", handle="child", parent="domain_music_notation_1",
            memory_refs=["m_2"]))

    def test_repair_merges_same_handle_roots(self, storage):
        self._two_roots(storage)
        result = storage.repair_mindmap()
        roots = storage.get_root_nodes()
        handles = [_norm(r.handle) for r in roots]
        assert handles.count("music notation") == 1
        assert result["roots_merged"] == 1
        survivor = next(r for r in roots if _norm(r.handle) == "music notation")
        # Unsuffixed id survives; union of refs, tags, children; stats kept.
        assert survivor.id == "domain_music_notation"
        assert set(survivor.memory_refs) == {"m_0", "m_1", "m_2"}
        assert {"music-notation", "pitch-integrity"} <= set(survivor.tags)
        assert "dmn1_child" in survivor.children
        assert storage.get_mindmap_node("dmn1_child").parent == "domain_music_notation"
        assert storage.get_mindmap_node("domain_music_notation_1") is None
        assert survivor.access_count == 4

    def test_merge_updates_memory_scope(self, storage):
        self._two_roots(storage)
        m2 = storage.get("m_2")
        m2.scope.domain_node = "domain_music_notation_1"
        storage.save(m2)
        storage.repair_mindmap()
        assert storage.get("m_2").scope.domain_node == "domain_music_notation"

    def test_distinct_handles_not_merged(self, storage):
        storage.save(Memory(id="m_a", content="a", tags=["x"]))
        storage.save_mindmap_node(MindMapNode(id="r1", handle="music notation",
                                              memory_refs=["m_a"]))
        storage.save_mindmap_node(MindMapNode(id="r2", handle="music theory",
                                              memory_refs=["m_a"]))
        result = storage.repair_mindmap()
        assert result["roots_merged"] == 0
        assert len(storage.get_root_nodes()) == 2

    @pytest.mark.asyncio
    async def test_bootstrap_reuses_existing_same_handle_root(self, storage):
        """When clustering returns a handle whose derived id already exists
        as a root, the organizer must merge into it, not create ``_1``."""
        from app.memory.organizer import bootstrap_mindmap
        storage.save(Memory(id="m_old", content="old fact", tags=["zeta"]))
        storage.save(Memory(id="m_new", content="new fact", tags=["omega"]))
        # Existing root whose tags/words will NOT satisfy the fuzzy matcher
        # for the incoming domain (different tags; 2 handle words * 2 = 4
        # would match, so use a one-word handle to keep score < 4).
        storage.save_mindmap_node(MindMapNode(
            id="domain_widgets", handle="widgets", tags=["zeta"],
            memory_refs=["m_old"]))

        async def fake_cluster(batch, existing=None, batch_target=None):
            return [{"handle": "Widgets", "tags": ["omega"],
                     "memory_ids": ["m_new"]}]

        with patch("app.memory.organizer.cluster_memories", side_effect=fake_cluster):
            await bootstrap_mindmap(storage)

        roots = storage.get_root_nodes()
        assert [r.id for r in roots] == ["domain_widgets"]
        assert set(roots[0].memory_refs) == {"m_old", "m_new"}
        assert storage.get_mindmap_node("domain_widgets_1") is None


class TestRepairMindMap:

    def test_repair_drops_dangling_refs(self, storage):
        live = Memory(content="Live fact")
        storage.save(live)
        storage.save_mindmap_node(MindMapNode(
            id="root", handle="Root", memory_refs=["m_ghost", live.id],
        ))
        result = storage.repair_mindmap()
        assert result["dangling_refs_removed"] == 1
        assert storage.get_mindmap_node("root").memory_refs == [live.id]

    def test_repair_prunes_ghost_only_subtree(self, storage):
        """A root whose every descendant references only deleted memories
        is removed; a sibling with a live ref survives."""
        live = Memory(content="Live fact")
        storage.save(live)
        storage.save_mindmap_node(MindMapNode(id="ghost", handle="Ghost"))
        storage.save_mindmap_node(MindMapNode(
            id="ghost-child", handle="Ghost child", parent="ghost",
            memory_refs=["m_gone"],
        ))
        storage.save_mindmap_node(MindMapNode(
            id="alive", handle="Alive", memory_refs=[live.id],
        ))
        result = storage.repair_mindmap()
        assert result["empty_nodes_removed"] == 2
        ids = {n.id for n in storage.list_mindmap_nodes()}
        assert ids == {"alive"}

    def test_repair_places_unplaced_active(self, storage):
        storage.save_mindmap_node(MindMapNode(id="dom", handle="D", tags=["qos"]))
        mem = Memory(content="Unplaced", tags=["qos"])
        storage.save(mem)
        result = storage.repair_mindmap()
        assert result["placed"] == 1
        assert mem.id in storage.get_mindmap_node("dom").memory_refs

    def test_repair_is_idempotent(self, storage):
        _placed_memory(storage)
        storage.save_mindmap_node(MindMapNode(id="g", handle="G", memory_refs=["m_x"]))
        storage.repair_mindmap()
        second = storage.repair_mindmap()
        assert second == {"dangling_refs_removed": 0, "empty_nodes_removed": 0,
                          "placed": 0, "roots_merged": 0}

    def test_repair_restores_invariant(self, storage):
        live = Memory(content="Live fact")
        storage.save(live)
        storage.save_mindmap_node(MindMapNode(
            id="r", handle="R", memory_refs=["a", "b", live.id],
        ))
        storage.repair_mindmap()
        active = {m.id for m in storage.list_memories(status="active")}
        refs = {r for n in storage.list_mindmap_nodes() for r in n.memory_refs}
        assert refs <= active


# ── search() → semantic leg allow-list ────────────────────────────────


class TestSearchSemanticAllowList:

    def test_semantic_leg_restricted_to_active_ids(self, storage, monkeypatch):
        """The cache holds far more non-active ids (proposals, deleted
        memories) than ``limit*2``.  Without an allow-list the semantic
        leg returns only ghosts and the fused result comes back short."""
        actives = [Memory(content=f"Fact number {i}") for i in range(6)]
        for m in actives:
            storage.save(m)
        active_ids = [m.id for m in actives]
        ghosts = [f"prop_{i:04d}" for i in range(40)]
        cache_ids = ghosts + active_ids  # ghosts rank first

        class _FakeCache:
            def missing_ids(self, ids):
                return [i for i in ids if i not in cache_ids]

        seen = {}

        def _fake_semantic_search(query, top_k=10, exclude_ids=None,
                                  include_ids=None):
            seen["include_ids"] = include_ids
            ranked = cache_ids
            if include_ids is not None:
                ranked = [i for i in ranked if i in include_ids]
            if exclude_ids:
                ranked = [i for i in ranked if i not in exclude_ids]
            return [(i, 1.0) for i in ranked[:top_k]]

        import app.services.embedding_service as es
        monkeypatch.setattr(es, "get_embedding_cache", lambda: _FakeCache())
        monkeypatch.setattr(es, "semantic_search", _fake_semantic_search)

        # Query shares no tokens with any content so only the semantic
        # leg can contribute.
        results = storage.search("zzqx", limit=3)

        assert seen["include_ids"] is not None
        assert set(seen["include_ids"]) == set(active_ids)
        assert len(results) == 3
        assert all(r.id in active_ids for r in results)


class TestEmbeddingCacheIncludeIds:

    def test_include_ids_masks_everything_else(self, tmp_path):
        import numpy as np
        from app.services.embedding_service import EmbeddingCache
        cache = EmbeddingCache(tmp_path, dim=3)
        q = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        cache.put("ghost", q.copy())            # perfect match, excluded
        cache.put("live", np.array([0.9, 0.1, 0.0], dtype=np.float32))
        hits = cache.search(q, top_k=5, include_ids={"live"})
        assert [h[0] for h in hits] == ["live"]
