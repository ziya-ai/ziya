"""
run_time — one clock convention for task-run records.

Every timestamp on a TaskRun and everything nested in it (block states,
iteration summaries, attempts, progress notes, asks, artifacts) is an
integer of **epoch milliseconds**.  That is the ``BaseStorage``
convention ``created_at``/``updated_at`` always used, and the one
TaskCard, TaskBinding and Bead already share.

Until 2026-09 the executor-side fields (``started_at``, ``completed_at``,
``last_activity_at``, block/iteration ``*_at``, ``ProgressNote.at``,
``Artifact.created_at``, ask ``opened_at``/``answered_at``) were float
epoch *seconds*.  No consumer subtracted one unit from the other, which
is why the split survived, but a record read raw was unreadable and a
``completed_at - created_at`` would have been silently wrong.

This module owns the change:

* ``now_ms()`` is the single stamping function for writers.
* ``normalize_run_record`` / ``normalize_artifact`` upgrade a record
  read from disk that predates the change.  Records are upgraded lazily,
  in memory, on read: a mass rewrite at startup would race a sibling
  server still writing a live run (the same shared-``~/.ziya`` topology
  that broke run d2c18548), and a terminal v1 record left as-is on disk
  is harmless because the shim is permanent.

The upgrade is a heuristic on magnitude, not a flag: any populated value
below ``SECONDS_CEILING`` is seconds.  1e11 seconds is the year 5138;
1e11 milliseconds is 1973.  No real timestamp is ambiguous.  A record
carrying ``schema_version >= 2`` skips the heuristic entirely, so the
walk is only paid for unversioned files.
"""

from __future__ import annotations

import time
from typing import Annotated, Any, Dict, Optional

from pydantic import BeforeValidator

# Records at or above this version have every timestamp in ms already.
RUN_RECORD_SCHEMA_VERSION = 2

# A populated timestamp below this is epoch seconds; at or above, epoch
# milliseconds.  See module docstring for why the gap is unambiguous.
SECONDS_CEILING = 1e11


def now_ms() -> int:
    """Current wall-clock time as integer epoch milliseconds."""
    return int(time.time() * 1000)


def to_ms(value: Any) -> Any:
    """Return ``value`` as integer epoch milliseconds.

    A non-numeric or non-positive value (None, 0, a string) is returned
    unchanged: 0 is the ``BaseStorage`` "unset" default and must stay a
    falsy int, and anything unexpected is not this function's to fix.
    Idempotent — a value already in ms is only truncated to int.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if value <= 0:
        return value
    if value < SECONDS_CEILING:
        return int(round(value * 1000))
    return int(value)


# Field type for every persisted run-record timestamp.  Declared as
# ``int`` (milliseconds) and upgraded at the model boundary, so a caller
# that still passes ``time.time()`` produces an ms record rather than a
# ValidationError or, worse, a silently mixed one.  The coercion is the
# same heuristic the on-disk normalizer uses.
EpochMs = Annotated[int, BeforeValidator(to_ms)]


# --- record walkers ----------------------------------------------------

_TIME_KEYS = ("started_at", "completed_at", "last_activity_at",
              "created_at", "updated_at", "opened_at", "answered_at", "at")


def _fix_keys(d: Optional[Dict[str, Any]]) -> None:
    if not isinstance(d, dict):
        return
    for k in _TIME_KEYS:
        if k in d:
            d[k] = to_ms(d[k])


def normalize_artifact(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Upgrade an ``Artifact`` dict in place (``created_at``).  Returns it."""
    _fix_keys(data)
    return data


def _fix_block_state(state: Any) -> None:
    """Block state, superseded block state, or iteration summary — all
    carry the same ``*_at`` + ``artifact`` shape, and a block state nests
    iteration summaries and a history of superseded states."""
    if not isinstance(state, dict):
        return
    _fix_keys(state)
    normalize_artifact(state.get("artifact"))
    for it in state.get("iteration_summaries") or []:
        _fix_block_state(it)
    for old in state.get("history") or []:
        _fix_block_state(old)


def normalize_run_record(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Upgrade a raw TaskRun dict read from disk to the ms convention.

    In place and idempotent.  Stamps ``schema_version`` so the next read
    of the same dict (or of the file, once a writer persists it) skips
    the walk.  Returns ``data`` for call-site convenience.
    """
    if not isinstance(data, dict):
        return data
    if (data.get("schema_version") or 0) >= RUN_RECORD_SCHEMA_VERSION:
        return data

    _fix_keys(data)
    normalize_artifact(data.get("artifact"))

    for state in (data.get("block_states") or {}).values():
        _fix_block_state(state)
    for attempt in data.get("attempts") or []:
        _fix_keys(attempt)
    for note in data.get("progress_notes") or []:
        _fix_keys(note)
    _fix_keys(data.get("pending_ask"))
    for ans in (data.get("ask_answers") or {}).values():
        _fix_keys(ans)
    for art in (data.get("resume_iteration_artifacts") or {}).values():
        normalize_artifact(art)

    data["schema_version"] = RUN_RECORD_SCHEMA_VERSION
    return data
