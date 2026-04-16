"""
Embedding service for the memory system.

Provides vector embeddings for semantic search, deduplication, and
clustering.  Pluggable provider architecture with Bedrock Titan as
the primary backend and graceful degradation to keyword-only search
when embeddings are unavailable.

Storage: embeddings are kept in a separate numpy .npz file to avoid
bloating the memories JSON.  Loaded lazily into memory on first search.
At 10K memories × 256-dim × float32 = 10MB resident — acceptable up
to ~100K before needing an index (HNSW/IVF).
"""

import json
import os
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.utils.logging_utils import logger

# Embedding dimensions.  256 is the sweet spot: good quality, low storage.
# Titan Embed V2 supports 256, 512, 1024.
DEFAULT_DIM = 256
DEFAULT_MODEL = "amazon.titan-embed-text-v2:0"
DEFAULT_REGION = "us-east-1"

# Batch size for backfill operations (avoid API throttling)
BACKFILL_BATCH_SIZE = 20
BACKFILL_BATCH_DELAY = 0.5  # seconds between batches


class EmbeddingProvider:
    """Abstract base for embedding providers."""

    def embed_text(self, text: str) -> Optional[np.ndarray]:
        """Embed a single text string.  Returns None on failure."""
        raise NotImplementedError

    def embed_batch(self, texts: List[str]) -> List[Optional[np.ndarray]]:
        """Embed multiple texts.  Default: sequential calls."""
        return [self.embed_text(t) for t in texts]

    @property
    def dim(self) -> int:
        raise NotImplementedError


class BedrockTitanProvider(EmbeddingProvider):
    """Amazon Titan Embed V2 via Bedrock invoke_model."""

    def __init__(self, region: str = None, model_id: str = None, dim: int = None,
                 aws_profile: str = None):
        self._region = region or os.environ.get("ZIYA_EMBEDDING_REGION", DEFAULT_REGION)
        self._model_id = model_id or os.environ.get("ZIYA_EMBEDDING_MODEL", DEFAULT_MODEL)
        self._dim = dim or int(os.environ.get("ZIYA_EMBEDDING_DIM", str(DEFAULT_DIM)))
        self._profile = aws_profile or os.environ.get("AWS_PROFILE", "default")
        self._client = None
        self._warned = False

    def _get_client(self):
        if self._client is None:
            import boto3
            session = boto3.Session(profile_name=self._profile)
            self._client = session.client("bedrock-runtime", region_name=self._region)
        return self._client

    @property
    def dim(self) -> int:
        return self._dim

    def embed_text(self, text: str) -> Optional[np.ndarray]:
        if not text or not text.strip():
            return None
        try:
            client = self._get_client()
            body = json.dumps({
                "inputText": text[:8000],  # Titan V2 limit: 8192 tokens
                "dimensions": self._dim,
            })
            response = client.invoke_model(
                modelId=self._model_id,
                body=body,
                accept="application/json",
                contentType="application/json",
            )
            result = json.loads(response["body"].read())
            vec = np.array(result["embedding"], dtype=np.float32)
            # Normalize for cosine similarity via dot product
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec /= norm
            return vec
        except Exception as e:
            if not self._warned:
                logger.warning(f"Embedding API call failed (will use keyword fallback): {e}")
                self._warned = True
            return None


class NoopProvider(EmbeddingProvider):
    """Fallback provider that returns None for all embeddings."""

    @property
    def dim(self) -> int:
        return DEFAULT_DIM

    def embed_text(self, text: str) -> Optional[np.ndarray]:
        return None


class EmbeddingCache:
    """In-memory cache of memory ID → embedding vector.

    Backed by a numpy .npz file on disk.  Loaded lazily on first access.
    Thread-safe for concurrent reads; writes are serialized via a lock.

    File format:
        embeddings.npz contains:
          - 'ids': 1-D array of memory ID strings
          - 'vectors': 2-D float32 array (n_memories × dim)
    """

    def __init__(self, memory_dir: Path, dim: int = DEFAULT_DIM):
        self._file = memory_dir / "embeddings.npz"
        self._dim = dim
        self._lock = threading.Lock()
        # Lazy-loaded state
        self._ids: Optional[List[str]] = None
        self._vectors: Optional[np.ndarray] = None  # (N, dim) float32, pre-normalized
        self._id_to_idx: Optional[Dict[str, int]] = None
        self._dirty = False

    def _ensure_loaded(self):
        """Load from disk if not yet in memory."""
        if self._ids is not None:
            return
        if self._file.exists():
            try:
                data = np.load(self._file, allow_pickle=True)
                self._ids = list(data["ids"])
                self._vectors = data["vectors"].astype(np.float32)
                self._id_to_idx = {mid: i for i, mid in enumerate(self._ids)}
                logger.debug(f"Loaded {len(self._ids)} embeddings from cache")
                return
            except Exception as e:
                logger.warning(f"Could not load embeddings cache: {e}")
        # Initialize empty
        self._ids = []
        self._vectors = np.zeros((0, self._dim), dtype=np.float32)
        self._id_to_idx = {}

    def get(self, memory_id: str) -> Optional[np.ndarray]:
        """Get the embedding for a memory ID, or None if not cached."""
        with self._lock:
            self._ensure_loaded()
            idx = self._id_to_idx.get(memory_id)
            if idx is None:
                return None
            return self._vectors[idx]

    def put(self, memory_id: str, vector: np.ndarray):
        """Store or update an embedding."""
        with self._lock:
            self._ensure_loaded()
            idx = self._id_to_idx.get(memory_id)
            if idx is not None:
                self._vectors[idx] = vector
            else:
                self._ids.append(memory_id)
                self._id_to_idx[memory_id] = len(self._ids) - 1
                self._vectors = np.vstack([self._vectors, vector.reshape(1, -1)])
            self._dirty = True

    def remove(self, memory_id: str):
        """Remove an embedding from the cache."""
        with self._lock:
            self._ensure_loaded()
            idx = self._id_to_idx.get(memory_id)
            if idx is None:
                return
            # Swap with last element for O(1) removal
            last_idx = len(self._ids) - 1
            if idx != last_idx:
                last_id = self._ids[last_idx]
                self._ids[idx] = last_id
                self._vectors[idx] = self._vectors[last_idx]
                self._id_to_idx[last_id] = idx
            self._ids.pop()
            self._vectors = self._vectors[:len(self._ids)]
            del self._id_to_idx[memory_id]
            self._dirty = True

    def search(self, query_vec: np.ndarray, top_k: int = 10,
               exclude_ids: Optional[set] = None) -> List[Tuple[str, float]]:
        """Find the top-K most similar memory IDs by cosine similarity.

        Returns list of (memory_id, similarity_score) tuples, descending.
        """
        with self._lock:
            self._ensure_loaded()
            if len(self._ids) == 0:
                return []
            # Dot product = cosine similarity (vectors are pre-normalized)
            scores = self._vectors @ query_vec
            if exclude_ids:
                for mid, idx in self._id_to_idx.items():
                    if mid in exclude_ids:
                        scores[idx] = -1.0
            # argpartition is O(N) vs O(N log N) for full sort
            k = min(top_k, len(self._ids))
            top_indices = np.argpartition(scores, -k)[-k:]
            # Sort just the top-K
            top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]
            return [(self._ids[i], float(scores[i])) for i in top_indices
                    if scores[i] > 0]

    def flush(self):
        """Write cache to disk if dirty."""
        with self._lock:
            if not self._dirty or self._ids is None:
                return
            try:
                self._file.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._file.with_suffix(".tmp.npz")
                np.savez(tmp,
                         ids=np.array(self._ids, dtype=object),
                         vectors=self._vectors)
                tmp.rename(self._file)
                self._dirty = False
                logger.debug(f"Flushed {len(self._ids)} embeddings to disk")
            except Exception as e:
                logger.error(f"Failed to flush embeddings: {e}")

    @property
    def count(self) -> int:
        with self._lock:
            self._ensure_loaded()
            return len(self._ids)

    def missing_ids(self, all_ids: List[str]) -> List[str]:
        """Return memory IDs that are not in the cache."""
        with self._lock:
            self._ensure_loaded()
            return [mid for mid in all_ids if mid not in self._id_to_idx]


# -- Module-level singleton --------------------------------------------------

_provider: Optional[EmbeddingProvider] = None
_cache: Optional[EmbeddingCache] = None
_init_lock = threading.Lock()


def _resolve_provider() -> EmbeddingProvider:
    """Create the appropriate embedding provider based on configuration."""
    choice = os.environ.get("ZIYA_EMBEDDING_PROVIDER", "auto").lower()
    if choice == "none":
        return NoopProvider()
    if choice in ("bedrock", "titan", "auto"):
        try:
            provider = BedrockTitanProvider()
            # Quick smoke test
            vec = provider.embed_text("test")
            if vec is not None:
                logger.info(f"Embedding provider: Bedrock Titan ({provider._dim}-dim)")
                return provider
        except Exception as e:
            logger.info(f"Bedrock embedding unavailable: {e}")
    logger.info("Embedding provider: disabled (keyword search only)")
    return NoopProvider()


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    with _init_lock:
        if _provider is None:
            _provider = _resolve_provider()
    return _provider


def get_embedding_cache() -> EmbeddingCache:
    global _cache
    with _init_lock:
        if _cache is None:
            from app.utils.paths import get_ziya_home
            memory_dir = get_ziya_home() / "memory"
            dim = int(os.environ.get("ZIYA_EMBEDDING_DIM", str(DEFAULT_DIM)))
            _cache = EmbeddingCache(memory_dir, dim=dim)
    return _cache


def embed_and_cache(memory_id: str, content: str) -> Optional[np.ndarray]:
    """Embed a memory's content and store in cache.  Returns the vector."""
    provider = get_embedding_provider()
    if isinstance(provider, NoopProvider):
        return None
    vec = provider.embed_text(content)
    if vec is not None:
        cache = get_embedding_cache()
        cache.put(memory_id, vec)
        cache.flush()
    return vec


def semantic_search(query: str, top_k: int = 10,
                    exclude_ids: Optional[set] = None) -> List[Tuple[str, float]]:
    """Embed query and return top-K similar memory IDs with scores."""
    provider = get_embedding_provider()
    if isinstance(provider, NoopProvider):
        return []
    query_vec = provider.embed_text(query)
    if query_vec is None:
        return []
    cache = get_embedding_cache()
    return cache.search(query_vec, top_k=top_k, exclude_ids=exclude_ids)


def remove_embedding(memory_id: str):
    """Remove an embedding from the cache (on memory delete)."""
    cache = get_embedding_cache()
    cache.remove(memory_id)
    cache.flush()


async def backfill_embeddings(memory_ids_and_content: List[Tuple[str, str]],
                              progress_callback=None) -> int:
    """Embed all memories that lack vectors.  Returns count embedded."""
    import asyncio
    provider = get_embedding_provider()
    if isinstance(provider, NoopProvider):
        return 0
    cache = get_embedding_cache()
    embedded = 0
    total = len(memory_ids_and_content)
    for i in range(0, total, BACKFILL_BATCH_SIZE):
        batch = memory_ids_and_content[i:i + BACKFILL_BATCH_SIZE]
        for mid, content in batch:
            vec = provider.embed_text(content)
            if vec is not None:
                cache.put(mid, vec)
                embedded += 1
        cache.flush()
        if progress_callback:
            progress_callback(min(i + BACKFILL_BATCH_SIZE, total), total)
        if i + BACKFILL_BATCH_SIZE < total:
            await asyncio.sleep(BACKFILL_BATCH_DELAY)
    logger.info(f"Backfill complete: {embedded}/{total} memories embedded")
    return embedded
