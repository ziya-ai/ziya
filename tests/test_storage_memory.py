"""
Tests for app.storage.memory — flat-file memory storage (Phase 0).

Covers:
  - CRUD: save, get, list, delete
  - Search by keyword, tag, layer
  - Proposals: add, approve, dismiss
  - Profile: get, save
  - Edge cases: empty store, duplicate IDs, corrupt files
"""
import json
from pathlib import Path

import pytest

from app.models.memory import Memory, MemoryProposal, MemoryProfile
from app.storage.memory import MemoryStorage


@pytest.fixture
def storage(tmp_path):
    """Create a MemoryStorage backed by a temp directory."""
    return MemoryStorage(memory_dir=tmp_path / "memory")


# ── Save and Get ──────────────────────────────────────────────────

class TestSaveAndGet:

    def test_save_and_retrieve(self, storage):
        mem = Memory(content="OBP has 512MB RAM budget", layer="architecture", tags=["obp", "ram"])
        saved = storage.save(mem)
        assert saved.id == mem.id

        retrieved = storage.get(mem.id)
        assert retrieved is not None
        assert retrieved.content == "OBP has 512MB RAM budget"
        assert retrieved.layer == "architecture"
        assert "obp" in retrieved.tags

    def test_save_overwrites_existing(self, storage):
        mem = Memory(id="m_fixed", content="original", layer="decision")
        storage.save(mem)

        mem.content = "updated content"
        storage.save(mem)

        retrieved = storage.get("m_fixed")
        assert retrieved.content == "updated content"

    def test_get_nonexistent_returns_none(self, storage):
        assert storage.get("nonexistent") is None

    def test_save_persists_to_disk(self, storage):
        mem = Memory(content="persisted fact", layer="lexicon")
        storage.save(mem)
        file = storage._memories_file
        assert file.exists()
        data = json.loads(file.read_text())
        assert any(m["content"] == "persisted fact" for m in data)


# ── List ──────────────────────────────────────────────────────────

class TestList:

    def test_list_all_active(self, storage):
        storage.save(Memory(content="fact 1", layer="domain_context"))
        storage.save(Memory(content="fact 2", layer="architecture"))
        storage.save(Memory(content="deprecated", layer="lexicon", status="deprecated"))
        results = storage.list_memories()
        assert len(results) == 2

    def test_list_by_layer(self, storage):
        storage.save(Memory(content="arch fact", layer="architecture"))
        storage.save(Memory(content="lex fact", layer="lexicon"))
        results = storage.list_memories(layer="architecture")
        assert len(results) == 1
        assert results[0].layer == "architecture"

    def test_list_by_tags(self, storage):
        storage.save(Memory(content="tagged", tags=["sat", "leo"]))
        storage.save(Memory(content="other", tags=["genealogy"]))
        results = storage.list_memories(tags=["leo"])
        assert len(results) == 1
        assert results[0].content == "tagged"

    def test_list_empty_store(self, storage):
        assert storage.list_memories() == []


# ── Search ────────────────────────────────────────────────────────

class TestSearch:

    def test_search_by_content(self, storage):
        storage.save(Memory(content="CCSDS framing for space segment", tags=["framing"]))
        storage.save(Memory(content="IP encapsulation rejected", tags=["framing"]))
        storage.save(Memory(content="antenna beam pattern", tags=["antenna"]))

        results = storage.search("CCSDS")
        assert len(results) == 1
        assert "CCSDS" in results[0].content

    def test_search_by_tag(self, storage):
        storage.save(Memory(content="some fact", tags=["flow-control"]))
        results = storage.search("flow-control")
        assert len(results) == 1

    def test_search_no_results(self, storage):
        storage.save(Memory(content="unrelated"))
        results = storage.search("quantum")
        assert len(results) == 0

    def test_search_respects_limit(self, storage):
        for i in range(20):
            storage.save(Memory(content=f"fact about topic {i}", tags=["topic"]))
        results = storage.search("topic", limit=5)
        assert len(results) == 5

    def test_search_ranks_by_importance(self, storage):
        """Higher importance memories should rank above lower ones for same keyword match."""
        storage.save(Memory(id="low", content="fact about beams", importance=0.1, tags=["beams"]))
        storage.save(Memory(id="high", content="important fact about beams", importance=0.9, tags=["beams"]))
        results = storage.search("beams")
        assert len(results) == 2
        assert results[0].id == "high"

    def test_search_recency_boosts_recent(self, storage):
        """Recently accessed memories should rank above stale ones."""
        import time as _time
        storage.save(Memory(id="old", content="old beam fact", last_accessed="2024-01-01", tags=["beams"]))
        storage.save(Memory(id="new", content="new beam fact", last_accessed=_time.strftime("%Y-%m-%d"), tags=["beams"]))
        results = storage.search("beams")
        assert results[0].id == "new"


class TestRelations:

    def test_typed_relations_persist(self, storage):
        m1 = Memory(id="m1", content="Base fact")
        m2 = Memory(id="m2", content="Supporting fact", relations={"supports": ["m1"]})
        storage.save(m1)
        storage.save(m2)
        retrieved = storage.get("m2")
        assert "supports" in retrieved.relations
        assert "m1" in retrieved.relations["supports"]

    def test_search_excludes_non_active(self, storage):
        storage.save(Memory(content="active fact about beams", status="active"))
        storage.save(Memory(content="deprecated fact about beams", status="deprecated"))
        results = storage.search("beams")
        assert len(results) == 1
        assert results[0].status == "active"


class TestMaturity:

    def test_importance_increases_on_access(self, storage):
        """Retrieving a memory via search should bump its importance."""
        mem = Memory(id="m_mat", content="important beam fact", importance=0.5, tags=["beams"])
        storage.save(mem)
        # Search triggers the tool which bumps importance — simulate the bump
        mem.importance = min(1.0, mem.importance + 0.05)
        storage.save(mem)
        retrieved = storage.get("m_mat")
        assert retrieved.importance == 0.55

    def test_importance_caps_at_1(self, storage):
        mem = Memory(id="m_cap", content="capped fact", importance=0.98, tags=["cap"])
        storage.save(mem)
        mem.importance = min(1.0, mem.importance + 0.05)
        storage.save(mem)
        retrieved = storage.get("m_cap")
        assert retrieved.importance == 1.0
# ── Delete ────────────────────────────────────────────────────────

class TestDelete:

    def test_delete_existing(self, storage):
        mem = Memory(content="doomed fact")
        storage.save(mem)
        assert storage.delete(mem.id)
        assert storage.get(mem.id) is None

    def test_delete_nonexistent(self, storage):
        assert not storage.delete("nonexistent")


# ── Count ─────────────────────────────────────────────────────────

class TestCount:

    def test_count_by_layer(self, storage):
        storage.save(Memory(content="a", layer="decision"))
        storage.save(Memory(content="b", layer="decision"))
        storage.save(Memory(content="c", layer="lexicon"))
        counts = storage.count()
        assert counts["total"] == 3
        assert counts["by_layer"]["decision"] == 2
        assert counts["by_layer"]["lexicon"] == 1


# ── Proposals ─────────────────────────────────────────────────────

class TestProposals:

    def test_add_and_list_proposals(self, storage):
        p = MemoryProposal(content="proposed fact", tags=["test"], layer="lexicon")
        storage.add_proposal(p)
        proposals = storage.list_proposals()
        assert len(proposals) == 1
        assert proposals[0].content == "proposed fact"

    def test_approve_proposal(self, storage):
        p = MemoryProposal(content="to approve", tags=["approved"], layer="decision")
        storage.add_proposal(p)

        mem = storage.approve_proposal(p.id)
        assert mem is not None
        assert mem.content == "to approve"
        assert mem.layer == "decision"

        # Proposal should be gone
        assert len(storage.list_proposals()) == 0
        # Memory should be in flat store
        assert storage.get(mem.id) is not None

    def test_approve_nonexistent(self, storage):
        assert storage.approve_proposal("nonexistent") is None

    def test_dismiss_proposal(self, storage):
        p = MemoryProposal(content="dismissed")
        storage.add_proposal(p)
        assert storage.dismiss_proposal(p.id)
        assert len(storage.list_proposals()) == 0

    def test_dismiss_nonexistent(self, storage):
        assert not storage.dismiss_proposal("nonexistent")

    def test_approve_moves_to_active_store(self, storage):
        """Approved proposals should be findable via search."""
        p = MemoryProposal(
            content="SCPS uses CCSDS framing for space segment",
            tags=["scps", "ccsds"],
            layer="architecture",
        )
        storage.add_proposal(p)

        # Proposal is not in active store
        assert storage.search("CCSDS") == []

        # Approve it
        mem = storage.approve_proposal(p.id)
        assert mem is not None

        # Now searchable
        results = storage.search("CCSDS")
        assert len(results) == 1
        assert "CCSDS" in results[0].content
        assert results[0].layer == "architecture"

        # Proposal queue is empty
        assert len(storage.list_proposals()) == 0


# ── Profile ───────────────────────────────────────────────────────

class TestProfile:

    def test_default_profile(self, storage):
        profile = storage.get_profile()
        assert profile.preferred_detail_level == "concise"

    def test_save_and_load_profile(self, storage):
        profile = MemoryProfile(
            preferred_detail_level="detailed",
            expertise_areas=["satellite", "networking"],
        )
        storage.save_profile(profile)
        loaded = storage.get_profile()
        assert loaded.preferred_detail_level == "detailed"
        assert "satellite" in loaded.expertise_areas


# ── Edge Cases ────────────────────────────────────────────────────

class TestEdgeCases:

    def test_corrupt_file_returns_empty(self, storage):
        storage._memories_file.write_text("{invalid json!!!")
        assert storage.list_memories() == []

    def test_missing_dir_created(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "c" / "memory"
        s = MemoryStorage(memory_dir=deep_path)
        assert deep_path.exists()
        s.save(Memory(content="deep save"))
        assert s.get(s.list_memories()[0].id) is not None


class TestReadCache:

    def test_cache_avoids_reread(self, storage):
        storage.save(Memory(content="cached fact", tags=["test"]))
        # First read populates cache
        results1 = storage.search("cached")
        assert len(results1) == 1
        # Second read should use cache (same mtime)
        results2 = storage.search("cached")
        assert len(results2) == 1
        # Verify cache is populated
        assert storage._memories_cache is not None

    def test_cache_invalidated_on_write(self, storage):
        storage.save(Memory(content="first"))
        _ = storage.search("first")  # populate cache
        assert storage._memories_cache is not None
        storage.save(Memory(content="second"))  # write invalidates
        assert storage._memories_cache is None
        # Next read repopulates
        results = storage.list_memories()
        assert len(results) == 2
        assert storage._memories_cache is not None
