"""Operator steer notes for running Task Card blocks.

A steer note is a message from the operator to ONE running block (or one
iteration of a loop) of a live run.  It reuses the chat mid-stream feedback
channel rather than adding a second inbound path: ``StreamingToolExecutor``
already drains a per-key pending-feedback list at the top of every tool
round and injects each item as a ``[User feedback]`` user turn.  The only
thing that channel lacked for task runs was a *target* — every task in a
run shares ``conversation_id=run_id``, so a note enqueued under the run id
would have reached whichever parallel iteration drained first.

``steer_key`` is that target: the executor binds its feedback list to it
(``feedback_key``), and the API enqueues under it.  Everything else here is
bookkeeping so the run record can say, for each note, whether it is still
``queued``, was ``delivered`` (injected before a model round), or
``expired`` (the block finished without another round).

Kept free of storage and FastAPI imports so it is testable in isolation.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from .resume_targets import find_block, is_loop_node, locate_block

# Bound on the persisted note list per run.  A note is ~200 bytes; a run
# steered a thousand times is not a real workflow, but an unbounded list
# on a record that is re-read on every heartbeat is a real cost.
MAX_STEER_NOTES = 200

STEER_STATUSES = ("queued", "delivered", "expired")


def steer_key(run_id: str, block_id: str, index: Optional[int]) -> str:
    """The feedback-list key one block/iteration listens on.

    ``index`` is None for a block that is not a loop iteration; the literal
    ``-`` keeps the key unambiguous against a real index.
    """
    idx = "-" if index is None else str(int(index))
    return f"steer:{run_id}:{block_id}:{idx}"


def resolve_steer_target(
    card_snapshot: Optional[Dict[str, Any]],
    call_snapshots: Optional[Dict[str, Any]],
    block_id: str,
    index: Optional[int],
) -> Tuple[Optional[Tuple[str, Optional[int]]], Optional[str]]:
    """Normalize a UI target onto the identity the executor emits under.

    The executor tags a task's deltas with ``(block_id, index)`` where
    ``block_id`` is the enclosing loop's id and ``index`` its ordinal, or
    the task's own id and ``None`` outside a loop.  The frontend groups
    live output the same way but synthesizes ``index: 0`` for a bare task,
    so an index is REQUIRED for a loop node and DISCARDED for anything
    else — otherwise a bare task's note would be enqueued under a key
    nobody listens on and sit ``queued`` forever.

    Returns ``((block_id, index), None)`` or ``(None, error)``.
    """
    if not card_snapshot:
        return None, "run has no card_snapshot; cannot resolve the block"
    tree, _chain = locate_block(card_snapshot, call_snapshots, block_id)
    if tree is None:
        return None, f"block {block_id!r} is not in this run's card"
    node = find_block(tree, block_id)
    if node is None:
        return None, f"block {block_id!r} is not in this run's card"
    if is_loop_node(node):
        if index is None or int(index) < 0:
            return None, (
                f"block {block_id!r} is a loop; an iteration index is required"
            )
        return (block_id, int(index)), None
    return (block_id, None), None


def new_steer_note(
    block_id: str, index: Optional[int], text: str, *,
    hold: bool = False, created_at: int,
) -> Dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:12],
        "block_id": block_id,
        "index": index,
        "text": text,
        "hold": bool(hold),
        "status": "queued",
        "created_at": created_at,
        "delivered_at": None,
    }


def _same_target(note: Dict[str, Any], block_id: str, index: Optional[int]) -> bool:
    return note.get("block_id") == block_id and note.get("index") == index


def next_queued(
    notes: List[Dict[str, Any]], block_id: str, index: Optional[int],
) -> Optional[Dict[str, Any]]:
    """Oldest still-queued note for a target, or None.

    Delivery attribution is FIFO per target because the executor's
    ``feedback_delivered`` chunk carries only an 80-char preview, not an
    id, and the pending list it drains is itself FIFO.
    """
    for n in notes:
        if n.get("status") == "queued" and _same_target(n, block_id, index):
            return n
    return None


def queued_for(
    notes: List[Dict[str, Any]], block_id: str, index: Optional[int],
) -> List[Dict[str, Any]]:
    return [
        n for n in notes
        if n.get("status") == "queued" and _same_target(n, block_id, index)
    ]


def trim_notes(notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop the oldest SETTLED notes past the cap; queued ones are kept."""
    if len(notes) <= MAX_STEER_NOTES:
        return notes
    overflow = len(notes) - MAX_STEER_NOTES
    out: List[Dict[str, Any]] = []
    for n in notes:
        if overflow > 0 and n.get("status") != "queued":
            overflow -= 1
            continue
        out.append(n)
    return out
