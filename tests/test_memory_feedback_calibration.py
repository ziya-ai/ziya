"""
Seam tests for the retrieval-feedback "used" signal made measurable.

Background (memory audit, Sep 2026): on the live store the `response_match`
signal fired on 2 of 29 loaded memories.  Every layer above it — lifecycle
promotion rules 1/3/3b, the importance bump, the labile window — is inert
when it does not fire.  Two things were wrong with how it was computed:

  1. A ~300-char fact was compared against 800-char response windows, so
     the paraphrase that would match it was diluted by 500 chars of
     unrelated text in the same window.
  2. Nothing recorded the best cosine each loaded memory actually achieved,
     so the threshold (0.55) could not be calibrated from data.

These tests pin the fix:
  - windows are sentence-scale and their count is bounded;
  - every scored memory writes a stats row (memory id, best cosine, used);
  - the stats file is bounded and summarizable with percentiles;
  - the threshold is overridable via ZIYA_MEMORY_USE_THRESHOLD and the
    override is honoured at call time, not import time;
  - the API exposes the summary.
"""
import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import app.memory.feedback as fb
from app.memory.feedback import (
    _windowize, _WINDOW_CHARS, _MAX_WINDOWS,
    apply_feedback, record_load, _loaded_per_conversation,
    load_feedback_stats, feedback_stats_summary, FEEDBACK_STATS_MAX_ROWS,
)


@pytest.fixture(autouse=True)
def _isolated_stats(tmp_path, monkeypatch):
    """Point the stats file at tmp so tests never touch ~/.ziya."""
    monkeypatch.setattr(fb, "_stats_file", lambda: tmp_path / "feedback_stats.jsonl")
    _loaded_per_conversation.clear()
    yield
    _loaded_per_conversation.clear()


def _unit(*xs):
    v = np.array(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


# ── Windowing ──────────────────────────────────────────────────────────

class TestSentenceScaleWindows:

    def test_default_window_is_fact_scale(self):
        # Live store: average memory content is ~300 chars.  A window much
        # larger than the facts it is compared against dilutes the match.
        assert _WINDOW_CHARS <= 400

    def test_long_text_uses_default_size(self):
        text = "x" * 5000
        windows = _windowize(text)
        assert all(len(w) <= _WINDOW_CHARS for w in windows)
        assert len(windows) > 5000 // _WINDOW_CHARS

    def test_window_count_is_bounded(self):
        # A 200k-char response must not become 1000+ embedding calls.
        text = "y" * 200_000
        windows = _windowize(text)
        assert len(windows) <= _MAX_WINDOWS
        # ...and still covers the tail of the text.
        assert text[-1] in windows[-1]

    def test_short_text_single_window(self):
        assert _windowize("short") == ["short"]


# ── Stats recording ────────────────────────────────────────────────────

def _run_feedback(store, mem_vec, window_vec, response="a response", conv="c1"):
    provider = MagicMock()
    provider.embed_text.return_value = window_vec
    cache = MagicMock()
    cache.get.return_value = mem_vec
    with patch("app.services.embedding_service.get_embedding_provider", return_value=provider), \
         patch("app.services.embedding_service.get_embedding_cache", return_value=cache), \
         patch("app.storage.memory.get_memory_storage", return_value=store), \
         patch("app.storage.proposals.get_proposals_store",
               return_value=MagicMock(list_open=MagicMock(return_value=[]))):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            apply_feedback(conv, response))


class TestStatsRecorded:

    @pytest.mark.asyncio
    async def test_each_loaded_memory_writes_a_row(self, tmp_path):
        from app.storage.memory import MemoryStorage
        from app.models.memory import Memory
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        m1 = Memory(content="fact one"); m2 = Memory(content="fact two")
        store.save(m1); store.save(m2)
        record_load("c1", [m1.id, m2.id])

        provider = MagicMock(); provider.embed_text.return_value = _unit(1, 0, 0)
        cache = MagicMock()
        # m1 identical (cos 1.0, used); m2 orthogonal (cos 0.0, unused)
        cache.get.side_effect = lambda mid: _unit(1, 0, 0) if mid == m1.id else _unit(0, 1, 0)
        with patch("app.services.embedding_service.get_embedding_provider", return_value=provider), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.storage.proposals.get_proposals_store",
                   return_value=MagicMock(list_open=MagicMock(return_value=[]))):
            result = await apply_feedback("c1", "some response text")

        assert result["used"] == 1
        rows = load_feedback_stats()
        by_id = {r["memory_id"]: r for r in rows}
        assert set(by_id) == {m1.id, m2.id}
        assert by_id[m1.id]["used"] is True and by_id[m1.id]["best_cos"] == pytest.approx(1.0)
        assert by_id[m2.id]["used"] is False and by_id[m2.id]["best_cos"] == pytest.approx(0.0)
        assert by_id[m1.id]["kind"] == "memory"
        assert by_id[m1.id]["threshold"] == pytest.approx(fb._resolve_use_threshold())

    def test_stats_file_is_bounded(self):
        for i in range(FEEDBACK_STATS_MAX_ROWS + 50):
            fb._record_feedback_stats([{"memory_id": f"m{i}", "best_cos": 0.1,
                                        "used": False, "kind": "memory"}])
        rows = load_feedback_stats()
        assert len(rows) == FEEDBACK_STATS_MAX_ROWS
        # Newest retained, oldest dropped.
        assert rows[-1]["memory_id"] == f"m{FEEDBACK_STATS_MAX_ROWS + 49}"
        assert rows[0]["memory_id"] == "m50"

    def test_stats_write_failure_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(fb, "_stats_file", lambda: 1 / 0)
        fb._record_feedback_stats([{"memory_id": "m", "best_cos": 0.5, "used": False}])


# ── Summary ────────────────────────────────────────────────────────────

class TestSummary:

    def test_empty_summary(self):
        s = feedback_stats_summary()
        assert s["count"] == 0
        assert s["threshold"] == pytest.approx(fb._resolve_use_threshold())

    def test_percentiles_and_hit_rate(self):
        cos = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        fb._record_feedback_stats([
            {"memory_id": f"m{i}", "best_cos": c, "used": c >= 0.55, "kind": "memory"}
            for i, c in enumerate(cos)
        ])
        s = feedback_stats_summary()
        assert s["count"] == 10
        assert s["used"] == 5
        assert s["hit_rate"] == pytest.approx(0.5)
        assert s["p50"] == pytest.approx(0.55, abs=0.06)
        assert s["p90"] == pytest.approx(0.91, abs=0.06)
        assert s["max"] == pytest.approx(1.0)
        # Per-kind breakdown so proposal and memory signals can be tuned apart.
        assert s["by_kind"]["memory"]["count"] == 10


# ── Threshold override ─────────────────────────────────────────────────

class TestThresholdOverride:

    @pytest.mark.asyncio
    async def test_env_override_applies_at_call_time(self, tmp_path, monkeypatch):
        from app.storage.memory import MemoryStorage
        from app.models.memory import Memory
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        m = Memory(content="fact"); store.save(m)

        # cos(mem, window) = 0.6 — above 0.55 default, below an 0.8 override.
        mem_vec = _unit(1, 0); win_vec = _unit(0.6, 0.8)
        provider = MagicMock(); provider.embed_text.return_value = win_vec
        cache = MagicMock(); cache.get.return_value = mem_vec

        monkeypatch.setenv("ZIYA_MEMORY_USE_THRESHOLD", "0.8")
        record_load("c1", [m.id])
        with patch("app.services.embedding_service.get_embedding_provider", return_value=provider), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.storage.proposals.get_proposals_store",
                   return_value=MagicMock(list_open=MagicMock(return_value=[]))):
            strict = await apply_feedback("c1", "resp")
        assert strict["used"] == 0

        monkeypatch.setenv("ZIYA_MEMORY_USE_THRESHOLD", "0.5")
        record_load("c2", [m.id])
        with patch("app.services.embedding_service.get_embedding_provider", return_value=provider), \
             patch("app.services.embedding_service.get_embedding_cache", return_value=cache), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.storage.proposals.get_proposals_store",
                   return_value=MagicMock(list_open=MagicMock(return_value=[]))):
            lenient = await apply_feedback("c2", "resp")
        assert lenient["used"] == 1

    def test_explicit_argument_still_wins(self, monkeypatch):
        monkeypatch.setenv("ZIYA_MEMORY_USE_THRESHOLD", "0.9")
        assert fb._resolve_use_threshold(0.3) == 0.3
        assert fb._resolve_use_threshold(None) == pytest.approx(0.9)


# ── API ────────────────────────────────────────────────────────────────

class TestApi:

    def test_feedback_stats_endpoint(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.memory import router
        fb._record_feedback_stats([{"memory_id": "m1", "best_cos": 0.7,
                                    "used": True, "kind": "memory"}])
        app = FastAPI(); app.include_router(router)
        resp = TestClient(app).get("/api/v1/memory/feedback/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1 and body["used"] == 1
        assert "threshold" in body and "p50" in body
