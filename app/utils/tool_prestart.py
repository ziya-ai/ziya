"""Start read-only tool calls while the model is still writing its message.

The streaming executor (app/streaming_tool_executor.py) queues each tool call
when its ``tool_use`` block closes and runs the whole set through
app/tool_batch.run_tool_batch after the message ends.  A read that finished
streaming early in a long message used to wait for every token after it.
``ToolPrestarter.offer`` starts such a call at once; ``run_tool_batch``
(``prestarted=``) then replays its buffered events instead of executing it a
second time, so the chat renders exactly as before, but the tool time
overlaps the model's generation.

What may start early: only the LEADING run of read-only calls in a message
(app/tool_batch.is_read_only_call).  The first call that is not a read
closes early start for that message, so every pre-started call precedes
every barrier in issue order and lands in the batch's first group, and
nothing that writes or could ask for approval ever runs before the model
has finished the message.

Feedback: ``execute_single_tool`` drains pending user feedback before it
runs.  A pre-started call runs while the streaming loop is still checking
the same list for a stop, so the call gets a no-op drain (feedback stays
for the stream loop and the iteration-boundary drain points to act on), and
nothing starts early while feedback is pending.  The call's peek-based stop
watcher is unchanged.

Abandoned work: ``discard`` cancels every pre-started call the batch did not
take.  The executor calls it when an iteration is retried, when the stream
is stopped mid-message, and after each batch.

``ZIYA_TOOL_PRESTART=0`` turns the whole mechanism off.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def prestart_enabled() -> bool:
    return os.environ.get("ZIYA_TOOL_PRESTART", "1").strip().lower() not in ("0", "false", "no", "off")


@dataclass
class Prestart:
    """One call started early: its task, its buffered events, its timing."""
    tool_id: str
    task: asyncio.Task
    queue: asyncio.Queue
    started: float
    ended: Optional[float] = None

    @property
    def duration(self) -> float:
        return (self.ended or time.monotonic()) - self.started


def _no_feedback() -> list:
    return []


def _default_execute() -> Callable:
    # Resolved per call through app.tool_batch so a test that patches the
    # batch runner's executor patches this path too.
    from app import tool_batch
    return tool_batch.execute_single_tool


def _startable(ctx: Any) -> bool:
    from app import tool_batch
    peek = getattr(ctx, "peek_feedback_fn", None)
    try:
        if peek is not None and peek():
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        tool = tool_batch._find_tool(ctx)
        return tool_batch.is_read_only_call(ctx.actual_tool_name, ctx.args, tool)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"tool_prestart: cannot classify {getattr(ctx, 'actual_tool_name', '?')}: {exc}")
        return False


class ToolPrestarter:
    """Per-message: offer each queued call in issue order."""

    def __init__(self, execute: Optional[Callable] = None, enabled: Optional[bool] = None):
        self._execute = execute
        self._open = prestart_enabled() if enabled is None else enabled
        self._started: Dict[str, Prestart] = {}

    def offer(self, ctx: Any) -> bool:
        """Start ``ctx`` now if it may run early; True if it was started."""
        if not self._open:
            return False
        if not _startable(ctx):
            self._open = False  # nothing after a barrier may start early
            return False
        try:
            ctx.drain_feedback_fn = _no_feedback
        except Exception:  # noqa: BLE001
            self._open = False
            return False
        execute = self._execute or _default_execute()
        queue: asyncio.Queue = asyncio.Queue()
        pre_holder: Dict[str, Prestart] = {}

        async def _drive() -> None:
            try:
                async for evt in execute(ctx):
                    await queue.put(evt)
            finally:
                pre = pre_holder.get("p")
                if pre is not None:
                    pre.ended = time.monotonic()
                queue.put_nowait(None)  # end sentinel; no await while cancelled

        started = time.monotonic()
        pre = Prestart(tool_id=ctx.tool_id, task=None, queue=queue, started=started)  # type: ignore[arg-type]
        pre_holder["p"] = pre
        pre.task = asyncio.create_task(_drive())
        self._started[ctx.tool_id] = pre
        logger.debug(f"⚡ PRESTART: {ctx.actual_tool_name} ({ctx.tool_id}) started before message end")
        return True

    def take(self, tool_id: str) -> Optional[Prestart]:
        """Hand a started call to the batch runner (at most once)."""
        return self._started.pop(tool_id, None)

    def started_ids(self) -> list:
        return list(self._started)

    async def discard(self) -> None:
        """Cancel and reap every started call nobody took."""
        pending = list(self._started.values())
        self._started.clear()
        self._open = False
        for pre in pending:
            if not pre.task.done():
                pre.task.cancel()
        if pending:
            await asyncio.gather(*(p.task for p in pending), return_exceptions=True)
            logger.debug(f"⚡ PRESTART: discarded {len(pending)} untaken call(s)")


async def replay(pre: Prestart) -> AsyncGenerator[Dict[str, Any], None]:
    """The call's events, buffered or still arriving, then end."""
    while True:
        evt = await pre.queue.get()
        if evt is None:
            return
        yield evt
