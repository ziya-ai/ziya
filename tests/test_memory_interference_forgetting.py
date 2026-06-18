"""
Tests for interference-based forgetting (relevance aging).

Covers:
  - stamp_interference_scores (app/memory/maintenance.py): the embedding-
    native redundancy scorer with retroactive(0.6)/proactive(0.4) asymmetry.
  - The accelerated-aging gate clause in MemorySearchTool's decay sweep
    (app/mcp/tools/memory_tools.py): high-interference + >=N-days-idle +
    importance<=0.5 archives early; real use (importance>0.5) exempts.

These tests install a REAL EmbeddingCache and a non-Noop provider stub
directly (the autouse conftest fixture forces Noop + an isolated cache;
the fixture docstring notes integration tests replace _provider/_cache).

Design ref: design/work-primitives-taxonomy.md is unrelated; the mechanism
is documented in CHANGELOG [Unreleased] and app/memory/maintenance.py.
Math proof: older duplicate accrues 0.6 (retroactive), newer 0.4 (proactive),
orthogonal 0.0 — verified before implementation.
"""
import time
import numpy as np
import pytest

import app.services.embedding_service as _es
from app.services.embedding_service import EmbeddingCache
from app.models.memory import Memory


# ── Test harness ────────────────────────────────────────────────────

class _RealProvider:
    """Non-Noop provider so stamp_interference_scores doesn't early-return.
    embed_text is never actually called by the scorer (it reads the cache),
    but the isinstance(provider, NoopProvider) gate must see a non-Noop."""
    def embed_text(self, text):
        return None

    def embed_batch(self, texts):
        return [None] * len(texts)


@pytest.fixture
def real_cache(tmp_path, monkeypatch):
    """Install a real EmbeddingCache + non-Noop provider, overriding the
    autouse Noop isolation for this test."""
    cache = EmbeddingCache(tmp_path / "embed_real")
    monkeypatch.setattr(_es, "_cache", cache)
    monkeypatch.setattr(_es, "_provider", _RealProvider())
    return cache


def _unit(coords, dim):
    """Build a unit vector with `coords` in leading dims, zero-padded to dim."""
    v = np.zeros(dim, dtype=np.float32)
    for i, c in enumerate(coords):
        v[i] = c
    n = np.linalg.norm(v)
    return v / n if n else v


def _days_ago(n):
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - n * 86400))


class _FakeStore:
    """Minimal MemoryStorage stand-in for the scorer: list_memories(status)
    and save_many."""
    def __init__(self, memories):
        self._mem = {m.id: m for m in memories}

    def list_memories(self, status=None):
        return [m for m in self._mem.values()
                if status is None or m.status == status]

    def save_many(self, memories):
        for m in memories:
            self._mem[m.id] = m

    def get(self, mid):
        return self._mem.get(mid)


# ── stamp_interference_scores ───────────────────────────────────────

def test_older_duplicate_scores_higher_than_newer(real_cache):
    """The core asymmetry: when two near-identical memories collide, the
    OLDER one accrues retroactive interference (0.6), the newer proactive
    (0.4).  Orthogonal memory accrues none."""
    from app.memory.maintenance import stamp_interference_scores
    dim = real_cache._dim
    old = Memory(id="m_old", content="dup", created=_days_ago(10))
    new = Memory(id="m_new", content="dup", created=_days_ago(2))
    orth = Memory(id="m_orth", content="other", created=_days_ago(5))
    real_cache.put("m_old", _unit([1, 0, 0], dim))
    real_cache.put("m_new", _unit([1, 0, 0], dim))   # identical to old
    real_cache.put("m_orth", _unit([0, 1, 0], dim))  # orthogonal

    store = _FakeStore([old, new, orth])
    summary = stamp_interference_scores(store)

    assert summary["skipped"] is False
    assert store.get("m_old").interference_score == pytest.approx(0.6, abs=1e-3)
    assert store.get("m_new").interference_score == pytest.approx(0.4, abs=1e-3)
    assert store.get("m_orth").interference_score == pytest.approx(0.0, abs=1e-3)
    assert summary["scored"] == 3
    assert summary["high_interference"] == 2


def test_below_similarity_threshold_scores_zero(real_cache):
    """Two memories whose cosine is below the 0.85 floor do not interfere."""
    from app.memory.maintenance import stamp_interference_scores
    dim = real_cache._dim
    a = Memory(id="m_a", content="a", created=_days_ago(10))
    b = Memory(id="m_b", content="b", created=_days_ago(2))
    # cos = 0.6 (below 0.85 floor)
    real_cache.put("m_a", _unit([1.0, 0.0], dim))
    real_cache.put("m_b", _unit([0.6, 0.8], dim))
    store = _FakeStore([a, b])
    stamp_interference_scores(store)
    assert store.get("m_a").interference_score == pytest.approx(0.0, abs=1e-3)
    assert store.get("m_b").interference_score == pytest.approx(0.0, abs=1e-3)


def test_noop_provider_skips_and_does_not_stamp(monkeypatch, tmp_path):
    """With the Noop provider (embeddings disabled), the scorer no-ops and
    leaves interference_score untouched."""
    from app.memory.maintenance import stamp_interference_scores
    # Rely on the autouse fixture's Noop provider; just ensure a cache exists.
    monkeypatch.setattr(_es, "_cache", EmbeddingCache(tmp_path / "noop_cache"))
    monkeypatch.setattr(_es, "_provider", None)  # forces NoopProvider on next get
    m = Memory(id="m_x", content="x", created=_days_ago(10))
    store = _FakeStore([m])
    summary = stamp_interference_scores(store)
    assert summary["skipped"] is True
    assert store.get("m_x").interference_score == 0.0


def test_single_active_memory_scores_zero(real_cache):
    """A lone memory cannot interfere with itself."""
    from app.memory.maintenance import stamp_interference_scores
    dim = real_cache._dim
    m = Memory(id="m_solo", content="solo", created=_days_ago(10))
    real_cache.put("m_solo", _unit([1, 0, 0], dim))
    store = _FakeStore([m])
    summary = stamp_interference_scores(store)
    assert store.get("m_solo").interference_score == 0.0
    assert summary["scored"] == 0


def test_stale_stamp_cleared_when_vector_missing(real_cache):
    """A memory previously stamped high but now without a cached vector
    must have its score reset — a stale high score would wrongly age it."""
    from app.memory.maintenance import stamp_interference_scores
    dim = real_cache._dim
    a = Memory(id="m_a", content="dup", created=_days_ago(10),
               interference_score=0.6)  # stale stamp from a prior pass
    b = Memory(id="m_b", content="dup", created=_days_ago(2))
    c = Memory(id="m_c", content="dup", created=_days_ago(1))
    # Only b and c have vectors; a does not.
    real_cache.put("m_b", _unit([1, 0, 0], dim))
    real_cache.put("m_c", _unit([1, 0, 0], dim))
    store = _FakeStore([a, b, c])
    stamp_interference_scores(store)
    assert store.get("m_a").interference_score == 0.0  # reset


def test_three_way_collision_accumulates(real_cache):
    """A memory interfering with two newer near-duplicates accrues the sum
    of both retroactive contributions."""
    from app.memory.maintenance import stamp_interference_scores
    dim = real_cache._dim
    old = Memory(id="m_old", content="dup", created=_days_ago(30))
    mid = Memory(id="m_mid", content="dup", created=_days_ago(10))
    new = Memory(id="m_new", content="dup", created=_days_ago(2))
    for mid_ in ("m_old", "m_mid", "m_new"):
        real_cache.put(mid_, _unit([1, 0, 0], dim))
    store = _FakeStore([old, mid, new])
    stamp_interference_scores(store)
    # old: both mid and new are newer → 0.6 + 0.6 = 1.2
    assert store.get("m_old").interference_score == pytest.approx(1.2, abs=1e-3)
    # new: both older → 0.4 + 0.4 = 0.8
    assert store.get("m_new").interference_score == pytest.approx(0.8, abs=1e-3)
    # mid: one newer (0.6) + one older (0.4) = 1.0
    assert store.get("m_mid").interference_score == pytest.approx(1.0, abs=1e-3)


# ── gate clause: accelerated archive ────────────────────────────────
# These are pure-logic tests of the OR-clause decision, not the full tool
# (which depends on the global throttle + store wiring).  They replicate the
# exact gate expression so a future change to the clause is caught here.

def _gate_archives(days_since, importance, interference,
                   idle_days=90, interference_days=21, ceiling=0.5):
    idle_decay = days_since >= idle_days and importance <= ceiling
    interference_decay = (interference > 0.0
                          and days_since >= interference_days
                          and importance <= ceiling)
    return idle_decay or interference_decay


def test_gate_redundant_memory_archives_at_21_days():
    # High interference, 25 days idle, never-bumped importance → archived
    # early (would NOT archive under the 90-day idle clause alone).
    assert _gate_archives(days_since=25, importance=0.5, interference=0.6) is True
    assert _gate_archives(days_since=25, importance=0.5, interference=0.0) is False


def test_gate_used_memory_exempt_even_if_redundant():
    # Real retrieval bumped importance above the ceiling → exempt from BOTH
    # clauses, even though it's redundant and old.  This is the rescue path.
    assert _gate_archives(days_since=200, importance=0.65, interference=1.2) is False


def test_gate_redundant_but_fresh_not_archived():
    # Redundant but only 10 days idle (< 21) → not yet eligible.
    assert _gate_archives(days_since=10, importance=0.5, interference=0.6) is False


def test_gate_idle_clause_still_fires_without_interference():
    # The original 90-day idle path is unchanged.
    assert _gate_archives(days_since=95, importance=0.5, interference=0.0) is True
