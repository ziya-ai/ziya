"""
Cross-process executor liveness for task runs.

Regression for run d2c18548 (2026-09-16): a second ``ziya`` server started
on another port and its startup ``reconcile_stale_runs`` swept the first
server's healthy, in-flight run to "failed" because liveness was a
process-local set and the reconciler assumed "live on disk == orphaned".

The fix is a per-run advisory ``flock`` held by the executor for the life
of ``_run``.  flock locks are per open-file-description, so two
``TaskRunStorage`` instances over one directory model two processes
faithfully: the second instance's probe must see the first one's lock.
"""

import multiprocessing
import sys
import time
from pathlib import Path

import pytest

from app.models.task_run import TaskRunCreate
from app.storage.task_runs import TaskRunStorage

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="flock-based executor lock is POSIX-only"
)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "proj"


def _running_run(storage: TaskRunStorage):
    run = storage.create(TaskRunCreate(card_id="card-1"))
    storage.update_status(run.id, "running")
    return storage.get(run.id)


class TestExecutorLock:
    def test_sibling_reconciler_leaves_owned_run_alone(self, project_dir):
        owner = TaskRunStorage(project_dir)
        run = _running_run(owner)
        owner.mark_active(run.id)  # the executor is alive here

        sibling = TaskRunStorage(project_dir)  # "another server process"
        assert sibling.executor_alive(run.id) is True
        assert sibling.reconcile_stale_runs() == 0
        assert sibling.get(run.id).status == "running"
        assert sibling.get(run.id).error is None

        # Heartbeats from the real owner keep landing — the whole point.
        owner.record_activity(run.id, note="still working")
        assert sibling.get(run.id).progress_note == "still working"

    def test_released_run_is_reconciled(self, project_dir):
        owner = TaskRunStorage(project_dir)
        run = _running_run(owner)
        owner.mark_active(run.id)
        owner.mark_inactive(run.id)  # normal exit via the finally block

        sibling = TaskRunStorage(project_dir)
        assert sibling.executor_alive(run.id) is False
        assert sibling.reconcile_stale_runs() == 1
        assert sibling.get(run.id).status == "held"
        assert not (project_dir / "task_runs" / f"{run.id}.lock").exists()

    def test_never_claimed_run_is_reconciled(self, project_dir):
        """A row left 'running' with no lock file at all (the pre-lock
        on-disk shape, or a hard crash before mark_active) still sweeps."""
        storage = TaskRunStorage(project_dir)
        run = _running_run(storage)
        assert storage.executor_alive(run.id) is False
        assert storage.reconcile_stale_runs() == 1

    def test_lock_dies_with_owner_process(self, project_dir):
        """The kernel releases flock on process death, so a crashed owner
        leaves a lock FILE but not a held lock: the run must reconcile."""
        storage = TaskRunStorage(project_dir)
        run = _running_run(storage)

        ctx = multiprocessing.get_context("fork")
        started = ctx.Event()
        release = ctx.Event()

        def _hold(run_id, pdir, started, release):
            s = TaskRunStorage(pdir)
            s.mark_active(run_id)
            started.set()
            release.wait(30)
            # Exit WITHOUT mark_inactive: simulate a crash.
            import os
            os._exit(0)

        p = ctx.Process(target=_hold, args=(run.id, project_dir, started, release))
        p.start()
        try:
            assert started.wait(10), "child never took the lock"
            assert storage.executor_alive(run.id) is True
            assert storage.reconcile_stale_runs() == 0
        finally:
            release.set()
            p.join(10)

        assert (project_dir / "task_runs" / f"{run.id}.lock").exists()
        assert storage.executor_alive(run.id) is False
        assert storage.reconcile_stale_runs() == 1
        assert storage.get(run.id).status == "held"

    def test_delete_removes_lock_file(self, project_dir):
        storage = TaskRunStorage(project_dir)
        run = _running_run(storage)
        storage.mark_active(run.id)
        storage.mark_inactive(run.id)
        # Recreate a stale lock file as a crash would leave it.
        (project_dir / "task_runs" / f"{run.id}.lock").touch()
        assert storage.delete(run.id) is True
        assert not (project_dir / "task_runs" / f"{run.id}.lock").exists()

    def test_awaiting_input_with_live_owner_is_not_held(self, project_dir):
        """The Ask branch of the reconciler must also defer to a live owner:
        an in-process Ask whose executor is alive is not a stranded Ask."""
        owner = TaskRunStorage(project_dir)
        run = _running_run(owner)
        owner.mark_active(run.id)
        owner.update_status(run.id, "awaiting_input")

        sibling = TaskRunStorage(project_dir)
        assert sibling.reconcile_stale_runs() == 0
        assert sibling.get(run.id).status == "awaiting_input"
