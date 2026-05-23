"""
Tests for app.utils.memory_lifecycle (Diff 7).

Two layers of coverage:

  - Pure decision functions (_evaluate_promotion, _evaluate_archival)
    tested with synthetic proposal dicts.  No I/O.

  - run_lifecycle_pass end-to-end with tmp_path-backed stores.  Verifies
    that promotion and archival actually transition state in the
    proposals jsonl and create active-store memories.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.utils.memory_lifecycle import (
    _evaluate_promotion,
    _evaluate_archival,
    _proposal_age,
    _has_response_match_signal,
    run_lifecycle_pass,
    ARCHIVAL_AGE_THRESHOLD,
    REDUNDANCY_THRESHOLD,
)


# -- Pure decision functions ---------------------------------------------

def _proposal(corroborations=0, layer="domain_context",
              activity_count_at_proposal=0, signals=None,
              content="some content", pid="prop_abc"):
    """Build a synthetic proposal dict matching the ProposalsStore projection."""
    return {
        "id": pid,
        "content": content,
        "layer": layer,
        "corroborations": corroborations,
        "activity_count_at_proposal": activity_count_at_proposal,
        "signals": signals or [],
    }


class TestProposalAge:

    def test_age_is_counter_difference(self):
        p = _proposal(activity_count_at_proposal=10)
        assert _proposal_age(p, current_counter=15) == 5

    def test_age_floors_at_zero(self):
        """If somehow current < proposal counter, age is 0 not negative."""
        p = _proposal(activity_count_at_proposal=20)
        assert _proposal_age(p, current_counter=10) == 0

    def test_missing_counter_treated_as_zero(self):
        p = {"id": "prop_x"}  # No activity_count_at_proposal
        assert _proposal_age(p, current_counter=5) == 5


class TestSignalDetection:

    def test_no_signals(self):
        p = _proposal(signals=[])
        assert _has_response_match_signal(p) is False

    def test_response_match_present(self):
        p = _proposal(signals=[{"name": "response_match", "ts": 123}])
        assert _has_response_match_signal(p) is True

    def test_other_signal_only(self):
        p = _proposal(signals=[{"name": "something_else", "ts": 123}])
        assert _has_response_match_signal(p) is False

    def test_mixed_signals(self):
        p = _proposal(signals=[
            {"name": "other", "ts": 1},
            {"name": "response_match", "ts": 2},
        ])
        assert _has_response_match_signal(p) is True

    def test_missing_signals_key(self):
        p = {"id": "x"}  # No signals key at all
        assert _has_response_match_signal(p) is False


class TestEvaluatePromotion:
    """Promotion rules in priority order:
    1. corroborations >= 1 AND response_match -> 'corroborated_and_used'
    2. corroborations >= 2 -> 'highly_corroborated'
    3. layer == 'reference' AND response_match -> 'reference_used'
    """

    def test_no_signals_no_promotion(self):
        p = _proposal(corroborations=0, signals=[])
        assert _evaluate_promotion(p) is None

    def test_corroborated_and_used_promotes(self):
        p = _proposal(corroborations=1,
                      signals=[{"name": "response_match"}])
        assert _evaluate_promotion(p) == "corroborated_and_used"

    def test_corroboration_alone_doesnt_promote_at_one(self):
        """One corroboration without use is not enough."""
        p = _proposal(corroborations=1, signals=[])
        assert _evaluate_promotion(p) is None

    def test_two_corroborations_promote_without_use(self):
        """3 distinct conversations (originator + 2 in corroborated_by)
        graduate even without explicit use signal."""
        p = _proposal(corroborations=2, signals=[])
        assert _evaluate_promotion(p) == "highly_corroborated"

    def test_reference_with_use_promotes(self):
        """Reference layer has a lower bar: one use is enough."""
        p = _proposal(corroborations=0, layer="reference",
                      signals=[{"name": "response_match"}])
        assert _evaluate_promotion(p) == "reference_used"

    def test_reference_without_use_doesnt_promote(self):
        p = _proposal(corroborations=0, layer="reference", signals=[])
        assert _evaluate_promotion(p) is None

    def test_highest_priority_wins(self):
        """A proposal that matches multiple rules gets the highest-priority reason."""
        p = _proposal(corroborations=3, layer="reference",
                      signals=[{"name": "response_match"}])
        # Both rule 1 (corroborated_and_used) and rule 2 (highly_corroborated)
        # match, but rule 1 is checked first per the function's order.
        assert _evaluate_promotion(p) == "corroborated_and_used"


class TestEvaluateArchival:

    def test_young_proposal_never_archived(self):
        """Even a useless proposal doesn't archive while young."""
        p = _proposal(corroborations=0, signals=[],
                      activity_count_at_proposal=5)
        # Just under threshold
        result = _evaluate_archival(p, current_counter=5 + ARCHIVAL_AGE_THRESHOLD - 1,
                                     active_embedding_lookup=lambda _: 0.0)
        assert result is None

    def test_decayed_proposal_archives(self):
        """Old proposal with no signals: decay."""
        p = _proposal(corroborations=0, signals=[],
                      activity_count_at_proposal=0)
        result = _evaluate_archival(p, current_counter=ARCHIVAL_AGE_THRESHOLD,
                                     active_embedding_lookup=lambda _: 0.0)
        assert result == "decayed"

    def test_old_proposal_with_corroboration_not_decayed(self):
        """Old but partly-corroborated: only redundancy can archive it now."""
        p = _proposal(corroborations=1, signals=[],
                      activity_count_at_proposal=0)
        result = _evaluate_archival(p, current_counter=ARCHIVAL_AGE_THRESHOLD,
                                     active_embedding_lookup=lambda _: 0.0)
        # Not decayed (has corroboration), and active store has no
        # near-duplicate, so stays probationary.
        assert result is None

    def test_redundant_proposal_archives(self):
        """Old proposal whose content matches existing active memory: redundant."""
        p = _proposal(corroborations=1, signals=[],
                      activity_count_at_proposal=0)
        result = _evaluate_archival(p, current_counter=ARCHIVAL_AGE_THRESHOLD,
                                     active_embedding_lookup=lambda _: REDUNDANCY_THRESHOLD + 0.01)
        assert result is not None and "redundant" in result

    def test_below_redundancy_threshold_doesnt_archive(self):
        p = _proposal(corroborations=1, signals=[],
                      activity_count_at_proposal=0)
        result = _evaluate_archival(p, current_counter=ARCHIVAL_AGE_THRESHOLD,
                                     active_embedding_lookup=lambda _: REDUNDANCY_THRESHOLD - 0.01)
        assert result is None


# -- run_lifecycle_pass end-to-end --------------------------------------

class TestRunLifecyclePass:
    """Integration tests using tmp_path-backed stores."""

    @pytest.mark.asyncio
    async def test_no_open_proposals_returns_zero_counts(self, tmp_path):
        from app.storage.proposals import ProposalsStore
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")

        with patch("app.storage.proposals.get_proposals_store", return_value=proposals):
            result = await run_lifecycle_pass()

        assert result == {"scanned": 0, "promoted": 0, "archived": 0, "noop": 0}

    @pytest.mark.asyncio
    async def test_promotes_corroborated_and_used_proposal(self, tmp_path):
        """A proposal with corroboration AND a response_match signal
        should be promoted to the active memory store."""
        from app.storage.memory import MemoryStorage
        from app.storage.proposals import ProposalsStore
        from app.models.memory import MemoryProposal

        store = MemoryStorage(memory_dir=tmp_path / "memory")
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")

        # Create a proposal, then corroborate + signal it
        p = MemoryProposal(
            content="Architectural fact worth promoting",
            layer="architecture",
            tags=["arch"],
            conversation_id="conv-1",
        )
        with patch("app.services.embedding_service.embed_and_cache"):
            pid = proposals.add(p, activity_count=0)
        # Corroboration from a different conversation
        proposals._append({
            "kind": "corroborate",
            "id": pid,
            "ts": 1000,
            "conversation_id": "conv-2",
        })
        proposals.record_signal(pid, name="response_match", value={"score": 0.7})

        with patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.utils.memory_extractor._next_activity_count", return_value=1), \
             patch("app.services.embedding_service.get_embedding_cache",
                    return_value=MagicMock(get=MagicMock(return_value=None))), \
             patch("app.services.embedding_service.embed_and_cache"):
            result = await run_lifecycle_pass()

        assert result["promoted"] == 1
        # Active store should now have the promoted memory
        memories = store.list_memories()
        assert len(memories) == 1
        assert memories[0].content == "Architectural fact worth promoting"
        assert memories[0].learned_from == "promoted_from_proposal"
        assert memories[0].corroborations == 1
        # Proposal should be marked promoted (not in list_open anymore)
        assert len(proposals.list_open()) == 0

    @pytest.mark.asyncio
    async def test_archives_decayed_proposal(self, tmp_path):
        """An old proposal with no signals should be archived."""
        from app.storage.memory import MemoryStorage
        from app.storage.proposals import ProposalsStore
        from app.models.memory import MemoryProposal

        store = MemoryStorage(memory_dir=tmp_path / "memory")
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")

        # Old proposal at counter 0
        p = MemoryProposal(content="No one cares about this fact",
                            layer="domain_context")
        with patch("app.services.embedding_service.embed_and_cache"):
            proposals.add(p, activity_count=0)

        # Mock current counter at 10 (well past ARCHIVAL_AGE_THRESHOLD=7)
        # Mock the file read to return 10
        with patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.services.embedding_service.get_embedding_cache",
                    return_value=MagicMock(get=MagicMock(return_value=None))), \
             patch("app.utils.memory_lifecycle.open", create=True) as mock_open:
            # Mock the activity-counter file read
            mock_open.return_value.__enter__.return_value.read.return_value = (
                '{"count": 10}'
            )
            # Use json.loads via the mock — easier to patch json.load directly
            with patch("json.load", return_value={"count": 10}):
                with patch("pathlib.Path.exists", return_value=True):
                    result = await run_lifecycle_pass()

        assert result["archived"] == 1
        assert len(proposals.list_open()) == 0

    @pytest.mark.asyncio
    async def test_young_proposal_stays_probationary(self, tmp_path):
        """Young proposal with no signals: no-op."""
        from app.storage.memory import MemoryStorage
        from app.storage.proposals import ProposalsStore
        from app.models.memory import MemoryProposal

        store = MemoryStorage(memory_dir=tmp_path / "memory")
        proposals = ProposalsStore(memory_dir=tmp_path / "memory")

        # Young proposal at counter 0, current counter = 2 (under threshold)
        p = MemoryProposal(content="Recent uncorroborated fact",
                            layer="domain_context")
        with patch("app.services.embedding_service.embed_and_cache"):
            proposals.add(p, activity_count=0)

        with patch("app.storage.proposals.get_proposals_store", return_value=proposals), \
             patch("app.storage.memory.get_memory_storage", return_value=store), \
             patch("app.services.embedding_service.get_embedding_cache",
                    return_value=MagicMock(get=MagicMock(return_value=None))), \
             patch("json.load", return_value={"count": 2}), \
             patch("pathlib.Path.exists", return_value=True):
            result = await run_lifecycle_pass()

        assert result["promoted"] == 0
        assert result["archived"] == 0
        assert result["noop"] == 1
        # Proposal should still be open
        assert len(proposals.list_open()) == 1
