"""Contradiction signal: writer, arbiter, and the lifecycle veto seam.

`lifecycle._is_fast_track_eligible` has always consulted a ``contradicted``
signal, but nothing ever wrote one, so the veto was inert: a
fast-track-eligible architecture/decision proposal promoted on quality
alone even after a later conversation reversed it.

Worse than merely absent.  A contradiction is SEMANTICALLY NEAR-IDENTICAL
to the statement it contradicts -- "the threshold is 0.75" and "the
threshold was lowered to 0.60" sit far above the 0.88 cosine dedup
threshold -- so the proposal-paraphrase branch in ``deduplicate`` treated
the correction as agreement: it CORROBORATED the superseded proposal and
DISCARDED the correction.  Contradicting a pending fact made it more
likely to promote and threw the fix away.

These tests pin all three parts:
  1. the store->lifecycle seam (a written signal actually vetoes),
  2. the LLM arbiter that separates AGREES from CONTRADICTS,
  3. the dedup branch deferring the agree/contradict decision instead of
     assuming agreement.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.memory.lifecycle import (
    FAST_TRACK_MIN_AGE,
    _evaluate_promotion,
    _is_fast_track_eligible,
)
from app.memory.extractor import deduplicate
from app.models.memory import MemoryProposal
from app.storage.proposals import ProposalsStore


def _fast_track_proposal(**overrides):
    """A proposal that qualifies for quality-only promotion."""
    base = {
        "content": "The fast-track promotion threshold is 0.75 composite quality.",
        "layer": "architecture",
        "quality": 0.9,
        "tags": ["memory", "promotion"],
    }
    base.update(overrides)
    return MemoryProposal(**base)


class TestSignalVetoSeam:
    """The signal the store writes must be the signal lifecycle reads.

    This is the seam a wiring change is most likely to miss: record_signal
    appends an EVENT_SIGNAL row, the projection folds it into a ``signals``
    list, and `_has_signal` reads that list.  A shape mismatch anywhere in
    that chain makes the veto silently no-op.
    """

    def test_written_signal_blocks_fast_track_promotion(self):
        with tempfile.TemporaryDirectory() as td:
            store = ProposalsStore(memory_dir=Path(td))
            pid = store.add(_fast_track_proposal(), activity_count=0)

            # POSITIVE CONTROL: without the signal this proposal really
            # does fast-track.  Without this half, the assertion below
            # could pass for the wrong reason (e.g. wrong layer).
            before = store.get(pid)
            assert _is_fast_track_eligible(before) is True
            assert _evaluate_promotion(
                before, current_counter=FAST_TRACK_MIN_AGE + 1
            ) == "quality_fast_track"

            assert store.record_signal(pid, "contradicted") is True

            after = store.get(pid)
            assert _is_fast_track_eligible(after) is False
            assert _evaluate_promotion(
                after, current_counter=FAST_TRACK_MIN_AGE + 1
            ) != "quality_fast_track"

    def test_unrelated_signal_does_not_veto(self):
        """Only ``contradicted`` vetoes -- a search_hit must not."""
        with tempfile.TemporaryDirectory() as td:
            store = ProposalsStore(memory_dir=Path(td))
            pid = store.add(_fast_track_proposal(), activity_count=0)
            store.record_signal(pid, "search_hit")
            assert _is_fast_track_eligible(store.get(pid)) is True


class TestContradictionArbiter:
    """The LLM arbiter that separates a restatement from a reversal."""

    @pytest.mark.asyncio
    async def test_model_contradiction_is_reported(self):
        from app.memory.comparator import classify_proposal_relation

        with patch("app.services.model_resolver.call_service_model",
                   new=AsyncMock(return_value='{"relation": "CONTRADICTS"}')):
            relation = await classify_proposal_relation(
                {"content": "The threshold was lowered to 0.60.",
                 "layer": "architecture"},
                {"content": "The threshold is 0.75.", "layer": "architecture"},
            )
        assert relation == "CONTRADICTS"

    @pytest.mark.asyncio
    async def test_agreement_is_reported(self):
        from app.memory.comparator import classify_proposal_relation

        with patch("app.services.model_resolver.call_service_model",
                   new=AsyncMock(return_value='{"relation": "AGREES"}')):
            relation = await classify_proposal_relation(
                {"content": "Threshold: 0.75 composite.", "layer": "architecture"},
                {"content": "The threshold is 0.75.", "layer": "architecture"},
            )
        assert relation == "AGREES"

    @pytest.mark.asyncio
    async def test_model_failure_fails_open_to_agrees(self):
        """A model or network failure must never manufacture a veto.

        Failing open to AGREES reproduces the pre-existing
        corroborate-and-drop behaviour exactly, so an outage degrades to
        the old semantics rather than blocking every promotion.
        """
        from app.memory.comparator import classify_proposal_relation

        with patch("app.services.model_resolver.call_service_model",
                   new=AsyncMock(side_effect=RuntimeError("endpoint down"))):
            relation = await classify_proposal_relation(
                {"content": "anything", "layer": "architecture"},
                {"content": "anything else", "layer": "architecture"},
            )
        assert relation == "AGREES"

    @pytest.mark.asyncio
    async def test_unparseable_response_fails_open_to_agrees(self):
        from app.memory.comparator import classify_proposal_relation

        with patch("app.services.model_resolver.call_service_model",
                   new=AsyncMock(return_value="I think they disagree, maybe?")):
            relation = await classify_proposal_relation(
                {"content": "a", "layer": "architecture"},
                {"content": "b", "layer": "architecture"},
            )
        assert relation == "AGREES"


def _embedding_patches(matched_id, score):
    """Force the embedding dedup path to report one match."""
    import numpy as np
    provider = MagicMock()
    provider.embed_text.return_value = np.array([1.0, 0.0])
    cache = MagicMock()
    cache.search.return_value = [(matched_id, score)]
    return (
        patch("app.services.embedding_service.get_embedding_provider",
              return_value=provider),
        patch("app.services.embedding_service.get_embedding_cache",
              return_value=cache),
    )


class TestDedupDefersProposalDecision:
    """``deduplicate`` must stop assuming a proposal match means agreement."""

    CANDIDATE = {"content": "The threshold was lowered to 0.60.",
                 "layer": "architecture", "tags": ["promotion"]}
    EXISTING = [{"content": "Unrelated existing memory", "tags": []}]

    def test_match_sink_defers_and_keeps_candidate(self):
        """With the sink supplied: candidate SURVIVES, match recorded, and
        no corroboration is written on the mere basis of similarity."""
        match_sink: list = []
        corr_sink: list = []
        p1, p2 = _embedding_patches("prop_abc123", 0.95)
        with p1, p2:
            result = deduplicate(
                [dict(self.CANDIDATE)], self.EXISTING,
                proposal_corroboration_sink=corr_sink,
                proposal_match_sink=match_sink,
            )

        # The correction is no longer discarded before anyone judges it.
        assert len(result) == 1
        assert result[0]["content"] == self.CANDIDATE["content"]
        # The match is handed to the arbiter, with the candidate object
        # itself so the caller can drop it if the verdict is AGREES.
        assert len(match_sink) == 1
        assert match_sink[0]["proposal_id"] == "prop_abc123"
        assert match_sink[0]["candidate"]["content"] == self.CANDIDATE["content"]
        # Corroboration is the arbiter's call now, not similarity's.
        assert corr_sink == []

    def test_without_match_sink_behaviour_is_unchanged(self):
        """Backward compatibility: every existing caller and test that does
        not pass the new sink keeps corroborate-and-drop."""
        corr_sink: list = []
        p1, p2 = _embedding_patches("prop_abc123", 0.95)
        with p1, p2:
            result = deduplicate(
                [dict(self.CANDIDATE)], self.EXISTING,
                proposal_corroboration_sink=corr_sink,
            )
        assert result == []
        assert corr_sink == ["prop_abc123"]

    def test_active_memory_match_does_not_populate_match_sink(self):
        """An m_* match is the active-memory corroboration path and must not
        be routed to the contradiction arbiter."""
        match_sink: list = []
        active_sink: list = []
        p1, p2 = _embedding_patches("m_active001", 0.95)
        with p1, p2:
            deduplicate(
                [dict(self.CANDIDATE)], self.EXISTING,
                corroboration_sink=active_sink,
                proposal_match_sink=match_sink,
            )
        assert "m_active001" in active_sink
        assert match_sink == []
