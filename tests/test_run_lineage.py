"""
Tests for attempt lineage on TaskRun.

Why lineage exists: a resume creates a NEW run and leaves the source
intact (so the source stays an immutable record), but nothing recorded
the relationship — so the GUI could only show a second tile
materializing beside the first with no stated connection, leaving the
user unable to tell whether prior state had been preserved.  It IS
preserved; these fields are what let the UI say so.
"""

import pytest

from app.models.task_run import TaskRun, TaskRunCreate
from app.storage.task_runs import TaskRunStorage


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


class TestCreateSeedsLineage:
    def test_initial_run_is_root_of_its_own_lineage(self, storage):
        # root_run_id defaults to SELF so the whole chain is always one
        # ``root_run_id`` filter — no parent-pointer walk, and no
        # null-root special case in the UI.
        run = storage.create(TaskRunCreate(card_id="c1"))
        assert run.root_run_id == run.id
        assert run.parent_run_id is None
        assert run.attempt == 1
        assert run.resume_kind == "initial"

    def test_resume_joins_the_source_lineage(self, storage):
        first = storage.create(TaskRunCreate(card_id="c1"))
        second = storage.create(TaskRunCreate(
            card_id="c1",
            parent_run_id=first.id,
            root_run_id=first.root_run_id,
            attempt=2,
            resume_kind="retry_from",
            resumed_from_block_id="b5",
        ))
        assert second.root_run_id == first.id
        assert second.parent_run_id == first.id
        assert second.attempt == 2
        assert second.resume_kind == "retry_from"
        assert second.resumed_from_block_id == "b5"
        assert second.id != first.id, "a resume must be a NEW run"

    def test_attempt_floors_at_one(self, storage):
        run = storage.create(TaskRunCreate(card_id="c1", attempt=0))
        assert run.attempt == 1

    def test_lineage_survives_a_round_trip_to_disk(self, storage):
        # storage.create() enumerates fields one by one rather than
        # splatting the payload, so a field not named there is silently
        # dropped.  Re-reading is what proves it was actually persisted.
        first = storage.create(TaskRunCreate(card_id="c1"))
        second = storage.create(TaskRunCreate(
            card_id="c1", parent_run_id=first.id,
            root_run_id=first.id, attempt=2, resume_kind="continue_from",
            resumed_from_block_id="b2",
        ))
        reloaded = storage.get(second.id)
        assert reloaded is not None
        assert reloaded.parent_run_id == first.id
        assert reloaded.root_run_id == first.id
        assert reloaded.attempt == 2
        assert reloaded.resume_kind == "continue_from"
        assert reloaded.resumed_from_block_id == "b2"


class TestListLineage:
    def _chain(self, storage, length=3):
        runs = [storage.create(TaskRunCreate(card_id="c1"))]
        for i in range(2, length + 1):
            runs.append(storage.create(TaskRunCreate(
                card_id="c1",
                parent_run_id=runs[-1].id,
                root_run_id=runs[0].id,
                attempt=i,
                resume_kind="retry_from",
            )))
        return runs

    def test_returns_every_attempt_oldest_first(self, storage):
        runs = self._chain(storage, 3)
        chain = storage.list_lineage(runs[0].id)
        assert [r.id for r in chain] == [r.id for r in runs]
        assert [r.attempt for r in chain] == [1, 2, 3]

    def test_sorted_by_attempt_not_wall_clock(self, storage):
        # Two attempts can land in the same millisecond, which would make
        # a created_at sort non-deterministic and the displayed ordinals
        # jump around between renders.
        first = storage.create(TaskRunCreate(card_id="c1"))
        later = storage.create(TaskRunCreate(
            card_id="c1", root_run_id=first.id, attempt=3,
            resume_kind="retry_from"))
        middle = storage.create(TaskRunCreate(
            card_id="c1", root_run_id=first.id, attempt=2,
            resume_kind="retry_from"))
        chain = storage.list_lineage(first.id)
        assert [r.attempt for r in chain] == [1, 2, 3]
        assert [r.id for r in chain] == [first.id, middle.id, later.id]

    def test_excludes_an_unrelated_lineage(self, storage):
        a = self._chain(storage, 2)
        b = self._chain(storage, 2)
        ids = {r.id for r in storage.list_lineage(a[0].id)}
        assert ids == {a[0].id, a[1].id}
        assert b[0].id not in ids

    def test_pre_lineage_record_returns_itself(self, storage):
        # A run file written before lineage tracking has no root_run_id;
        # the id-fallback must still return it rather than nothing, or
        # its tile would render with an empty attempt rail.
        run = storage.create(TaskRunCreate(card_id="c1"))
        run.root_run_id = None
        storage._write_json(storage._run_file(run.id), run.model_dump())
        chain = storage.list_lineage(run.id)
        assert [r.id for r in chain] == [run.id]

    def test_unknown_root_is_empty(self, storage):
        storage.create(TaskRunCreate(card_id="c1"))
        assert storage.list_lineage("no-such-run") == []


class TestPartialIsTerminalInStorage:
    def test_partial_stamps_completed_at(self, storage):
        # Without 'partial' in the terminal tuple, completed_at is never
        # set — so the tile shows no runtime — and record_activity's
        # terminal guard keeps letting heartbeats through on a run that
        # has already finished.
        run = storage.create(TaskRunCreate(card_id="c1"))
        storage.update_status(run.id, "running")
        updated = storage.update_status(run.id, "partial")
        assert updated is not None
        assert updated.status == "partial"
        assert updated.completed_at is not None

    def test_heartbeats_do_not_resurrect_a_partial_run(self, storage):
        run = storage.create(TaskRunCreate(card_id="c1"))
        storage.update_status(run.id, "running")
        storage.update_status(run.id, "partial")
        assert storage.record_activity(run.id, note="late tool call") is None
        assert storage.get(run.id).progress_note is None
