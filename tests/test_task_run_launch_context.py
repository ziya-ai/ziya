"""Tests for TaskRun.launch_context (Feature 2, step 2a).

launch_context tags how a run was invoked so the scope-authorization
seam (app/agents/task_executor.py::authorize_scope) can treat an
unsigned/stale escalation differently by context:

  - "interactive"  → clamp-and-continue (a user can sign + resume);
                     the frontend launch gate handles signing.
  - "headless"     → fail loudly (scheduler fire, no attached session).

This step only introduces and threads the field; the seam behaviour
change is step 2b.  These tests pin:

  1. the default is "interactive" (backward-compatible: preserves
     today's clamp-and-continue for any legacy/unforeseen run);
  2. a legacy run persisted WITHOUT the field loads as "interactive"
     (the extra="allow" + default round-trip);
  3. TaskRunStorage.create copies an explicit "headless" through — the
     seam this guards is a one-by-one constructor that silently drops
     unnamed fields, which is exactly how such a field goes missing;
  4. the value survives a storage round-trip (get after create).

The Call-block inheritance ("a callee under a headless run is headless")
is asserted structurally rather than by executing a run: a Call creates
no run of its own, so there is nothing to set — the property is that the
callee reads the SAME run record.  That is covered by (3)+(4): the run
carries one launch_context and the callee reads that same run.
"""

import json

import pytest

from app.models.task_run import TaskRun, TaskRunCreate
from app.storage.task_runs import TaskRunStorage


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


class TestLaunchContextDefault:
    def test_taskrun_defaults_to_interactive(self):
        run = TaskRun(card_id="c1")
        assert run.launch_context == "interactive"

    def test_taskruncreate_defaults_to_interactive(self):
        data = TaskRunCreate(card_id="c1")
        assert data.launch_context == "interactive"

    def test_legacy_run_without_field_loads_as_interactive(self, tmp_path):
        """A run file written before the field existed must load as
        interactive — the safe default that keeps today's behaviour."""
        run_id = "legacy-run-1"
        legacy = {
            "id": run_id,
            "card_id": "c1",
            "status": "done",
            # deliberately NO launch_context key
        }
        # Run files live under project_dir/task_runs/, not the project
        # root — write the legacy fixture where the storage layer reads.
        runs_dir = tmp_path / "task_runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / f"{run_id}.json").write_text(json.dumps(legacy))
        loaded = TaskRunStorage(tmp_path).get(run_id)
        assert loaded is not None
        assert loaded.launch_context == "interactive"


class TestLaunchContextThreading:
    def test_create_defaults_interactive_when_unset(self, storage):
        run = storage.create(TaskRunCreate(card_id="c1"))
        assert run.launch_context == "interactive"
        # and it persisted, not just defaulted in-memory
        assert storage.get(run.id).launch_context == "interactive"

    def test_create_copies_headless_through(self, storage):
        """The load-bearing assertion: create() lists fields one-by-one,
        so a headless launch must be explicitly copied or it silently
        reverts to interactive — the reverse of what the scheduler needs."""
        run = storage.create(TaskRunCreate(
            card_id="c1", launch_context="headless",
        ))
        assert run.launch_context == "headless"

    def test_headless_survives_round_trip(self, storage):
        run = storage.create(TaskRunCreate(
            card_id="c1", launch_context="headless",
        ))
        reloaded = storage.get(run.id)
        assert reloaded is not None
        assert reloaded.launch_context == "headless"

    def test_interactive_and_headless_are_distinct_on_disk(self, storage):
        """Two runs launched differently must not collapse to one value —
        the whole point of the field is to tell them apart at the seam."""
        i = storage.create(TaskRunCreate(card_id="c1"))
        h = storage.create(TaskRunCreate(
            card_id="c1", launch_context="headless",
        ))
        assert storage.get(i.id).launch_context == "interactive"
        assert storage.get(h.id).launch_context == "headless"
