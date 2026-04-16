"""
Tests for the embedding service — vector storage, search, and caching.

Uses mock embedding providers to test logic without network dependencies.
"""
import numpy as np
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.services.embedding_service import (
    EmbeddingCache,
    NoopProvider,
    BedrockTitanProvider,
    embed_and_cache,
    semantic_search,
    remove_embedding,
)


@pytest.fixture
def tmp_cache(tmp_path):
    """Create an EmbeddingCache backed by a temp directory."""
    return EmbeddingCache(tmp_path, dim=8)


def _random_vec(dim=8):
    """Create a random normalized vector."""
    v = np.random.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


class TestEmbeddingCache:
    def test_put_and_get(self, tmp_cache):
        vec = _random_vec()
        tmp_cache.put("m_1", vec)
        retrieved = tmp_cache.get("m_1")
        assert retrieved is not None
        np.testing.assert_array_almost_equal(retrieved, vec)

    def test_get_missing(self, tmp_cache):
        assert tmp_cache.get("nonexistent") is None

    def test_search_returns_ranked_results(self, tmp_cache):
        # Create vectors with clear directional difference
        # Normalize before storing so the cache sees correct vectors
        query = np.zeros(8, dtype=np.float32)
        query[0] = 1.0  # unit vector along dim 0

        similar = np.zeros(8, dtype=np.float32)
        similar[0] = 0.95
        similar[1] = 0.05
        similar /= np.linalg.norm(similar)

        dissimilar = np.zeros(8, dtype=np.float32)
        dissimilar[7] = 1.0  # unit vector along dim 7 — orthogonal to query

        tmp_cache.put("m_similar", similar)
        tmp_cache.put("m_dissimilar", dissimilar)

        results = tmp_cache.search(query, top_k=2)
        assert len(results) >= 1
        assert results[0][0] == "m_similar"
        # Dissimilar should have score ~0 (orthogonal), filtered out by > 0 check
        # or ranked much lower
        assert results[0][1] > 0.9

    def test_search_with_exclude(self, tmp_cache):
        vec = _random_vec()
        tmp_cache.put("m_1", vec)
        tmp_cache.put("m_2", vec)  # identical

        results = tmp_cache.search(vec, top_k=5, exclude_ids={"m_1"})
        result_ids = [mid for mid, _ in results]
        assert "m_1" not in result_ids
        assert "m_2" in result_ids

    def test_remove(self, tmp_cache):
        vec = _random_vec()
        tmp_cache.put("m_1", vec)
        assert tmp_cache.get("m_1") is not None

        tmp_cache.remove("m_1")
        assert tmp_cache.get("m_1") is None
        assert tmp_cache.count == 0

    def test_remove_nonexistent(self, tmp_cache):
        # Should not raise
        tmp_cache.remove("nonexistent")

    def test_flush_and_reload(self, tmp_path):
        cache1 = EmbeddingCache(tmp_path, dim=8)
        vec = _random_vec()
        cache1.put("m_1", vec)
        cache1.flush()

        # Create new cache instance (simulates restart)
        cache2 = EmbeddingCache(tmp_path, dim=8)
        retrieved = cache2.get("m_1")
        assert retrieved is not None
        np.testing.assert_array_almost_equal(retrieved, vec)

    def test_missing_ids(self, tmp_cache):
        tmp_cache.put("m_1", _random_vec())
        tmp_cache.put("m_2", _random_vec())

        missing = tmp_cache.missing_ids(["m_1", "m_2", "m_3", "m_4"])
        assert set(missing) == {"m_3", "m_4"}

    def test_update_existing(self, tmp_cache):
        vec1 = _random_vec()
        vec2 = _random_vec()
        tmp_cache.put("m_1", vec1)
        tmp_cache.put("m_1", vec2)  # update

        assert tmp_cache.count == 1
        retrieved = tmp_cache.get("m_1")
        np.testing.assert_array_almost_equal(retrieved, vec2)

    def test_search_empty_cache(self, tmp_cache):
        results = tmp_cache.search(_random_vec(), top_k=5)
        assert results == []

    def test_large_scale_search(self, tmp_path):
        """Test search performance at 10K scale."""
        cache = EmbeddingCache(tmp_path, dim=256)
        # Insert 1000 random vectors (10K would be slow in test)
        vectors = np.random.randn(1000, 256).astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / norms

        for i in range(1000):
            cache.put(f"m_{i}", vectors[i])

        query = vectors[42]  # search for something we know is there
        results = cache.search(query, top_k=5)
        assert len(results) == 5
        # The exact match should be first
        assert results[0][0] == "m_42"
        assert results[0][1] > 0.99


class TestNoopProvider:
    def test_returns_none(self):
        provider = NoopProvider()
        assert provider.embed_text("anything") is None
        assert provider.dim == 256


class TestBedrockProvider:
    def test_embed_text_calls_bedrock(self):
        mock_client = MagicMock()
        mock_client.invoke_model.return_value = {
            "body": MagicMock(read=lambda: b'{"embedding": [0.1, 0.2, 0.3]}')
        }

        provider = BedrockTitanProvider(dim=3)
        provider._client = mock_client

        vec = provider.embed_text("test text")
        assert vec is not None
        assert vec.shape == (3,)
        # Should be normalized
        assert abs(np.linalg.norm(vec) - 1.0) < 0.01

    def test_empty_text_returns_none(self):
        provider = BedrockTitanProvider()
        assert provider.embed_text("") is None
        assert provider.embed_text("   ") is None
