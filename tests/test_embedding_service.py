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


# ── PenPal #159 (CWE-502): pickle RCE via allow_pickle=True ────────
#
# _ensure_loaded() previously called np.load(..., allow_pickle=True) on
# a cleartext .npz derived entirely from user/attacker-controlled input
# (a planted file at ~/.ziya/memory/embeddings.npz, or a widened
# ZIYA_HOME). A pickled object's __reduce__ executes during np.load,
# BEFORE the surrounding except Exception can react. The fix switches
# to allow_pickle=False and dtype=str on save so no cache ever needs
# pickle to round-trip. These tests prove the actual exploit payload
# from the report cannot execute, not just that behavior changed.

class TestPickleRCEClosed:
    def test_malicious_pickle_payload_never_executes(self, tmp_path):
        """Recreate the report's PoC: a pickled __reduce__ gadget that
        would call os.system on load. Must not execute, and must not
        propagate an exception past _ensure_loaded (self-heals to an
        empty cache instead, matching normal 'corrupt cache' handling)."""
        import io as _io
        marker = tmp_path / "PWNED"

        class Exploit:
            def __reduce__(self):
                # Would touch disk if pickle were ever deserialized during load.
                return (_touch_marker, (str(marker),))

        buf = _io.BytesIO()
        np.savez(buf, ids=np.array([Exploit()], dtype=object),
                  vectors=np.zeros((1, 8), dtype=np.float32))
        (tmp_path / "embeddings.npz").write_bytes(buf.getvalue())

        cache = EmbeddingCache(tmp_path, dim=8)
        # Triggers _ensure_loaded via any public read path.
        result = cache.get("m_1")

        assert not marker.exists(), "pickle payload executed — CWE-502 regression"
        # Corrupt/rejected cache degrades to empty rather than raising.
        assert result is None
        assert cache.count == 0

    def test_load_rejects_object_dtype_ids_without_crashing(self, tmp_path):
        """A pre-existing legacy cache saved with the old dtype=object
        format (no pickle payload, just plain strings) must not crash
        the caller — allow_pickle=False rejects it, and the cache
        self-heals to empty so the store rebuilds transparently."""
        import io as _io
        buf = _io.BytesIO()
        np.savez(buf, ids=np.array(["m_legacy"], dtype=object),
                  vectors=np.zeros((1, 8), dtype=np.float32))
        (tmp_path / "embeddings.npz").write_bytes(buf.getvalue())

        cache = EmbeddingCache(tmp_path, dim=8)
        assert cache.get("m_legacy") is None
        assert cache.count == 0

    def test_new_cache_saved_with_dtype_str_round_trips(self, tmp_path):
        """flush()/reload with the new dtype=str format must work
        end-to-end with allow_pickle=False on the read side — this is
        the migration path every fresh cache now takes."""
        cache1 = EmbeddingCache(tmp_path, dim=8)
        vec = _random_vec()
        cache1.put("m_new", vec)
        cache1.flush()

        # Saved bytes must not require pickle to load.
        raw = (tmp_path / "embeddings.npz").read_bytes()
        import io as _io
        data = np.load(_io.BytesIO(raw), allow_pickle=False)
        assert list(data["ids"]) == ["m_new"] or list(data["ids"])[0] == "m_new"

        cache2 = EmbeddingCache(tmp_path, dim=8)
        retrieved = cache2.get("m_new")
        assert retrieved is not None
        np.testing.assert_array_almost_equal(retrieved, vec)


def _touch_marker(path: str) -> None:
    """Side-effect helper for TestPickleRCEClosed — writes a marker file
    if invoked. Used to prove a pickle reduction did/didn't execute."""
    with open(path, "w") as f:
        f.write("pwned")

