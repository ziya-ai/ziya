"""
A fact that names a stable identifier must survive being learned once.

The fable (2026-10-20 → 10-27): the user's two Kuiper terminal ids were
found, proposed to memory, re-found from chat history the next day,
re-proposed, and still absent a week later — by which point the model
re-inferred one of them from telemetry with the wrong cell.  Four
independent gaps, each covered here:

1. search: a literal identifier in the query pins the memory that
   contains it ahead of rank fusion (app/storage/memory.py).
2. search: probation is consulted on every query, not only on a miss
   (tests/test_memory_search_probation.py).
3. lifecycle: an explicit, unhedged proposal naming an identifier promotes
   on the identifier fast track; hedged, harvested, or already-known
   identifiers do not (app/memory/lifecycle.py).
4. provenance: a proposal made after chat_read of a conversation that
   contains the fact is corroborated by that conversation
   (app/utils/memory_provenance.py, memory_propose, chat_read).

Each positive assertion fails on the pre-fix code.
"""
from __future__ import annotations

import pytest

from app.models.memory import Memory, MemoryProposal

SEATTLE = "ti-c6e49a8d711f4b326"
BAINBRIDGE = "ti-9a35b1307f4751a73"
UUID = "92120111-62bc-4d3b-bc7f-e97a0d94cc5b"


# ── 0. identifier detection ─────────────────────────────────────────────────

class TestIdentifiers:
    def test_classes(self):
        from app.utils.memory_identifiers import extract_identifiers
        text = (f"Seattle CT {SEATTLE} (conversation {UUID}), fixed in "
                f"CR-314341848 for NETREL-893; hash 7d7f51c8 is too short; "
                f"deadbeefcafe1234 counts.")
        got = extract_identifiers(text)
        assert SEATTLE in got
        assert UUID in got
        assert "cr-314341848" in got
        assert "netrel-893" in got
        assert "deadbeefcafe1234" in got
        assert "7d7f51c8" not in got
        # UUID reported once, not again as its 12-hex tail.
        assert "e97a0d94cc5b" not in got

    def test_prose_is_not_an_identifier(self):
        from app.utils.memory_identifiers import extract_identifiers
        assert extract_identifiers(
            "us-west-2 hosts python3 on sea900-br-kep-c1-h9-left since 2026") == []

    def test_hedge(self):
        from app.utils.memory_identifiers import is_hedged
        assert is_hedged(f"terminal is very likely {SEATTLE}; dcohn has not confirmed it")
        assert not is_hedged(f"Seattle terminal is {SEATTLE}, cell 1288419")


# ── 1. exact-identifier lane in search ──────────────────────────────────────

@pytest.fixture
def mem(tmp_path, monkeypatch):
    from app.storage.memory import MemoryStorage
    import app.storage.memory as mem_mod
    store = MemoryStorage(tmp_path / "mem")
    monkeypatch.setattr(mem_mod, "_instance", store)
    return store


def _rig_semantic(monkeypatch, order):
    """Warm embedding cache + a semantic ranking of our choosing."""
    class _Cache:
        def missing_ids(self, ids):
            return []
    monkeypatch.setattr("app.services.embedding_service.get_embedding_cache",
                        lambda: _Cache(), raising=False)
    monkeypatch.setattr(
        "app.services.embedding_service.semantic_search",
        lambda query, top_k=10, include_ids=None: [(mid, 0.9 - 0.1 * i) for i, mid in enumerate(order)],
        raising=False)


def test_identifier_query_pins_the_memory_containing_it(mem, monkeypatch):
    target = mem.save(Memory(content=f"Seattle terminal {SEATTLE} is in cell 1288419."))
    decoy = mem.save(Memory(content="Cell towers in Seattle use cell ids."))
    filler = mem.save(Memory(content="Lunar tides are semidiurnal."))
    # Semantic ranks the decoy first and the target last; keyword ranks the
    # target first.  Under plain RRF the decoy wins (1/61+1/62 > 1/61+1/63).
    _rig_semantic(monkeypatch, [decoy.id, filler.id, target.id])

    got = mem.search(f"{SEATTLE} cell", limit=5)
    assert [m.id for m in got][0] == target.id


def test_non_identifier_query_order_is_unchanged(mem, monkeypatch):
    a = mem.save(Memory(content="Cell towers in Seattle use cell ids."))
    b = mem.save(Memory(content="Seattle terminal cells are numbered."))
    _rig_semantic(monkeypatch, [a.id, b.id])
    got = mem.search("seattle cell", limit=5)
    assert [m.id for m in got][0] == a.id


def test_uuid_query_pins(mem, monkeypatch):
    target = mem.save(Memory(content=f"Conversation {UUID} is the 15-minute outage thread."))
    decoy = mem.save(Memory(content="LTC3758 12V regulator failure killed V_12P0_IBC."))
    _rig_semantic(monkeypatch, [decoy.id, target.id])
    got = mem.search(UUID, limit=5)
    assert got[0].id == target.id


# ── 3. identifier fast track ────────────────────────────────────────────────

def _prop(content, learned_from="observation", layer="domain_context", signals=()):
    return {"id": "prop_x", "content": content, "layer": layer,
            "learned_from": learned_from, "corroborations": 0,
            "signals": list(signals), "activity_count_at_proposal": 0}


class TestIdentifierFastTrack:
    def test_explicit_identifier_fact_promotes_at_min_age(self):
        from app.memory.lifecycle import _evaluate_promotion, FAST_TRACK_MIN_AGE
        p = _prop(f"dcohn's Seattle terminal is {SEATTLE}, cell 1288419.")
        assert _evaluate_promotion(p, 0) is None
        assert _evaluate_promotion(p, FAST_TRACK_MIN_AGE) == "identifier_fast_track"

    def test_no_identifier_keeps_ordinary_bar(self):
        from app.memory.lifecycle import _evaluate_promotion
        p = _prop("dcohn's Seattle terminal is in cell 1288419.")
        assert _evaluate_promotion(p, 10) is None

    def test_hedged_identifier_does_not_fast_track(self):
        """Tonight's prop_d676abe7: a real id, a wrong cell, self-declared guess."""
        from app.memory.lifecycle import _evaluate_promotion
        p = _prop(f"Seattle terminal is very likely {SEATTLE} (cell 775474); "
                  f"dcohn has not confirmed it.")
        assert _evaluate_promotion(p, 10) is None

    def test_harvested_identifier_does_not_fast_track(self):
        from app.memory.lifecycle import _evaluate_promotion
        for src in ("auto_extraction", "task_run", "user_directional_phrase"):
            p = _prop(f"Seattle terminal is {SEATTLE}.", learned_from=src)
            assert _evaluate_promotion(p, 10) is None, src

    def test_identifier_already_in_active_store_blocks_fast_track(self):
        from app.memory.lifecycle import _evaluate_promotion
        p = _prop(f"Seattle terminal {SEATTLE} is in cell 775474.")
        assert _evaluate_promotion(p, 10, active_identifiers={SEATTLE}) is None
        assert _evaluate_promotion(p, 10, active_identifiers={BAINBRIDGE}) == "identifier_fast_track"

    def test_contradicted_blocks(self):
        from app.memory.lifecycle import _evaluate_promotion
        p = _prop(f"Seattle terminal is {SEATTLE}.", signals=[{"name": "contradicted"}])
        assert _evaluate_promotion(p, 10) is None

    def test_archival_never_decays_an_eligible_proposal(self):
        from app.memory.lifecycle import _evaluate_archival, ARCHIVAL_AGE_THRESHOLD
        p = _prop(f"Seattle terminal is {SEATTLE}.")
        assert _evaluate_archival(p, ARCHIVAL_AGE_THRESHOLD, lambda _: 0.0) is None
        # Same proposal, identifier already active: ordinary path, decays.
        assert _evaluate_archival(p, ARCHIVAL_AGE_THRESHOLD, lambda _: 0.0,
                                  active_identifiers={SEATTLE}) == "decayed"

    def test_api_would_promote_matches_sweep(self, mem, tmp_path, monkeypatch):
        """Seam: /api/v1/memory/proposals must apply the same active-store
        conflict guard the sweep does, or it advertises a promotion that
        never happens."""
        from app.storage.proposals import ProposalsStore
        import app.storage.proposals as prop_mod
        from app.api import memory as api
        props = ProposalsStore(tmp_path / "mem")
        monkeypatch.setattr(prop_mod, "_instance", props)
        monkeypatch.setattr("app.memory.lifecycle.current_activity_count", lambda: 10)
        mem.save(Memory(content=f"Seattle terminal {SEATTLE} is in cell 1288419."))

        conflict = props.add(MemoryProposal(
            content=f"Seattle terminal {SEATTLE} is in cell 775474.",
            learned_from="observation"), activity_count=5)
        fresh = props.add(MemoryProposal(
            content=f"Bainbridge terminal is {BAINBRIDGE}, cell 1242687.",
            learned_from="observation"), activity_count=5)

        by_id = {r["id"]: r for r in api.list_proposals()}
        assert by_id[fresh]["would_promote"] == "identifier_fast_track"
        assert by_id[conflict]["would_promote"] is None

    def test_sweep_builds_active_index_and_blocks_conflict(self, mem, tmp_path, monkeypatch):
        """Seam: the sweep must actually consult the active store."""
        from app.storage.proposals import ProposalsStore
        import app.storage.proposals as prop_mod
        from app.memory import lifecycle
        props = ProposalsStore(tmp_path / "mem")
        monkeypatch.setattr(prop_mod, "_instance", props)
        monkeypatch.setattr(lifecycle, "current_activity_count", lambda: 10)
        monkeypatch.setattr(lifecycle, "_make_active_embedding_lookup", lambda: (lambda _p: 0.0))
        monkeypatch.setattr(lifecycle, "_evict_orphan_vectors", lambda *_a, **_k: 0)
        mem.save(Memory(content=f"Seattle terminal {SEATTLE} is in cell 1288419."))

        conflict = props.add(MemoryProposal(
            content=f"Seattle terminal {SEATTLE} is in cell 775474.",
            learned_from="observation"), activity_count=5)
        fresh = props.add(MemoryProposal(
            content=f"Bainbridge terminal is {BAINBRIDGE}, cell 1242687.",
            learned_from="observation"), activity_count=5)

        lifecycle._run_lifecycle_pass_sync()
        assert props.get(fresh)["status"] == "promoted"
        assert props.get(conflict)["status"] != "promoted"


# ── 4. provenance corroboration ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_provenance():
    from app.utils import memory_provenance
    memory_provenance.clear()
    yield
    memory_provenance.clear()


class TestProvenance:
    def test_read_text_supporting_the_fact_is_credited(self):
        from app.utils.memory_provenance import note_history_read, provenance_for
        note_history_read("conv-now", "conv-old",
                          f"Seattle: {SEATTLE} cell 1288419; Bainbridge: {BAINBRIDGE}")
        assert provenance_for("conv-now", f"Seattle terminal is {SEATTLE}.") == ["conv-old"]

    def test_unrelated_read_is_not_credited(self):
        from app.utils.memory_provenance import note_history_read, provenance_for
        note_history_read("conv-now", "conv-old", "LTC3758 regulator failure on KS102.")
        assert provenance_for("conv-now", f"Seattle terminal is {SEATTLE}.") == []

    def test_self_read_is_ignored(self):
        from app.utils.memory_provenance import note_history_read, provenance_for
        note_history_read("conv-now", "conv-now", f"{SEATTLE}")
        assert provenance_for("conv-now", f"{SEATTLE}") == []

    def test_token_overlap_without_identifier(self):
        from app.utils.memory_provenance import note_history_read, provenance_for
        note_history_read("conv-now", "conv-old",
                          "The phaser scheduler uses DWRR across five traffic classes.")
        assert provenance_for("conv-now", "Phaser scheduler uses DWRR across five classes") == ["conv-old"]
        assert provenance_for("conv-now", "Kubernetes ingress uses nginx") == []

    @pytest.mark.asyncio
    async def test_memory_propose_corroborates_from_history(self, tmp_path, monkeypatch):
        """Seam: chat_read → register → memory_propose → corroboration event."""
        from app.storage.proposals import ProposalsStore
        import app.storage.proposals as prop_mod
        from app.utils.memory_provenance import note_history_read
        from app.mcp.tools.memory_tools import MemoryProposeTool
        props = ProposalsStore(tmp_path / "mem")
        monkeypatch.setattr(prop_mod, "_instance", props)
        monkeypatch.setattr("app.memory.lifecycle.current_activity_count", lambda: 3)

        note_history_read("conv-now", "conv-old", f"Micah: your Seattle TIID is {SEATTLE}")
        out = await MemoryProposeTool().execute(
            content=f"dcohn's Seattle terminal is {SEATTLE}.", conversation_id="conv-now")

        assert out["corroborated_by_history"] == ["conv-old"]
        row = props.get(out["proposal_id"])
        assert row["corroborations"] == 1
        assert "conv-old" in row["corroborated_by"]
        assert any(s.get("name") == "provenance" for s in row.get("signals", []))

    @pytest.mark.asyncio
    async def test_memory_propose_without_history_has_no_corroboration(self, tmp_path, monkeypatch):
        from app.storage.proposals import ProposalsStore
        import app.storage.proposals as prop_mod
        from app.mcp.tools.memory_tools import MemoryProposeTool
        props = ProposalsStore(tmp_path / "mem")
        monkeypatch.setattr(prop_mod, "_instance", props)
        monkeypatch.setattr("app.memory.lifecycle.current_activity_count", lambda: 3)

        out = await MemoryProposeTool().execute(
            content=f"dcohn's Seattle terminal is {SEATTLE}.", conversation_id="conv-now")
        assert out["corroborated_by_history"] == []
        assert props.get(out["proposal_id"]).get("corroborations", 0) == 0

    @pytest.mark.asyncio
    async def test_chat_read_registers_what_it_read(self, tmp_path, monkeypatch):
        """Seam: the real chat_read tool feeds the register."""
        from app.mcp.tools import chat_history_tools as cht
        from app.utils.memory_provenance import provenance_for
        record = {
            "id": "conv-old", "title": "my CT", "projectId": "p1",
            "messages": [
                {"role": "human", "content": "whats my seattle ctid", "timestamp": 1},
                {"role": "assistant", "content": f"Your Seattle CT is {SEATTLE}.", "timestamp": 2},
            ]}
        monkeypatch.setattr(cht, "_resolve_scope",
                            lambda: {"ziya_home": tmp_path, "project_id": "p1",
                                     "conversation_id": "conv-now"})
        monkeypatch.setattr(cht, "_load_chat", lambda _home, cid: ("p1", record) if cid == "conv-old" else None)
        monkeypatch.setattr(cht, "_folder_paths", lambda *_a, **_k: {})

        out = await cht.ChatReadTool().execute(chat_id="conv-old", conversation_id="conv-now")
        assert not out.get("error"), out
        assert provenance_for("conv-now", f"Seattle terminal is {SEATTLE}.") == ["conv-old"]
