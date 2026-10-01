"""Simulated clock for the replay.

The memory modules stamp wall time into what they persist (``created``,
``archived_at``, ``status_changed_at``, domain-clock ``ts``/``last``, the
dormant backstop, organize-history ``timestamp``, cleanup ``last_run``,
system-job ``last_check_ms``).  Replaying months of history in hours would
otherwise put every stamp in replay-wall-time and make age-based rules
meaningless.

We do NOT patch ``time`` globally (asyncio, boto3, filelock and logging need
real time).  Instead ``install`` replaces the *module reference* named
``time`` / ``datetime`` / ``uuid`` inside each memory module with a shim:

  * ``time.time()/time_ns()/strftime()/localtime()/gmtime()/ctime()`` follow
    the event clock; ``mktime/strptime/sleep/monotonic/perf_counter`` and
    everything else pass through to the real module.
  * ``datetime.datetime.now()/utcnow()/today()`` and ``date.today()`` follow
    the clock.
  * ``uuid.uuid4()`` is deterministic: a seeded counter, so two replays of the
    same run mint the same memory ids (``Memory.id`` defaults to uuid4).

Module-level references are looked up at call time (including pydantic
``default_factory`` lambdas in app.models.memory), so swapping the attribute
is enough.  Function-local ``import time`` would bypass the shim; the
memory modules import it at module level (checked by the tests).
"""
from __future__ import annotations

import datetime as _real_datetime
import hashlib
import importlib
import sys
import threading
import time as _real_time
import types
import uuid as _real_uuid
from typing import Iterable, List, Optional, Tuple

# Modules whose clock/uuid references are replaced.  Missing modules (e.g.
# app.utils.memory_clock in the baseline arm) are skipped.
TARGET_MODULES = (
    "app.memory",
    "app.memory.extractor",
    "app.memory.lifecycle",
    "app.memory.feedback",
    "app.memory.maintenance",
    "app.memory.organizer",
    "app.memory.organize_history",
    "app.memory.rem",
    "app.memory.comparator",
    "app.memory.prompt",
    "app.storage.memory",
    "app.storage.proposals",
    "app.utils.memory_clock",
    "app.utils.memory_collections",
    "app.services.embedding_service",
    "app.mcp.tools.memory_tools",
    "app.models.memory",
    "app.agents.system_jobs",
)


class SimClock:
    """Monotone simulated wall clock in ms.  Thread-safe (memory code runs
    embeddings in ``asyncio.to_thread`` workers)."""

    def __init__(self, now_ms: int = 0, seed: str = "replay") -> None:
        self._lock = threading.Lock()
        self._now_ms = int(now_ms)
        self.seed = seed
        self.uuid_counter = 0

    def set(self, now_ms: int) -> None:
        """Advance to ``now_ms``; never moves backwards (events that share a
        timestamp, or a reply stamped before a tool call, keep order)."""
        with self._lock:
            if now_ms > self._now_ms:
                self._now_ms = int(now_ms)

    def now_ms(self) -> int:
        with self._lock:
            return self._now_ms

    def now_s(self) -> float:
        return self.now_ms() / 1000.0

    def next_uuid(self) -> _real_uuid.UUID:
        with self._lock:
            self.uuid_counter += 1
            n = self.uuid_counter
        b = bytearray(hashlib.sha256(f"{self.seed}:{n}".encode()).digest()[:16])
        b[6] = (b[6] & 0x0F) | 0x40
        b[8] = (b[8] & 0x3F) | 0x80
        return _real_uuid.UUID(bytes=bytes(b))

    def state(self) -> dict:
        return {"now_ms": self.now_ms(), "seed": self.seed, "uuid_counter": self.uuid_counter}

    def restore(self, st: dict) -> None:
        with self._lock:
            self._now_ms = int(st["now_ms"])
            self.seed = st.get("seed", self.seed)
            self.uuid_counter = int(st.get("uuid_counter", 0))


def make_time_shim(clock: SimClock) -> types.ModuleType:
    m = types.ModuleType("time")
    m.__dict__.update({k: getattr(_real_time, k) for k in dir(_real_time) if not k.startswith("__")})
    m.__replay_shim__ = True
    m.time = lambda: clock.now_s()
    m.time_ns = lambda: clock.now_ms() * 1_000_000
    m.localtime = lambda secs=None: _real_time.localtime(clock.now_s() if secs is None else secs)
    m.gmtime = lambda secs=None: _real_time.gmtime(clock.now_s() if secs is None else secs)
    m.ctime = lambda secs=None: _real_time.ctime(clock.now_s() if secs is None else secs)
    m.strftime = lambda fmt, t=None: _real_time.strftime(fmt, _real_time.localtime(clock.now_s()) if t is None else t)
    m.asctime = lambda t=None: _real_time.asctime(_real_time.localtime(clock.now_s()) if t is None else t)
    return m


def make_datetime_shim(clock: SimClock) -> types.ModuleType:
    class datetime(_real_datetime.datetime):  # noqa: N801 - mirrors stdlib
        @classmethod
        def now(cls, tz=None):
            return _real_datetime.datetime.fromtimestamp(clock.now_s(), tz)

        @classmethod
        def utcnow(cls):
            return _real_datetime.datetime.utcfromtimestamp(clock.now_s())

        @classmethod
        def today(cls):
            return _real_datetime.datetime.fromtimestamp(clock.now_s())

    class date(_real_datetime.date):  # noqa: N801
        @classmethod
        def today(cls):
            return _real_datetime.date.fromtimestamp(clock.now_s())

    m = types.ModuleType("datetime")
    m.__dict__.update({k: getattr(_real_datetime, k) for k in dir(_real_datetime) if not k.startswith("__")})
    m.datetime, m.date = datetime, date
    m.__replay_shim__ = True
    return m


def make_uuid_shim(clock: SimClock) -> types.ModuleType:
    m = types.ModuleType("uuid")
    m.__dict__.update({k: getattr(_real_uuid, k) for k in dir(_real_uuid) if not k.startswith("__")})
    m.uuid4 = clock.next_uuid
    m.__replay_shim__ = True
    return m


def install(clock: SimClock, modules: Iterable[str] = TARGET_MODULES) -> List[Tuple[object, str, object]]:
    """Swap clock/uuid references in ``modules``.  Returns an undo list for
    ``uninstall``."""
    tshim, dshim, ushim = make_time_shim(clock), make_datetime_shim(clock), make_uuid_shim(clock)
    undo: List[Tuple[object, str, object]] = []
    for name in modules:
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        for attr, val in list(vars(mod).items()):
            new = None
            if val is _real_time:
                new = tshim
            elif val is _real_datetime:
                new = dshim
            elif val is _real_uuid:
                new = ushim
            elif val is _real_datetime.datetime:
                new = dshim.datetime
            elif val is _real_datetime.date:
                new = dshim.date
            if new is not None:
                undo.append((mod, attr, val))
                setattr(mod, attr, new)
    return undo


def uninstall(undo: List[Tuple[object, str, object]]) -> None:
    for mod, attr, val in reversed(undo):
        setattr(mod, attr, val)
