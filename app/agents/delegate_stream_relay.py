"""
Delegate Stream Relay — pushes live delegate chunks to WebSocket clients.

Module-level singleton that DelegateManager calls on every chunk.
The WebSocket endpoint in server.py registers/unregisters connections.

Flow:
    _run_delegate() → relay.push(conv_id, chunk)
        → active_connections[conv_id] → ws.send_json(chunk)
        → frontend useDelegateStreaming hook picks it up
"""

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from app.utils.logging_utils import logger

# conversation_id → list of WebSocket objects
_active_connections: Dict[str, List[Any]] = {}
_lock = asyncio.Lock()


async def connect(conversation_id: str, ws: Any) -> None:
    """Register a WebSocket for delegate stream relay."""
    async with _lock:
        if conversation_id not in _active_connections:
            _active_connections[conversation_id] = []
        _active_connections[conversation_id].append(ws)
        logger.info(
            f"📡 RELAY: Client connected for {conversation_id[:8]} "
            f"({len(_active_connections[conversation_id])} clients)"
        )


async def disconnect(conversation_id: str, ws: Any) -> None:
    """Unregister a WebSocket from delegate stream relay."""
    async with _lock:
        if conversation_id in _active_connections:
            _active_connections[conversation_id] = [
                w for w in _active_connections[conversation_id] if w is not ws
            ]
            if not _active_connections[conversation_id]:
                del _active_connections[conversation_id]
            logger.debug(f"📡 RELAY: Client disconnected for {conversation_id[:8]}")


async def push(conversation_id: str, chunk: Dict[str, Any]) -> None:
    """Broadcast a chunk to all connected clients for a conversation."""
    conns = _active_connections.get(conversation_id, [])
    if not conns:
        return

    dead: List[Any] = []
    payload = chunk  # Already a dict, send_json handles serialization

    for ws in conns:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)

    # Clean up dead connections
    if dead:
        async with _lock:
            if conversation_id in _active_connections:
                _active_connections[conversation_id] = [
                    w for w in _active_connections[conversation_id]
                    if w not in dead
                ]
                if not _active_connections[conversation_id]:
                    del _active_connections[conversation_id]


def has_clients(conversation_id: str) -> bool:
    """Check if any clients are connected for a conversation."""
    return bool(_active_connections.get(conversation_id))


async def push_to_orchestrator(orchestrator_id: str, chunk: Dict[str, Any]) -> None:
    """Push a chunk to the orchestrator conversation's connected clients."""
    await push(orchestrator_id, chunk)
