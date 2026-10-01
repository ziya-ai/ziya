"""Tell each iteration of a PARALLEL fan-out what it owns.

A parallel Repeat runs N copies of its body concurrently against one shared
workspace.  Each copy's instructions carry only its own ``{{item}}``; nothing
told the agent that the other items exist, are being worked on right now,
and belong to someone else.  On 2026-09-27 (memory-replay card, run
8c5deca5) iteration 2 of a 3-wide fan-out owned ``sep-current-aa``, read
all three runs' progress files, and advanced ``sep-baseline`` instead -- a
run its sibling owned -- so two items got no work and a third risked two
writers.  Card wording ("never touch another run") fixes one card; this
fixes every card.

``fanout_descriptor`` is built by the block executor per iteration and
carried on the task iteration context; ``fanout_ownership_notice`` renders
it into the task's system prompt.  Pure functions, no I/O.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Peers listed by name up to this many; beyond it only the count is given,
# so a 200-wide fan-out does not put 200 labels in every iteration's prompt.
MAX_LISTED_PEERS = 20
_LABEL_MAX = 80


def _label(value: Any) -> str:
    s = str(value)
    return s if len(s) <= _LABEL_MAX else s[:_LABEL_MAX - 1] + "…"


def fanout_descriptor(loop_name: Optional[str], index: int, width: int,
                      labels: List[str]) -> Dict[str, Any]:
    """Descriptor for iteration ``index`` of a parallel loop whose
    iterations are labelled ``labels`` (one per iteration, in order)."""
    own = _label(labels[index]) if 0 <= index < len(labels) else f"#{index}"
    peers = [_label(l) for i, l in enumerate(labels) if i != index]
    return {"loop": loop_name or "", "index": index, "width": width,
            "item": own, "peers": peers}


def fanout_ownership_notice(desc: Optional[Dict[str, Any]]) -> Optional[str]:
    """System-prompt text for a fan-out iteration, or None outside one."""
    if not desc or int(desc.get("width") or 0) < 2:
        return None
    peers = list(desc.get("peers") or [])
    shown = ", ".join(repr(p) for p in peers[:MAX_LISTED_PEERS])
    more = len(peers) - MAX_LISTED_PEERS
    if more > 0:
        shown += f", and {more} more"
    loop = f" {desc['loop']!r}" if desc.get("loop") else ""
    return (
        "PARALLEL FAN-OUT OWNERSHIP\n"
        f"You are iteration {int(desc['index']) + 1} of {int(desc['width'])} of the "
        f"parallel loop{loop}, running CONCURRENTLY with the others.\n"
        f"Your item: {desc['item']!r}.  Work ONLY on your item.\n"
        f"The other items ({shown}) are owned by sibling iterations that are "
        "running now.  Do not advance, modify, restart or 'help' them, even if "
        "they look stalled, unstarted or behind yours -- a sibling that appears "
        "idle may simply not have reached that step, and acting on its item "
        "races it.  Reading shared state for context is fine; acting on a "
        "peer's item is not.  If your item cannot proceed, say so in your "
        "summary and stop rather than switching items."
    )
