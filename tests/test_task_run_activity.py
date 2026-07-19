"""
Tests for TaskRunStorage.record_activity — the throttled heartbeat
behind the running-task live-progress surface (last_activity_at +
progress_note on the run file).
"""

import pytest

from app.models.task_run import TaskRunCreate
from app.storage.task_runs import TaskRunStorage


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def run(storage):
    r = storage.create(TaskRunCreate(card_id="card-1"))
    storage.update_status(r.id, "running")
    return storage.get(r.id)


def test_records_activity_and_note(storage, run):
    out = storage.record_activity(run.id, note="ran file_read: app/x.py")
    assert out is not None
    persisted = storage.get(run.id)
    assert persisted.last_activity_at is not None
    assert persisted.progress_note == "ran file_read: app/x.py"


def test_noteless_heartbeat_stamps_activity(storage, run):
    assert storage.record_activity(run.id) is not None
    assert storage.get(run.id).last_activity_at is not None
    # No note ever supplied — progress_note stays unset.
    assert storage.get(run.id).progress_note is None


def test_throttle_suppresses_rapid_noteless_writes(storage, run):
    assert storage.record_activity(run.id, min_interval_s=60.0) is not None
    first = storage.get(run.id).last_activity_at
    # Immediate second heartbeat inside the window: throttled no-op.
    assert storage.record_activity(run.id, min_interval_s=60.0) is None
    assert storage.get(run.id).last_activity_at == first


def test_new_note_bypasses_throttle(storage, run):
    storage.record_activity(run.id, note="ran a", min_interval_s=60.0)
    # Different note inside the throttle window must still write —
    # otherwise the surfaced note goes stale relative to tool calls.
    assert storage.record_activity(
        run.id, note="ran b", min_interval_s=60.0) is not None
    assert storage.get(run.id).progress_note == "ran b"


def test_same_note_within_throttle_is_suppressed(storage, run):
    storage.record_activity(run.id, note="ran a", min_interval_s=60.0)
    first = storage.get(run.id).last_activity_at
    assert storage.record_activity(
        run.id, note="ran a", min_interval_s=60.0) is None
    assert storage.get(run.id).last_activity_at == first


def test_unknown_run_is_noop(storage):
    assert storage.record_activity("no-such-run", note="x") is None


@pytest.mark.parametrize("terminal", ["done", "failed", "cancelled"])
def test_terminal_run_never_resurrected(storage, run, terminal):
    storage.update_status(run.id, terminal)
    assert storage.record_activity(run.id, note="late event") is None
    persisted = storage.get(run.id)
    assert persisted.progress_note is None
    assert persisted.status == terminal


def test_throttle_is_per_run(storage):
    a = storage.create(TaskRunCreate(card_id="c"))
    b = storage.create(TaskRunCreate(card_id="c"))
    storage.update_status(a.id, "running")
    storage.update_status(b.id, "running")
    assert storage.record_activity(a.id, min_interval_s=60.0) is not None
    # Run B's first heartbeat must not be throttled by run A's.
    assert storage.record_activity(b.id, min_interval_s=60.0) is not None
