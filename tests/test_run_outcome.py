"""
Tests for app.utils.run_outcome — deriving ``partial`` from what a run
accomplished rather than from how it stopped.

The bug being fixed: a seven-stage card that completed four stages
(writing files along the way) reported identically to one that died on
stage one having touched nothing.  Both were flat red "Failed", which
actively discourages a user from looking for changes the run left in
their workspace.
"""

import pytest

from app.models.task_card import Artifact, ArtifactPart
from app.models.task_run import IterationSummary, TaskRunBlockState
from app.utils.run_outcome import (
    classify_terminal_status,
    has_incomplete_work,
    has_progress,
    summarize_progress,
    summarize_side_effects,
)


def _state(block_id, status, block_type="task", iterations=None, artifact=None):
    return TaskRunBlockState(
        block_id=block_id, block_type=block_type, status=status,
        iteration_summaries=iterations or [], artifact=artifact,
    )


def _iter(index, status, has_artifact=True):
    return IterationSummary(index=index, status=status, has_artifact=has_artifact)


def _states(*states):
    return {s.block_id: s for s in states}


class TestClassifyTerminalStatus:
    def test_failed_with_progress_becomes_partial(self):
        states = _states(
            _state("a", "done"), _state("b", "done"),
            _state("c", "failed"), _state("d", "skipped"),
        )
        assert classify_terminal_status("failed", states) == "partial"

    def test_failed_with_no_progress_stays_failed(self):
        # A total loss must keep its own distinct signal — calling this
        # "partial" would be a lie about a run that touched nothing.
        states = _states(_state("a", "failed"), _state("b", "skipped"))
        assert classify_terminal_status("failed", states) == "failed"

    def test_cancelled_with_progress_becomes_partial(self):
        # User-stopped-midway carries the same workspace hazard as a
        # crash-midway, so it earns the same amber treatment.
        states = _states(_state("a", "done"), _state("b", "cancelled"))
        assert classify_terminal_status("cancelled", states) == "partial"

    def test_cancelled_before_anything_ran_stays_cancelled(self):
        states = _states(_state("a", "cancelled"), _state("b", "queued"))
        assert classify_terminal_status("cancelled", states) == "cancelled"

    def test_done_is_never_reclassified(self):
        # Even a shape that looks partial: a clean finish is not partial.
        states = _states(_state("a", "done"), _state("b", "skipped"))
        assert classify_terminal_status("done", states) == "done"

    def test_all_blocks_done_but_artifact_failed_stays_failed(self):
        # Guards the has_incomplete_work half of the predicate: every
        # block completed and only the ROOT artifact's self-assessment
        # reported failure.  That is a failure of the whole, not a
        # partial — nothing is left to continue from.
        states = _states(_state("a", "done"), _state("b", "done"))
        assert classify_terminal_status("failed", states) == "failed"

    def test_empty_block_states_degrades_to_old_behaviour(self):
        assert classify_terminal_status("failed", {}) == "failed"
        assert classify_terminal_status("failed", None) == "failed"

    def test_unknown_status_untouched(self):
        # Never invent a state the caller did not ask for.
        states = _states(_state("a", "done"), _state("b", "failed"))
        assert classify_terminal_status("running", states) == "running"
        assert classify_terminal_status("paused", states) == "paused"

    def test_accepts_plain_dicts(self):
        # A raw JSON read of a run file yields dicts, not models.
        states = {
            "a": {"block_id": "a", "block_type": "task", "status": "done"},
            "b": {"block_id": "b", "block_type": "task", "status": "failed"},
        }
        assert classify_terminal_status("failed", states) == "partial"


class TestLoopIterationProgress:
    """A Repeat's inner Task shares ONE block_states entry across every
    iteration (last-write-wins), so a loop whose 3rd of 10 iterations
    failed leaves that entry ``failed`` — with the successes visible
    only in iteration_summaries.  Counting blocks alone would call that
    run a total loss."""

    def test_passed_iterations_count_as_progress(self):
        states = _states(_state(
            "loop", "failed", block_type="repeat",
            iterations=[_iter(0, "passed"), _iter(1, "passed"), _iter(2, "failed")],
        ))
        assert has_progress(states) is True
        assert classify_terminal_status("failed", states) == "partial"

    def test_all_iterations_failed_is_not_progress(self):
        states = _states(_state(
            "loop", "failed", block_type="repeat",
            iterations=[_iter(0, "failed"), _iter(1, "failed")],
        ))
        assert has_progress(states) is False
        assert classify_terminal_status("failed", states) == "failed"


class TestSummarizeProgress:
    def test_counts_visible_blocks_only(self):
        # Group blocks render chromeless in the run map
        # (runMapModel.flattenBlocks), so counting them would produce an
        # "N of M" figure that disagrees with the rows on screen.
        states = _states(
            _state("g", "done", block_type="group"),
            _state("a", "done"), _state("b", "done"),
            _state("c", "failed"), _state("d", "skipped"),
        )
        p = summarize_progress(states)
        assert p["total"] == 4, "group must not be counted"
        assert p["completed"] == 2
        assert p["failed"] == 1
        assert p["skipped"] == 1

    def test_counts_iterations_separately(self):
        states = _states(_state(
            "loop", "failed", block_type="repeat",
            iterations=[_iter(0, "passed"), _iter(1, "failed"), _iter(2, "passed")],
        ))
        p = summarize_progress(states)
        assert p["passed_iterations"] == 2
        assert p["failed_iterations"] == 1

    def test_empty(self):
        p = summarize_progress({})
        assert p["total"] == 0 and p["completed"] == 0


class TestHasIncompleteWork:
    def test_queued_and_running_are_incomplete(self):
        assert has_incomplete_work(_states(_state("a", "queued"))) is True
        assert has_incomplete_work(_states(_state("a", "running"))) is True

    def test_all_done_is_complete(self):
        assert has_incomplete_work(_states(_state("a", "done"))) is False


class TestSummarizeSideEffects:
    """The first question a user asks about a partial run: did this
    change my workspace?"""

    def _snapshot(self, block_scopes):
        return {
            "schema_version": 1,
            "project_root": "/proj",
            "block_scopes": block_scopes,
        }

    def test_reports_block_with_write_grant(self):
        snap = self._snapshot({
            "b1": {
                "block_name": "Rewrite adapter", "block_type": "task",
                "paths": [{"path": "app/", "is_dir": True, "write": True}],
                "tools": [], "skills": [], "shell_commands": [],
            },
        })
        effects = summarize_side_effects(snap, _states(_state("b1", "done")))
        assert len(effects) == 1
        assert effects[0]["block_name"] == "Rewrite adapter"
        assert effects[0]["had_write_grant"] is True

    def test_reports_block_with_shell_grant(self):
        snap = self._snapshot({
            "b1": {
                "block_name": "Run migration", "block_type": "task",
                "paths": [], "tools": [], "skills": [],
                "shell_commands": ["git"],
            },
        })
        effects = summarize_side_effects(snap, _states(_state("b1", "done")))
        assert len(effects) == 1
        assert effects[0]["had_write_grant"] is True

    def test_read_only_block_is_not_a_side_effect(self):
        snap = self._snapshot({
            "b1": {
                "block_name": "Inventory", "block_type": "task",
                "paths": [{"path": "app/", "is_dir": True, "read": True}],
                "tools": [], "skills": [], "shell_commands": [],
            },
        })
        assert summarize_side_effects(snap, _states(_state("b1", "done"))) == []

    def test_never_ran_block_excluded(self):
        # A queued block never had the chance to change anything.
        snap = self._snapshot({
            "b1": {
                "block_name": "Later stage", "block_type": "task",
                "paths": [{"path": "app/", "is_dir": True, "write": True}],
                "tools": [], "skills": [], "shell_commands": [],
            },
        })
        assert summarize_side_effects(snap, _states(_state("b1", "queued"))) == []

    def test_failed_block_with_write_grant_is_still_reported(self):
        # A block that crashed PART WAY through may have written before
        # crashing — excluding it would understate the hazard.
        snap = self._snapshot({
            "b1": {
                "block_name": "Half-written", "block_type": "task",
                "paths": [{"path": "app/", "is_dir": True, "write": True}],
                "tools": [], "skills": [], "shell_commands": [],
            },
        })
        effects = summarize_side_effects(snap, _states(_state("b1", "failed")))
        assert len(effects) == 1
        assert effects[0]["status"] == "failed"

    def test_declared_file_artifacts_listed(self):
        # file_uri is what build_artifact_part actually persists (it
        # resolves a caller's file_path to an absolute path and stores
        # it here).  Reading file_path instead found nothing on real
        # data and fell through to \`\`name\`\`, reporting a label
        # ("adapter") where a path belonged.
        artifact = Artifact(
            summary="wrote it",
            outputs=[ArtifactPart(part_type="file", name="adapter",
                                  file_uri="/proj/app/auth/adapter.py")],
        )
        snap = self._snapshot({
            "b1": {
                "block_name": "Rewrite", "block_type": "task",
                "paths": [{"path": "app/", "is_dir": True, "write": True}],
                "tools": [], "skills": [], "shell_commands": [],
            },
        })
        effects = summarize_side_effects(
            snap, _states(_state("b1", "done", artifact=artifact)))
        assert effects[0]["files"] == ["app/auth/adapter.py"], (
            "absolute file_uri should be shown relative to the project root"
        )

    def test_file_outside_project_root_shown_as_given(self):
        artifact = Artifact(
            summary="wrote it",
            outputs=[ArtifactPart(part_type="file", name="tmp",
                                  file_uri="/tmp/scratch.md")],
        )
        effects = summarize_side_effects(
            self._snapshot({}), _states(_state("b1", "done", artifact=artifact)))
        assert effects[0]["files"] == ["/tmp/scratch.md"]

    def test_file_path_fallback_accepted(self):
        # A hand-constructed or future-shaped part carrying file_path
        # rather than file_uri must not be silently dropped.
        artifact = Artifact(
            summary="wrote it",
            outputs=[ArtifactPart(part_type="file", name="notes",
                                  file_path=".ziya/notes.md")],
        )
        effects = summarize_side_effects(
            self._snapshot({}), _states(_state("b1", "done", artifact=artifact)))
        assert len(effects) == 1
        assert effects[0]["files"] == [".ziya/notes.md"]

    def test_label_is_never_mistaken_for_a_path(self):
        # The regression guard: a file part with a name but NO usable
        # path must contribute no file entry at all, rather than
        # reporting its label as though it were a path.
        artifact = Artifact(
            summary="declared badly",
            outputs=[ArtifactPart(part_type="file", name="adapter")],
        )
        effects = summarize_side_effects(
            self._snapshot({}), _states(_state("b1", "done", artifact=artifact)))
        assert effects == []

    def test_non_file_parts_ignored(self):
        artifact = Artifact(
            summary="thought about it",
            outputs=[ArtifactPart(part_type="text", name="verdict", text="ok")],
        )
        effects = summarize_side_effects(
            self._snapshot({}), _states(_state("b1", "done", artifact=artifact)))
        assert effects == []

    def test_missing_snapshot_tolerated(self):
        assert summarize_side_effects(None, _states(_state("b1", "done"))) == []
        assert summarize_side_effects({}, None) == []

    def test_write_patterns_alone_counts_as_a_hazard(self):
        # A file-task callee grants fnmatch globs and has no ``paths`` at
        # all, so before write_patterns was consulted this intersected to
        # nothing and the banner claimed the run changed nothing — an
        # actively wrong answer, not a missing row.
        snap = {"block_scopes": {"b1": {
            "block_name": "release", "block_type": "task",
            "paths": [], "shell_commands": [],
            "write_patterns": ["*.toml"],
        }}}
        effects = summarize_side_effects(snap, _states(_state("b1", "done")))
        assert len(effects) == 1
        assert effects[0]["had_write_grant"] is True

    def test_callee_block_scope_reaches_the_hazard_report(self):
        # The end-to-end shape of the fix: a callee block recorded by
        # _record_call_audit appears in block_scopes, so the intersection
        # with block_states now finds it.
        snap = {"block_scopes": {"callee-leaf": {
            "block_name": "Deploy", "block_type": "task",
            "paths": [{"path": "out/", "write": True}],
            "via_call": {"call_block_id": "c1", "target": "Helper",
                         "kind": "card"},
        }}}
        effects = summarize_side_effects(
            snap, _states(_state("callee-leaf", "done")))
        assert len(effects) == 1
        assert effects[0]["block_name"] == "Deploy"
