"""Feedback left over from a dead turn must not be injected into the next one.

Reproduction this pins (observed 2026-11-09): message A errors mid-stream
after feedback B was typed; the frontend recovers B as its own turn, which
also errors.  Both A and B now sit unanswered.  The user edits A and submits,
truncating B away -- and the new turn immediately shows "Feedback received:
B", because the server-side feedback stores (``feedback_queues`` and
``pending_feedback_lists``) survive a turn ending and were never discarded at
the start of a NEW turn.  Those survivals are intentional for a reload
mid-turn and for the diff-validation retry inside one request, so the
discard is gated on no generator being bound to the conversation, and the
queue is further gated on an enqueue timestamp against the last generator's
exit instant so feedback typed between clicking Send and the server binding
the new turn is kept.
"""

import time

import pytest

srv = pytest.importorskip("app.server")


CID = "conv-stale-feedback-test"
STORES = ("feedback_queues", "pending_feedback_lists",
          "pending_feedback_refcounts", "last_generator_exit")


@pytest.fixture(autouse=True)
def _clean_stores():
    for name in STORES:
        getattr(srv, name).pop(CID, None)
    yield
    for name in STORES:
        getattr(srv, name).pop(CID, None)


def test_enqueue_stamps_items():
    srv._enqueue_feedback(CID, {"type": "tool_feedback", "message": "x"})
    item = srv.feedback_queues[CID].get_nowait()
    assert isinstance(item["_enqueued_at"], float)
    assert item["_enqueued_at"] <= time.monotonic()


def test_enqueue_keeps_an_existing_stamp():
    srv._enqueue_feedback(CID, {"type": "tool_feedback", "message": "x",
                                "_enqueued_at": 1.0})
    assert srv.feedback_queues[CID].get_nowait()["_enqueued_at"] == 1.0


def test_discards_both_stores_when_items_predate_the_last_exit():
    srv._enqueue_feedback(CID, {"type": "tool_feedback", "message": "B (queue)"})
    srv.pending_feedback_lists[CID] = [{"type": "feedback", "message": "B (staged)"}]
    srv.last_generator_exit[CID] = time.monotonic()  # the dead turn ended after B

    dropped = srv.discard_stale_feedback(CID)

    assert dropped == 2
    assert CID not in srv.pending_feedback_lists
    assert srv.feedback_queues[CID].empty()


def test_keeps_queue_items_enqueued_after_the_last_exit():
    """The Send-click race: feedback typed after the dead turn ended but
    before the new turn bound belongs to the new turn."""
    srv._enqueue_feedback(CID, {"type": "tool_feedback", "message": "old"})
    srv.last_generator_exit[CID] = time.monotonic()
    srv._enqueue_feedback(CID, {"type": "tool_feedback", "message": "new"})
    srv.pending_feedback_lists[CID] = [{"type": "feedback", "message": "staged"}]

    dropped = srv.discard_stale_feedback(CID)

    assert dropped == 2  # "old" + the staged item
    q = srv.feedback_queues[CID]
    assert q.qsize() == 1
    assert q.get_nowait()["message"] == "new"
    assert CID not in srv.pending_feedback_lists


def test_unstamped_queue_item_counts_as_stale():
    srv.get_feedback_queue(CID).put_nowait({"type": "tool_feedback", "message": "?"})
    srv.last_generator_exit[CID] = time.monotonic()
    assert srv.discard_stale_feedback(CID) == 1
    assert srv.feedback_queues[CID].empty()


def test_no_recorded_exit_keeps_the_queue_but_drops_the_staged_list():
    """First turn in this process: nothing in the queue can be stale, but a
    staged item with no generator bound was necessarily left by one."""
    srv._enqueue_feedback(CID, {"type": "tool_feedback", "message": "first"})
    srv.pending_feedback_lists[CID] = [{"type": "feedback", "message": "staged"}]

    assert srv.discard_stale_feedback(CID) == 1
    assert srv.feedback_queues[CID].qsize() == 1
    assert CID not in srv.pending_feedback_lists


def test_keeps_everything_while_a_generator_is_bound():
    """Reload-mid-turn: the running executor still owns this feedback."""
    srv.last_generator_exit[CID] = 0.0
    srv._enqueue_feedback(CID, {"type": "tool_feedback", "message": "live"})
    srv.pending_feedback_lists[CID] = [{"type": "feedback", "message": "staged"}]
    srv.pending_feedback_refcounts[CID] = 1

    assert srv.discard_stale_feedback(CID) == 0
    assert srv.pending_feedback_lists[CID] == [{"type": "feedback", "message": "staged"}]
    assert srv.feedback_queues[CID].qsize() == 1


def test_noop_on_empty_or_missing_conversation():
    assert srv.discard_stale_feedback(CID) == 0
    assert srv.discard_stale_feedback("") == 0
    assert srv.discard_stale_feedback(None) == 0


def test_next_turn_does_not_rebind_the_stale_list():
    """The seam the executor relies on: ``setdefault`` at generator start
    must now find nothing left from the previous turn."""
    srv.pending_feedback_lists[CID] = [{"type": "feedback", "message": "B"}]
    srv.discard_stale_feedback(CID)
    assert srv.pending_feedback_lists.setdefault(CID, []) == []


# ── seams: the helper is only useful if both ends are wired ──────────────

def test_stream_chunks_discards_before_the_first_generator():
    """A correct helper that stream_chunks never calls fixes nothing."""
    src = open("app/server.py").read()
    fn = src[src.index("async def stream_chunks(body):"):]
    call = fn.index("discard_stale_feedback(conversation_id)")
    first_stream = fn.index("executor.stream_with_tools(")
    assert call < first_stream, (
        "stream_chunks must discard stale feedback before any generator binds "
        "the conversation's pending list"
    )
    # Must run after conversation_id is read from the body, not before.
    assert fn.index('conversation_id = body.get("conversation_id")') < call


def test_executor_records_the_exit_instant_when_the_last_generator_unbinds():
    """Without the exit instant the queue gate has nothing to compare
    against and would keep every stale item."""
    src = open("app/streaming_tool_executor.py").read()
    fn = src[src.index("async def stream_with_tools("):src.index("async def _stream_with_tools_impl(")]
    fin = fn[fn.index("finally:"):]
    assert "last_generator_exit[_fb_key] = time.monotonic()" in fin
    # Recorded inside the refcount-reached-zero branch, not on every exit:
    # a concurrent validation-retry generator still running must not move it.
    assert fin.index("<= 0:") < fin.index("last_generator_exit[_fb_key]")
    assert "last_generator_exit" in fn[:fn.index("try:")], (
        "last_generator_exit is used in the finally but not imported")
