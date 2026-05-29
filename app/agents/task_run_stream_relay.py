"""
Task Run Stream Relay — pushes live block-executor events to WebSocket
clients.  Module-level singleton called by block_executor on every
event boundary (run_started, iteration_completed, etc.).

Events accumulate in a bounded per-run history buffer so a client
that connects mid-run (or reconnects after navigating away) gets a
replay of what it missed.  Persisted storage remains the source of
truth for completed iterations and the final artifact; the buffer
covers the streaming gap between the last persisted iteration and
the live tail.  See design/task-cards.md §Live observation.

On-the-fly collapse:
    Adjacent ``task_text_delta`` events for the same ``block_id``
    are folded into one ``task_text_delta_run`` entry as they're
    pushed.  A 50-token streaming response would otherwise consume
    ~50 buffer slots per call site; folded, it's one slot.  Mirrors
    the frontend's collapseEventRuns (frontend/src/components/
    TaskCard/eventLog.ts).  Replay sends the collapsed entries
    directly — the frontend already handles task_text_delta_run.

Modeled on app/agents/delegate_stream_relay.py.
"""

import asyncio
from collections import deque
from typing import Any, Deque, Dict, List

from app.utils.logging_utils import logger

# run_id → list of WebSocket objects
_active_connections: Dict[str, List[Any]] = {}
# run_id → bounded ring buffer of recent events (for replay).
# Bounded to prevent a long-lived run from consuming arbitrary
# memory; on-the-fly delta collapse keeps the slot count well
# below the cap for typical workloads.
_history: Dict[str, Deque[Dict[str, Any]]] = {}
# run_id → grace-period drop task.  Set when a run terminates;
# fires after _GRACE_PERIOD_SECONDS so late connectors can still
# replay the tail.  Cancelled if a new event arrives for the same
# run_id (shouldn't happen for terminated runs, but defensive).
_drop_tasks: Dict[str, asyncio.Task] = {}
_lock = asyncio.Lock()

# Cap chosen to comfortably hold a multi-hour run's lifecycle
# events plus collapsed text-delta runs.  At ~200 bytes per dict
# this caps per-run buffer memory at roughly 200 KB.
_HISTORY_CAP = 1000
# How long after a terminal event we keep history before dropping.
# Long enough for a tab-switch reconnect to land but short enough
# to avoid leaking through long-lived server processes with many
# completed runs.
_GRACE_PERIOD_SECONDS = 300.0  # 5 minutes

# Event types that mark a run as terminated for buffer-drop purposes.
_TERMINAL_EVENT_TYPES = {"run_completed"}


def _record(run_id: str, event: Dict[str, Any]) -> None:
    """Append ``event`` to the run's history buffer, collapsing
    adjacent same-block ``task_text_delta`` events as we go.

    Pure mutation of ``_history``; caller holds whatever
    synchronization is appropriate (here we rely on the asyncio
    single-thread guarantee — push is awaited from one event loop).
    """
    buf = _history.get(run_id)
    if buf is None:
        buf = deque(maxlen=_HISTORY_CAP)
        _history[run_id] = buf

    if event.get("type") == "task_text_delta":
        block_id = event.get("block_id")
        content = event.get("content", "")
        # Fold into the previous entry if it's a run for the same block.
        if buf and buf[-1].get("type") == "task_text_delta_run" \
                and buf[-1].get("block_id") == block_id:
            last = buf[-1]
            last["count"] += 1
            last["content"] = last.get("content", "") + content
            return
        # Start a new run entry.
        buf.append({
            "type": "task_text_delta_run",
            "block_id": block_id,
            "count": 1,
            "content": content,
        })
        return

    buf.append(event)


async def _drop_after_grace(run_id: str, delay: float) -> None:
    """Coroutine that waits ``delay`` seconds and clears the run's
    history buffer.  Cancellable; cancellation leaves history
    intact for the next caller to schedule again."""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    _history.pop(run_id, None)
    _drop_tasks.pop(run_id, None)
    logger.debug(
        f"📡 TASK_RUN_RELAY: dropped history for {run_id[:8]} after grace"
    )


async def connect(run_id: str, ws: Any) -> None:
    """Register a WebSocket for a task run's event stream."""
    async with _lock:
        if run_id not in _active_connections:
            _active_connections[run_id] = []
        _active_connections[run_id].append(ws)
        logger.info(
            f"📡 TASK_RUN_RELAY: Client connected for {run_id[:8]} "
            f"({len(_active_connections[run_id])} clients)"
        )
    # Replay buffered history to the new connector.  Done outside
    # the lock since send_json may await on a slow client and we
    # don't want to stall other connect/disconnect calls.  Errors
    # are swallowed: a closed socket here just means the client
    # gave up before we finished the replay; the live event stream
    # will hit the same failure and get pruned by push().
    history = list(_history.get(run_id, ()))
    for event in history:
        try:
            await ws.send_json(event)
        except Exception as exc:
            logger.debug(f"task_run_stream_relay.connect replay failed (non-fatal): {exc}")
            return


async def disconnect(run_id: str, ws: Any) -> None:
    async with _lock:
        if run_id in _active_connections:
            _active_connections[run_id] = [
                w for w in _active_connections[run_id] if w is not ws
            ]
            if not _active_connections[run_id]:
                del _active_connections[run_id]
            logger.debug(f"📡 TASK_RUN_RELAY: Client disconnected for {run_id[:8]}")


async def push(run_id: str, event: Dict[str, Any]) -> None:
    """Broadcast an event to all connected clients for a run.

    Non-blocking in the sense that dead sockets are pruned but errors
    are swallowed — the block executor must never stall on a slow
    client.  Events are recorded in the per-run history buffer
    regardless of connected-client count so a future reconnect can
    replay.
    """
    # Record before fanout so even a push that fails to reach any
    # client is preserved for replay.  This is the whole point of
    # the buffer: a client connecting mid-run sees what happened
    # while it was disconnected.
    _record(run_id, event)

    # If this event terminates the run, schedule a delayed drop.
    if event.get("type") in _TERMINAL_EVENT_TYPES:
        existing = _drop_tasks.get(run_id)
        if existing and not existing.done():
            existing.cancel()
        _drop_tasks[run_id] = asyncio.create_task(
            _drop_after_grace(run_id, _GRACE_PERIOD_SECONDS)
        )

    conns = _active_connections.get(run_id, [])
    if not conns:
        return

    dead: List[Any] = []
    for ws in conns:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)

    if dead:
        async with _lock:
            if run_id in _active_connections:
                _active_connections[run_id] = [
                    w for w in _active_connections[run_id] if w not in dead
                ]
                if not _active_connections[run_id]:
                    del _active_connections[run_id]


async def safe_push(run_id: str, event: Dict[str, Any]) -> None:
    """Best-effort push that never raises.

    Live observation is optional — if the relay module fails to import,
    the connection registry is corrupt, or any other error escapes
    ``push``, we swallow it.  Task execution progress must not depend
    on whether anyone is listening.
    """
    try:
        await push(run_id, event)
    except Exception as exc:
        logger.debug(f"task_run_stream_relay.safe_push failed (non-fatal): {exc}")


def has_clients(run_id: str) -> bool:
    return bool(_active_connections.get(run_id))
