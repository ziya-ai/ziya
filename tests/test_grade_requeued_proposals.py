"""
scripts/grade_requeued_proposals.py: grades written into probationary.jsonl
must reach the lifecycle's own promotion decision.

The anchor test goes through the real ProposalsStore (write -> projection)
and lifecycle._evaluate_promotion, not the script's own report: a grade
that lands on a non-canonical record event, or on the wrong key, would be
invisible to the projection and the proposal would still decay.
"""
import importlib.util
import json
import os

import pytest

from app.memory.lifecycle import FAST_TRACK_MIN_AGE, _evaluate_promotion
from app.models.memory import MemoryProposal
from app.storage.proposals import ProposalsStore

_spec = importlib.util.spec_from_file_location(
    "grade_requeued", os.path.join(os.path.dirname(__file__), "..", "scripts",
                                   "grade_requeued_proposals.py"))
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)


@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    import app.services.embedding_service as _es
    monkeypatch.setattr(_es, "_provider", None)
    monkeypatch.setattr(_es, "_cache", None)


def _add(store, content, layer):
    return store.add(MemoryProposal(content=content, layer=layer, tags=["projb"]),
                     activity_count=100)


GOOD = {"atomicity": 0.95, "self_containment": 0.9, "durability": 0.9}


def test_graded_record_promotes_through_lifecycle(tmp_path):
    store = ProposalsStore(memory_dir=tmp_path)
    pid = _add(store, "Gen2 GA caps each shard at 10 tenants in high-throughput mode.",
               "decision")
    ctr = 100 + FAST_TRACK_MIN_AGE
    assert _evaluate_promotion(store.get(pid), ctr) is None      # ungraded: no fast track

    res = g.grade_with_gate([store.get(pid)], {pid: GOOD})
    events = store._read_lines()
    assert g.apply_to_events(events, res) == 1
    store._write_lines(events); store._cache = None

    row = store.get(pid)
    assert row["quality"] >= 0.75 and row["requeue_grade"] == "graded"
    assert _evaluate_promotion(row, ctr) is not None


def test_gate_reject_keeps_quality_none(tmp_path):
    store = ProposalsStore(memory_dir=tmp_path)
    # quality_gate hard-rejects 3+ backtick code identifiers
    pid = _add(store, "Calls `a_fn` then `b_fn` then `c_fn` in the loop body here.",
               "architecture")
    res = g.grade_with_gate([store.get(pid)], {pid: GOOD})
    assert res[pid] == {"rejected": True}
    events = store._read_lines()
    g.apply_to_events(events, res)
    store._write_lines(events); store._cache = None
    row = store.get(pid)
    assert row["quality"] is None and row["requeue_grade"] == "rejected"
    assert _evaluate_promotion(row, 100 + FAST_TRACK_MIN_AGE) is None


def test_only_first_record_is_patched():
    ev = [{"kind": "record", "id": "p", "data": {}},
          {"kind": "record", "id": "p", "data": {}}]
    g.apply_to_events(ev, {"p": {"quality": 0.8, "quality_components": {}}})
    assert ev[0]["data"]["quality"] == 0.8 and "quality" not in ev[1]["data"]


def test_parse_grades_drops_unknown_ids_and_fences():
    raw = "```json\n" + json.dumps([{"id": "a", **GOOD}, {"id": "zzz", **GOOD}, 3]) + "\n```"
    assert set(g.parse_grades(raw, {"a", "b"})) == {"a"}
    assert g.parse_grades("not json", {"a"}) == {}
