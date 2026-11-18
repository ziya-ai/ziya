"""feedback_daily.json keeps use rates past the 2,000-row stats ring.

The ring held ~1 day (2026-11-18), so a shipped change could not be compared
against the period before it.  Pins: the aggregate is written by the real
apply_feedback path, counts at each threshold, survives the ring trimming,
and is exposed by feedback_stats_summary().
"""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import app.memory.feedback as fb


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "_stats_file", lambda: tmp_path / "feedback_stats.jsonl")
    fb._loaded_per_conversation.clear()


def test_aggregate_counts_by_kind_and_threshold():
    rows = [{"kind": "memory", "best_cos": 0.58}, {"kind": "memory", "best_cos": 0.62},
            {"kind": "memory", "best_cos": 0.75}, {"kind": "proposal", "best_cos": 0.30}]
    fb._record_daily_aggregate(rows, ts=1_794_960_000_000)
    fb._record_daily_aggregate(rows[:1], ts=1_794_960_000_000)
    doc = fb.load_feedback_daily()
    assert len(doc) == 1
    day = next(iter(doc.values()))
    assert day["memory"]["n"] == 4
    assert (day["memory"]["used@0.55"], day["memory"]["used@0.60"], day["memory"]["used@0.70"]) == (4, 2, 1)
    assert day["proposal"]["n"] == 1 and day["proposal"]["used@0.55"] == 0


def test_aggregate_outlives_the_ring(monkeypatch):
    monkeypatch.setattr(fb, "FEEDBACK_STATS_MAX_ROWS", 3)
    for _ in range(4):
        fb._record_feedback_stats([{"kind": "memory", "best_cos": 0.9, "used": True, "memory_id": "m"}] * 2)
    assert len(fb.load_feedback_stats()) == 3
    total = sum(d["memory"]["n"] for d in fb.load_feedback_daily().values())
    assert total == 8


def test_apply_feedback_writes_the_aggregate_and_summary_exposes_it():
    """Seam: the real scoring path must reach the aggregate."""
    vec = np.array([1.0, 0.0])
    provider = MagicMock()
    provider.embed_text.return_value = vec
    cache = MagicMock()
    cache.get.return_value = vec
    fb.record_load("conv-1", ["m_1"])
    with patch("app.services.embedding_service.get_embedding_provider", return_value=provider), \
         patch("app.services.embedding_service.get_embedding_cache", return_value=cache), \
         patch.object(fb, "_apply_updates", lambda *a, **k: None), \
         patch.object(fb, "_score_open_proposals", lambda *a, **k: 0):
        out = fb._apply_feedback_sync("conv-1", "some reply text that matches")
    assert out["used"] == 1
    daily = fb.feedback_stats_summary()["daily"]
    assert sum(d["memory"]["used@0.60"] for d in daily.values()) == 1
