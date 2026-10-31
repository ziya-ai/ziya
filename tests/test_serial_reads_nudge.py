"""The one-read-per-message nudge (app/utils/serial_reads_nudge.py).

Audit baseline 2026-10-15..20: 77% of tool batches were a single call and
the median model round-trip between batches was 9.3 s, so the batch runner
(which demonstrably overlaps co-issued reads) rarely had anything to
overlap.  The nudge tells the model, in the tool-result turn, when it has
just issued its second consecutive lone read.  Pinned here: the policy
(when it fires, when it resets), the classification seam (same predicate as
the runner), and the executor hop (the tracker is constructed per stream
and its note goes through _append_to_last_user_turn).
"""
import inspect

from app.utils.serial_reads_nudge import (
    FIRST_NOTE_AT, REPEAT_EVERY, SerialReadTracker, serial_reads_note, should_note,
)

RO_SHELL = ("run_shell_command", {"command": "grep -n foo app/x.py"})
RO_SEARCH = ("InternalSearch", {"query": "x"})
WRITE = ("file_write", {"path": ".ziya/x", "content": "y"})
MUT_SHELL = ("run_shell_command", {"command": "rm -rf /tmp/x"})


def _streak(tracker, calls_per_iter):
    return [tracker.observe(c) for c in calls_per_iter]


def test_fires_on_the_second_consecutive_lone_read_not_the_first():
    notes = _streak(SerialReadTracker(), [[RO_SHELL], [RO_SEARCH]])
    assert notes[0] is None
    assert notes[1] is not None and "2nd consecutive message" in notes[1]
    assert "concurrently" in notes[1] and "same message" in notes[1]


def test_repeats_on_a_schedule_not_every_iteration():
    t = SerialReadTracker()
    notes = _streak(t, [[RO_SHELL]] * 9)
    fired = [i + 1 for i, n in enumerate(notes) if n]
    assert fired == [2, 5, 8]
    assert [should_note(n) for n in range(1, 10)] == [n in fired for n in range(1, 10)]
    assert t.notes_sent == 3
    assert FIRST_NOTE_AT == 2 and REPEAT_EVERY == 3


def test_a_batched_message_resets_the_streak():
    t = SerialReadTracker()
    assert _streak(t, [[RO_SHELL], [RO_SHELL, RO_SEARCH], [RO_SHELL]]) == [None, None, None]
    assert t.streak == 1  # the trailing single read restarts a fresh streak
    assert t.observe([RO_SEARCH]) is not None


def test_a_write_or_mutating_shell_call_resets_the_streak():
    for reset in (WRITE, MUT_SHELL):
        t = SerialReadTracker()
        assert _streak(t, [[RO_SHELL], [reset], [RO_SHELL]]) == [None, None, None]


def test_a_lone_write_is_never_nudged():
    """Serial writes are the correct shape (the runner treats them as
    barriers); the note is only about reads that would have overlapped."""
    assert _streak(SerialReadTracker(), [[WRITE]] * 6) == [None] * 6


def test_an_empty_iteration_resets():
    t = SerialReadTracker()
    assert _streak(t, [[RO_SHELL], [], [RO_SHELL]]) == [None, None, None]


def test_classification_is_the_batch_runners_predicate():
    """Seam: the nudge must agree with app.tool_batch about what a read is,
    otherwise it nags about calls the runner would not have overlapped."""
    from app import tool_batch
    from app.utils import serial_reads_nudge as mod
    src = inspect.getsource(mod._is_read_only)
    assert "from app.tool_batch import is_read_only_call" in src
    assert mod._is_read_only(*RO_SHELL) == tool_batch.is_read_only_call(*RO_SHELL) is True
    assert mod._is_read_only(*MUT_SHELL) == tool_batch.is_read_only_call(*MUT_SHELL) is False


def test_note_wording_and_ordinals():
    assert "1st" in serial_reads_note(1)
    assert "3rd" in serial_reads_note(3)
    assert "11th" in serial_reads_note(11)
    assert "22nd" in serial_reads_note(22)
    assert serial_reads_note(2).startswith("[System:") and serial_reads_note(2).endswith("]")


def test_executor_constructs_a_tracker_per_stream_and_routes_the_note():
    """The hop into the executor: a tracker is created where the other
    per-stream counters live, fed the iteration's (normalized name, args)
    pairs after the tool-result turn is appended, and its note goes through
    _append_to_last_user_turn like the already-shown note."""
    from app import streaming_tool_executor as ste
    # stream_with_tools is a thin wrapper; the iteration loop (and the
    # per-stream counters this tracker sits beside) is in _stream_with_tools_impl.
    src = inspect.getsource(ste.StreamingToolExecutor._stream_with_tools_impl)
    assert "SerialReadTracker()" in src
    assert "_serial_reads.observe(" in src
    i_obs = src.index("_serial_reads.observe(")
    i_note = src.index("_append_to_last_user_turn", i_obs)
    assert i_note - i_obs < 1500, "the observed note must be attached right where it is produced"
    # Constructed before the iteration loop, i.e. once per stream, not per iteration.
    assert src.index("SerialReadTracker()") < src.index("tools_executed_this_iteration = False")
