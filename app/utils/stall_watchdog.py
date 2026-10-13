"""Event-loop / executor stall watchdog.

Diagnostic for "the server serves nothing" episodes.  A daemon thread pings
the asyncio loop once a second with ``call_soon_threadsafe`` and reads the
loop's default ``ThreadPoolExecutor`` (the pool every ``asyncio.to_thread``
shares).  When the loop fails to answer within ``threshold`` seconds, or the
executor has queued work with every worker busy for that long, it logs one
report per episode: the trigger, the executor census, and every thread's
Python stack via ``sys._current_frames()``.

Why not the alternatives:

* ``py-spy`` segfaulted the server on macOS before the stall (2026-10-08).
* ``faulthandler.register(SIGUSR1, all_threads=True)`` dumps from a C
  signal handler without the GIL and crashed the process on its second use
  while formatting another thread's frame -- documented as not safe against
  running threads.
* ``loop.set_debug(True)`` (ZIYA_DEBUG_LOOP) instruments every callback and
  slowed startup enough to change what was being measured.

``sys._current_frames()`` takes the GIL like any Python code, so it cannot
run while another thread holds the GIL in C -- but the stalls seen so far
(inline ``ssl.read``, ``os.walk``) all release it, and a pure-Python loop
yields every switch interval.  It therefore cannot crash the process and
cannot miss the cases that matter.

Enabled by ``ZIYA_STALL_DUMP=<seconds>`` (the threshold); unset = off.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import traceback
from typing import Callable, Dict, List, Optional

from app.utils.logging_utils import logger

PING_INTERVAL_S = 1.0


def executor_census(loop) -> Dict[str, Optional[int]]:
    """Live threads, max workers and queued items of ``loop``'s default
    executor.  ``asyncio.to_thread`` and ``run_in_executor(None, ...)`` both
    use it; a non-zero queue with ``live == max`` means new offloaded work --
    chat listings aside, that is most of the request path -- is waiting for
    a worker, which from the browser is indistinguishable from a hang."""
    ex = getattr(loop, "_default_executor", None)
    if ex is None:
        return {"created": 0, "max_workers": None, "live_threads": None, "queue_depth": None}
    try:
        live = len(getattr(ex, "_threads", ()) or ())
    except Exception:  # noqa: BLE001 -- CPython internals; degrade, never raise
        live = None
    try:
        depth = ex._work_queue.qsize()
    except Exception:  # noqa: BLE001
        depth = None
    return {"created": 1, "max_workers": getattr(ex, "_max_workers", None),
            "live_threads": live, "queue_depth": depth}


def format_all_stacks(main_ident: Optional[int] = None) -> str:
    """Every thread's stack, main thread first, named where possible."""
    names = {t.ident: t.name for t in threading.enumerate()}
    frames = sys._current_frames()
    order = sorted(frames, key=lambda i: (i != main_ident, names.get(i, "")))
    out: List[str] = []
    for ident in order:
        label = names.get(ident, "?")
        tag = " (event loop)" if ident == main_ident else ""
        out.append(f"--- thread {label} 0x{ident:x}{tag}")
        out.extend(line.rstrip("\n") for line in traceback.format_stack(frames[ident]))
    return "\n".join(out)


class StallWatchdog:
    """See module docstring.  ``emit`` receives the report text (test seam)."""

    def __init__(self, loop, threshold_s: float, *,
                 emit: Optional[Callable[[str], None]] = None,
                 ping_interval_s: float = PING_INTERVAL_S,
                 main_ident: Optional[int] = None):
        self.loop = loop
        self.threshold = float(threshold_s)
        self.ping_interval = ping_interval_s
        self.emit = emit or (lambda text: logger.warning(text))
        self.main_ident = main_ident if main_ident is not None else threading.main_thread().ident
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.reports = 0

    def start(self) -> "StallWatchdog":
        self._thread = threading.Thread(target=self._run, name="ziya-stall-watchdog", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    # -- internals -------------------------------------------------------

    def _ping(self) -> Optional[float]:
        """Seconds until the loop runs a trivial callback; None if it did not
        answer within the threshold."""
        done = threading.Event()
        t0 = time.monotonic()
        try:
            self.loop.call_soon_threadsafe(done.set)
        except RuntimeError:  # loop closed
            return None
        return (time.monotonic() - t0) if done.wait(self.threshold) else None

    def _run(self) -> None:
        in_episode = False
        saturated_since: Optional[float] = None
        while not self._stop.wait(self.ping_interval):
            if self.loop.is_closed():
                return
            latency = self._ping()
            census = executor_census(self.loop)
            depth, live, mx = census["queue_depth"], census["live_threads"], census["max_workers"]
            saturated = bool(depth) and live is not None and mx is not None and live >= mx
            now = time.monotonic()
            if saturated:
                saturated_since = saturated_since or now
            else:
                saturated_since = None
            stalled_exec = saturated_since is not None and now - saturated_since >= self.threshold
            stalled_loop = latency is None

            if not (stalled_loop or stalled_exec):
                in_episode = False
                continue
            if in_episode:
                continue  # one report per episode
            in_episode = True
            self.reports += 1
            why = []
            if stalled_loop:
                why.append(f"event loop did not run a callback within {self.threshold:.0f}s")
            if stalled_exec:
                why.append(f"default executor saturated for {now - saturated_since:.0f}s "
                           f"({live}/{mx} workers busy, {depth} queued)")
            self.emit(
                "⏱️  STALL: " + "; ".join(why)
                + f"\nexecutor: {census}\n" + format_all_stacks(self.main_ident)
            )


def start_from_env(loop) -> Optional[StallWatchdog]:
    """Start the watchdog when ``ZIYA_STALL_DUMP`` names a threshold."""
    import os
    raw = (os.environ.get("ZIYA_STALL_DUMP") or "").strip()
    if not raw:
        return None
    try:
        threshold = float(raw)
    except ValueError:
        logger.warning(f"ZIYA_STALL_DUMP={raw!r} is not a number; watchdog disabled")
        return None
    if threshold <= 0:
        return None
    wd = StallWatchdog(loop, threshold).start()
    logger.info(f"⏱️  stall watchdog armed: dumps all thread stacks when the loop or "
                f"the to_thread executor is stuck for {threshold:.0f}s")
    return wd
