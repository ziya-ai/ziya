"""
Tests for app.utils.memory_comparator — LLM-guided memory comparison.

Covers:
  - find_similar_memories: local tag+word overlap scoring
  - compare_memory: LLM classification (ADD/UPDATE/NOOP)
  - Fail-open behavior on LLM errors
  - Integration with extraction pipeline
"""

import json
from unittest.mock import patch, AsyncMock

import pytest

from app.utils.memory_comparator import find_similar_memories, compare_memory


@pytest.fixture(autouse=True)
def _disable_embeddings(monkeypatch):
    """Force keyword-only path for comparator tests."""
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    import app.services.embedding_service as _es
    monkeypatch.setattr(_es, "_provider", None)
    monkeypatch.setattr(_es, "_cache", None)


# ── find_similar_memories ─────────────────────────────────────────

class TestFindSimilar:

    def test_finds_tag_overlap(self):
        candidate = {"content": "OBP RAM increased to 1GB", "tags": ["obp", "ram"]}
        existing = [
            {"id": "m1", "content": "OBP has 512MB RAM budget", "tags": ["obp", "ram"], "status": "active"},
            {"id": "m2", "content": "FCTS uses CCSDS framing", "tags": ["fcts", "ccsds"], "status": "active"},
        ]
        result = find_similar_memories(candidate, existing)
        assert len(result) == 1
        assert result[0]["id"] == "m1"

    def test_finds_word_overlap(self):
        candidate = {"content": "credit-based flow control for return link", "tags": []}
        existing = [
            {"id": "m1", "content": "return link uses credit-based flow control", "tags": [], "status": "active"},
        ]
        result = find_similar_memories(candidate, existing)
        assert len(result) == 1

    def test_empty_existing_returns_empty(self):
        candidate = {"content": "some fact", "tags": ["test"]}
        assert find_similar_memories(candidate, []) == []

    def test_no_overlap_returns_empty(self):
        candidate = {"content": "quantum computing basics", "tags": ["quantum"]}
        existing = [
            {"id": "m1", "content": "OBP has 512MB RAM", "tags": ["obp", "ram"], "status": "active"},
        ]
        assert find_similar_memories(candidate, existing) == []

    def test_skips_non_active(self):
        candidate = {"content": "OBP fact", "tags": ["obp"]}
        existing = [
            {"id": "m1", "content": "OBP has 512MB RAM", "tags": ["obp"], "status": "archived"},
        ]
        assert find_similar_memories(candidate, existing) == []

    def test_respects_top_n(self):
        candidate = {"content": "satellite beam pattern", "tags": ["beam", "satellite"]}
        existing = [
            {"id": f"m{i}", "content": f"beam fact {i}", "tags": ["beam"], "status": "active"}
            for i in range(10)
        ]
        result = find_similar_memories(candidate, existing, top_n=3)
        assert len(result) == 3

    def test_embedding_threshold_filters_spurious_matches(self, tmp_path):
        """When the embedding cache returns top-K with low cosine scores
        (small active store, no real topical overlap), find_similar_memories
        should drop those below the threshold rather than feed them to the
        LLM comparator."""
        from unittest.mock import MagicMock, patch
        from app.services.embedding_service import EmbeddingCache
        import app.services.embedding_service as es
        import numpy as np

        cache = EmbeddingCache(memory_dir=tmp_path, dim=4)
        # Two memories: one orthogonal to candidate (low cosine), one aligned (high cosine)
        cache.put("m_unrelated", np.array([0, 1, 0, 0], dtype=np.float32))
        cache.put("m_aligned",   np.array([0.95, 0.31, 0, 0], dtype=np.float32))

        # Candidate vector aligned with m_aligned
        mock_provider = MagicMock()
        mock_provider.embed_text.return_value = np.array([1.0, 0, 0, 0], dtype=np.float32)

        candidate = {"content": "candidate text", "tags": []}
        existing = [
            {"id": "m_unrelated", "content": "unrelated topic", "tags": [], "status": "active"},
            {"id": "m_aligned",   "content": "aligned topic",   "tags": [], "status": "active"},
        ]

        with patch.object(es, "_provider", mock_provider), \
             patch.object(es, "_cache", cache):
            # Default threshold (0.55) should drop the orthogonal match
            result = find_similar_memories(candidate, existing)
            ids = [r["id"] for r in result]
            assert "m_aligned" in ids
            assert "m_unrelated" not in ids

    def test_keyword_fallback_rejects_single_generic_tag_match(self):
        """A single generic tag like 'design' or 'system' shared between
        a logo-design candidate and a hardware-design memory is not enough
        signal — the comparator would just waste an LLM call. Require
        either >=2 tags, >=2 content words, or 1 tag + 1 word."""
        candidate = {
            "content": "Logo typography uses bold weight for the primary mark",
            "tags": ["design", "branding"],
        }
        existing = [
            # Single generic tag overlap, no content words in common
            {"id": "m_unrelated", "content": "PHASER MAC priority bit handling",
             "tags": ["design", "hardware"], "status": "active"},
        ]
        # No embedding cache active in this test so we hit the keyword path
        result = find_similar_memories(candidate, existing)
        # The single 'design' tag overlap should NOT be enough to pass through
        assert result == [], f"Single generic tag should not match: got {result}"

    def test_keyword_fallback_accepts_two_tag_match(self):
        """Two tags in common is real signal — both records are about
        the same topical territory."""
        candidate = {
            "content": "Logo typography uses bold weight",
            "tags": ["logo", "typography"],
        }
        existing = [
            {"id": "m_logo_old", "content": "old logo had different typography",
             "tags": ["logo", "typography"], "status": "active"},
        ]
        result = find_similar_memories(candidate, existing)
        assert len(result) == 1
        assert result[0]["id"] == "m_logo_old"

    def test_embedding_excludes_proposal_self_matches(self, tmp_path):
        """Cache contains both m_* and prop_* entries.  When extraction
        embeds a candidate's own prop_* shortly before the comparator
        runs, that prop_* will rank highest in cache.search — but it's
        the candidate echoing back, not a real existing memory.
        find_similar_memories must filter out prop_* keys so the comparator
        only sees true active memories."""
        from unittest.mock import MagicMock, patch
        from app.services.embedding_service import EmbeddingCache
        import app.services.embedding_service as es
        import numpy as np

        cache = EmbeddingCache(memory_dir=tmp_path, dim=4)
        # The candidate's own embedding under a prop_* key
        cache.put("prop_self", np.array([1.0, 0, 0, 0], dtype=np.float32))
        # An unrelated active memory (orthogonal — should NOT match)
        cache.put("m_unrelated", np.array([0, 1, 0, 0], dtype=np.float32))

        mock_provider = MagicMock()
        mock_provider.embed_text.return_value = np.array([1.0, 0, 0, 0], dtype=np.float32)

        candidate = {"content": "echoes its own prop_*", "tags": []}
        existing = [
            {"id": "m_unrelated", "content": "x", "tags": [], "status": "active"},
        ]

        with patch.object(es, "_provider", mock_provider), \
             patch.object(es, "_cache", cache):
            result = find_similar_memories(candidate, existing)
            # Without the m_* filter, prop_self would have scored 1.0 and
            # poisoned the result.  But prop_* is excluded, m_unrelated is
            # below threshold, so we get nothing.
            assert result == []


# ── compare_memory ────────────────────────────────────────────────

class TestCompareMemory:

    @pytest.mark.asyncio
    async def test_add_decision(self):
        async def mock_call(category, system_prompt, user_message, max_tokens=100, temperature=0.0):
            return '{"action": "ADD"}'

        with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await compare_memory(
                {"content": "new fact", "tags": ["test"], "layer": "domain_context"},
                [{"id": "m1", "content": "old fact", "tags": ["test"], "layer": "domain_context"}],
            )
        assert result["action"] == "ADD"

    @pytest.mark.asyncio
    async def test_update_decision(self):
        async def mock_call(category, system_prompt, user_message, max_tokens=100, temperature=0.0):
            return '{"action": "UPDATE", "target_id": "m1"}'

        with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await compare_memory(
                {"content": "OBP RAM now 1GB", "tags": ["obp", "ram"], "layer": "architecture"},
                [{"id": "m1", "content": "OBP has 512MB RAM", "tags": ["obp", "ram"], "layer": "architecture"}],
            )
        assert result["action"] == "UPDATE"
        assert result["target_id"] == "m1"

    @pytest.mark.asyncio
    async def test_noop_decision(self):
        async def mock_call(category, system_prompt, user_message, max_tokens=100, temperature=0.0):
            return '{"action": "NOOP"}'

        with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await compare_memory(
                {"content": "same thing paraphrased", "tags": ["test"], "layer": "domain_context"},
                [{"id": "m1", "content": "same thing original", "tags": ["test"], "layer": "domain_context"}],
            )
        assert result["action"] == "NOOP"

    @pytest.mark.asyncio
    async def test_fail_open_on_error(self):
        """LLM errors should default to ADD, not lose knowledge."""
        async def mock_call(category, system_prompt, user_message, max_tokens=100, temperature=0.0):
            raise RuntimeError("Service unavailable")

        with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await compare_memory(
                {"content": "important fact", "tags": ["test"], "layer": "domain_context"},
                [{"id": "m1", "content": "existing", "tags": ["test"], "layer": "domain_context"}],
            )
        assert result["action"] == "ADD"

    @pytest.mark.asyncio
    async def test_handles_markdown_wrapped_json(self):
        """Some models wrap JSON in markdown fences."""
        async def mock_call(category, system_prompt, user_message, max_tokens=100, temperature=0.0):
            return '```json\n{"action": "NOOP"}\n```'

        with patch("app.services.model_resolver.call_service_model", side_effect=mock_call):
            result = await compare_memory(
                {"content": "fact", "tags": [], "layer": "domain_context"},
                [{"id": "m1", "content": "fact", "tags": [], "layer": "domain_context"}],
            )
        assert result["action"] == "NOOP"
