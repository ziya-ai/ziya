"""Task-run timestamps: one unit (epoch ms), with a lazy read-side upgrade
for records written under the old mixed convention.

Background: TaskRun.created_at/updated_at were always ms (BaseStorage
convention) while every executor-side field was float seconds.  Run
d2c18548 (2026-09-16) read as started_at=2026-01-21 next to
created_at=2026-09-16 until the units were guessed.  app/utils/run_time.py
owns the fix; these tests are its contract.

Two layers are asserted separately:
  * the normalizer itself (pure function on dicts), and
  * the SEAM — TaskRunStorage.get/list/read_iteration_artifact must return
    ms for a v1 file, and freshly written records must be ms end to end.
"""

import copy
import json
import time
from pathlib import Path

import pytest

from app.utils.run_time import (
    RUN_RECORD_SCHEMA_VERSION, SECONDS_CEILING,
    normalize_artifact, normalize_run_record, now_ms, to_ms,
)

# A record in the exact shape the OLD storage wrote: ms at the top for
# created/updated, seconds everywhere else.  Values are real ones from
# run d2c18548 so the test documents what "mixed" looked like.
V1_RECORD = {
    "id": "d2c18548",
    "card_id": "31cf3856",
    "status": "held",
    "created_at": 1789595482000,        # ms
    "updated_at": 1789601623000,        # ms
    "started_at": 1789595484.750686,    # s
    "last_activity_at": 1789601617.037,  # s
    "completed_at": 1789601624.1,       # s
    "artifact": {"summary": "x", "created_at": 1789601624.0},
    "block_states": {
        "b-group": {
            "block_id": "b-group", "block_type": "group",
            "status": "running", "started_at": 1789595484.750686,
            "completed_at": None, "artifact": None,
            "iteration_summaries": [], "history": [],
        },
        "b-loop": {
            "block_id": "b-loop", "block_type": "repeat",
            "status": "running", "started_at": 1789595484.9,
            "completed_at": None,
            "artifact": {"summary": "it", "created_at": 1789598000.0},
            "iteration_summaries": [
                {"index": 0, "status": "passed",
                 "started_at": 1789595500.0, "completed_at": 1789596000.0,
                 "has_artifact": True},
            ],
            "history": [
                {"block_id": "b-loop", "block_type": "repeat",
                 "status": "failed", "started_at": 1789590000.0,
                 "completed_at": 1789591000.0, "artifact": None,
                 "iteration_summaries": [
                     {"index": 0, "status": "failed",
                      "started_at": 1789590100.0,
                      "completed_at": 1789590200.0, "artifact": None},
                 ]},
            ],
        },
    },
    "attempts": [{"attempt": 1, "started_at": 1789595484.75,
                  "completed_at": 1789591000.0}],
    "progress_notes": [{"note": "ran grep", "at": 1789601617.037822,
                        "source": None}],
    "pending_ask": {"block_id": "b-ask", "question": "ok?",
                    "opened_at": 1789600000.0},
    "ask_answers": {"b-old": {"decision": "yes", "answer": "",
                              "answered_by": "u",
                              "answered_at": 1789599000.0}},
    "resume_iteration_artifacts": {"3": {"summary": "r",
                                         "created_at": 1789597000.0}},
}


def _walk_times(obj, path=""):
    """Yield (path, value) for every populated timestamp key in a dict tree."""
    keys = ("started_at", "completed_at", "last_activity_at", "created_at",
            "updated_at", "opened_at", "answered_at", "at")
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k in keys and isinstance(v, (int, float)) and v:
                yield p, v
            else:
                yield from _walk_times(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_times(v, f"{path}[{i}]")


class TestToMs:
    def test_seconds_become_ms(self):
        assert to_ms(1789595484.750686) == 1789595484751

    def test_ms_untouched(self):
        assert to_ms(1789595482000) == 1789595482000

    def test_unset_and_garbage_pass_through(self):
        assert to_ms(None) is None
        assert to_ms(0) == 0
        assert to_ms("nope") == "nope"
        assert to_ms(True) is True

    def test_now_ms_is_ms(self):
        assert now_ms() >= SECONDS_CEILING
        assert abs(now_ms() / 1000 - time.time()) < 2


class TestNormalizeRunRecord:
    def test_every_populated_timestamp_lands_in_ms(self):
        rec = normalize_run_record(copy.deepcopy(V1_RECORD))
        bad = [(p, v) for p, v in _walk_times(rec) if v < SECONDS_CEILING]
        assert bad == [], f"still in seconds: {bad}"
        # Positive check that the walk actually reached the deep sites.
        paths = {p for p, _ in _walk_times(rec)}
        for must in (
            "started_at", "last_activity_at", "completed_at",
            "artifact.created_at",
            "block_states.b-loop.started_at",
            "block_states.b-loop.artifact.created_at",
            "block_states.b-loop.iteration_summaries[0].completed_at",
            "block_states.b-loop.history[0].started_at",
            "block_states.b-loop.history[0].iteration_summaries[0].started_at",
            "attempts[0].started_at",
            "progress_notes[0].at",
            "pending_ask.opened_at",
            "ask_answers.b-old.answered_at",
            "resume_iteration_artifacts.3.created_at",
        ):
            assert must in paths, must

    def test_ms_fields_are_not_double_scaled(self):
        rec = normalize_run_record(copy.deepcopy(V1_RECORD))
        assert rec["created_at"] == V1_RECORD["created_at"]
        assert rec["updated_at"] == V1_RECORD["updated_at"]

    def test_nones_stay_none(self):
        rec = normalize_run_record(copy.deepcopy(V1_RECORD))
        assert rec["block_states"]["b-group"]["completed_at"] is None
        assert rec["block_states"]["b-group"]["artifact"] is None

    def test_idempotent_and_versioned(self):
        once = normalize_run_record(copy.deepcopy(V1_RECORD))
        assert once["schema_version"] == RUN_RECORD_SCHEMA_VERSION
        twice = normalize_run_record(copy.deepcopy(once))
        assert twice == once

    def test_versioned_record_is_left_alone(self):
        # Proves the version short-circuit: a v2 record with a (bogus)
        # small value is NOT rescaled, so the heuristic is only consulted
        # for unversioned files.
        rec = {"schema_version": 2, "started_at": 5.0}
        assert normalize_run_record(rec)["started_at"] == 5.0

    def test_relative_order_is_preserved(self):
        rec = normalize_run_record(copy.deepcopy(V1_RECORD))
        assert rec["created_at"] < rec["started_at"] < rec["last_activity_at"]
        # and now they are comparable to updated_at at all:
        assert rec["last_activity_at"] < rec["updated_at"] < rec["completed_at"]

    def test_none_input(self):
        assert normalize_run_record(None) is None
        assert normalize_artifact(None) is None


# --- the seam: storage must apply it -------------------------------------

@pytest.fixture
def project_dir(tmp_path):
    return tmp_path


def _write_v1(project_dir: Path, rec: dict) -> Path:
    """Plant a v1 file the way the old storage would have left it."""
    runs = project_dir / "task_runs"
    runs.mkdir(parents=True, exist_ok=True)
    p = runs / f"{rec['id']}.json"
    p.write_text(json.dumps(rec))
    return p


class TestStorageSeam:
    def test_get_upgrades_a_v1_file(self, project_dir):
        from app.storage.task_runs import TaskRunStorage
        _write_v1(project_dir, V1_RECORD)
        run = TaskRunStorage(project_dir).get("d2c18548")
        assert run is not None
        assert run.started_at == 1789595484751
        assert run.last_activity_at >= SECONDS_CEILING
        assert run.block_states["b-loop"].started_at >= SECONDS_CEILING
        assert run.block_states["b-loop"].iteration_summaries[0] \
            .completed_at >= SECONDS_CEILING
        assert run.block_states["b-loop"].artifact.created_at >= SECONDS_CEILING
        assert run.progress_notes[0].at >= SECONDS_CEILING
        assert run.pending_ask["opened_at"] >= SECONDS_CEILING

    def test_list_upgrades_a_v1_file(self, project_dir):
        from app.storage.task_runs import TaskRunStorage
        _write_v1(project_dir, V1_RECORD)
        runs = TaskRunStorage(project_dir).list()
        assert len(runs) == 1
        assert runs[0].completed_at >= SECONDS_CEILING

    def test_read_iteration_artifact_upgrades(self, project_dir):
        from app.storage.task_runs import TaskRunStorage
        it_dir = project_dir / "task_runs" / "r1" / "iterations"
        it_dir.mkdir(parents=True)
        (it_dir / "b-loop_0.json").write_text(
            json.dumps({"summary": "i0", "created_at": 1789596000.0}))
        art = TaskRunStorage(project_dir).read_iteration_artifact(
            "r1", "b-loop", 0)
        assert art is not None
        assert art.created_at == 1789596000000

    def test_fresh_writes_are_ms_end_to_end(self, project_dir):
        """Phase-3 contract: a run created and driven through storage
        must have every populated timestamp in ms on disk, with no
        normalizer needed.  Reads the raw file to prove it."""
        from app.models.task_card import Artifact
        from app.models.task_run import TaskRunBlockState, TaskRunCreate
        from app.storage.task_runs import TaskRunStorage
        s = TaskRunStorage(project_dir)
        run = s.create(TaskRunCreate(card_id="c"))
        s.update_status(run.id, "running")
        s.set_block_state(run.id, TaskRunBlockState(
            block_id="b1", block_type="task"))
        s.update_block_status(run.id, "b1", "running")
        s.record_activity(run.id, note="ran x")
        s.open_ask(run.id, "b1", "ok?")
        s.record_ask_answer(run.id, "b1", "yes", "", "u")
        # time.time() (seconds) on purpose: the executor's 18 call sites
        # still pass seconds until phase 3b; the model must coerce to ms.
        s.set_artifact(run.id, Artifact(summary="done", created_at=time.time()))
        s.update_block_status(run.id, "b1", "done")
        s.update_status(run.id, "done")

        raw = json.loads((project_dir / "task_runs" / f"{run.id}.json")
                         .read_text())
        assert raw.get("schema_version", 0) >= RUN_RECORD_SCHEMA_VERSION
        bad = [(p, v) for p, v in _walk_times(raw) if v < SECONDS_CEILING]
        assert bad == [], f"written in seconds: {bad}"
        # Positive: the fields we exercised are actually present.
        paths = {p for p, _ in _walk_times(raw)}
        for must in ("started_at", "completed_at", "last_activity_at",
                     "block_states.b1.started_at",
                     "block_states.b1.completed_at",
                     "progress_notes[0].at",
                     "ask_answers.b1.answered_at",
                     "artifact.created_at"):
            assert must in paths, must
