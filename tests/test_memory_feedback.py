"""
Tests for app.utils.memory_feedback (Diff 6).

Covers:
  - Pure helpers: windowing, cosine, load tracking, clear
  - apply_feedback() end-to-end with mocked embedding provider
  - Above/below threshold semantics: used vs loaded-only
  - Graceful degradation: NoopProvider, empty response, embed failures
  - _score_open_proposals signal recording
  - Proposal embedding at write time
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.utils.memory_feedback import (
    record_load,
    get_loaded_memory_ids,
    clear_conversation,
    apply_feedback,
    _score_open_proposals,
    _windowize,
    _cosine,
    _loaded_per_conversation,
)


# -- Pure helpers --------------------------------------------------------

class TestRecordLoad:

    def setup_method(self):
        _loaded_per_conversation.clear()

    def test_records_memory_ids(self):
        record_load("conv-1", ["m_001", "m_002"])
        assert get_loaded_memory_ids("conv-1") == {"m_001", "m_002"}

    def test_idempotent_within_conversation(self):
        record_load("conv-1", ["m_001"])
        record_load("conv-1", ["m_001", "m_002"])
        assert get_loaded_memory_ids("conv-1") == {"m_001", "m_002"}

    def test_separate_conversations_isolated(self):
        record_load("conv-1", ["m_001"])
        record_load("conv-2", ["m_002"])
        assert get_loaded_memory_ids("conv-1") == {"m_001"}
        assert get_loaded_memory_ids("conv-2") == {"m_002"}

    def test_no_op_on_empty_conversation_id(self):
        record_load(None, ["m_001"])
        record_load("", ["m_001"])
        assert _loaded_per_conversation == {}

    def test_no_op_on_empty_memory_list(self):
        record_load("conv-1", [])
        assert get_loaded_memory_ids("conv-1") == set()


class TestClearConversation:

    def setup_method(self):
        _loaded_per_conversation.clear()

    def test_drops_all_state(self):
        record_load("conv-1", ["m_001", "m_002"])
        clear_conversation("conv-1")
        assert get_loaded_memory_ids("conv-1") == set()

    def test_unknown_conversation_doesnt_raise(self):
        clear_conversation("never-existed")  # No assertion needed; just shouldn't raise.


class TestWindowize:

    def test_short_text_returns_single_window(self):
        text = "hello world"
        windows = _windowize(text, size=800, stride=400)
        assert windows == ["hello world"]

    def test_empty_text_returns_empty_list(self):
        assert _windowize("", size=800, stride=400) == []

    def test_exactly_size_returns_one_window(self):
        text = "a" * 800
        windows = _windowize(text, size=800, stride=400)
        assert len(windows) == 1

    def test_overlapping_windows_at_50pct_stride(self):
        text = "a" * 1200
        windows = _windowize(text, size=800, stride=400)
        # 1200 chars at 400 stride = windows starting at 0, 400, 800
        assert len(windows) == 3
        # Each window is `size` chars except possibly the last
        assert all(len(w) <= 800 for w in windows)


class TestCosine:

    def test_identical_normalized_vectors_return_one(self):
        v = np.array([1.0, 0.0, 0.0])
        assert _cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal_normalized_vectors_return_zero(self):
        v1 = np.array([1.0, 0.0, 0.0])
        v2 = np.array([0.0, 1.0, 0.0])
        assert _cosine(v1, v2) == pytest.approx(0.0)

    def test_opposite_vectors_return_negative_one(self):
        v1 = np.array([1.0, 0.0, 0.0])
        v2 = np.array([-1.0, 0.0, 0.0])
        assert _cosine(v1, v2) == pytest.approx(-1.0)

    def test_returns_python_float(self):
        v = np.array([1.0, 0.0, 0.0])
        assert isinstance(_cosine(v, v), float)


# -- apply_feedback end-to-end ------------------------------------------

class TestApplyFeedback:

    def setup_method(self):
        _loaded_per_conversation.clear()

    @pytest.mark.asyncio
    async def test_no_loaded_memories_returns_zeros(self):
        result = await apply_feedback("conv-empty", "some response")
        assert result == {"loaded": 0, "used": 0, "errors": 0}

    @pytest.mark.asyncio
    async def test_no_conversation_id_returns_zeros(self):
        result = await apply_feedback(None, "some response")
        assert result == {"loaded": 0, "used": 0, "errors": 0}

    @pytest.mark.asyncio
    async def test_used_memory_bumps_counters(self, tmp_path):
        """A memory whose embedding is similar to a response window
        should get retrieval_used_count bumped and importance increased."""
        from app.storage.memory import MemoryStorage
        from app.models.memory import Memory
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        # Memory with importance 0.5
        mem = Memory(content="OBP has 512MB RAM", layer="architecture",
                      importance=0.5)
        store.save(mem)

        # Mock embedding provider to return known vectors
        provider = MagicMock()
        # Memory and response window: identical vectors → cosine = 1.0
        identical_vec = np.array([1.0, 0.0, 0.0])
        provider.embed_text.return_value = identical_vec

        cache = MagicMock()
        cache.get.return_value = identical_vec  # memory's cached embedding

        record_load("conv-1", [mem.id])

        with patch("app.services.embedding_service.get_embedding_provider",
                   return_value=provider), \
             patch("app.services.embedding_service.get_embedding_cache",
                   return_value=cache), \
             patch("app.storage.memory.get_memory_storage", return_value=store):
            result = await apply_feedback("conv-1", "OBP has 512MB RAM budget")

        assert result["loaded"] == 1
        assert result["used"] == 1
        # Memory state should be updated
        refreshed = store.get(mem.id)
        assert refreshed.retrieval_loaded_count == 1
        assert refreshed.retrieval_used_count == 1
        assert refreshed.importance > 0.5

    @pytest.mark.asyncio
    async def test_unused_memory_bumps_loaded_only(self, tmp_path):
        """A memory whose embedding does NOT match the response should
        only get retrieval_loaded_count bumped, not used."""
        from app.storage.memory import MemoryStorage
        from app.models.memory import Memory
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        mem = Memory(content="OBP has 512MB RAM", layer="architecture",
                      importance=0.5)
        store.save(mem)

        provider = MagicMock()
        # Orthogonal vectors → cosine ≈ 0, well below 0.55 threshold
        provider.embed_text.return_value = np.array([0.0, 1.0, 0.0])

        cache = MagicMock()
        cache.get.return_value = np.array([1.0, 0.0, 0.0])

        record_load("conv-1", [mem.id])

        with patch("app.services.embedding_service.get_embedding_provider",
                   return_value=provider), \
             patch("app.services.embedding_service.get_embedding_cache",
                   return_value=cache), \
             patch("app.storage.memory.get_memory_storage", return_value=store):
            result = await apply_feedback("conv-1", "completely unrelated text")

        assert result["loaded"] == 1
        assert result["used"] == 0


# -- _score_open_proposals (Diff 6c) -------------------------------------

class TestScoreOpenProposals:
    """Probationary proposals should receive 'response_match' signals
    when the response embedding aligns with their content embedding."""

    def test_no_open_proposals_returns_zero(self):
        store = MagicMock()
        store.list_open.return_value = []
        cache = MagicMock()
        with patch("app.storage.proposals.get_proposals_store", return_value=store), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
            result = _score_open_proposals(
                window_vecs=[np.array([1.0, 0.0])],
                use_threshold=0.55,
            )
        assert result == 0
        store.record_signal.assert_not_called()

    def test_proposal_with_no_cached_embedding_skipped(self):
        store = MagicMock()
        store.list_open.return_value = [{"id": "prop_abc"}]
        cache = MagicMock()
        cache.get.return_value = None  # No embedding cached
        with patch("app.storage.proposals.get_proposals_store", return_value=store), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
            result = _score_open_proposals(
                window_vecs=[np.array([1.0, 0.0])],
                use_threshold=0.55,
            )
        assert result == 0
        store.record_signal.assert_not_called()

    def test_proposal_above_threshold_records_signal(self):
        """A proposal whose embedding is similar to a response window
        should get a 'response_match' signal recorded."""
        store = MagicMock()
        store.list_open.return_value = [{"id": "prop_abc"}]
        cache = MagicMock()
        # Identical vectors → cosine = 1.0, well above 0.55 threshold
        cache.get.return_value = np.array([1.0, 0.0, 0.0])

        with patch("app.storage.proposals.get_proposals_store", return_value=store), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
            result = _score_open_proposals(
                window_vecs=[np.array([1.0, 0.0, 0.0])],
                use_threshold=0.55,
            )
        assert result == 1
        store.record_signal.assert_called_once()
        args, kwargs = store.record_signal.call_args
        # First positional arg is pid; keyword is name and value
        assert "prop_abc" in args
        assert kwargs.get("name") == "response_match"
        assert "score" in kwargs.get("value", {})
        # Score should be the cosine similarity, rounded to 3 decimals
        assert kwargs["value"]["score"] == 1.0

    def test_proposal_below_threshold_no_signal(self):
        """A proposal whose embedding doesn't match shouldn't receive a signal."""
        store = MagicMock()
        store.list_open.return_value = [{"id": "prop_xyz"}]
        cache = MagicMock()
        # Orthogonal vectors → cosine = 0, below 0.55 threshold
        cache.get.return_value = np.array([0.0, 1.0, 0.0])

        with patch("app.storage.proposals.get_proposals_store", return_value=store), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
            result = _score_open_proposals(
                window_vecs=[np.array([1.0, 0.0, 0.0])],
                use_threshold=0.55,
            )
        assert result == 0
        store.record_signal.assert_not_called()

    def test_max_pool_across_windows(self):
        """A proposal should match if ANY response window scores above
        threshold, not requiring the average to be above."""
        store = MagicMock()
        store.list_open.return_value = [{"id": "prop_match"}]
        cache = MagicMock()
        cache.get.return_value = np.array([1.0, 0.0, 0.0])

        # Three windows: only the second matches the proposal
        windows = [
            np.array([0.0, 1.0, 0.0]),  # cos = 0 (no match)
            np.array([1.0, 0.0, 0.0]),  # cos = 1 (match)
            np.array([0.0, 0.0, 1.0]),  # cos = 0 (no match)
        ]
        with patch("app.storage.proposals.get_proposals_store", return_value=store), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
            result = _score_open_proposals(window_vecs=windows, use_threshold=0.55)
        assert result == 1
        store.record_signal.assert_called_once()

    def test_multiple_matching_proposals_each_signaled(self):
        store = MagicMock()
        store.list_open.return_value = [
            {"id": "prop_a"},
            {"id": "prop_b"},
            {"id": "prop_c"},
        ]
        cache = MagicMock()
        # All three proposals have the same embedding, all match the response
        matching_vec = np.array([1.0, 0.0, 0.0])
        cache.get.return_value = matching_vec

        with patch("app.storage.proposals.get_proposals_store", return_value=store), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
            result = _score_open_proposals(
                window_vecs=[matching_vec],
                use_threshold=0.55,
            )
        assert result == 3
        assert store.record_signal.call_count == 3

    def test_proposal_id_missing_skipped(self):
        """Defensive: if list_open returns a proposal dict without 'id',
        it should be skipped without crashing."""
        store = MagicMock()
        store.list_open.return_value = [{}]  # No 'id' key
        cache = MagicMock()
        with patch("app.storage.proposals.get_proposals_store", return_value=store), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
            result = _score_open_proposals(
                window_vecs=[np.array([1.0, 0.0])],
                use_threshold=0.55,
            )
        assert result == 0

    @pytest.mark.asyncio
    async def test_clears_conversation_state_after_apply(self, tmp_path):
        """After apply_feedback, the conversation's loaded-memory tracking
        should be cleared so subsequent feedback doesn't re-process."""
        from app.storage.memory import MemoryStorage
        store = MemoryStorage(memory_dir=tmp_path / "memory")

        record_load("conv-1", ["m_001"])
        provider = MagicMock(spec=["embed_text"])
        provider.embed_text.return_value = None  # Simulates failure
        cache = MagicMock()

        with patch("app.services.embedding_service.get_embedding_provider",
                   return_value=provider), \
             patch("app.services.embedding_service.get_embedding_cache",
                   return_value=cache), \
             patch("app.storage.memory.get_memory_storage", return_value=store):
            await apply_feedback("conv-1", "some text")

        assert get_loaded_memory_ids("conv-1") == set()

    @pytest.mark.asyncio
    async def test_empty_response_bumps_loaded_only(self, tmp_path):
        """Empty response text should bump loaded count but skip the
        embedding work entirely."""
        from app.storage.memory import MemoryStorage
        from app.models.memory import Memory
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        mem = Memory(content="X", layer="domain_context")
        store.save(mem)
        record_load("conv-1", [mem.id])

        with patch("app.storage.memory.get_memory_storage", return_value=store):
            result = await apply_feedback("conv-1", "")

        assert result == {"loaded": 1, "used": 0, "errors": 0}
        refreshed = store.get(mem.id)
        assert refreshed.retrieval_loaded_count == 1

    @pytest.mark.asyncio
    async def test_noop_provider_bumps_loaded_only(self, tmp_path):
        """When NoopProvider is in use (no embeddings available), should
        gracefully fall back to loaded-only counter bump."""
        from app.storage.memory import MemoryStorage
        from app.models.memory import Memory
        from app.services.embedding_service import NoopProvider
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        mem = Memory(content="Y", layer="domain_context")
        store.save(mem)
        record_load("conv-1", [mem.id])

        with patch("app.services.embedding_service.get_embedding_provider",
                   return_value=NoopProvider()), \
             patch("app.storage.memory.get_memory_storage", return_value=store):
            result = await apply_feedback("conv-1", "real response text")

        assert result["loaded"] == 1
        assert result["used"] == 0
