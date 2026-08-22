"""
Refusal log — the record of launches that never became runs.

A card refused by pre-launch validation produces no TaskRun, which is
correct (``record_run`` bumps ``run_count``, so minting a record for a
launch that never executed would corrupt the deck's "never run" vs "has
history" distinction, and a held run offers resume affordances for
progress that does not exist).  But it also means the entire class of
authoring defect that is caught BEFORE execution leaves no trace to
mine: which mistakes recur, which block types attract them, whether a
model-authored card fails differently from a hand-written one.

This store is that trace.  It follows ``app/storage/proposals.py``
exactly — append-only JSONL, whole-file ALE envelope, read-modify-write
because the envelope covers the whole file — rather than inventing a
third persistence shape.

The tests below pin the properties that make the log worth having:
  * a refusal is recorded at all, with the findings intact
  * append does not lose earlier records (the read-modify-write is the
    risky part of the borrowed pattern)
  * a logging failure never propagates into the launch path — the log
    is an observability sink, and losing a record must not convert a
    clean 422 into a 500
  * records carry enough structure to CLUSTER, which is the whole point
    of keeping them
"""

import json
from pathlib import Path

import pytest

from app.storage.task_card_refusals import (
    RefusalLog,
    build_refusal_record,
)
from app.models.task_card import Block
from app.utils.task_card_validation import validate_card_tree


@pytest.fixture
def log(tmp_path) -> RefusalLog:
    return RefusalLog(tmp_path / "proj")


def _broken_root() -> Block:
    return Block(block_type="group", id="g", name="Audit", body=[
        Block(block_type="task", id="t1", name="Recon"),          # no instructions
        Block(block_type="call", id="c1", name="Merge"),           # no target
    ])


class TestRecording:
    def test_empty_log_reads_as_empty_not_error(self, log):
        assert log.list_all() == []

    def test_a_refusal_is_recorded(self, log):
        res = validate_card_tree(_broken_root())
        log.record(build_refusal_record(
            card_id="card-1", card_name="Audit", result=res,
        ))
        rows = log.list_all()
        assert len(rows) == 1
        assert rows[0]["card_id"] == "card-1"
        assert rows[0]["card_name"] == "Audit"
        assert rows[0]["error_count"] == 2

    def test_findings_survive_the_round_trip(self, log):
        res = validate_card_tree(_broken_root())
        log.record(build_refusal_record(
            card_id="card-1", card_name="Audit", result=res,
        ))
        errors = log.list_all()[0]["errors"]
        assert len(errors) == 2
        # Path is what makes a finding locatable in a large card, so it
        # must not be dropped in serialization.
        assert all(e["path"] for e in errors)
        assert {e["block_id"] for e in errors} == {"t1", "c1"}

    def test_warnings_recorded_alongside_errors(self, log):
        # A card refused for one reason may carry warnings that explain
        # it; dropping them would hide the context at mining time.
        root = Block(block_type="group", id="g", name="W", body=[
            Block(block_type="task", id="t1", name="No instr"),
            Block(block_type="until", id="u1", name="Loop",
                  body=[Block(block_type="task", id="t2", name="w",
                              instructions="go")]),
        ])
        res = validate_card_tree(root)
        log.record(build_refusal_record(
            card_id="c", card_name="W", result=res,
        ))
        row = log.list_all()[0]
        assert row["error_count"] >= 1
        assert row["warning_count"] >= 1
        assert row["warnings"]


class TestAppendDoesNotLose:
    """Read-modify-write is the fragile half of the borrowed pattern."""

    def test_three_appends_yield_three_records(self, log):
        res = validate_card_tree(_broken_root())
        for i in range(3):
            log.record(build_refusal_record(
                card_id=f"card-{i}", card_name=f"C{i}", result=res,
            ))
        rows = log.list_all()
        assert len(rows) == 3
        assert [r["card_id"] for r in rows] == ["card-0", "card-1", "card-2"]

    def test_records_are_appended_in_order(self, log):
        res = validate_card_tree(_broken_root())
        log.record(build_refusal_record(
            card_id="first", card_name="A", result=res))
        log.record(build_refusal_record(
            card_id="second", card_name="B", result=res))
        assert [r["card_id"] for r in log.list_all()] == ["first", "second"]

    def test_a_second_store_instance_sees_prior_records(self, tmp_path):
        """Persistence, not in-memory accumulation."""
        d = tmp_path / "proj"
        res = validate_card_tree(_broken_root())
        RefusalLog(d).record(build_refusal_record(
            card_id="x", card_name="X", result=res))
        assert len(RefusalLog(d).list_all()) == 1


class TestNeverBreaksTheLaunch:
    def test_a_write_failure_is_swallowed(self, log, monkeypatch):
        """The log is an observability sink, not part of the contract.

        A refused launch must still return its 422; converting that into
        a 500 because a sink failed would replace an actionable error
        with a misleading one.
        """
        def boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(log, "_write_lines", boom)
        res = validate_card_tree(_broken_root())
        # Must not raise.
        log.record(build_refusal_record(
            card_id="c", card_name="C", result=res))

    def test_a_corrupt_line_does_not_poison_the_whole_log(self, log):
        res = validate_card_tree(_broken_root())
        log.record(build_refusal_record(
            card_id="good", card_name="G", result=res))
        # Simulate a truncated/garbled line landing in the file.
        p = log._path
        raw = p.read_bytes()
        from app.utils.encryption import is_encrypted
        if not is_encrypted(raw):
            p.write_bytes(raw + b"{not json\n")
            # Reading must degrade, not explode.
            rows = log.list_all()
            assert isinstance(rows, list)


class TestMineability:
    """A record nobody can group is a record nobody will use."""

    def test_record_carries_a_signature_for_clustering(self, log):
        """Two cards broken the same way must share a signature."""
        a = validate_card_tree(Block(
            block_type="task", id="t", name="A"))          # no instructions
        b = validate_card_tree(Block(
            block_type="task", id="t", name="B"))          # same defect
        ra = build_refusal_record(card_id="1", card_name="A", result=a)
        rb = build_refusal_record(card_id="2", card_name="B", result=b)
        assert ra["signature"] == rb["signature"]

    def test_different_defects_get_different_signatures(self, log):
        a = validate_card_tree(Block(block_type="task", id="t", name="A"))
        b = validate_card_tree(Block(
            block_type="repeat", id="r", name="R", repeat_mode="for_each",
            body=[Block(block_type="task", id="t", name="w",
                        instructions="go")]))
        ra = build_refusal_record(card_id="1", card_name="A", result=a)
        rb = build_refusal_record(card_id="2", card_name="R", result=b)
        assert ra["signature"] != rb["signature"]

    def test_signature_ignores_card_identity(self, log):
        """Clustering is by DEFECT, not by which card hit it."""
        res = validate_card_tree(Block(block_type="task", id="t", name="X"))
        r1 = build_refusal_record(card_id="aaa", card_name="One", result=res)
        r2 = build_refusal_record(card_id="bbb", card_name="Two", result=res)
        assert r1["signature"] == r2["signature"]

    def test_record_is_timestamped(self, log):
        res = validate_card_tree(_broken_root())
        rec = build_refusal_record(card_id="c", card_name="C", result=res)
        assert rec["at"] > 0

    def test_resume_launches_are_distinguishable(self, log):
        """A refused resume is a different signal from a refused launch.

        Resume executes a card_snapshot, so a refusal there means the
        snapshot was already broken when it ran — which is a different
        finding from an author breaking a card just now.
        """
        res = validate_card_tree(_broken_root())
        fresh = build_refusal_record(
            card_id="c", card_name="C", result=res, is_resume=False)
        resumed = build_refusal_record(
            card_id="c", card_name="C", result=res, is_resume=True)
        assert fresh["is_resume"] is False
        assert resumed["is_resume"] is True

    def test_clustering_over_a_populated_log(self, log):
        """The end-to-end use case: group refusals by signature."""
        no_instr = validate_card_tree(Block(
            block_type="task", id="t", name="A"))
        no_src = validate_card_tree(Block(
            block_type="repeat", id="r", name="R", repeat_mode="for_each",
            body=[Block(block_type="task", id="t", name="w",
                        instructions="go")]))
        for i in range(3):
            log.record(build_refusal_record(
                card_id=f"a{i}", card_name="A", result=no_instr))
        log.record(build_refusal_record(
            card_id="b0", card_name="R", result=no_src))

        counts: dict = {}
        for row in log.list_all():
            counts[row["signature"]] = counts.get(row["signature"], 0) + 1
        assert sorted(counts.values()) == [1, 3]
