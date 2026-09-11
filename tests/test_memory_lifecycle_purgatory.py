"""
Seam tests for two lifecycle defects found auditing the live store
(2026-09-03):

1. Self-corroboration.  Extraction re-runs each time a conversation streams
   another turn, re-derives the same fact, hash-matches the open proposal,
   and logs a ``corroborate`` event — from the SAME conversation that
   proposed it.  228 of 234 "corroborated" proposals in the live store had
   been corroborated only by their own conversation.  A corroboration must
   come from a distinct, foreign conversation to count.

2. Purgatory.  Two states never terminate under the promotion/archival
   rules:
     - corroborations == 1, no use signal, age >= threshold  (233 rows,
       ages up to 658 ticks)
     - corroborations == 0, has use signal,  age >= threshold  (13 rows)
   Rule 4 archives only at corroborations == 0 AND no use; rule 5 needs a
   near-duplicate in the active store.  Nothing else fires.  A hard expiry
   is required so no proposal is immortal.

3. ``prune_terminal`` had no callers, so probationary.jsonl grew to 11.9k
   events / 3 MB, every one of which is re-read, decrypted, and rewritten
   on each append.  The lifecycle pass must compact after it archives.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.memory.lifecycle import (
    ARCHIVAL_AGE_THRESHOLD,
    EXPIRY_AGE_THRESHOLD,
    _evaluate_archival,
    _evaluate_promotion,
    run_lifecycle_pass,
)
from app.models.memory import MemoryProposal
from app.storage.memory import MemoryStorage
from app.storage.proposals import ProposalsStore


@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    import app.services.embedding_service as _es
    monkeypatch.setattr(_es, "_provider", None)
    monkeypatch.setattr(_es, "_cache", None)


def _row(corroborations=0, signals=None, age=0, layer="domain_context"):
    return {
        "id": "prop_x",
        "content": "x",
        "layer": layer,
        "corroborations": corroborations,
        "signals": signals or [],
        "activity_count_at_proposal": 0,
        "status": "open",
    }


_USE = [{"name": "response_match", "ts": 1, "value": {"score": 0.7}}]


# ── 1. Corroboration provenance ─────────────────────────────────────────────

class TestCorroborationProvenance:

    def _store(self, tmp_path):
        return ProposalsStore(memory_dir=tmp_path / "memory")

    def test_same_conversation_re_add_does_not_corroborate(self, tmp_path):
        ps = self._store(tmp_path)
        with patch("app.services.embedding_service.embed_and_cache"):
            pid = ps.add(MemoryProposal(content="fact", conversation_id="conv-A"))
            # Same conversation streams another turn; extraction re-derives it.
            ps.add(MemoryProposal(content="fact", conversation_id="conv-A"))
            ps.add(MemoryProposal(content="fact", conversation_id="conv-A"))
        row = ps.get(pid)
        assert row["corroborations"] == 0
        assert row.get("corroborated_by", []) == []

    def test_foreign_conversation_corroborates_once(self, tmp_path):
        ps = self._store(tmp_path)
        with patch("app.services.embedding_service.embed_and_cache"):
            pid = ps.add(MemoryProposal(content="fact", conversation_id="conv-A"))
            ps.add(MemoryProposal(content="fact", conversation_id="conv-B"))
            ps.add(MemoryProposal(content="fact", conversation_id="conv-B"))
        row = ps.get(pid)
        assert row["corroborations"] == 1
        assert row["corroborated_by"] == ["conv-B"]

    def test_distinct_foreign_conversations_each_count(self, tmp_path):
        ps = self._store(tmp_path)
        with patch("app.services.embedding_service.embed_and_cache"):
            pid = ps.add(MemoryProposal(content="fact", conversation_id="conv-A"))
        ps.corroborate_by_id(pid, conversation_id="conv-B")
        ps.corroborate_by_id(pid, conversation_id="conv-C")
        ps.corroborate_by_id(pid, conversation_id="conv-A")   # self — ignored
        row = ps.get(pid)
        assert row["corroborations"] == 2
        assert sorted(row["corroborated_by"]) == ["conv-B", "conv-C"]

    def test_unattributed_corroboration_does_not_count(self, tmp_path):
        """A corroborate event with no conversation_id cannot be shown to be
        independent, so it must not move the counter."""
        ps = self._store(tmp_path)
        with patch("app.services.embedding_service.embed_and_cache"):
            pid = ps.add(MemoryProposal(content="fact", conversation_id="conv-A"))
        ps.corroborate_by_id(pid, conversation_id=None)
        assert ps.get(pid)["corroborations"] == 0


# ── 2. Expiry closes the purgatory states ───────────────────────────────────

class TestExpiry:

    def test_expiry_is_beyond_archival_threshold(self):
        assert EXPIRY_AGE_THRESHOLD > ARCHIVAL_AGE_THRESHOLD

    def test_corroborated_once_unused_still_open_at_archival_age(self):
        """Behaviour preserved: at the archival threshold a partly-corroborated
        proposal is only archived if redundant."""
        r = _evaluate_archival(_row(corroborations=1), ARCHIVAL_AGE_THRESHOLD,
                               lambda _: 0.0)
        assert r is None

    def test_corroborated_once_unused_expires(self):
        r = _evaluate_archival(_row(corroborations=1), EXPIRY_AGE_THRESHOLD,
                               lambda _: 0.0)
        assert r == "expired"

    def test_used_but_uncorroborated_expires(self):
        row = _row(corroborations=0, signals=_USE)
        assert _evaluate_promotion(row) is None          # not promotable
        assert _evaluate_archival(row, ARCHIVAL_AGE_THRESHOLD, lambda _: 0.0) is None
        assert _evaluate_archival(row, EXPIRY_AGE_THRESHOLD, lambda _: 0.0) == "expired"

    def test_promotable_row_is_never_expired_by_archival(self):
        """Promotion is evaluated first in the pass; but archival on its own
        must also refuse to expire something that qualifies for promotion,
        so the two evaluators cannot disagree if call order changes."""
        row = _row(corroborations=2)
        assert _evaluate_promotion(row) is not None
        assert _evaluate_archival(row, EXPIRY_AGE_THRESHOLD * 10, lambda _: 0.0) is None

    def test_malformed_signal_entry_does_not_crash_evaluation(self):
        row = _row(corroborations=1, signals=["response_match", {"name": "other"}])
        assert _evaluate_promotion(row) is None
        assert _evaluate_archival(row, ARCHIVAL_AGE_THRESHOLD, lambda _: 0.0) is None


# ── 3. End-to-end: the pass drains and compacts ─────────────────────────────

class TestPassDrainsAndCompacts:

    @pytest.mark.asyncio
    async def test_pass_expires_immortal_rows(self, tmp_path):
        store = MemoryStorage(memory_dir=tmp_path / "memory")
        ps = ProposalsStore(memory_dir=tmp_path / "memory")
        with patch("app.services.embedding_service.embed_and_cache"):
            a = ps.add(MemoryProposal(content="self-corroborated", conversation_id="c1"),
                       activity_count=0)
            ps.add(MemoryProposal(content="self-corroborated", conversation_id="c1"))
            b = ps.add(MemoryProposal(content="foreign once", conversation_id="c1"),
                       activity_count=0)
            ps.corroborate_by_id(b, conversation_id="c2")
            c = ps.add(MemoryProposal(content="used only", conversation_id="c1"),
                       activity_count=0)
            ps.record_signal(c, name="response_match", value={"score": 0.6})

        with patch("app.storage.proposals.get_proposals_store", return_value=ps), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.memory.lifecycle.current_activity_count",
                   return_value=EXPIRY_AGE_THRESHOLD), \
             patch("app.services.embedding_service.get_embedding_cache",
                   return_value=MagicMock(get=MagicMock(return_value=None))):
            result = await run_lifecycle_pass()

        assert ps.list_open() == []
        assert result["archived"] == 3
        assert result["promoted"] == 0
        assert store.list_memories() == []

    @pytest.mark.asyncio
    async def test_pass_compacts_terminal_records(self, tmp_path, monkeypatch):
        import app.memory.lifecycle as lc
        monkeypatch.setattr(lc, "PRUNE_TERMINAL_MAX", 2)

        store = MemoryStorage(memory_dir=tmp_path / "memory")
        ps = ProposalsStore(memory_dir=tmp_path / "memory")
        with patch("app.services.embedding_service.embed_and_cache"):
            for i in range(5):
                ps.add(MemoryProposal(content=f"stale {i}", conversation_id="c1"),
                       activity_count=0)
        events_before = len(ps._read_lines())

        with patch("app.storage.proposals.get_proposals_store", return_value=ps), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.memory.lifecycle.current_activity_count",
                   return_value=ARCHIVAL_AGE_THRESHOLD), \
             patch("app.services.embedding_service.get_embedding_cache",
                   return_value=MagicMock(get=MagicMock(return_value=None))):
            result = await run_lifecycle_pass()

        assert result["archived"] == 5
        assert result["pruned"] > 0
        terminal = [r for r in ps.list_all() if r["status"] != "open"]
        assert len(terminal) == 2
        # Event log actually shrank on disk, not just in projection.
        assert len(ps._read_lines()) < events_before
