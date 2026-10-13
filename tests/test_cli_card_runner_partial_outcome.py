"""The CLI verdict line has a third form between ✓ and ✗.

2026-10-12: a card whose root repeat passed most of its iterations and
failed the last one printed ``✗ Task card failed`` -- the same line a
total loss prints -- because ``_render_artifact`` read only
``artifact.failed``, which a loop sets last-wins.  The GUI already
derives ``partial`` for such a run (app/utils/run_outcome); the CLI
runs with ``storage=None`` so it must derive the same verdict from the
artifact's ``stages`` plus the ``iteration_completed`` events it can
observe on the relay.

Layer 1 (classify_artifact_outcome) is pure.  Layer 2 asserts the CLI
seam: the tally listener counts relay events and the rendered line is
amber, not red.
"""
import pytest

from app.models.task_card import Artifact
from app.utils.run_outcome import classify_artifact_outcome, stage_tally


def _stage(status, i=0):
    return {"index": i, "label": f"iteration {i}", "status": status, "summary": ""}


class TestClassifyArtifactOutcome:
    def test_clean_artifact_is_done(self):
        assert classify_artifact_outcome(Artifact(summary="ok")) == "done"

    def test_done_is_never_reclassified_by_a_tally(self):
        assert classify_artifact_outcome(
            Artifact(summary="ok"), passed_iterations=3, failed_iterations=2) == "done"

    def test_loop_with_passed_and_failed_stages_is_partial(self):
        art = Artifact(summary="last one broke", failed=True,
                       stages=[_stage("passed", 0), _stage("passed", 1), _stage("failed", 2)])
        assert classify_artifact_outcome(art) == "partial"

    def test_every_stage_failed_stays_failed(self):
        art = Artifact(failed=True, stages=[_stage("failed", 0), _stage("failed", 1)])
        assert classify_artifact_outcome(art) == "failed"

    def test_whole_level_verdict_on_all_passed_stages_stays_failed(self):
        """A gather contract (or self-assessment) judging a loop whose
        every iteration passed is a failure of the whole, not a partial."""
        art = Artifact(failed=True, signature="gather_contract:findings",
                       stages=[_stage("passed", 0), _stage("passed", 1)])
        assert classify_artifact_outcome(art) == "failed"

    def test_leaf_failure_with_no_evidence_stays_failed(self):
        assert classify_artifact_outcome(Artifact(failed=True)) == "failed"

    def test_iteration_tally_sees_through_a_sole_child_loop(self):
        """Root group -> one repeat: the group records a single failed
        stage, so only the event tally knows 99 iterations passed."""
        art = Artifact(failed=True, stages=[{"index": 0, "label": "sweep", "status": "failed"}])
        assert classify_artifact_outcome(art) == "failed"
        assert classify_artifact_outcome(
            art, passed_iterations=99, failed_iterations=1) == "partial"

    def test_replayed_stages_are_neither_progress_nor_unfinished(self):
        art = Artifact(failed=True, stages=[_stage("replayed", 0), _stage("failed", 1)])
        assert classify_artifact_outcome(art) == "failed"
        assert stage_tally(art.stages) == {"passed": 0, "unfinished": 1, "total": 1}

    def test_cancelled_stage_counts_as_unfinished(self):
        art = Artifact(failed=True, stages=[_stage("passed", 0), _stage("cancelled", 1)])
        assert classify_artifact_outcome(art) == "partial"


# ── Layer 2: the CLI seam ───────────────────────────────────────

@pytest.mark.asyncio
async def test_tally_counts_iteration_completed_events_only():
    from app.cli_card_runner import _IterationTally
    t = _IterationTally()
    await t.send_json({"type": "iteration_completed", "status": "passed"})
    await t.send_json({"type": "iteration_completed", "status": "passed"})
    await t.send_json({"type": "iteration_completed", "status": "failed"})
    await t.send_json({"type": "task_finished", "ok": False})       # ignored
    await t.send_json({"type": "iteration_completed", "status": "cancelled"})  # neither
    assert (t.passed, t.failed) == (2, 1)


@pytest.mark.asyncio
async def test_tally_receives_events_through_the_relay():
    """The seam: a tally registered with the relay is fed by push()."""
    from app.agents import task_run_stream_relay as relay
    from app.cli_card_runner import _IterationTally
    t = _IterationTally()
    await relay.connect("cli-test-tally", t)
    try:
        await relay.push("cli-test-tally", {"type": "iteration_completed", "status": "passed"})
    finally:
        await relay.disconnect("cli-test-tally", t)
    assert t.passed == 1


def test_render_partial_loop_is_amber_with_counts(capsys):
    from app.cli_card_runner import _render_artifact
    art = Artifact(summary="last iteration broke", failed=True,
                   stages=[_stage("passed", 0), _stage("passed", 1), _stage("failed", 2)])
    _render_artifact(art)
    out = capsys.readouterr().out
    assert "◐ Task card partially complete" in out
    assert "2 of 3 stages passed" in out
    assert "✗ Task card failed" not in out
    assert "✓ Task card complete" not in out


def test_render_uses_tally_for_sole_child_loop(capsys):
    from app.cli_card_runner import _IterationTally, _render_artifact
    t = _IterationTally()
    t.passed, t.failed = 90, 10
    art = Artifact(failed=True, stages=[{"index": 0, "label": "sweep", "status": "failed"}])
    _render_artifact(art, t)
    out = capsys.readouterr().out
    assert "◐ Task card partially complete" in out
    assert "90 of 100 iterations passed" in out


def test_render_total_loss_is_still_red(capsys):
    from app.cli_card_runner import _render_artifact
    _render_artifact(Artifact(failed=True, stages=[_stage("failed", 0)]))
    out = capsys.readouterr().out
    assert "✗ Task card failed" in out
    assert "◐" not in out


def test_render_clean_run_is_still_green(capsys):
    from app.cli_card_runner import _render_artifact
    _render_artifact(Artifact(summary="ok"))
    assert "✓ Task card complete" in capsys.readouterr().out
