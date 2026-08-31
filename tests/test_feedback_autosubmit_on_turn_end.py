"""Feedback stranded by a turn ending must be auto-submitted, not "deferred".

Background — why the previous designs could not work:

  * The executor's teardown straggler path re-enqueued leftover feedback onto
    the conversation's feedback queue "for the next turn".  Nothing drains that
    queue until a new turn starts and spawns a feedback monitor, so if the user
    never sent another message the text sat there forever.
  * Even a chunk yielded from teardown cannot reach the browser: server.py's
    SSE relay ``break``s on ``stream_end``, and the ordinary turn-end path
    yields ``stream_end`` before leaving the agent loop.  Everything after the
    loop is downstream of the relay's exit.
  * The recovery then lived in the composer (SendChatContainer), gated on the
    stranded feedback belonging to the VIEWED conversation.  A turn that
    stranded feedback while the user was elsewhere was therefore not recovered
    when it ended — it was recovered whenever the user next navigated back,
    firing an unexpected turn on arrival.  It also built its history from the
    viewed conversation's messages, so it could not have targeted another
    conversation even if the gate were removed.

The recovery now lives in FeedbackRecoveryWatcher, which watches
``streamingConversations`` for EVERY conversation and resubmits into whichever
one stranded the text, using that conversation's own history.  Retention lives
in a module-level store keyed by conversation.

These tests pin the seams that behavioural tests cannot see:
  * the backend must not re-enqueue (a queued copy would be injected a second
    time on top of the frontend's resubmission),
  * the store must be keyed by conversation,
  * the composer must record into it and must NOT recover from it,
  * the watcher must not gate recovery on the viewed conversation,
  * and the watcher must actually be MOUNTED — a recovery component that is
    written but never rendered is the exact failure this file exists to catch.
"""

import re

EXECUTOR = "app/streaming_tool_executor.py"
COMPOSER = "frontend/src/components/SendChatContainer.tsx"
WATCHER = "frontend/src/components/FeedbackRecoveryWatcher.tsx"
STORE = "frontend/src/utils/feedbackRetention.ts"
APP = "frontend/src/components/App.tsx"


def _read(path: str) -> str:
    return open(path).read()


def _straggler_region() -> str:
    """The teardown recovery block, located by name rather than line number."""
    src = _read(EXECUTOR)
    start = src.index("FEEDBACK_STRAGGLER")
    # The block ends at the autocompaction hook that follows it.
    end = src.index("Autocompaction hook", start)
    return src[start - 2000:end]


# ── backend: no re-enqueue ────────────────────────────────────────────────

def test_teardown_does_not_reenqueue_feedback():
    """A re-enqueued copy duplicates the frontend's resubmission.

    Both mechanisms firing means the model sees the same feedback twice: once
    as the resubmitted user turn, once injected by the next turn's monitor.
    """
    region = _straggler_region()
    assert "_enqueue_feedback" not in region, (
        "teardown still re-enqueues stranded feedback onto the conversation "
        "feedback queue; the frontend now resubmits the same text, so this "
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
    src = _read("app/server.py")
    idx = src.index("chunk.get('type') == 'stream_end'")
    assert "break" in src[idx:idx + 120], (
        "relay no longer breaks on stream_end — teardown-emitted chunks may "
        "now be deliverable, so the backend could carry the recovery itself"
    )


# ── the retention store ───────────────────────────────────────────────────

def test_store_is_keyed_by_conversation():
    """A single slot silently discarded one conversation's text when another
    conversation sent feedback."""
    src = _read(STORE)
    assert re.search(r"new Map<string,\s*string\[\]>\(\)", src), (
        "the retention store is not a per-conversation Map; a single slot "
        "loses one conversation's retained feedback as soon as feedback is "
        "sent in another"
    )


def test_store_take_is_atomic():
    """Two observers of the same turn end must not both resubmit.

    React 18 StrictMode double-invokes effects, and a remount mid-flight
    replays the transition, so the take must remove before it returns.
    """
    src = _read(STORE)
    fn = src[src.index("export function takeRetainedFeedback"):]
    fn = fn[:fn.index("\n}") + 2]
    assert "retained.delete(" in fn, (
        "takeRetainedFeedback does not remove the entry, so a repeated "
        "turn-end observation resubmits the same feedback twice"
    )


def test_store_is_not_react_state():
    """Presence-independence requires the retention to outlive any mount."""
    src = _read(STORE)
    assert "useState" not in src and "useRef" not in src, (
        "the retention store uses React state, so it is scoped to a mounted "
        "component again — the recovery must not depend on what is rendered"
    )


# ── the composer records, and does NOT recover ────────────────────────────

def test_composer_records_sent_feedback_into_the_store():
    """Recovery is impossible if the text is never retained.

    sendToolFeedback clears the editor on success, so the store holds the only
    surviving copy.
    """
    src = _read(COMPOSER)
    send_fn = src[src.index("const sendToolFeedback"):src.index("const handleSend")]
    assert "recordSentFeedback(" in send_fn, (
        "sendToolFeedback does not record the feedback text, so a turn that "
        "ends without consuming it has nothing to recover"
    )


def test_composer_retires_on_the_ack_conversation_not_the_viewed_one():
    """Gating retirement on the viewed conversation was the duplicate-send bug:
    an ack arriving while the user was elsewhere retired nothing."""
    src = _read(COMPOSER)
    handler = src[src.index("const handleDelivered"):src.index("feedbackDelivered', handleDelivered")]
    assert "retireDeliveredFeedback(" in handler, (
        "a delivery ack does not retire the retained copy, so successfully "
        "delivered feedback gets resubmitted a second time"
    )
    assert "retireDeliveredFeedback(ackConvId" in handler, (
        "retirement is not keyed on the ACK's conversation; an ack arriving "
        "while the user views another conversation would retire nothing"
    )


def test_composer_no_longer_resubmits():
    """The composer can only ever see the viewed conversation, so a recovery
    living here is necessarily presence-coupled."""
    src = _read(COMPOSER)
    assert "submitRecoveredFeedback" not in src, (
        "the composer still owns the resubmission; it must live in "
        "FeedbackRecoveryWatcher, which is not scoped to the viewed "
        "conversation"
    )
    assert "takeRetainedFeedback" not in src, (
        "the composer drains the retention store, which races the watcher "
        "for the same text"
    )


def test_composer_no_longer_advertises_a_deferral():
    src = _read(COMPOSER)
    assert "Deferred to next turn" not in src, (
        "the composer still shows 'Deferred to next turn' for feedback that "
        "nothing will deliver"
    )
    assert "'undelivered'" not in src, (
        "the 'undelivered' terminal state is gone; feedback is either "
        "delivered or resubmitted"
    )


# ── the watcher recovers, presence-independently ──────────────────────────

def test_watcher_resubmits_without_regard_to_the_viewed_conversation():
    """The correction this refactor implements: feedback stays on the delivery
    path to the conversation it was typed into, wherever the user is."""
    src = _read(WATCHER)
    effect = src[src.index("previousStreamingRef.current"):]
    decision = effect[:effect.index("}, [streamingConversations")]
    assert "currentConversationId" not in decision, (
        "the recovery decision still consults the viewed conversation; a turn "
        "that strands feedback while the user is elsewhere would again be "
        "recovered only on navigating back"
    )
    assert "takeRetainedFeedback(" in decision


def test_watcher_sends_the_stranded_conversations_own_history():
    """Sending the viewed conversation's messages would deliver the feedback
    into the wrong context."""
    src = _read(WATCHER)
    fn = src[src.index("const resubmit"):src.index("useEffect(")]
    assert "conversationsRef.current.find" in fn, (
        "the recovery does not look up the target conversation's messages; it "
        "would send whatever the user is currently looking at"
    )
    assert "conversationId," in fn, (
        "send() is not given an explicit conversationId, so it defaults to "
        "the viewed conversation"
    )


def test_watcher_starts_a_real_turn():
    """The recovery must send, not merely stage a message."""
    src = _read(WATCHER)
    fn = src[src.index("const resubmit"):src.index("useEffect(")]
    assert "addMessageToConversation" in fn
    assert "addStreamingConversation" in fn
    assert "await send(" in fn, (
        "the watcher never calls send(); the stranded feedback is staged but "
        "not submitted"
    )
    assert "editorRef" not in fn, (
        "the recovery writes through the editor, which would clobber anything "
        "the user typed after the turn ended"
    )


def test_watcher_is_actually_mounted():
    """A recovery component that is never rendered recovers nothing.

    This is the seam every other test in this file assumes: the store, the
    composer's record call, and the watcher's logic can all be correct while
    the watcher is absent from the tree.
    """
    src = _read(APP)
    assert "FeedbackRecoveryWatcher" in src, (
        "FeedbackRecoveryWatcher is not referenced by App.tsx"
    )
    assert re.search(r"<FeedbackRecoveryWatcher\s*/>", src), (
        "FeedbackRecoveryWatcher is imported but never rendered, so no "
        "turn-end recovery ever runs"
    )
