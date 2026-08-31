"""The feedback status chip must describe the conversation on screen.

`feedbackStatus` and `pendingFeedbackRef` are single slots on one
SendChatContainer instance shared by every conversation — the component is not
remounted per conversation.  Two defects came out of that:

  * The only reset (`setFeedbackStatus('idle')`) required
    `!isCurrentlyStreaming`, so switching into a conversation that WAS
    streaming left the previous conversation's "📤 Queued — awaiting model…"
    (or "↩️ Turn ended — sending as a new message") on screen with no path back
    to idle for the rest of the session.
  * The delivered-ack prune was gated on the VIEWED conversation, so an ack
    arriving while the user was looking elsewhere pruned nothing and the
    turn-end recovery re-sent feedback the model had already been given.

These tests pin the guards at the seams: the effect must re-derive the chip on
a conversation switch, the prune must key off the ack's conversation, and the
WebSocket ack must carry a conversation id for the frontend to filter on.
Behavioural coverage of the resulting state machine lives in
frontend/src/components/__tests__/feedbackChipLifecycle.test.ts.
"""

import re

COMPOSER = "frontend/src/components/SendChatContainer.tsx"
EXECUTOR = "app/streaming_tool_executor.py"


def _composer() -> str:
    return open(COMPOSER).read()


def _turn_end_effect(src: str) -> str:
    """The [isCurrentlyStreaming, currentConversationId] effect, by content."""
    start = src.index("const stranded = pendingFeedbackRef.current;")
    end = src.index("[isCurrentlyStreaming, currentConversationId]", start)
    return src[start:end]


def _delivered_handler(src: str) -> str:
    start = src.index("const handleDelivered")
    return src[start:src.index("feedbackDelivered', handleDelivered")]


# ── the chip is re-derived on a conversation switch ───────────────────────

def test_effect_tracks_which_conversation_the_chip_describes():
    src = _composer()
    assert "feedbackChipConvRef" in src, (
        "nothing records which conversation the status chip currently "
        "describes, so a switch cannot tell a stale chip from a live one"
    )
    effect = _turn_end_effect(src)
    assert "feedbackChipConvRef.current !== currentConversationId" in effect, (
        "the turn-end effect does not detect a conversation switch"
    )


def test_reset_is_not_gated_solely_on_not_streaming():
    """The exact shape of the stuck-chip bug.

    `if (!isCurrentlyStreaming && ...)` as the ONLY reset means switching into
    a streaming conversation can never clear another conversation's chip.
    """
    effect = _turn_end_effect(_composer())
    assert re.search(r"if \(switched \|\|", effect), (
        "the chip reset still requires !isCurrentlyStreaming, so switching "
        "into a streaming conversation leaves the previous conversation's "
        "status text stuck on screen"
    )


def test_stranded_check_is_scoped_before_it_suppresses_the_reset():
    """Feedback held for conversation A must not pin conversation B's chip."""
    effect = _turn_end_effect(_composer())
    assert "strandedHere" in effect, (
        "the reset tests the unscoped pendingFeedbackRef, so feedback retained "
        "for another conversation suppresses this conversation's reset"
    )
    assert "!stranded)" not in effect, (
        "an unscoped !stranded test remains in the reset condition"
    )
    # The auto-submit path must still refuse to send A's text into B.
    assert "stranded.conversationId === currentConversationId" in effect


# ── delivered ack prunes by the ACK's conversation, not the viewed one ────

def test_delivered_prune_keys_off_the_ack_conversation():
    handler = _delivered_handler(_composer())
    assert "pf.conversationId === ackConvId" in handler, (
        "the delivered ack prunes the retained copy only when the ack's "
        "conversation is the one on screen; an ack arriving while the user is "
        "elsewhere prunes nothing and the text is re-sent at turn end"
    )


def test_delivered_chip_update_is_still_scoped_to_the_viewed_conversation():
    """The prune widened; the chip must not.  It is one shared slot."""
    handler = _delivered_handler(_composer())
    assert "ackConvId === currentConversationId" in handler, (
        "a delivery ack for a background conversation now lights the chip of "
        "whichever conversation is on screen"
    )


# ── the WebSocket queued ack must be attributable ─────────────────────────

def test_ws_queued_ack_carries_its_conversation_id():
    src = open(EXECUTOR).read()
    idx = src.index("'type': 'feedback_status'")
    payload = src[idx:idx + 400]
    assert "'conversation_id': conv_id" in payload, (
        "the feedback_status ack has no conversation id, so the frontend "
        "cannot tell an ack for the viewed conversation from one for a "
        "conversation the user has already left"
    )


def test_frontend_filters_ws_acks_by_conversation():
    src = _composer()
    assert "data.conversation_id !== currentConversationId" in src, (
        "the WebSocket ack handler does not filter by conversation; the "
        "singleton socket follows whichever conversation streamed most "
        "recently, so its ack can light the chip in an unrelated conversation"
    )
    assert "data.conversation_id &&" in src, (
        "the filter drops acks from a backend that does not yet stamp "
        "conversation_id instead of accepting them"
    )
