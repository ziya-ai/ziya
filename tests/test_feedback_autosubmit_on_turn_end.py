"""Feedback stranded by a turn ending must be auto-submitted, not "deferred".

Background — why the previous design could not work:

  * The executor's teardown straggler path re-enqueued leftover feedback onto
    the conversation's feedback queue "for the next turn".  Nothing drains that
    queue until a new turn starts and spawns a feedback monitor, so if the user
    never sent another message the text sat there forever.
  * Even a chunk yielded from teardown cannot reach the browser: server.py's
    SSE relay ``break``s on ``stream_end``, and the ordinary turn-end path
    yields ``stream_end`` before leaving the agent loop.  Everything after the
    loop is downstream of the relay's exit.

The recovery therefore belongs in the composer, which retained the text it
sent.  These tests pin both halves: the backend must not re-enqueue (a queued
copy would be injected a second time on top of the composer's resubmission),
and the composer must retain, auto-submit, and stop advertising a deferral
that never happens.
"""

import re

EXECUTOR = "app/streaming_tool_executor.py"
COMPOSER = "frontend/src/components/SendChatContainer.tsx"


def _straggler_region() -> str:
    """The teardown recovery block, located by name rather than line number."""
    src = open(EXECUTOR).read()
    start = src.index("FEEDBACK_STRAGGLER")
    # The block ends at the autocompaction hook that follows it.
    end = src.index("Autocompaction hook", start)
    return src[start - 2000:end]


# ── backend: no re-enqueue ────────────────────────────────────────────────

def test_teardown_does_not_reenqueue_feedback():
    """A re-enqueued copy duplicates the composer's auto-submission.

    Both mechanisms firing means the model sees the same feedback twice: once
    as the auto-submitted user turn, once injected by the next turn's monitor.
    """
    region = _straggler_region()
    assert "_enqueue_feedback" not in region, (
        "teardown still re-enqueues stranded feedback onto the conversation "
        "feedback queue; the composer now auto-submits the same text, so this "
        "produces a duplicate injection"
    )


def test_teardown_still_collects_staged_and_pending_feedback():
    """Dropping the re-enqueue must not drop the detection.

    The warning is the only signal that a turn stranded feedback at all; the
    staged-recovery drain is what makes the tool-boundary case visible.
    """
    region = _straggler_region()
    assert "deferred_feedback_messages" in region
    assert "_drain_pending_feedback()" in region
    assert re.search(r"logger\.warning\(", region), (
        "stranded feedback is now silent — nothing logs that a turn ended "
        "without consuming it"
    )


def test_teardown_does_not_yield_to_a_relay_that_has_already_exited():
    """Guard against 'fixing' this by emitting a chunk from teardown.

    server.py's relay breaks on stream_end, which the ordinary turn-end path
    yields before leaving the loop, so a post-loop yield reaches nobody.
    """
    region = _straggler_region()
    assert "feedback_undelivered" not in region, (
        "teardown yields a feedback_undelivered chunk, but the SSE relay has "
        "already broken on stream_end by then — the chunk cannot reach the "
        "browser"
    )


def test_relay_still_breaks_on_stream_end():
    """The premise of the test above; if this changes, re-evaluate the design."""
    src = open("app/server.py").read()
    idx = src.index("chunk.get('type') == 'stream_end'")
    assert "break" in src[idx:idx + 120], (
        "relay no longer breaks on stream_end — teardown-emitted chunks may "
        "now be deliverable, so the backend could carry the recovery itself"
    )


# ── frontend: retain, auto-submit, stop lying ─────────────────────────────

def test_composer_no_longer_advertises_a_deferral():
    src = open(COMPOSER).read()
    assert "Deferred to next turn" not in src, (
        "the composer still shows 'Deferred to next turn' for feedback that "
        "nothing will deliver"
    )
    assert "'undelivered'" not in src, (
        "the 'undelivered' terminal state is gone; feedback is either "
        "delivered or resubmitted"
    )


def test_composer_retains_sent_feedback_text():
    """Auto-submission is impossible if the text is not kept.

    sendToolFeedback clears the editor on success, so the only surviving copy
    is the one it stashes.
    """
    src = open(COMPOSER).read()
    assert "pendingFeedbackRef" in src
    send_fn = src[src.index("const sendToolFeedback"):src.index("const handleSend")]
    assert "pendingFeedbackRef.current = {" in send_fn, (
        "sendToolFeedback does not retain the feedback text, so a turn that "
        "ends without consuming it has nothing to auto-submit"
    )


def test_retained_feedback_is_bound_to_its_conversation():
    """Switching tabs must not resubmit one conversation's text into another."""
    src = open(COMPOSER).read()
    assert re.search(
        r"pendingFeedbackRef\s*=\s*useRef<\{\s*conversationId:", src), (
        "retained feedback is not keyed by conversation; a tab switch would "
        "auto-submit it into whichever conversation is on screen"
    )
    assert "stranded.conversationId === currentConversationId" in src, (
        "the auto-submit path does not check that the stranded feedback "
        "belongs to the conversation it is about to be sent to"
    )


def test_delivered_ack_prunes_the_retained_copy():
    """Otherwise every delivered feedback is also resubmitted as a new turn."""
    src = open(COMPOSER).read()
    handler = src[src.index("const handleDelivered"):src.index("feedbackDelivered', handleDelivered")]
    assert "pendingFeedbackRef" in handler, (
        "a delivery ack does not clear the retained copy, so successfully "
        "delivered feedback gets auto-submitted a second time"
    )


def test_auto_submit_starts_a_real_turn():
    """The recovery must send, not merely restore text to the input box."""
    src = open(COMPOSER).read()
    fn = src[src.index("const submitRecoveredFeedback = useCallback"):]
    fn = fn[:fn.index("submitRecoveredFeedbackRef.current =")]
    assert "addMessageToConversation" in fn
    assert "addStreamingConversation" in fn
    assert "await send(" in fn, (
        "submitRecoveredFeedback never calls send(); the stranded feedback is "
        "staged but not submitted"
    )
    assert "editorRef" not in fn, (
        "the recovery writes through the editor, which would clobber anything "
        "the user typed after the turn ended"
    )
