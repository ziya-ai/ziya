"""Dedicated worker pool for the latency-critical chat-turn path.

``asyncio.to_thread`` runs on the event loop's default executor, which is
sized ``min(32, cpu_count + 4)`` — 14 threads on a 10-core laptop.  On the
first request after startup (or after a project switch) that pool fills up
with scan work: ``get_ignored_patterns`` for a fresh project holds its
per-directory build lock for up to ``ZIYA_GITIGNORE_TIMEOUT`` seconds, and
every ``/api/folders`` / ``/api/context-estimate`` that ``to_thread``s the
same call parks a pool thread on that lock.  A chat turn that then does
``to_thread(resolve_bench_prompt)`` / ``to_thread(build_messages_for_streaming)``
queues behind them and the user's message does not reach the model until
the scan deadline frees the pool.

Turn work therefore runs on this separate, small pool so it is never
starved by background scans.  The pool is intentionally tiny: a turn's
off-loop work is a few hundred ms of file reads and prompt assembly, and
turns are already serialised per conversation.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")

TURN_EXECUTOR_WORKERS = 4

_turn_executor = ThreadPoolExecutor(
    max_workers=TURN_EXECUTOR_WORKERS, thread_name_prefix="ziya-turn",
)


def turn_executor() -> ThreadPoolExecutor:
    """The pool chat-turn work runs on (exposed for tests)."""
    return _turn_executor


async def run_on_turn_executor(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run ``fn(*args, **kwargs)`` on the turn pool and await the result.

    Drop-in replacement for ``asyncio.to_thread`` on the chat path: same
    signature, same return, but immune to default-executor saturation.

    Like ``to_thread`` (and unlike a bare ``run_in_executor``) the call runs
    inside a copy of the caller's ``contextvars`` context, so request-scoped
    state such as ``app.context`` (project root, conversation id, task
    grants) is visible to the worker.  Without this the prompt builder saw
    ``get_project_root_or_none() is None`` and fell back to the server's
    launch directory for ``CurrentWorkingDirectory`` and the write policy.
    """
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    call = functools.partial(ctx.run, fn, *args, **kwargs)
    return await loop.run_in_executor(_turn_executor, call)
