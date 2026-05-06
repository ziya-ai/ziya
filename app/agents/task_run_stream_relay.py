"""
Task Run Stream Relay — pushes live block-executor events to WebSocket
clients.  Module-level singleton called by block_executor on every
event boundary (run_started, iteration_completed, etc.).

Events are transient; persisted storage remains the source of truth.
See design/task-cards.md §Live observation.

Modeled on app/agents/delegate_stream_relay.py.
"""

import asyncio
from typing import Any, Dict, List

from app.utils.logging_utils import logger

# run_id → list of WebSocket objects
_active_connections: Dict[str, List[Any]] = {}
_lock = asyncio.Lock()


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
    client.  If no clients are connected, this is a cheap no-op.
    """
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


def has_clients(run_id: str) -> bool:
    return bool(_active_connections.get(run_id))
