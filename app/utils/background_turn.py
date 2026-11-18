"""
Background turns: a second model call on a conversation that already has a
turn in progress, without disturbing that turn.

The handoff drawer's "Ask the model to draft it" sends an ordinary
``/api/chat`` request whose stream the client drains and discards; its only
durable effect is the ``handoff_write`` the model performs.  Sent as an
ordinary turn it collides with everything keyed by conversation id:

* ``chat_turn_relay.start_turn`` allows one turn per conversation and cancels
  the running one ("superseded");
* ``apply_ledger.begin_turn`` moves the conversation's turn-start seq, under
  a turn that may be held waiting for its diffs to be applied;
* the executor's feedback monitor and its staged-feedback list are shared by
  conversation id, so the background turn could take a reply the user typed
  for the held turn.

A request carrying ``"background": true`` opts out of all three.  The chat
endpoint streams it directly (no relay, so it is also invisible to the
reattach watcher and dies with its socket), ``stream_chunks`` turns the
diff-apply hold and note off and skips diff validation, and the executor
drains a feedback key of its own (``feedback_key``) that nothing writes to.

The conversation id itself is kept: the request-scoped ContextVar, the
prompt assembly and ``handoff_write`` all need the real one.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional

FIELD = "background"


def is_background(body: Any) -> bool:
    """Whether an ``/api/chat`` request body asks for a background turn.
    Only a literal JSON ``true`` counts."""
    return isinstance(body, dict) and body.get(FIELD) is True


def config_for(body: Any) -> Dict[str, Any]:
    """Entries to merge into the ``stream_chunks`` body's ``config``; empty
    for an ordinary turn.  The hold is off because nobody is at the Apply
    buttons for this response, and the same flag gates ``begin_turn``."""
    return {FIELD: True, "hold_for_diff_apply": False} if is_background(body) else {}


def in_config(body: Any) -> bool:
    """``is_background`` for the body ``stream_chunks`` receives."""
    cfg = body.get("config") if isinstance(body, dict) else None
    return isinstance(cfg, dict) and cfg.get(FIELD) is True


def feedback_key(conversation_id: Optional[str]) -> Optional[str]:
    """The feedback key a background turn drains instead of the
    conversation's.  One per conversation, so repeated drafts reuse it."""
    return f"{conversation_id}#background" if conversation_id else None


def release(conversation_id: Optional[str]) -> None:
    """Drop the feedback stores created for ``feedback_key``.  A no-op while
    a generator is still bound to the key.  Never raises."""
    key = feedback_key(conversation_id)
    if not key:
        return
    try:
        from app import server
        if server.pending_feedback_refcounts.get(key, 0) > 0:
            return
        server.feedback_queues.pop(key, None)
        server.pending_feedback_lists.pop(key, None)
        server.last_generator_exit.pop(key, None)
    except Exception:  # noqa: BLE001 -- cleanup must never break a response
        pass


async def stream(source: AsyncIterator[str], conversation_id: Optional[str]) -> AsyncIterator[str]:
    """``source`` unchanged, releasing the background feedback stores when it
    ends.  On a dropped socket the executor may still be bound when this
    runs; the entry it leaves is reused by the conversation's next draft."""
    try:
        async for frame in source:
            yield frame
    finally:
        release(conversation_id)
