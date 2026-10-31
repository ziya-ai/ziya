"""
Corroboration counts DISTINCT FOREIGN conversations, not events.

Why this is load-bearing: the server dispatches extraction with the full
non-muted history on EVERY turn, so a long conversation re-extracts its
earlier windows every time.  Each re-extraction of an already-proposed fact
lands as a ``corroborate`` event.  Before the projection guard in
``ProposalsStore._project``, that incremented ``corroborations`` once per
turn, and ``_evaluate_promotion`` promotes at ``corroborations >= 2`` — so
a fact stated once in a conversation that ran three turns promoted with
ZERO independent confirmation, contradicting the rule's own docstring
("3 distinct conversations have produced this content").

The guard exists in the store but had no test.  These pin it at the outer
surface (what ``get()``/``list_open()`` report, and what the lifecycle rule
decides), so a projection rewrite cannot silently reintroduce it.
"""
import pytest

from app.models.memory import MemoryProposal
from app.storage.proposals import ProposalsStore


ORIGIN = "conv-origin"
OTHER_A = "conv-a"
OTHER_B = "conv-b"


def _store(tmp_path):
    return ProposalsStore(memory_dir=tmp_path / "memory")


def _propose(store, content="The sweep runs every thirty minutes.", conv=ORIGIN):
    return store.add(MemoryProposal(content=content, conversation_id=conv),
                     activity_count=1)


def test_same_conversation_re_extraction_does_not_corroborate(tmp_path):
    # The exact production shape: the originating conversation re-derives
    # its own fact on later turns (content-hash path in add()).
    st = _store(tmp_path)
    pid = _propose(st)
    for _ in range(5):
        assert _propose(st) == pid          # same hash → corroborate event
    row = st.get(pid)
    assert row["corroborations"] == 0, (
        "self-corroboration counted: a conversation re-extracting its own "
        "fact across turns must not accumulate promotion evidence"
    )
    assert row.get("corroborated_by", []) == []


def test_same_conversation_embedding_path_does_not_corroborate(tmp_path):
    # The paraphrase path (corroborate_by_id) must apply the same rule.
    st = _store(tmp_path)
    pid = _propose(st)
    for _ in range(3):
        assert st.corroborate_by_id(pid, conversation_id=ORIGIN) is True
    assert st.get(pid)["corroborations"] == 0


def test_distinct_foreign_conversations_each_count_once(tmp_path):
    st = _store(tmp_path)
    pid = _propose(st)
    _propose(st, conv=OTHER_A)
    _propose(st, conv=OTHER_A)              # repeat from A: not new evidence
    st.corroborate_by_id(pid, conversation_id=OTHER_A)
    assert st.get(pid)["corroborations"] == 1
    _propose(st, conv=OTHER_B)
    row = st.get(pid)
    assert row["corroborations"] == 2
    assert set(row["corroborated_by"]) == {OTHER_A, OTHER_B}


def test_unattributed_corroboration_does_not_count(tmp_path):
    # An event with no conversation_id cannot be shown to be independent.
    st = _store(tmp_path)
    pid = _propose(st)
    st.corroborate_by_id(pid, conversation_id=None)
    _propose(st, conv=None)
    assert st.get(pid)["corroborations"] == 0


def test_lifecycle_rule_does_not_promote_on_self_corroboration(tmp_path):
    # The seam: the number the store reports is the number the promotion
    # rule reads.  Three turns of the same conversation must not reach
    # "highly_corroborated"; two distinct foreign conversations must.
    from app.memory.lifecycle import _evaluate_promotion
    st = _store(tmp_path)
    pid = _propose(st)
    for _ in range(3):
        _propose(st)
    assert _evaluate_promotion(st.get(pid)) is None
    _propose(st, conv=OTHER_A)
    _propose(st, conv=OTHER_B)
    assert _evaluate_promotion(st.get(pid)) == "highly_corroborated"


def test_positive_control_counter_can_still_grow(tmp_path):
    # Guards against a "fix" that simply never increments: the counter must
    # move for genuinely distinct conversations, or the gate tests nothing.
    st = _store(tmp_path)
    pid = _propose(st)
    for i in range(6):
        _propose(st, conv=f"conv-{i}")
    assert st.get(pid)["corroborations"] == 6
    # Display list is capped to the last 5; the COUNT is not.
    assert len(st.get(pid)["corroborated_by"]) == 5
