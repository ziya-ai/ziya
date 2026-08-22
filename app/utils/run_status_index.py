"""
Project-wide per-conversation run-status counts for the sidebar.

The conversation list needs, for every conversation in a project, how many
task runs sit in each status -- so a run that holds or fails in a
conversation the user is not looking at still shows up.  ``useTaskBindings``
cannot answer that: it loads the OPEN chat only, which is the one case a
background indicator does not need to cover.

Why this is a projection and not ``TaskRunStorage.list()``
---------------------------------------------------------
A ``TaskRun`` record carries ``block_states``, per-iteration summaries and
artifact metadata, and is encrypted at rest.  Measured in one real project:
134 records, 14.5 MB, mean 108 KB each.  Reading all of that to learn eight
status strings costs work proportional to total run HISTORY, on every poll,
forever -- to answer a question about the handful of runs that are live.

Why the cache is PER FILE and not per directory
-----------------------------------------------
The first version of this memoised on the run directory's mtime, which is
wrong in exactly the case it has to survive.  ``BaseStorage._write_json``
writes a temp file and renames it over the target, and a rename bumps the
DIRECTORY mtime (verified: an in-place rewrite does not, a temp+rename
does).  A running task heartbeats every 5 seconds
(``TaskRunStorage.record_activity``, ``min_interval_s=5.0``), so while
anything is running -- precisely when the sidebar is polling -- the
directory mtime moves constantly, the memo never hits, and each poll pays a
full decrypt of every historical run.  Measured rebuild cost at that scale:
176 ms for 134 records, 569 ms for 500.

So the cache holds one summary per RUN FILE, keyed by path, and re-reads a
file only when that file's own mtime or size changed.  A heartbeat on one
live run therefore costs one file read (~0.5 ms for a 100 KB record), not N.
Finished runs are read once per process and then never again, which is the
right shape: run history is append-mostly and overwhelmingly terminal.

A directory-mtime PRE-GATE sits in front of the per-file stats, because an
idle project is the case that dominates over time and 203 stats to learn
nothing is waste.  Measured on a 203-run / 20 MB project:

    idle poll        0.039 ms   (one directory stat, no per-file work)
    one live run     ~0.5 ms    (one record re-read)
    cold start       ~160 ms    (once per process, per project)
    memory           ~49 KB     (vs ~20 MB for the records themselves)

At a 40-second poll that is 0.005% of one core for fifty idle projects.

Deliberately process-local and unbounded-by-time: it is a read cache over
files that are the durable record, so a stale entry can only ever be
corrected by re-reading, never lost.  Entries for deleted files are dropped
on the next scan, and the summaries retained are ~200 bytes each rather than
the ~108 KB records they came from.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# Every status a run can hold.  Mirrors app.models.task_run.RunStatus; an
# unknown status from a newer writer is counted under its own key rather
# than dropped, so the client can decide what to do with it.
KNOWN_STATUSES: Tuple[str, ...] = (
    "queued", "running", "paused", "held",
    "done", "partial", "failed", "cancelled",
)

# Statuses that can still change without a user acting.  Drives whether the
# client keeps polling.  ``held`` is excluded on purpose: it is terminal for
# the run object (the executor coroutine has unwound) and only a human
# fixing the environment moves it, so treating it as live would poll forever
# on a study that is waiting for exactly that.
LIVE_STATUSES: Tuple[str, ...] = ("queued", "running", "paused")


class _Summary:
    """The handful of fields the sidebar needs from one run.

    A tiny object rather than the ``TaskRun`` model: holding parsed models
    for every historical run would keep the very payload this module exists
    to avoid resident in memory.  Measured against the real project, this is
    roughly 200 bytes per run versus ~108 KB.
    """

    __slots__ = ("status", "conversation_id", "lineage", "attempt")

    def __init__(
        self, status: str, conversation_id: Optional[str],
        lineage: str, attempt: int,
    ) -> None:
        self.status = status
        self.conversation_id = conversation_id
        self.lineage = lineage
        self.attempt = attempt


def summarize_run(run: Any) -> Optional[_Summary]:
    """Reduce a run record (model or dict) to its sidebar-relevant fields.

    Returns None for a run with no status, which cannot be counted.
    """
    def field(name: str) -> Any:
        if isinstance(run, dict):
            return run.get(name)
        return getattr(run, name, None)

    status = field("status")
    if not status:
        return None
    run_id = field("id") or ""
    # ``or run_id`` mirrors the storage default: a record written before
    # lineage tracking is its own single-attempt lineage rather than
    # colliding with every other pre-lineage run under a null key.
    lineage = field("root_run_id") or run_id
    try:
        attempt = int(field("attempt") or 1)
    except (TypeError, ValueError):
        attempt = 1
    return _Summary(str(status), field("source_conversation_id"),
                    str(lineage), attempt)


def collapse_to_newest_attempt(
    summaries: Iterable[_Summary],
) -> List[_Summary]:
    """One summary per attempt-lineage, newest attempt winning.

    A resume creates a NEW run and leaves the source intact, so a card
    retried twice has three records for one logical piece of work.  Counting
    those separately would report "3 failed" for a card that failed once --
    wrong in the direction that matters, since it inflates apparent damage.
    Mirrors ``frontend/.../lineageCollapse.ts`` so the sidebar's two data
    paths cannot disagree about the same runs.

    Strict ``>`` so a tie keeps the first seen: two records claiming the same
    attempt is a corrupt state, but it must still resolve to ONE entry.
    """
    best: Dict[str, _Summary] = {}
    for s in summaries:
        held = best.get(s.lineage)
        if held is None or s.attempt > held.attempt:
            best[s.lineage] = s
    return list(best.values())


def count_summaries(
    summaries: Iterable[_Summary],
) -> Dict[str, Dict[str, int]]:
    """conversation_id -> {status: count} over ALREADY-summarized runs.

    Split from ``build_status_index`` because the cache holds ``_Summary``
    objects, and feeding those back through ``summarize_run`` silently
    dropped every run: a ``_Summary`` exposes ``.status`` but not
    ``.source_conversation_id``, so re-summarizing produced a conversation
    id of None and the "no conversation" filter below discarded it.  The
    index came back empty with no error anywhere -- a summarize step that is
    accidentally idempotent-looking but lossy.

    Runs with no ``source_conversation_id`` are omitted: they belong to no
    row, so counting them would inflate a total nothing displays.
    """
    index: Dict[str, Dict[str, int]] = {}
    by_conv: Dict[str, List[_Summary]] = {}
    for s in summaries:
        if not s.conversation_id:
            continue
        by_conv.setdefault(s.conversation_id, []).append(s)
    # Collapse WITHIN a conversation.  A lineage cannot span conversations,
    # and collapsing globally would let an unrelated project-mate with a
    # colliding pre-lineage id suppress a real run.
    for conv, group in by_conv.items():
        counts: Dict[str, int] = {}
        for s in collapse_to_newest_attempt(group):
            counts[s.status] = counts.get(s.status, 0) + 1
        if counts:
            index[conv] = counts
    return index


def build_status_index(
    runs: Iterable[Any],
) -> Dict[str, Dict[str, int]]:
    """conversation_id -> {status: count} over RAW run records.

    Convenience wrapper: summarize, then count.  Callers holding summaries
    already (the cache) must use ``count_summaries`` directly.
    """
    return count_summaries(
        s for s in (summarize_run(r) for r in runs) if s is not None
    )


def has_live_runs(index: Dict[str, Dict[str, int]]) -> bool:
    """True if any conversation holds a run that can still change itself."""
    for counts in index.values():
        for status in LIVE_STATUSES:
            if counts.get(status):
                return True
    return False


class RunStatusIndexCache:
    """Per-file summary cache over a project's run directory.

    ``get`` returns the whole-project index, re-reading only run files whose
    mtime or size changed since last scan.  The scan itself is one
    ``os.scandir`` plus a stat per entry, which is what makes an idle poll
    effectively free while a poll during a live run costs one file read
    rather than a full history rebuild.
    """

    def __init__(self, runs_dir: str) -> None:
        self._runs_dir = runs_dir
        # path -> (mtime, size, summary-or-None).  None records a file that
        # could not be read or had no status, so a corrupt or undecryptable
        # record is not re-attempted on every poll.
        self._entries: Dict[str, Tuple[float, int, Optional[_Summary]]] = {}
        # Directory mtime at the last scan, used as a cheap pre-gate.
        self._dir_mtime: float = -1.0
        # Last computed index, returned when the pre-gate says nothing moved.
        self._index: Dict[str, Dict[str, int]] = {}
        self.built_at: float = 0.0
        self.reads_last_scan: int = 0
        self.scans: int = 0

    def invalidate(self) -> None:
        """Drop everything; the next ``get`` re-reads the directory."""
        self._entries.clear()
        self._index = {}
        self._dir_mtime = -1.0
        self.built_at = 0.0

    def get(
        self, read_one: Callable[[str], Optional[Any]],
    ) -> Dict[str, Dict[str, int]]:
        """Build the index, reading only files that changed.

        ``read_one`` takes an absolute path and returns a run record (model
        or dict) or None.  Injected rather than imported so this module
        stays free of a storage dependency and is testable without one.
        """
        # Pre-gate on the DIRECTORY mtime.  Every way a run file can change
        # moves it: creation, deletion, and rename-over (which is how
        # ``BaseStorage._write_json`` writes -- temp file then rename).  So an
        # unchanged directory mtime means nothing changed, and the per-file
        # stats below are pure waste: measured 6.1 ms for 203 files versus
        # 0.026 ms for one directory stat, a 235x difference on the case that
        # dominates (an idle project polled forever).
        #
        # This is the INVERSE use of the signal the first design got wrong.
        # There, "directory mtime moved" was taken to mean "rebuild the whole
        # index", which a 5-second heartbeat then triggered constantly.  Here
        # only the negative is trusted -- unchanged means untouched -- and a
        # change falls through to per-file checks that read just what moved.
        try:
            dir_mtime = os.stat(self._runs_dir).st_mtime
        except OSError:
            dir_mtime = -1.0
        if dir_mtime >= 0 and dir_mtime == self._dir_mtime:
            self.reads_last_scan = 0
            return self._index

        self.scans += 1
        try:
            with os.scandir(self._runs_dir) as it:
                found = [
                    e for e in it
                    if e.is_file() and e.name.endswith(".json")
                ]
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            self._entries.clear()
            self._index = {}
            self._dir_mtime = -1.0
            self.built_at = time.time()
            self.reads_last_scan = 0
            return {}

        seen: set = set()
        reads = 0
        for entry in found:
            path = entry.path
            seen.add(path)
            try:
                st = entry.stat()
                sig = (st.st_mtime, st.st_size)
            except OSError:
                continue
            cached = self._entries.get(path)
            if cached is not None and (cached[0], cached[1]) == sig:
                continue
            # Changed or new: read this ONE file.
            reads += 1
            try:
                record = read_one(path)
            except Exception:  # noqa: BLE001 — one bad file must not
                record = None  # break the whole index
            self._entries[path] = (
                sig[0], sig[1],
                summarize_run(record) if record is not None else None,
            )

        # Forget deleted runs, so a purged history does not linger as
        # phantom counts for the life of the process.
        for gone in set(self._entries) - seen:
            del self._entries[gone]

        self._dir_mtime = dir_mtime
        self.built_at = time.time()
        self.reads_last_scan = reads
        self._index = count_summaries(
            s for (_m, _s, s) in self._entries.values() if s is not None
        )
        return self._index


# Registry keyed by directory.  Module-level rather than an attribute on
# TaskRunStorage because that class is constructed PER USE -- a dozen call
# sites, one of them per HTTP request -- so an instance-scoped cache would
# be born empty on every poll and never hit, producing an implementation
# that looks right and saves nothing.
_CACHES: Dict[str, RunStatusIndexCache] = {}


def cache_for(runs_dir: str) -> RunStatusIndexCache:
    """The shared cache for a run directory, created on first use."""
    cache = _CACHES.get(runs_dir)
    if cache is None:
        cache = RunStatusIndexCache(runs_dir)
        _CACHES[runs_dir] = cache
    return cache


def invalidate_for(runs_dir: str) -> None:
    """Drop a directory's cache, if one exists.

    Called by status writers.  A no-op for a directory nothing has polled,
    so writers can call it unconditionally.

    Still needed despite per-file mtime checking: a status change rewrites a
    run in place via temp+rename, and while that does move the file's own
    mtime, an explicit drop makes the invalidation intent legible at the
    write site rather than relying on filesystem timestamp granularity --
    two writes inside one mtime tick would otherwise be indistinguishable.
    """
    cache = _CACHES.get(runs_dir)
    if cache is not None:
        cache.invalidate()


def clear_all_caches() -> None:
    """Test hook: drop every cache."""
    _CACHES.clear()
