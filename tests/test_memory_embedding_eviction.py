"""
Proposal vectors must leave the embedding cache when the proposal leaves
the live working set, and the lifecycle pass must sweep orphaned vectors.

Background: ``ProposalsStore.add`` caches an embedding under the proposal
id so retrieval-feedback can score open proposals.  Nothing ever removed
those vectors on promote/archive, and ``MemoryStorage.delete`` only removes
the memory's own vector.  The live cache reached 2,851 vectors for 98
active memories — 97% of the vector space belonged to ids that no search
could ever return.  Search now allow-lists active ids (so the dilution is
hidden), but the vectors still cost load time, disk, and the O(N)
argpartition on every query.

Seams asserted:
  - ProposalsStore.mark_promoted / mark_archived → cache.remove(pid)
  - EmbeddingCache.retain_only(ids) rebuilds the cache to exactly ``ids``
  - run_lifecycle_pass → retain_only(active memory ids ∪ open proposal ids)
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.models.memory import Memory, MemoryProposal
from app.services.embedding_service import EmbeddingCache, DEFAULT_DIM
from app.storage.memory import MemoryStorage
from app.storage.proposals import ProposalsStore


def _unit(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(DEFAULT_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def cache(tmp_path):
    return EmbeddingCache(tmp_path / "memory")


@pytest.fixture
def proposals(tmp_path):
    return ProposalsStore(memory_dir=tmp_path / "memory")


# ── Terminal transitions evict ──────────────────────────────────────────

class TestTerminalTransitionsEvict:

    def _seeded(self, proposals, cache, content="OBP has 512MB RAM"):
        with patch("app.services.embedding_service.embed_and_cache"):
            pid = proposals.add(MemoryProposal(content=content))
        cache.put(pid, _unit(1))
        assert cache.get(pid) is not None
        return pid

    def test_mark_promoted_removes_vector(self, proposals, cache):
        pid = self._seeded(proposals, cache)
        with patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
            assert proposals.mark_promoted(pid, target_memory_id="m_x") is True
        assert cache.get(pid) is None

    def test_mark_archived_removes_vector(self, proposals, cache):
        pid = self._seeded(proposals, cache)
        with patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
            assert proposals.mark_archived(pid, reason="decayed") is True
        assert cache.get(pid) is None

    def test_failed_transition_leaves_vector(self, proposals, cache):
        """A no-op transition (already terminal) must not touch the cache."""
        pid = self._seeded(proposals, cache)
        with patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
            proposals.mark_archived(pid)
            cache.put(pid, _unit(2))  # simulate something re-adding it
            assert proposals.mark_promoted(pid, target_memory_id="m_x") is False
        assert cache.get(pid) is not None

    def test_eviction_failure_does_not_block_transition(self, proposals, cache):
        pid = self._seeded(proposals, cache)
        with patch("app.services.embedding_service.get_embedding_cache",
                   side_effect=RuntimeError("boom")):
            assert proposals.mark_archived(pid) is True
        assert proposals.get(pid)["status"] == "archived"


# ── retain_only ─────────────────────────────────────────────────────────

class TestRetainOnly:

    def test_keeps_exactly_the_given_ids(self, cache):
        for i, mid in enumerate(["m_a", "m_b", "prop_c", "m_d"]):
            cache.put(mid, _unit(i))
        removed = cache.retain_only({"m_a", "m_d", "m_never_cached"})
        assert removed == 2
        assert set(cache._ids) == {"m_a", "m_d"}
        assert cache.count == 2
        # Vectors follow their ids after the rebuild.
        assert np.allclose(cache.get("m_a"), _unit(0))
        assert np.allclose(cache.get("m_d"), _unit(3))

    def test_noop_when_nothing_to_remove(self, cache):
        cache.put("m_a", _unit(0))
        cache._dirty = False
        assert cache.retain_only({"m_a"}) == 0
        assert cache._dirty is False

    def test_search_after_retain(self, cache):
        cache.put("m_a", _unit(0))
        cache.put("prop_x", _unit(0))  # identical vector, different id
        cache.retain_only({"m_a"})
        hits = cache.search(_unit(0), top_k=5)
        assert [h[0] for h in hits] == ["m_a"]


# ── Lifecycle pass sweeps orphans ───────────────────────────────────────

class TestLifecycleSweepsOrphans:

    @pytest.mark.asyncio
    async def test_pass_removes_orphan_vectors(self, tmp_path, cache):
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")
        mem = store.save(Memory(content="active fact"))
        with patch("app.services.embedding_service.embed_and_cache"):
            pid = proposals.add(MemoryProposal(content="young open proposal"))

        cache.put(mem.id, _unit(0))
        cache.put(pid, _unit(1))
        cache.put("m_deleted_long_ago", _unit(2))
        cache.put("prop_archived_long_ago", _unit(3))

        with patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
            from app.memory.lifecycle import run_lifecycle_pass
            result = await run_lifecycle_pass()

        assert result["evicted"] == 2
        assert set(cache._ids) == {mem.id, pid}

    @pytest.mark.asyncio
    async def test_pass_with_no_open_proposals_still_sweeps(self, tmp_path, cache):
        """The orphan sweep must not be gated on having open proposals —
        a store whose queue has drained is exactly where stale vectors
        accumulate."""
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")
        mem = store.save(Memory(content="active fact"))
        cache.put(mem.id, _unit(0))
        cache.put("m_gone", _unit(1))

        with patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
            from app.memory.lifecycle import run_lifecycle_pass
            result = await run_lifecycle_pass()

        assert result["evicted"] == 1
        assert set(cache._ids) == {mem.id}
