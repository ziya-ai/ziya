"""
Chat Turn Relay — makes a chat turn a server-owned task that HTTP responses
*subscribe to*, instead of a coroutine the response *owns*.

Today a chat turn lives exactly as long as the ``/api/chat`` response: when
the tab reloads, the socket drops, Starlette cancels the response task, and
the turn — including any tool call or human-consent wait inside it — dies
with it.  Under this relay the turn runs in its own task, every SSE frame it
produces is recorded in a bounded per-conversation buffer and fanned out to
whoever is currently subscribed, and a client that connects late (reload,
second window, CLI ``/join``) gets a replay of what it missed followed by the
live tail.  See design/consent-runtime.md §Chat turn relay.

Frames, not events.  ``stream_chunks`` already yields fully formatted SSE
frames (``"data: {...}\\n\\n"``), so the relay is content-agnostic: it never
has to know the event vocabulary, and the reattach endpoint can serve the
same bytes the original response would have.  The one place it looks inside
a frame is the on-the-fly fold of adjacent ``{"content": ...}`` frames,
which keeps a 50k-token answer from costing 50k buffer slots (mirrors the
``task_text_delta`` fold in task_run_stream_relay).  Because frames are
immutable strings the fold REPLACES the last entry rather than mutating it,
so the shared-dict replay hazard that relay documents cannot occur here.

Disconnect semantics (design decision, not incidental):
  * A subscriber leaving does NOT cancel the turn.  If it did, reconnection
    would be meaningless.
  * When the LAST subscriber leaves a running turn, a grace timer starts;
    on expiry the turn is cancelled, so a turn with no viewer cannot burn
    tokens indefinitely.  A new subscriber cancels the timer.
  * While the turn is HELD (``hold()`` — the consent runtime's hook for "a
    human decision is pending"), the grace timer is suspended: waiting on a
    human costs nothing, and the answer may arrive from a window that is
    not the one that disconnected.
  * Explicit ``cancel_turn`` (the Stop button, via ``/api/abort-stream``)
    cancels immediately regardless.

Modeled on app/agents/task_run_stream_relay.py (WebSocket, dict events) and
app/agents/delegate_stream_relay.py; this is the third instance of the same
pattern, applied to the SSE chat path.
"""

import asyncio
import json
import time
import uuid
from collections import deque
from typing import Any, AsyncGenerator, AsyncIterator, Deque, Dict, List, Optional, Set

from app.config.env_registry import ziya_env
from app.utils.logging_utils import logger

# Bounds on the per-turn replay buffer.  Content frames are folded, so the
# slot cap mostly bounds tool/lifecycle events; the byte cap bounds the
# folded text.  When either is exceeded the OLDEST frames are dropped and
# counted, and a late subscriber is told its replay is incomplete rather
# than being handed a stream that silently starts mid-answer.
_MAX_FRAMES = 20_000
_MAX_BYTES = 16 * 1024 * 1024

# How long a FINISHED turn's buffer is retained for late reattach.  A tab
# that reloaded mid-turn and came back after the turn completed has no
# client-side copy of the answer (the frontend persists the assistant
# message only from a live stream), so the relay is the only place it
# still exists.  Same figure as the task-run relay.
_FINISHED_RETENTION_SECONDS = 300.0

# Sentinel pushed onto subscriber queues when the turn ends.
_END = None

# A frame prefix that identifies a plain content delta.  json.dumps of a
# single-key dict {'content': x} is byte-stable, so a prefix test is exact.
_CONTENT_PREFIX = 'data: {"content": '


def _sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _grace_seconds() -> float:
    try:
        return float(ziya_env("ZIYA_CHAT_TURN_DISCONNECT_GRACE_SECS"))
    except Exception:  # noqa: BLE001 — registry absent in some test contexts
        return 60.0


class _Turn:
    __slots__ = (
        "conversation_id", "turn_id", "task", "frames", "frame_bytes",
        "dropped", "subscribers", "done", "cancelled", "error", "started_at",
        "finished_at", "headers", "holds", "grace_task", "retention_task",
    )

    def __init__(self, conversation_id: str, headers: Optional[Dict[str, str]]):
        self.conversation_id = conversation_id
        self.turn_id = uuid.uuid4().hex
        self.task: Optional[asyncio.Task] = None
        self.frames: Deque[str] = deque()
        self.frame_bytes = 0
        self.dropped = 0
        self.subscribers: Set[asyncio.Queue] = set()
        self.done = False
        self.cancelled = False
        self.error: Optional[str] = None
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.headers: Dict[str, str] = dict(headers or {})
        self.holds: Set[str] = set()
        self.grace_task: Optional[asyncio.Task] = None
        self.retention_task: Optional[asyncio.Task] = None

    def status(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "active": not self.done,
            "held": bool(self.holds),
            "hold_reasons": sorted(self.holds),
            "cancelled": self.cancelled,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "subscribers": len(self.subscribers),
            "buffered_frames": len(self.frames),
            "dropped_frames": self.dropped,
        }


# conversation_id → the current (running or recently finished) turn.
_turns: Dict[str, _Turn] = {}
_lock = asyncio.Lock()


# ── recording ─────────────────────────────────────────────────────────────

def _record(turn: _Turn, frame: str) -> None:
    """Append a frame, folding into the previous one when both are plain
    content deltas.  Caller holds ``_lock``."""
    buf = turn.frames
    if frame.startswith(_CONTENT_PREFIX) and buf and buf[-1].startswith(_CONTENT_PREFIX):
        try:
            prev = json.loads(buf[-1][6:])
            cur = json.loads(frame[6:])
            if set(prev) == {"content"} and set(cur) == {"content"}:
                folded = _sse({"content": prev["content"] + cur["content"]})
                turn.frame_bytes += len(folded) - len(buf[-1])
                buf[-1] = folded
                _trim(turn)
                return
        except (ValueError, KeyError, TypeError):
            pass  # not a clean content frame after all; append verbatim
    buf.append(frame)
    turn.frame_bytes += len(frame)
    _trim(turn)


def _trim(turn: _Turn) -> None:
    buf = turn.frames
    while buf and (len(buf) > _MAX_FRAMES or turn.frame_bytes > _MAX_BYTES):
        # Never drop the only frame: a single oversized frame must still be
        # replayable or the byte cap would empty the buffer entirely.
        if len(buf) == 1:
            break
        gone = buf.popleft()
        turn.frame_bytes -= len(gone)
        turn.dropped += 1


async def _publish(turn: _Turn, frame: str) -> None:
    """Record and fan out under ONE lock hold so replay and live tail
    partition the frame sequence exactly (nothing twice, nothing lost)."""
    async with _lock:
        _record(turn, frame)
        queues = list(turn.subscribers)
    for q in queues:
        q.put_nowait(frame)


async def _finish(turn: _Turn) -> None:
    async with _lock:
        turn.done = True
        turn.finished_at = time.time()
        queues = list(turn.subscribers)
        if turn.grace_task and not turn.grace_task.done():
            turn.grace_task.cancel()
        turn.grace_task = None
        turn.retention_task = asyncio.create_task(
            _drop_after(turn, _FINISHED_RETENTION_SECONDS))
    for q in queues:
        q.put_nowait(_END)


async def _drop_after(turn: _Turn, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    async with _lock:
        if _turns.get(turn.conversation_id) is turn:
            del _turns[turn.conversation_id]
    logger.debug(f"📡 CHAT_TURN_RELAY: dropped finished turn for {turn.conversation_id[:8]}")


# ── the pump ──────────────────────────────────────────────────────────────

async def _pump(turn: _Turn, source: AsyncIterator[str]) -> None:
    """Drive the source generator to completion, publishing every frame.

    Cancellation (Stop button, grace timer, supersede, shutdown) surfaces to
    subscribers as an explicit ``stream_end`` frame so a second window
    watching the same turn sees it end rather than hang.  An exception in
    the source is turned into the same client-visible error frame the
    keepalive wrapper would have produced — the wrapper no longer sees the
    source directly, so this responsibility moves here.
    """
    try:
        async for frame in source:
            await _publish(turn, frame)
    except asyncio.CancelledError:
        turn.cancelled = True
        await _publish(turn, _sse({"type": "stream_end", "reason": "cancelled"}))
        # Do not re-raise: the pump task is the unit being cancelled and
        # its cleanup below is the whole point.  Swallowing here cannot mask
        # anything — nobody awaits the pump task's result.
    except Exception as exc:  # noqa: BLE001 — wraps the entire chat stream
        logger.error(f"📡 CHAT_TURN_RELAY: turn for {turn.conversation_id[:8]} raised: {exc!r}",
                     exc_info=True)
        from app.utils.error_sanitizer import sanitize_client_error
        turn.error = sanitize_client_error(exc)
        await _publish(turn, _sse({"error": turn.error, "error_type": "stream_error"}))
        await _publish(turn, _sse({"type": "stream_end"}))
    finally:
        if hasattr(source, "aclose"):
            try:
                await source.aclose()  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
        await _finish(turn)


# ── public API ────────────────────────────────────────────────────────────

async def start_turn(
    conversation_id: str,
    source: AsyncIterator[str],
    headers: Optional[Dict[str, str]] = None,
) -> str:
    """Begin a server-owned turn for ``conversation_id`` and return its id.

    One turn per conversation: a running turn is superseded (cancelled) by
    a new submission, which is what the user asked for by submitting.
    ``headers`` are the per-response headers the original ``/api/chat``
    reply would have carried (model attribution); the reattach endpoint
    serves them again so a late client is told the same truth.
    """
    turn = _Turn(conversation_id, headers)
    async with _lock:
        prev = _turns.get(conversation_id)
        _turns[conversation_id] = turn
    if prev is not None:
        if prev.retention_task and not prev.retention_task.done():
            prev.retention_task.cancel()
        if not prev.done and prev.task and not prev.task.done():
            logger.info(f"📡 CHAT_TURN_RELAY: superseding running turn for {conversation_id[:8]}")
            prev.task.cancel()
    turn.task = asyncio.create_task(_pump(turn, source), name=f"chat-turn-{conversation_id[:8]}")
    logger.debug(f"📡 CHAT_TURN_RELAY: started turn {turn.turn_id[:8]} for {conversation_id[:8]}")
    return turn.turn_id


async def subscribe(conversation_id: str) -> AsyncGenerator[str, None]:
    """Yield the turn's frames: buffered history first, then the live tail.

    Registration and the history snapshot happen under one lock hold, so
    the replay and the live queue partition the sequence exactly.  Leaving
    (client disconnect, or the turn ending) unregisters; if that leaves a
    running, un-held turn with no subscribers, the disconnect grace timer
    is armed.
    """
    q: asyncio.Queue = asyncio.Queue()
    async with _lock:
        turn = _turns.get(conversation_id)
        if turn is None:
            return
        turn.subscribers.add(q)
        if turn.grace_task and not turn.grace_task.done():
            turn.grace_task.cancel()
            turn.grace_task = None
        history = list(turn.frames)
        dropped = turn.dropped
        already_done = turn.done
        logger.debug(
            f"📡 CHAT_TURN_RELAY: subscriber for {conversation_id[:8]} "
            f"({len(turn.subscribers)} total, {len(history)} replayed)")
    try:
        if dropped:
            yield _sse({"type": "relay_replay_truncated", "dropped_frames": dropped})
        for frame in history:
            yield frame
        if already_done:
            return
        while True:
            frame = await q.get()
            if frame is _END:
                return
            yield frame
    finally:
        async with _lock:
            turn.subscribers.discard(q)
            arm = (not turn.done and not turn.subscribers and not turn.holds
                   and (turn.grace_task is None or turn.grace_task.done()))
            if arm:
                turn.grace_task = asyncio.create_task(_grace_cancel(turn, _grace_seconds()))


async def _grace_cancel(turn: _Turn, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    async with _lock:
        still_orphaned = (not turn.done and not turn.subscribers and not turn.holds)
    if still_orphaned and turn.task and not turn.task.done():
        logger.info(
            f"📡 CHAT_TURN_RELAY: no subscriber for {delay:.0f}s, cancelling turn "
            f"for {turn.conversation_id[:8]}")
        turn.task.cancel()


async def cancel_turn(conversation_id: str, reason: str = "stopped") -> bool:
    """Explicit stop.  True if a running turn was cancelled."""
    async with _lock:
        turn = _turns.get(conversation_id)
    if turn is None or turn.done or turn.task is None or turn.task.done():
        return False
    logger.info(f"📡 CHAT_TURN_RELAY: cancel ({reason}) for {conversation_id[:8]}")
    turn.task.cancel()
    return True


async def hold(conversation_id: str, reason: str) -> bool:
    """Suspend the disconnect grace timer while ``reason`` is outstanding.

    The consent runtime calls this when a tool call is waiting on a human,
    so a turn parked on a decision is never cancelled for having no viewer.
    Reasons are a set so overlapping holds compose; ``release`` the same
    reason to lift it.
    """
    async with _lock:
        turn = _turns.get(conversation_id)
        if turn is None or turn.done:
            return False
        turn.holds.add(reason)
        if turn.grace_task and not turn.grace_task.done():
            turn.grace_task.cancel()
            turn.grace_task = None
    return True


async def release(conversation_id: str, reason: str) -> bool:
    async with _lock:
        turn = _turns.get(conversation_id)
        if turn is None:
            return False
        turn.holds.discard(reason)
        arm = (not turn.done and not turn.holds and not turn.subscribers
               and (turn.grace_task is None or turn.grace_task.done()))
        if arm:
            turn.grace_task = asyncio.create_task(_grace_cancel(turn, _grace_seconds()))
    return True


def status(conversation_id: str) -> Optional[Dict[str, Any]]:
    turn = _turns.get(conversation_id)
    return turn.status() if turn else None


def headers_for(conversation_id: str) -> Dict[str, str]:
    turn = _turns.get(conversation_id)
    return dict(turn.headers) if turn else {}


def has_turn(conversation_id: str) -> bool:
    return conversation_id in _turns


async def shutdown_all() -> None:
    """Cancel every running turn.  Called from the server lifespan."""
    async with _lock:
        turns = list(_turns.values())
    for t in turns:
        for task in (t.task, t.grace_task, t.retention_task):
            if task and not task.done():
                task.cancel()
    # Give pumps a moment to publish their stream_end and finish.
    pending: List[asyncio.Task] = [t.task for t in turns if t.task and not t.task.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _reset_for_tests() -> None:
    _turns.clear()
