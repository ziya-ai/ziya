"""Process-local registry of live task-run coroutines.

The launch path spawns each run with ``asyncio.create_task`` and used to
drop the handle.  Soft-cancel (``cancel_requested``) is honored only at
block boundaries, so a run whose in-flight Task invocation never returns
-- a hung tool call, a model stream that stalls without erroring -- could
not be stopped from inside the process at all: the tile's cancel button
set a flag nobody would ever read, and the run sat "running" for hours.
Keeping the handle here is what lets the cancel endpoint interrupt such
a run in place.

A module-level dict rather than a field on TaskRunStorage because the
API layer constructs a fresh storage per request; anything hung off a
storage instance is invisible to the request that needs it.  The
cross-process liveness question (is SOME process running this?) stays
with the storage's flock; this module answers only "is it THIS process,
and if so, hand me the task".

``abort`` also records intent.  The coroutine's ``CancelledError``
handler needs to distinguish a deliberate force-stop (record the run as
held so it can be resumed) from the event loop shutting down (leave the
record alone; the startup reconciler owns that case), and the exception
itself carries no such information.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional, Set

_LIVE: Dict[str, "asyncio.Task[object]"] = {}
_ABORT_REQUESTED: Set[str] = set()


def register(run_id: str, task: "asyncio.Task[object]") -> None:
    _LIVE[run_id] = task


def unregister(run_id: str) -> None:
    _LIVE.pop(run_id, None)
    _ABORT_REQUESTED.discard(run_id)


def get(run_id: str) -> Optional["asyncio.Task[object]"]:
    """The run's task if it is registered and not yet finished."""
    task = _LIVE.get(run_id)
    if task is not None and task.done():
        _LIVE.pop(run_id, None)
        return None
    return task


def consume_abort(run_id: str) -> bool:
    """True once, iff ``abort`` was called for this run.  Called from the
    coroutine's CancelledError handler."""
    if run_id in _ABORT_REQUESTED:
        _ABORT_REQUESTED.discard(run_id)
        return True
    return False


async def abort(run_id: str, wait_s: float = 5.0) -> bool:
    """Cancel the run's coroutine and wait up to ``wait_s`` for it to
    unwind.  Returns True iff a live task was found in this process.

    Cancellation is delivered at the coroutine's next ``await``, which
    for a hung tool call or stalled stream is the await it is stuck on,
    so it normally unwinds promptly.  The wait is bounded because a
    coroutine blocked in a synchronous call cannot be interrupted; the
    caller falls back to recording the abort directly in that case.
    """
    task = get(run_id)
    if task is None:
        return False
    _ABORT_REQUESTED.add(run_id)
    task.cancel()
    try:
        await asyncio.wait({task}, timeout=wait_s)
    except Exception:  # noqa: BLE001 -- best effort; the caller re-reads disk
        pass
    return True
