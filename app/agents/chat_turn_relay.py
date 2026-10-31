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

Durability.  The in-memory buffer lives for the turn plus 5 minutes, and
nothing else holds the assistant's answer (the frontend persists it only
from a live stream).  Each turn is therefore also checkpointed to disk
(app/agents/chat_turn_store.py) at most every few seconds while it runs and
once when it ends.  The read API (status, all_statuses, has_turn,
headers_for, subscribe) falls back to that record when the turn is no
longer in memory, so a turn that finished with nobody watching, or that a
server restart interrupted, can still be replayed by a reattaching tab.

Disconnect semantics (design decision, not incidental):
  * A subscriber leaving does NOT cancel the turn.  If it did, reconnection
    would be meaningless.
  * When the LAST subscriber leaves a running turn, a grace timer starts;
    on expiry the turn is cancelled, so a turn with no viewer cannot burn
    tokens indefinitely.  A new subscriber cancels the timer.
  * Once a turn has run a tool it is agentic work that may legitimately run
    unwatched (a job_wait, a headless batch); it then uses
    ZIYA_CHAT_TURN_UNWATCHED_GRACE_SECS instead, where 0 (the default)
    means it is never cancelled for lack of a viewer.  The iteration and
    per-turn tool ceilings still bound it.
  * While the turn is HELD (``hold()`` — a pending human decision, or a
    tool call in progress), the grace timer is suspended.
  * Explicit ``cancel_turn`` (the Stop button, via ``/api/abort-stream``)
    cancels immediately regardless.

Modeled on app/agents/task_run_stream_relay.py (WebSocket, dict events) and
app/agents/delegate_stream_relay.py; this is the third instance of the same
pattern, applied to the SSE chat path.
"""

import asyncio
import json
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, AsyncIterator, Deque, Dict, List, Optional, Set

from app.agents import chat_turn_store as _store
from app.config.env_registry import ziya_env
from app.utils.logging_utils import logger

# Bounds on the per-turn replay buffer.  Content frames are folded, so the
# slot cap mostly bounds tool/lifecycle events; the byte cap bounds the
# folded text.  When either is exceeded the OLDEST frames are dropped and
# counted, and a late subscriber is told its replay is incomplete rather
# than being handed a stream that silently starts mid-answer.
_MAX_FRAMES = 20_000
_MAX_BYTES = 16 * 1024 * 1024

# How long a FINISHED turn's buffer is retained IN MEMORY for late
# reattach.  After that the disk record answers.
_FINISHED_RETENTION_SECONDS = 300.0

# At most one disk checkpoint per turn per this many seconds while it runs.
_CHECKPOINT_INTERVAL_SECONDS = 5.0

# Hold reasons with this prefix mark a tool call (tool_execution).
TOOL_HOLD_PREFIX = "tool:"

# Identifies this server process in disk records: a record still marked
# active but written by another boot was interrupted by a restart.
_BOOT_ID = uuid.uuid4().hex

# Sentinel pushed onto subscriber queues when the turn ends.
_END = None

# A frame prefix that identifies a plain content delta.  json.dumps of a
# single-key dict {'content': x} is byte-stable, so a prefix test is exact.
_CONTENT_PREFIX = 'data: {"content": '

# Keys of the public status shape (what the frontend's ChatTurnStatus reads).
_STATUS_KEYS = (
    "conversation_id", "turn_id", "active", "held", "hold_reasons", "cancelled",
    "error", "started_at", "finished_at", "subscribers", "buffered_frames",
    "dropped_frames", "ran_tool", "end_reason",
)


def _sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _grace_seconds() -> float:
    try:
        return float(ziya_env("ZIYA_CHAT_TURN_DISCONNECT_GRACE_SECS"))
    except Exception:  # noqa: BLE001 — registry absent in some test contexts
        return 60.0


def _unwatched_grace_seconds() -> Optional[float]:
    """Grace for a turn that has run a tool; None = never cancel."""
    try:
        v = float(ziya_env("ZIYA_CHAT_TURN_UNWATCHED_GRACE_SECS"))
    except Exception:  # noqa: BLE001 — unregistered: default is "never"
        v = 0.0
    return v if v > 0 else None


class _Turn:
    __slots__ = (
        "conversation_id", "turn_id", "task", "frames", "frame_bytes",
        "dropped", "subscribers", "done", "cancelled", "error", "started_at",
        "finished_at", "headers", "holds", "grace_task", "retention_task",
        "ran_tool", "end_reason", "checkpoint_task", "snap_seq", "saved_seq",
        "save_mutex",
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
        self.ran_tool = False
        self.end_reason: Optional[str] = None
        self.checkpoint_task: Optional[asyncio.Task] = None
        self.snap_seq = 0
        self.saved_seq = 0
        self.save_mutex = threading.Lock()

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
            "ran_tool": self.ran_tool,
            "end_reason": self.end_reason,
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
        _schedule_checkpoint(turn)
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
        if turn.checkpoint_task and not turn.checkpoint_task.done():
            turn.checkpoint_task.cancel()
        turn.checkpoint_task = None
        turn.retention_task = asyncio.create_task(
            _drop_after(turn, _FINISHED_RETENTION_SECONDS))
    for q in queues:
        q.put_nowait(_END)
    await _checkpoint_now(turn)


async def _drop_after(turn: _Turn, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    async with _lock:
        if _turns.get(turn.conversation_id) is turn:
            del _turns[turn.conversation_id]
    logger.debug(f"📡 CHAT_TURN_RELAY: dropped finished turn for {turn.conversation_id[:8]}")


# ── disk checkpoints ──────────────────────────────────────────────────────

def _schedule_checkpoint(turn: _Turn, delay: float = None) -> None:
    """Arrange a checkpoint unless one is already pending.  Caller holds
    ``_lock``.  The snapshot is taken when the write runs, so frames that
    arrive while it is pending are included."""
    if turn.done:
        return
    t = turn.checkpoint_task
    if t is not None and not t.done():
        if delay is None:
            return
        # An immediate request (tool start) must not wait out a pending
        # interval checkpoint.  Cancelling one already mid-write is safe:
        # the thread finishes, and _save_sync's sequence check keeps the
        # newer snapshot authoritative.
        t.cancel()
    d = _CHECKPOINT_INTERVAL_SECONDS if delay is None else delay
    turn.checkpoint_task = asyncio.create_task(_checkpoint_after(turn, d))


async def _checkpoint_after(turn: _Turn, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    await _checkpoint_now(turn)


def _save_sync(turn: _Turn, seq: int, status: Dict[str, Any], frames: List[str],
               dropped: int) -> None:
    # Monotonic: a slower write of an older snapshot never lands after a
    # newer one (the final save at finish races a checkpoint in flight).
    with turn.save_mutex:
        if seq <= turn.saved_seq:
            return
        _store.save(status, frames, dropped)
        turn.saved_seq = seq


async def _checkpoint_now(turn: _Turn) -> None:
    async with _lock:
        turn.snap_seq += 1
        seq = turn.snap_seq
        status = {**turn.status(), "subscribers": 0, "headers": dict(turn.headers),
                  "boot_id": _BOOT_ID}
        frames = list(turn.frames)
        dropped = turn.dropped
    try:
        await asyncio.to_thread(_save_sync, turn, seq, status, frames, dropped)
    except Exception as exc:  # noqa: BLE001 — persistence must never break a turn
        logger.warning(f"📡 CHAT_TURN_RELAY: checkpoint failed for "
                       f"{turn.conversation_id[:8]}: {exc!r}")


def _from_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Public status for a disk record.  A record still marked active that
    another server process wrote was interrupted by a restart."""
    st = {k: rec.get(k) for k in _STATUS_KEYS}
    st["subscribers"] = 0
    st["held"] = False
    st["hold_reasons"] = []
    st["persisted"] = True
    st["interrupted"] = False
    if rec.get("active"):
        st["active"] = False
        st["cancelled"] = True
        st["interrupted"] = True
        st["end_reason"] = st.get("end_reason") or "server_restart"
        st["error"] = st.get("error") or "the server stopped before this turn finished"
        st["finished_at"] = st.get("finished_at") or rec.get("saved_at")
    return st


def _disk_record(conversation_id: str) -> Optional[Dict[str, Any]]:
    try:
        return _store.load_status(conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"📡 CHAT_TURN_RELAY: disk status unreadable: {exc!r}")
        return None


def _is_stream_end(frame: str) -> bool:
    try:
        return json.loads(frame[6:]).get("type") == "stream_end"
    except Exception:  # noqa: BLE001
        return False


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
        turn.end_reason = turn.end_reason or "cancelled"
        await _publish(turn, _sse({"type": "stream_end", "reason": "cancelled"}))
        # Do not re-raise: the pump task is the unit being cancelled and
        # its cleanup below is the whole point.  Swallowing here cannot mask
        # anything — nobody awaits the pump task's result.
    except Exception as exc:  # noqa: BLE001 — wraps the entire chat stream
        logger.error(f"📡 CHAT_TURN_RELAY: turn for {turn.conversation_id[:8]} raised: {exc!r}",
                     exc_info=True)
        from app.utils.error_handlers import sanitize_client_error
        turn.error = sanitize_client_error(exc)
        turn.end_reason = "error"
        await _publish(turn, _sse({"error": turn.error, "error_type": "stream_error"}))
        await _publish(turn, _sse({"type": "stream_end"}))
    else:
        turn.end_reason = turn.end_reason or "completed"
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
            prev.end_reason = "superseded"
            prev.task.cancel()
    turn.task = asyncio.create_task(_pump(turn, source), name=f"chat-turn-{conversation_id[:8]}")
    logger.debug(f"📡 CHAT_TURN_RELAY: started turn {turn.turn_id[:8]} for {conversation_id[:8]}")
    return turn.turn_id


def _arm_grace_locked(turn: _Turn) -> None:
    """Start the disconnect grace timer if the turn is running, unwatched,
    unheld and has none pending.  Caller holds ``_lock``."""
    if turn.done or turn.subscribers or turn.holds:
        return
    if turn.grace_task is not None and not turn.grace_task.done():
        return
    delay = _unwatched_grace_seconds() if turn.ran_tool else _grace_seconds()
    if delay is None:
        return
    turn.grace_task = asyncio.create_task(_grace_cancel(turn, delay))


async def _replay_from_disk(conversation_id: str) -> AsyncGenerator[str, None]:
    rec = _disk_record(conversation_id)
    if rec is None:
        return
    data = await asyncio.to_thread(_store.load_frames, conversation_id, rec["turn_id"])
    if data is None:
        return
    if data.get("dropped"):
        yield _sse({"type": "relay_replay_truncated", "dropped_frames": data["dropped"]})
    frames = [f for f in data["frames"] if isinstance(f, str)]
    for frame in frames:
        yield frame
    # Interrupted by a restart: the record ends mid-turn.  End it explicitly
    # so the client finalises what it replayed instead of waiting forever.
    if not frames or not _is_stream_end(frames[-1]):
        yield _sse({"type": "stream_end", "reason": "interrupted"})


async def subscribe(conversation_id: str) -> AsyncGenerator[str, None]:
    """Yield the turn's frames: buffered history first, then the live tail.

    Registration and the history snapshot happen under one lock hold, so
    the replay and the live queue partition the sequence exactly.  Leaving
    (client disconnect, or the turn ending) unregisters; if that leaves a
    running, un-held turn with no subscribers, the disconnect grace timer
    is armed.  A turn no longer in memory is replayed from its disk record.
    """
    q: asyncio.Queue = asyncio.Queue()
    async with _lock:
        turn = _turns.get(conversation_id)
        if turn is not None:
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
    if turn is None:
        async for frame in _replay_from_disk(conversation_id):
            yield frame
        return
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
            _arm_grace_locked(turn)


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
        turn.end_reason = "unwatched"
        turn.task.cancel()


async def cancel_turn(conversation_id: str, reason: str = "stopped") -> bool:
    """Explicit stop.  True if a running turn was cancelled."""
    async with _lock:
        turn = _turns.get(conversation_id)
    if turn is None or turn.done or turn.task is None or turn.task.done():
        return False
    logger.info(f"📡 CHAT_TURN_RELAY: cancel ({reason}) for {conversation_id[:8]}")
    turn.end_reason = reason
    turn.task.cancel()
    return True


async def hold(conversation_id: str, reason: str) -> bool:
    """Suspend the disconnect grace timer while ``reason`` is outstanding.

    The consent runtime calls this when a tool call is waiting on a human,
    and tool execution around every tool call (``held``), so a turn parked
    on a decision or a long tool is never cancelled for having no viewer.
    A ``tool:`` reason also marks the turn as agentic (see the module
    docstring's grace rules) and checkpoints it promptly: a long tool wait
    is when a disconnect, and a restart, are most likely.
    Reasons are a set so overlapping holds compose; ``release`` the same
    reason to lift it.
    """
    async with _lock:
        turn = _turns.get(conversation_id)
        if turn is None or turn.done:
            return False
        turn.holds.add(reason)
        if reason.startswith(TOOL_HOLD_PREFIX):
            turn.ran_tool = True
            _schedule_checkpoint(turn, delay=0.0)
        if turn.grace_task and not turn.grace_task.done():
            turn.grace_task.cancel()
            turn.grace_task = None
    return True


@asynccontextmanager
async def held(conversation_id: Optional[str], reason: str) -> AsyncIterator[bool]:
    """Hold the turn for the duration of the block; yields whether it held.

    Used around tool execution: a tool call costs no model tokens, and a
    long one (``job_wait`` up to an hour) is exactly when a user closes the
    tab or loses the tunnel.  Without the hold, the disconnect grace timer
    cancelled the turn mid-wait and, once the finished-turn retention ran
    out, nothing on either side remembered it.  A no-op (yields False) with
    no conversation, no turn, or a finished turn; never raises from its own
    bookkeeping, so it cannot change a tool's outcome.
    """
    ok = False
    if conversation_id:
        try:
            ok = await hold(conversation_id, reason)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"📡 CHAT_TURN_RELAY: hold failed for {conversation_id[:8]}: {exc!r}")
    try:
        yield ok
    finally:
        if ok:
            try:
                await release(conversation_id, reason)  # type: ignore[arg-type]
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"📡 CHAT_TURN_RELAY: release failed: {exc!r}")


async def release(conversation_id: str, reason: str) -> bool:
    async with _lock:
        turn = _turns.get(conversation_id)
        if turn is None:
            return False
        turn.holds.discard(reason)
        _arm_grace_locked(turn)
    return True


def status(conversation_id: str) -> Optional[Dict[str, Any]]:
    turn = _turns.get(conversation_id)
    if turn is not None:
        return turn.status()
    rec = _disk_record(conversation_id)
    return _from_record(rec) if rec else None


def all_statuses() -> List[Dict[str, Any]]:
    """Status of every turn the relay knows: in memory (running, held, or
    retained after finishing) and, for conversations not in memory, the
    disk record.

    The reattach watcher used to ask only about the conversation the tab
    was showing, so after a reload a turn running in any OTHER
    conversation got no subscriber, hit the disconnect grace timer and
    was cancelled -- along with whatever tool wait it was parked in.  A
    reloaded tab reads this list once and resubscribes to all of them.
    """
    out = {t.conversation_id: t.status() for t in list(_turns.values())}
    try:
        records = _store.list_statuses()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"📡 CHAT_TURN_RELAY: disk listing failed: {exc!r}")
        records = []
    for rec in records:
        cid = rec.get("conversation_id")
        if cid and cid not in out:
            out[cid] = _from_record(rec)
    return list(out.values())


def headers_for(conversation_id: str) -> Dict[str, str]:
    turn = _turns.get(conversation_id)
    if turn is not None:
        return dict(turn.headers)
    rec = _disk_record(conversation_id)
    hdrs = (rec or {}).get("headers")
    return dict(hdrs) if isinstance(hdrs, dict) else {}


def has_turn(conversation_id: str) -> bool:
    return conversation_id in _turns or _disk_record(conversation_id) is not None


async def shutdown_all() -> None:
    """Cancel every running turn.  Called from the server lifespan.  Each
    pump's final checkpoint records the turn as cancelled by shutdown, so
    a tab reattaching after the restart can still replay what it produced."""
    async with _lock:
        turns = list(_turns.values())
    for t in turns:
        if not t.done:
            t.end_reason = "server_shutdown"
        for task in (t.task, t.grace_task, t.retention_task, t.checkpoint_task):
            if task and not task.done():
                task.cancel()
    # Give pumps a moment to publish their stream_end and finish.
    pending: List[asyncio.Task] = [t.task for t in turns if t.task and not t.task.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _reset_for_tests() -> None:
    _turns.clear()
