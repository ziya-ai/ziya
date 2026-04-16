"""
Tests for embedding integration across the memory pipeline.

Covers:
  - Embedding-based dedup in extraction pipeline
  - Re-embed on UPDATE path
  - Startup initialization logic
  - Hybrid search with RRF fusion
"""

import json
import os
import numpy as np
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.memory import Memory, MemoryProposal
from app.storage.memory import MemoryStorage
from app.services.embedding_service import EmbeddingCache, NoopProvider


@pytest.fixture(autouse=True)
def isolate_embeddings(monkeypatch):
    """Force NoopProvider in tests to avoid hitting live Bedrock."""
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    # Reset singletons
    import app.services.embedding_service as es
    es._provider = None
    es._cache = None
    yield
    es._provider = None
    es._cache = None


@pytest.fixture
def tmp_store(tmp_path):
    return MemoryStorage(memory_dir=tmp_path / "memory")


def _make_memory(store, content, layer="domain_context", tags=None, importance=0.5):
    mem = Memory(content=content, layer=layer, tags=tags or [], importance=importance)
    return store.save(mem)


# ── Embedding-based dedup in extraction ────────────────────────────

class TestEmbeddingDedup:
    """Test that deduplicate() uses embedding similarity when available."""

    def test_keyword_dedup_still_works_without_embeddings(self):
        """With NoopProvider, keyword dedup catches substring matches."""
        from app.utils.memory_extractor import deduplicate

        candidates = [
            {"content": "VPC uses overlay networking", "tags": ["networking"]},
            {"content": "A completely different fact", "tags": ["other"]},
        ]
        existing = [
            {"content": "VPC uses overlay networking for isolation", "tags": ["networking"]},
        ]
        result = deduplicate(candidates, existing)
        # First candidate is a substring of existing — should be filtered
        assert len(result) == 1
        assert result[0]["tags"] == ["other"]

    def test_embedding_dedup_rejects_paraphrases(self, tmp_path):
        """With a mock provider, high-similarity candidates are rejected."""
        from app.utils.memory_extractor import deduplicate
        import app.services.embedding_service as es

        # Create a mock provider that returns similar vectors
        mock_provider = MagicMock()
        mock_provider.embed_text.return_value = np.array([1.0, 0, 0, 0], dtype=np.float32)

        # Create a cache with an existing memory that's nearly identical
        cache = EmbeddingCache(tmp_path, dim=4)
        existing_vec = np.array([0.99, 0.1, 0, 0], dtype=np.float32)
        existing_vec /= np.linalg.norm(existing_vec)
        cache.put("m_existing", existing_vec)

        es._provider = mock_provider
        es._cache = cache

        try:
            candidates = [
                {"content": "Network virtualization via overlays", "tags": ["net"]},
            ]
            existing = [
                {"id": "m_existing", "content": "VPC overlay networking", "tags": ["net"]},
            ]
            result = deduplicate(candidates, existing)
            # The embedding similarity > 0.92 threshold should catch this
            assert len(result) == 0
        finally:
            es._provider = None
            es._cache = None

    def test_embedding_dedup_keeps_genuinely_different(self, tmp_path):
        """Candidates with low similarity pass through."""
        from app.utils.memory_extractor import deduplicate
        import app.services.embedding_service as es

        mock_provider = MagicMock()
        # Return a vector pointing in a different direction
        query_vec = np.array([1.0, 0, 0, 0], dtype=np.float32)
        mock_provider.embed_text.return_value = query_vec

        cache = EmbeddingCache(tmp_path, dim=4)
        # Existing memory vector is orthogonal — low similarity
        existing_vec = np.array([0, 0, 0, 1.0], dtype=np.float32)
        cache.put("m_existing", existing_vec)

        es._provider = mock_provider
        es._cache = cache

        try:
            candidates = [
                {"content": "Something totally different", "tags": ["new"]},
            ]
            existing = [
                {"id": "m_existing", "content": "Old unrelated fact", "tags": ["old"]},
            ]
            result = deduplicate(candidates, existing)
            assert len(result) == 1  # Kept — low similarity
        finally:
            es._provider = None
            es._cache = None


# ── Re-embed on UPDATE ─────────────────────────────────────────────

class TestReEmbedOnUpdate:
    """Test that UPDATE in extraction triggers re-embedding."""

    @pytest.mark.asyncio
    async def test_update_calls_embed_and_cache(self, tmp_store):
        """When comparator says UPDATE, the updated memory should be re-embedded."""
        m1 = _make_memory(tmp_store, "Old content about VPC", tags=["vpc"])

        embed_calls = []
        _real_embed_and_cache = None
        def _track_embed(mid, content):
            embed_calls.append((mid, content))
            return None

        # Patch embed_and_cache at the module level in embedding_service,
        # AND ensure memory.py's lazy import picks up the mock.
        import app.services.embedding_service as _es_mod
        _real_embed_and_cache = _es_mod.embed_and_cache

        # Mock the full extraction pipeline dependencies
        with patch("app.utils.memory_extractor.extract_memories", new_callable=AsyncMock) as mock_extract, \
             patch("app.utils.memory_comparator.find_similar_memories") as mock_find, \
             patch("app.utils.memory_comparator.compare_memory", new_callable=AsyncMock) as mock_compare, \
             patch("app.storage.memory.get_memory_storage", return_value=tmp_store), \
             patch.object(_es_mod, "embed_and_cache", side_effect=_track_embed), \
             patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True):

            mock_extract.return_value = [
                {"content": "VPC architecture was redesigned to use VXLAN-based overlay networking with dedicated transit gateways for cross-region traffic",
                 "layer": "domain_context", "tags": ["vpc", "networking"], "confidence": "high"}
            ]
            mock_find.return_value = [m1.model_dump()]
            mock_compare.return_value = {"action": "UPDATE", "target_id": m1.id}

            from app.utils.memory_extractor import run_post_conversation_extraction
            # Messages must be long enough to survive strip_conversation (>200 chars)
            messages = [
                {"role": "user", "content": "I need to understand how VPC networking works at the overlay level, including all the encapsulation and tunneling details"},
                {"role": "assistant", "content": "VPC uses overlay networking with VXLAN encapsulation to provide tenant isolation across the physical infrastructure"},
                {"role": "user", "content": "What has changed in the latest architecture revision with respect to the overlay and underlay separation?"},
                {"role": "assistant", "content": "The updated VPC content includes significant changes to how overlays interact with the physical underlay network"},
                {"role": "user", "content": "Can you explain the implications for cross-region traffic routing and failover?"},
                {"role": "assistant", "content": "Cross-region traffic now uses dedicated transit gateways with automatic failover and health checking"},
            ]
            result = await run_post_conversation_extraction(messages)

            # store.save() triggers one embed_and_cache call (from storage),
            # and the UPDATE path triggers another (from extractor).
            # At least one should have the updated content.
            update_embeds = [(mid, c) for mid, c in embed_calls if "VXLAN" in c]
            assert len(update_embeds) >= 1, f"Expected re-embed with updated content, got: {embed_calls}"

    @pytest.mark.asyncio
    async def test_update_preserves_importance(self, tmp_store):
        """UPDATE should not reset importance of a heavily-used memory."""
        m1 = _make_memory(tmp_store, "Old content", tags=["topic"], importance=0.95)

        with patch("app.utils.memory_extractor.extract_memories", new_callable=AsyncMock) as mock_extract, \
             patch("app.utils.memory_comparator.find_similar_memories") as mock_find, \
             patch("app.utils.memory_comparator.compare_memory", new_callable=AsyncMock) as mock_compare, \
             patch("app.storage.memory.get_memory_storage", return_value=tmp_store), \
             patch("app.services.embedding_service.embed_and_cache"), \
             patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True):

            mock_extract.return_value = [
                {"content": "New content", "layer": "domain_context",
                 "tags": ["topic"], "confidence": "high"}
            ]
            mock_find.return_value = [m1.model_dump()]
            mock_compare.return_value = {"action": "UPDATE", "target_id": m1.id}

            from app.utils.memory_extractor import run_post_conversation_extraction
            messages = [{"role": "user", "content": f"msg {i}"} for i in range(6)]
            await run_post_conversation_extraction(messages)

            updated = tmp_store.get(m1.id)
            assert updated.importance == 0.95  # preserved, not reset


# ── Startup initialization ─────────────────────────────────────────

def _get_startup_fn():
    """Import the startup function, or None if not yet applied."""
    try:
        from app.server import _initialize_memory_background
        return _initialize_memory_background
    except ImportError:
        return None

_startup_fn = _get_startup_fn()
_skip_startup = pytest.mark.skipif(_startup_fn is None, reason="server.py startup hook not yet applied")


class TestStartupInitialization:
    """Test the _initialize_memory_background logic."""

    @_skip_startup
    @pytest.mark.asyncio
    async def test_backfill_runs_for_missing_embeddings(self, tmp_store):
        """Startup should backfill embeddings for memories without vectors."""
        created_mems = []
        for i in range(12):
            created_mems.append(_make_memory(tmp_store, f"Memory number {i}", tags=["test"]))
        created_ids = [m.id for m in created_mems]

        with patch("app.storage.memory.get_memory_storage", return_value=tmp_store), \
             patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
             patch("app.services.embedding_service.get_embedding_provider") as mock_prov, \
             patch("app.services.embedding_service.get_embedding_cache") as mock_cache_fn, \
             patch("app.services.embedding_service.backfill_embeddings", new_callable=AsyncMock) as mock_backfill:

            mock_prov.return_value = MagicMock()  # Not NoopProvider
            mock_prov.return_value.__class__ = type("FakeProvider", (), {})

            mock_cache = MagicMock()
            # Return the ACTUAL memory IDs as missing
            mock_cache.missing_ids.return_value = created_ids
            mock_cache_fn.return_value = mock_cache

            mock_backfill.return_value = 12

            # Run the startup function
            await _startup_fn()

            assert mock_backfill.called
            assert len(mock_backfill.call_args[0][0]) == 12

    @_skip_startup
    @pytest.mark.asyncio
    async def test_organize_triggered_when_no_mindmap(self, tmp_store):
        """Startup should auto-organize when memories exist but no mind-map."""
        for i in range(12):
            _make_memory(tmp_store, f"Memory {i}", tags=["test"])

        with patch("app.storage.memory.get_memory_storage", return_value=tmp_store), \
             patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True), \
             patch("app.services.embedding_service.get_embedding_provider") as mock_prov, \
             patch("app.services.embedding_service.get_embedding_cache") as mock_cache_fn, \
             patch("app.services.embedding_service.backfill_embeddings", new_callable=AsyncMock) as mock_backfill, \
             patch("app.utils.memory_organizer.reorganize", new_callable=AsyncMock) as mock_reorg:

            # Embeddings: all present, no backfill needed
            mock_prov.return_value = NoopProvider()
            mock_cache = MagicMock()
            mock_cache.missing_ids.return_value = []
            mock_cache_fn.return_value = mock_cache

            mock_reorg.return_value = {
                "bootstrap": {"status": "success", "domains_created": 3, "memories_placed": 12},
                "relations": {"status": "success"},
            }

            await _startup_fn()

            assert mock_reorg.called

    @_skip_startup
    @pytest.mark.asyncio
    async def test_startup_skips_when_memory_disabled(self):
        """Startup should silently return when memory category is disabled."""
        with patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=False):
            # Should return without error
            await _startup_fn()

    @_skip_startup
    @pytest.mark.asyncio
    async def test_startup_skips_when_no_memories(self, tmp_store):
        """Startup should silently return when memory store is empty."""
        with patch("app.storage.memory.get_memory_storage", return_value=tmp_store), \
             patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True):
            await _startup_fn()
            # No errors, no actions


# ── Hybrid search RRF ──────────────────────────────────────────────

class TestHybridSearch:
    """Test that search falls back gracefully and fuses signals."""

    def test_keyword_only_when_no_embeddings(self, tmp_store):
        """With NoopProvider, search should still work via keywords."""
        _make_memory(tmp_store, "FPGA has 1024 queues per DFU", tags=["fpga", "queues"])
        _make_memory(tmp_store, "Totally unrelated content", tags=["other"])

        results = tmp_store.search("FPGA queues")
        assert len(results) >= 1
        assert "FPGA" in results[0].content

    def test_search_handles_empty_store(self, tmp_store):
        results = tmp_store.search("anything")
        assert results == []

    def test_search_handles_empty_query(self, tmp_store):
        _make_memory(tmp_store, "Some content", tags=["test"])
        results = tmp_store.search("")
        assert results == []
