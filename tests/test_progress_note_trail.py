"""
Tests for the durable progress-note trail (B8b).

``TaskRun.progress_note`` is a SINGLE SLOT: every heartbeat overwrites
it.  That destroyed the narrative of a long run as it was being written
— a model-authored phase note ("reviewed 12/30 diffs; grouping into 3
commits") survived only until the next tool call, typically a second or
two later, and a finished run carried no progress history at all.

``progress_notes`` is the bounded trail that fixes it.  What these tests
pin, in order of what a reader needs to trust:

  1. A note is APPENDED, not just assigned — the trail accumulates.
  2. ``source`` is carried, so a model note stays distinguishable from a
     tool-derived line after the fact and not only live on the WS stream.
  3. An exact consecutive repeat is skipped, so a loop running the same
     command every iteration cannot evict the phase notes that give the
     trail its value.
  4. The trail is capped and evicts OLDEST, so the run file (rewritten
     in full on every heartbeat) cannot grow without bound.
  5. The pre-existing single-slot and throttle behaviours are unchanged.
"""

import time

import pytest

from app.models.task_run import ProgressNote, TaskRun, TaskRunCreate
from app.storage.task_runs import PROGRESS_NOTE_CAP, TaskRunStorage


@pytest.fixture
def storage(tmp_path):
    return TaskRunStorage(tmp_path)


@pytest.fixture
def running_run(storage):
    """A run in 'running' state — record_activity no-ops on terminal runs."""
    run = storage.create(TaskRunCreate(card_id="card-1"))
    storage.update_status(run.id, "running")
    return run


def _notes(storage, run_id):
    return storage.get(run_id).progress_notes


# ── 1. the trail accumulates ────────────────────────────────────────

def test_a_single_note_is_appended_to_the_trail(storage, running_run):
    storage.record_activity(running_run.id, note="ran grep: foo")
    trail = _notes(storage, running_run.id)
    assert len(trail) == 1
    assert trail[0].note == "ran grep: foo"


def test_successive_distinct_notes_all_survive(storage, running_run):
    # The whole point: the second note must not destroy the first.
    for n in ("surveying", "editing", "verifying"):
        storage.record_activity(running_run.id, note=n)
    assert [p.note for p in _notes(storage, running_run.id)] == [
        "surveying", "editing", "verifying",
    ]


def test_trail_is_oldest_first(storage, running_run):
    storage.record_activity(running_run.id, note="first")
    storage.record_activity(running_run.id, note="second")
    trail = _notes(storage, running_run.id)
    assert trail[0].note == "first"
    assert trail[-1].note == "second"
    # Timestamps must be non-decreasing in the same direction, or a UI
    # rendering the list in order would show time running backwards.
    assert trail[0].at <= trail[-1].at


def test_each_entry_carries_a_timestamp(storage, running_run):
    before = time.time()
    storage.record_activity(running_run.id, note="x")
    after = time.time()
    at = _notes(storage, running_run.id)[0].at
    assert before <= at <= after


# ── 2. source survives to the durable record ────────────────────────

def test_model_source_is_recorded(storage, running_run):
    # Without this the UI can only distinguish a rich model phase note
    # from "ran grep: ..." while the WS event is in flight — the trail
    # would flatten them and lose the emphasis that makes it readable.
    storage.record_activity(
        running_run.id, note="reviewed 12/30 diffs", source="model")
    assert _notes(storage, running_run.id)[0].source == "model"


def test_tool_derived_note_has_no_source(storage, running_run):
    storage.record_activity(running_run.id, note="ran grep: foo")
    assert _notes(storage, running_run.id)[0].source is None


def test_mixed_sources_are_kept_distinct(storage, running_run):
    storage.record_activity(running_run.id, note="ran grep: a")
    storage.record_activity(running_run.id, note="now editing", source="model")
    storage.record_activity(running_run.id, note="ran sed: b")
    trail = _notes(storage, running_run.id)
    assert [p.source for p in trail] == [None, "model", None]


# ── 3. consecutive-repeat dedup ─────────────────────────────────────

def test_exact_consecutive_repeat_is_not_appended(storage, running_run):
    # A loop re-running one command would otherwise fill the entire
    # window with that line and evict every phase note.
    storage.record_activity(running_run.id, note="ran pytest")
    storage.record_activity(running_run.id, note="ran pytest")
    storage.record_activity(running_run.id, note="ran pytest")
    assert len(_notes(storage, running_run.id)) == 1


def test_a_repeat_after_something_else_IS_appended(storage, running_run):
    # Only CONSECUTIVE repeats are collapsed: "ran pytest" recurring
    # after a different note is genuinely new information about where
    # the run is, so dropping it would misreport the narrative.
    storage.record_activity(running_run.id, note="ran pytest")
    storage.record_activity(running_run.id, note="ran git status")
    storage.record_activity(running_run.id, note="ran pytest")
    assert [p.note for p in _notes(storage, running_run.id)] == [
        "ran pytest", "ran git status", "ran pytest",
    ]


# ── 4. the cap ──────────────────────────────────────────────────────

def test_trail_is_capped_and_evicts_oldest(storage, running_run):
    # Distinct notes so dedup never interferes with the cap under test.
    for i in range(PROGRESS_NOTE_CAP + 25):
        storage.record_activity(running_run.id, note=f"note-{i}")
    trail = _notes(storage, running_run.id)
    assert len(trail) == PROGRESS_NOTE_CAP
    # Oldest evicted, newest kept — a trail that dropped the TAIL would
    # be worse than none, since the most recent notes are the ones a
    # reader wants when a run is still going.
    assert trail[-1].note == f"note-{PROGRESS_NOTE_CAP + 24}"
    assert trail[0].note == "note-25"


def test_cap_is_a_sane_bound():
    # Guard against a future edit making this effectively unbounded (the
    # run file is rewritten in full on every heartbeat).
    assert 0 < PROGRESS_NOTE_CAP <= 1000


# ── 5. pre-existing behaviour unchanged ─────────────────────────────

def test_single_slot_still_holds_the_latest(storage, running_run):
    # The trail is additive; ``progress_note`` remains the live "what is
    # it doing right now" slot the tile's status line already reads.
    storage.record_activity(running_run.id, note="first")
    storage.record_activity(running_run.id, note="second")
    assert storage.get(running_run.id).progress_note == "second"


def test_note_less_heartbeat_does_not_append(storage, running_run):
    # Per-token text deltas call record_activity with no note purely to
    # stamp liveness; appending an empty entry per token would blow the
    # cap in seconds and evict every real note.
    storage.record_activity(running_run.id, note="real note")
    for _ in range(5):
        storage.record_activity(running_run.id, note=None, min_interval_s=0)
    assert len(_notes(storage, running_run.id)) == 1


def test_note_less_heartbeat_still_stamps_liveness(storage, running_run):
    storage.record_activity(running_run.id, note="x")
    first = storage.get(running_run.id).last_activity_at
    time.sleep(0.01)
    storage.record_activity(running_run.id, note=None, min_interval_s=0)
    assert storage.get(running_run.id).last_activity_at > first


def test_terminal_run_is_never_appended_to(storage):
    run = storage.create(TaskRunCreate(card_id="card-1"))
    storage.update_status(run.id, "running")
    storage.record_activity(run.id, note="while running")
    storage.update_status(run.id, "done")
    storage.record_activity(run.id, note="after done")
    trail = _notes(storage, run.id)
    assert [p.note for p in trail] == ["while running"]


def test_a_fresh_run_has_an_empty_trail(storage):
    run = storage.create(TaskRunCreate(card_id="card-1"))
    assert storage.get(run.id).progress_notes == []


# ── model round-trip ────────────────────────────────────────────────

def test_progress_note_model_roundtrips():
    # The trail is persisted inside the run's JSON, so a note must
    # survive model_dump/re-validate or it is silently lost on reload.
    run = TaskRun(
        id="r", card_id="c",
        progress_notes=[ProgressNote(note="hello", at=1.5, source="model")],
    )
    revived = TaskRun(**run.model_dump())
    assert revived.progress_notes[0].note == "hello"
    assert revived.progress_notes[0].at == 1.5
    assert revived.progress_notes[0].source == "model"


def test_run_record_without_the_field_still_loads():
    # Runs written before this field existed must not fail validation.
    revived = TaskRun(**{"id": "r", "card_id": "c"})
    assert revived.progress_notes == []
