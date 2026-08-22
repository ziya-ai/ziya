"""
The replayed prefix of a mid-loop resume, as a persisted record.

The defect: a run resumed at iteration 3 of 5 recorded summaries for 3
and 4 only, because the executor's replay branch emits an
``iteration_completed`` event and ``continue``s without calling
``_record_iteration``.  The run map's dot strip is built from
``iteration_summaries``, so the resumed run drew TWO circles counting
from 1 — visually identical to a fresh two-iteration run, and reading as
though the three banked iterations had been thrown away.  They had not.

The fix seeds the prefix at launch with ``replayed=True``.  That flag is
load-bearing in two opposite directions, and both halves are pinned here:

  * the dot strip must SHOW these (the whole point), and
  * every progress aggregate must EXCLUDE them, or a resume would credit
    itself with a prior attempt's work — a lie in the other direction.
"""

import pytest

from app.models.task_card import Artifact
from app.models.task_run import (
    IterationSummary, TaskRunBlockState, TaskRunCreate,
)
from app.storage.task_runs import TaskRunStorage
from app.utils.run_outcome import (
    classify_terminal_status, has_progress, summarize_progress,
)


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


def _run_with_loop(storage, block_status="running"):
    run = storage.create(TaskRunCreate(card_id="card-1"))
    storage.set_block_state(run.id, TaskRunBlockState(
        block_id="loop", block_type="repeat", status=block_status,
    ))
    return run


def _replayed(index, status="passed", has_artifact=True):
    return IterationSummary(
        index=index, status=status,
        has_artifact=has_artifact, replayed=True,
    )


class TestFieldDefault:
    def test_replayed_defaults_false(self):
        # Every pre-existing record on disk materializes as executed,
        # which is what those runs were.
        assert IterationSummary(index=0, status="passed").replayed is False

    def test_survives_a_round_trip(self, storage):
        run = _run_with_loop(storage)
        storage.seed_replayed_iterations(run.id, "loop", [_replayed(0)])
        fresh = storage.get(run.id)
        assert fresh.block_states["loop"].iteration_summaries[0].replayed is True


class TestSeeding:
    def test_prefix_is_installed_in_index_order(self, storage):
        run = _run_with_loop(storage)
        storage.seed_replayed_iterations(
            run.id, "loop", [_replayed(i) for i in range(3)],
        )
        fresh = storage.get(run.id)
        got = fresh.block_states["loop"].iteration_summaries
        assert [s.index for s in got] == [0, 1, 2]
        assert all(s.replayed for s in got)

    def test_merges_ahead_of_executed_records(self, storage):
        # Ordering is what the dot strip renders, so a prefix seeded
        # after execution must still sort before it rather than
        # appending — otherwise the strip reads 3,4,0,1,2.
        run = _run_with_loop(storage)
        storage.append_iteration_summary(
            run.id, "loop", IterationSummary(index=3, status="passed"))
        storage.append_iteration_summary(
            run.id, "loop", IterationSummary(index=4, status="failed"))
        storage.seed_replayed_iterations(
            run.id, "loop", [_replayed(i) for i in range(3)],
        )
        got = storage.get(run.id).block_states["loop"].iteration_summaries
        assert [s.index for s in got] == [0, 1, 2, 3, 4]
        assert [s.replayed for s in got] == [True, True, True, False, False]

    def test_executed_record_wins_a_collision(self, storage):
        # A summary this run produced describes it better than one
        # carried from a prior attempt.
        run = _run_with_loop(storage)
        storage.append_iteration_summary(
            run.id, "loop", IterationSummary(index=0, status="failed"))
        storage.seed_replayed_iterations(run.id, "loop", [_replayed(0)])
        got = storage.get(run.id).block_states["loop"].iteration_summaries
        assert len(got) == 1
        assert got[0].status == "failed"
        assert got[0].replayed is False

    def test_preserves_a_failed_status(self, storage):
        # Recolouring a preserved failure green would misreport the
        # source run's outcome on the resumed run's face.
        run = _run_with_loop(storage)
        storage.seed_replayed_iterations(
            run.id, "loop", [_replayed(0, status="failed")],
        )
        got = storage.get(run.id).block_states["loop"].iteration_summaries
        assert got[0].status == "failed"

    def test_empty_prefix_is_a_noop(self, storage):
        run = _run_with_loop(storage)
        storage.seed_replayed_iterations(run.id, "loop", [])
        assert storage.get(run.id).block_states["loop"].iteration_summaries == []

    def test_unseeded_block_is_skipped_not_created(self, storage):
        # update_block_status has the same contract: a block with no
        # seeded state is not invented.  Inventing one here would add a
        # block_states entry for a block the card does not contain.
        run = _run_with_loop(storage)
        storage.seed_replayed_iterations(run.id, "nope", [_replayed(0)])
        assert "nope" not in storage.get(run.id).block_states

    def test_missing_run_is_tolerated(self, storage):
        storage.seed_replayed_iterations("no-such-run", "loop", [_replayed(0)])


class TestProgressExclusion:
    """The half that keeps the seeded prefix honest.

    A resumed run inherits passes it did not perform.  If those counted,
    a resume that died on its first real iteration would be reclassified
    ``partial`` — asserting it left workspace changes it never made.
    """

    def _states(self, executed):
        return {"loop": {
            "block_id": "loop", "block_type": "repeat", "status": "failed",
            "iteration_summaries": (
                [_replayed(i).model_dump() for i in range(3)] + executed
            ),
        }}

    def test_replayed_passes_are_not_progress(self):
        states = self._states([
            {"index": 3, "status": "failed", "has_artifact": True},
        ])
        assert has_progress(states) is False

    def test_a_resume_that_failed_immediately_stays_failed(self):
        states = self._states([
            {"index": 3, "status": "failed", "has_artifact": True},
        ])
        assert classify_terminal_status("failed", states) == "failed"

    def test_an_executed_pass_still_registers(self):
        states = self._states([
            {"index": 3, "status": "passed", "has_artifact": True},
            {"index": 4, "status": "failed", "has_artifact": True},
        ])
        assert has_progress(states) is True
        assert classify_terminal_status("failed", states) == "partial"

    def test_counts_report_executed_iterations_only(self):
        states = self._states([
            {"index": 3, "status": "passed", "has_artifact": True},
            {"index": 4, "status": "failed", "has_artifact": True},
        ])
        got = summarize_progress(states)
        assert got["passed_iterations"] == 1
        assert got["failed_iterations"] == 1

    def test_unflagged_summaries_count_exactly_as_before(self):
        # The regression guard: an ordinary run must be untouched.
        states = {"loop": {
            "block_id": "loop", "block_type": "repeat", "status": "failed",
            "iteration_summaries": [
                {"index": 0, "status": "passed"},
                {"index": 1, "status": "failed"},
            ],
        }}
        got = summarize_progress(states)
        assert (got["passed_iterations"], got["failed_iterations"]) == (1, 1)
        assert has_progress(states) is True


class TestArtifactCopy:
    """Carried artifacts must live under the RESUMED run's id.

    The dot's open action fetches
    ``/task-runs/{this_run}/iterations/{block}/{index}``, so a replayed
    dot whose artifact stayed with the source run is a visible circle
    that 404s on click — worse than an absent one, since it looks
    openable.
    """

    def test_written_under_the_new_run_id(self, storage):
        src = _run_with_loop(storage)
        dst = _run_with_loop(storage)
        storage.write_iteration_artifact(
            src.id, "loop", 0, Artifact(summary="from source"))
        carried = storage.read_iteration_artifact(src.id, "loop", 0)
        storage.write_iteration_artifact(dst.id, "loop", 0, carried)
        got = storage.read_iteration_artifact(dst.id, "loop", 0)
        assert got is not None and got.summary == "from source"

    def test_absent_artifact_reads_as_none(self, storage):
        run = _run_with_loop(storage)
        assert storage.read_iteration_artifact(run.id, "loop", 7) is None
