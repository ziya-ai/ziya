"""
Regression tests for the vectorized active-store similarity lookup in
app/memory/lifecycle.py (_make_active_embedding_lookup).

The lookup was O(active) per proposal via a Python-level
max(np.dot(...) for ...) generator; it is now a single (N, dim) @ (dim,)
matmul + np.max.  These tests pin:
  - numerical equivalence to the old generator-max formulation
  - the empty-active-store and missing-proposal-vector fast paths
  - that the returned similarity is the MAX cosine (not mean / first)
  - that vectors are treated as pre-normalized (dot == cosine)
  - that the active set is snapshotted at build time (np.stack copies),
    so a later cache mutation can't corrupt an in-flight pass

Vectors are sized to the embedding cache's actual dimension: the cache
enforces a fixed dim and rejects mismatched-length puts, so toy 3-D
vectors must be zero-padded up to cache._dim.  Cosine relationships are
preserved by placing the controlled values in the leading coordinates.
"""
import numpy as np
import pytest

from app.memory import lifecycle
from app.services.embedding_service import get_embedding_cache


def _unit(vec, dim):
    """Normalize and zero-pad `vec` up to `dim` so it satisfies the cache's
    fixed-dimension contract while preserving cosine relationships among the
    leading coordinates."""
    arr = np.zeros(dim, dtype=np.float32)
    src = np.asarray(vec, dtype=np.float32)
    arr[: len(src)] = src
    n = np.linalg.norm(arr)
    return arr / n if n else arr


def _seed_cache(entries):
    """Put {id: vector} into the (isolated, per-test) embedding cache,
    normalizing so dot products are true cosines — matching the
    BedrockTitanProvider contract that the cache relies on.  Vectors are
    sized to the cache's actual dimension."""
    cache = get_embedding_cache()
    dim = cache._dim
    for mid, vec in entries.items():
        cache.put(mid, _unit(vec, dim))
    return cache


class _FakeStore:
    """Minimal stand-in for MemoryStorage.list_memories(status='active')."""
    def __init__(self, active_ids):
        self._active = active_ids

    def list_memories(self, status=None):
        return [type("M", (), {"id": mid})() for mid in self._active]


@pytest.fixture
def patched_store(monkeypatch):
    """Route lifecycle's get_memory_storage at the call site to a fake."""
    def _install(active_ids):
        monkeypatch.setattr(
            "app.storage.memory.get_memory_storage",
            lambda: _FakeStore(active_ids),
        )
    return _install


def test_returns_max_cosine_not_mean_or_first(patched_store):
    # Proposal exactly matches m3, weakly/orthogonally related to the others.
    # A correct max-lookup must return the strong score (1.0), not a mean.
    _seed_cache({
        "m1": [1.0, 0.0, 0.0],
        "m2": [0.0, 1.0, 0.0],
        "m3": [1.0, 1.0, 0.0],
        "prop": [1.0, 1.0, 0.0],
    })
    patched_store(["m1", "m2", "m3"])
    lookup = lifecycle._make_active_embedding_lookup()
    sim = lookup({"id": "prop"})
    assert sim == pytest.approx(1.0, abs=1e-5)


def test_equivalence_to_generator_max(patched_store):
    # Pin numerical identity with the OLD generator-max formulation so a
    # future refactor can't silently change the value.
    rng = np.random.default_rng(42)
    active = {f"m{i}": rng.standard_normal(8) for i in range(20)}
    prop_vec = rng.standard_normal(8)
    seed = {**active, "prop": prop_vec}
    cache = _seed_cache(seed)
    patched_store(list(active.keys()))

    lookup = lifecycle._make_active_embedding_lookup()
    got = lookup({"id": "prop"})

    # Reconstruct the old generator-max over the SAME normalized vectors.
    pv = cache.get("prop")
    expected = max(float(np.dot(pv, cache.get(mid))) for mid in active)
    assert got == pytest.approx(expected, abs=1e-6)


def test_empty_active_store_returns_zero(patched_store):
    _seed_cache({"prop": [1.0, 0.0, 0.0]})
    patched_store([])  # no active memories
    lookup = lifecycle._make_active_embedding_lookup()
    assert lookup({"id": "prop"}) == 0.0


def test_active_ids_with_no_cached_vectors_returns_zero(patched_store):
    # Active IDs exist but none are in the embedding cache → no matrix.
    _seed_cache({"prop": [1.0, 0.0, 0.0]})
    patched_store(["ghost1", "ghost2"])
    lookup = lifecycle._make_active_embedding_lookup()
    assert lookup({"id": "prop"}) == 0.0


def test_proposal_without_id_returns_zero(patched_store):
    _seed_cache({"m1": [1.0, 0.0, 0.0]})
    patched_store(["m1"])
    lookup = lifecycle._make_active_embedding_lookup()
    assert lookup({}) == 0.0


def test_proposal_vector_missing_returns_zero(patched_store):
    # Proposal id present but not embedded → lookup returns 0, no crash.
    _seed_cache({"m1": [1.0, 0.0, 0.0]})
    patched_store(["m1"])
    lookup = lifecycle._make_active_embedding_lookup()
    assert lookup({"id": "never_embedded"}) == 0.0


def test_snapshot_isolated_from_later_cache_mutation(patched_store):
    # np.stack copies the row views, so mutating the cache after the lookup
    # is built must not change results — the lookup holds its own snapshot
    # of the active set at build time.  (Asserts the matmul-version behavior;
    # only valid once the production diff is applied.)
    cache = _seed_cache({
        "m1": [1.0, 0.0, 0.0],
        "prop": [1.0, 0.0, 0.0],
    })
    patched_store(["m1"])
    lookup = lifecycle._make_active_embedding_lookup()
    # Overwrite m1 with an orthogonal vector AFTER building the lookup.
    cache.put("m1", _unit([0.0, 1.0, 0.0], cache._dim))
    # Snapshot still reflects the original m1 → cosine 1.0, not 0.0.
    assert lookup({"id": "prop"}) == pytest.approx(1.0, abs=1e-5)
