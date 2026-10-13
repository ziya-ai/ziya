"""
Chronological placement of an UNANCHORED task binding in a chat history.

A binding carries ``anchor_message_id`` when it was created by a slash
command or a Run click, and nothing when the model created it mid-turn
through ``task_card_stage`` / ``task_card_launch``.  For the latter the
only placement datum is ``created_at``, and the obvious rule — "after the
last message timestamped at or before it" — is wrong in a way that is
easy to miss: the turn's human prompt is stamped BEFORE the tool call and
the assistant reply is stamped when the stream FINISHES, after it.  The
binding therefore lands between the prompt and the reply that staged it,
at the start of the turn, where it reads as something the user posted.

The rule here is "the end of the turn that built it": resolve the anchor
as before, and when it is a human message walk forward past every
non-human message that follows.  A binding created between turns (a Run
click after the reply) anchors to a non-human message and is unaffected.

Mirrors frontend/src/utils/taskBindingPlacement.ts; the two must agree or
the model's view of where a tile sits diverges from the user's.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence


def _is_human(message: Any) -> bool:
    """True for a human turn in either on-the-wire shape.

    ``processed_chat_history`` entries carry ``type`` ("human" / "ai");
    persisted chat records carry ``role`` ("human" / "assistant").  Read
    both so the helper is correct for whichever list a caller hands it.
    """
    if isinstance(message, Mapping):
        kind = message.get("type") or message.get("role")
    else:
        kind = getattr(message, "type", None) or getattr(message, "role", None)
    return kind == "human"


def _timestamp(message: Any) -> Optional[float]:
    if isinstance(message, Mapping):
        ts = message.get("_timestamp")
    else:
        ts = getattr(message, "_timestamp", None)
    # bool is an int subclass; a True timestamp is not a timestamp.
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return float(ts)
    return None


def unanchored_insert_index(history: Sequence[Any], created_at: float) -> int:
    """Index at which a binding created at ``created_at`` is inserted.

    Returns a value in ``0..len(history)`` suitable for ``list.insert``:
    0 when the binding predates every timestamped message (or none is
    timestamped), otherwise one past the last message of the turn the
    binding belongs to.

    Messages without a timestamp never serve as the anchor but are walked
    through when they are non-human messages of the anchoring turn, so a
    reply persisted without one still keeps the binding after itself.
    """
    anchor = -1
    for i, message in enumerate(history):
        ts = _timestamp(message)
        if ts is not None and ts <= created_at:
            anchor = i
    if anchor < 0:
        return 0
    end = anchor
    if _is_human(history[anchor]):
        j = anchor + 1
        while j < len(history) and not _is_human(history[j]):
            end = j
            j += 1
    return end + 1
