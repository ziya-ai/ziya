"""
Tests for proposal-write-time embedding (Diff 6c).

When ProposalsStore.add() is called, the proposal's content should be
embedded and cached under the proposal's ID, so that downstream
retrieval-feedback (_score_open_proposals) can find it without
re-embedding.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.models.memory import MemoryProposal
from app.storage.proposals import ProposalsStore


class TestProposalEmbeddingOnAdd:

    def test_embed_and_cache_called_on_successful_add(self, tmp_path):
        """A normal add() should call embed_and_cache with the new proposal's
        ID and content."""
        store = ProposalsStore(memory_dir=tmp_path / "memory")
        proposal = MemoryProposal(
            content="OBP enforces 512MB RAM budget for telemetry pipeline",
            layer="architecture",
            tags=["obp", "memory"],
        )

        with patch("app.services.embedding_service.embed_and_cache") as mock_embed:
            pid = store.add(proposal, activity_count=42)

        # embed_and_cache should be called once with (pid, content)
        mock_embed.assert_called_once_with(pid, proposal.content)

    def test_corroboration_does_not_re_embed(self, tmp_path):
        """When the same content is added twice, the second call is a
        corroboration event, not a record event — and should NOT
        trigger another embedding call."""
        store = ProposalsStore(memory_dir=tmp_path / "memory")
        content = "Same content twice"

        with patch("app.services.embedding_service.embed_and_cache") as mock_embed:
            pid1 = store.add(MemoryProposal(content=content, layer="domain_context"))
            pid2 = store.add(MemoryProposal(content=content, layer="domain_context"))

        # Same hash-stable ID
        assert pid1 == pid2
        # Embedding was called for the first add but the second was a
        # corroboration event, so embed shouldn't fire again
        assert mock_embed.call_count == 1

    def test_embed_failure_does_not_raise(self, tmp_path):
        """If embed_and_cache raises (e.g. Bedrock unavailable), the
        proposal should still be added successfully — embedding is
        opportunistic."""
        store = ProposalsStore(memory_dir=tmp_path / "memory")
        proposal = MemoryProposal(
            content="A perfectly valid proposal",
            layer="domain_context",
        )

        with patch("app.services.embedding_service.embed_and_cache",
                    side_effect=RuntimeError("embeddings down")):
            pid = store.add(proposal)

        # Proposal should still be in the store
        assert pid is not None
        opens = store.list_open()
        assert any(p.get("id") == pid for p in opens)

    def test_missing_embed_module_does_not_raise(self, tmp_path):
        """If app.services.embedding_service is unimportable for any
        reason, the proposal add must still succeed — the try/except
        in proposals.py guards this."""
        store = ProposalsStore(memory_dir=tmp_path / "memory")
        proposal = MemoryProposal(content="Survives embedding outage",
                                  layer="domain_context")

        # Patch the module to raise ImportError when imported
        with patch.dict("sys.modules", {"app.services.embedding_service": None}):
            pid = store.add(proposal)

        opens = store.list_open()
        assert any(p.get("id") == pid for p in opens)

    def test_empty_content_raises_before_embedding(self, tmp_path):
        """The existing content-empty guard should prevent both proposal
        creation AND embedding. Embedding should never be attempted on
        empty content."""
        store = ProposalsStore(memory_dir=tmp_path / "memory")
        proposal = MemoryProposal(content="", layer="domain_context")

        with patch("app.services.embedding_service.embed_and_cache") as mock_embed:
            with pytest.raises(ValueError):
                store.add(proposal)

        mock_embed.assert_not_called()


class TestCorroborateById:
    """Tests for ProposalsStore.corroborate_by_id — the paraphrase-path
    corroboration method added to support embedding-dedup sink writes."""

    def test_returns_true_and_increments_for_open_proposal(self, tmp_path):
        """Calling corroborate_by_id on an open proposal returns True and
        the projection shows corroborations incremented by one."""
        store = ProposalsStore(memory_dir=tmp_path / "memory")
        with patch("app.services.embedding_service.embed_and_cache"):
            pid = store.add(MemoryProposal(content="OBP uses 512MB RAM",
                                           layer="architecture"))

        result = store.corroborate_by_id(pid, conversation_id="conv-2")

        assert result is True
        opens = store.list_open()
        assert len(opens) == 1
        assert opens[0]["corroborations"] == 1

    def test_returns_false_for_nonexistent_id(self, tmp_path):
        """A completely unknown proposal ID returns False without raising."""
        store = ProposalsStore(memory_dir=tmp_path / "memory")
        result = store.corroborate_by_id("prop_doesnotexist")
        assert result is False

    def test_returns_false_for_promoted_proposal(self, tmp_path):
        """A proposal that has already been promoted (non-OPEN status)
        should not receive further corroboration events."""
        store = ProposalsStore(memory_dir=tmp_path / "memory")
        with patch("app.services.embedding_service.embed_and_cache"):
            pid = store.add(MemoryProposal(content="Promoted fact",
                                           layer="architecture"))
        store.mark_promoted(pid, target_memory_id="m_sentinel")

        result = store.corroborate_by_id(pid)
        assert result is False

    def test_multiple_calls_accumulate(self, tmp_path):
        """Each call to corroborate_by_id increments the counter independently."""
        store = ProposalsStore(memory_dir=tmp_path / "memory")
        with patch("app.services.embedding_service.embed_and_cache"):
            pid = store.add(MemoryProposal(content="Fact seen many times",
                                           layer="domain_context"))

        store.corroborate_by_id(pid, conversation_id="conv-2")
        store.corroborate_by_id(pid, conversation_id="conv-3")

        opens = store.list_open()
        assert opens[0]["corroborations"] == 2

    def test_conversation_id_stored_in_event(self, tmp_path):
        """The conversation_id passed to corroborate_by_id should appear
        in the raw event log so the audit trail is complete."""
        store = ProposalsStore(memory_dir=tmp_path / "memory")
        with patch("app.services.embedding_service.embed_and_cache"):
            pid = store.add(MemoryProposal(content="Traceable fact",
                                           layer="domain_context"))

        store.corroborate_by_id(pid, conversation_id="conv-audit")

        # The projection accumulates conversation_ids in corroborated_by
        opens = store.list_open()
        assert "conv-audit" in opens[0].get("corroborated_by", [])
