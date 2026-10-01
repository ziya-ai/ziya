"""A promoted proposal must say which memory it became.

mark_promoted writes ``target_memory_id`` (and mark_archived ``reason``) on
the status event, but the projection copied only ``status``, so
ProposalsStore.get()/list_all() never exposed them and the 2026-09-26
restore of cleanup-deleted memories had to match proposals by text.
"""
from app.models.memory import MemoryProposal
from app.storage.proposals import ProposalsStore


def test_projection_carries_promotion_and_archive_lineage(tmp_path):
    st = ProposalsStore(memory_dir=tmp_path)
    a = st.add(MemoryProposal(content="Order ids are ULIDs, chosen for sortability.", layer="decision"))
    b = st.add(MemoryProposal(content="Dashboards read the reporting replica, never the primary.", layer="decision"))
    assert st.mark_promoted(a, target_memory_id="m_abc123")
    assert st.mark_archived(b, reason="decayed")
    assert st.get(a)["target_memory_id"] == "m_abc123"
    assert st.get(b)["archive_reason"] == "decayed"
    # positive: status itself still projected
    assert st.get(a)["status"] == "promoted" and st.get(b)["status"] == "archived"
