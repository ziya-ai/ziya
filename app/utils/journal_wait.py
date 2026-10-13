"""Condition-driven waiting on an append-only journal.

Shared by ``shadow_wait`` (a shadow session's journal, fed by the PTY host
in the same process) and ``job_wait`` (a managed job's journal, written by
``ziya-jobwrap`` in another process).  Both journals use the record format
of ``app.shadow.journal`` -- ``cmd`` / ``output`` / ``exit`` / ``meta`` with
a monotonic ``seq`` -- so one matcher serves both, and a model that knows
one tool's conditions and return shape knows the other's.

The engine is agnostic to *how* it is woken.  The caller supplies a
``Waiter`` (``clear()`` + ``wait(seconds)``): the shadow socket server
passes a ``threading.Event`` set by a journal listener, so an append wakes
the waiter immediately; the job runner, which only sees the file, passes a
poller that sleeps briefly.  The wait is cleared BEFORE each drain so an
append racing the read is never lost.

Conditions (``until``; the first that holds wins, the rest of the pass is
still drained for the tail):

  exit      an ``exit`` record for ``cmd_seq`` (any exit when None)
  change    any new record
  pattern   new cmd/output/meta text matches the compiled regex
  lines     at least ``lines`` new output lines
  quiet     no new record for ``quiet_ms``
  ask       a ``meta`` record with event ``ask`` (or ``initial_ask``)

Two reasons arrive regardless of ``until``: ``timeout`` and whatever the
``stopped`` callback returns (``session_ended`` for a shadow session that
is shutting down, ``lost`` for a job whose wrapper died without an exit).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Set, Tuple

WAIT_CONDITIONS = ("exit", "change", "pattern", "lines", "quiet", "ask")
DEFAULT_DRAIN = 2000          # records read per pass
_MATCH_CONTEXT_CHARS = 400


class Waiter(Protocol):
    def clear(self) -> None: ...
    def wait(self, timeout: float) -> Any: ...


class PollWaiter:
    """A ``Waiter`` for journals written by another process: there is no
    in-process append event, so sleep in short slices and re-read."""

    def __init__(self, slice_s: float = 0.2):
        self.slice_s = slice_s

    def clear(self) -> None:
        return None

    def wait(self, timeout: float) -> None:
        time.sleep(max(0.0, min(timeout, self.slice_s)))


@dataclass
class WaitSpec:
    until: Set[str]
    cmd_seq: Optional[int] = None
    from_seq: Optional[int] = None
    pattern: Any = None                 # compiled regex when "pattern" in until
    lines: int = 5
    quiet_ms: int = 800
    timeout_ms: int = 60000
    max_tail_records: int = 40


def parse_wait_request(req: Dict[str, Any], *, cap_ms: int, max_tail_records: int,
                       compile_pattern: Callable[[Any], Any]
                       ) -> Tuple[Optional[WaitSpec], Optional[Tuple[str, str]]]:
    """Validate a wait request dict into a ``WaitSpec``.

    Returns ``(spec, None)`` or ``(None, (error_code, message))``.  The
    error codes and messages are those the shadow socket op has always
    returned, so its callers see no change.
    """
    try:
        raw_cmd = req.get("cmd_seq")
        cmd_seq = int(raw_cmd) if raw_cmd is not None else None
        lines_n = max(1, int(req.get("lines", 5) or 5))
        quiet_ms = max(50, min(int(req.get("quiet_ms", 800) or 800), 5000))
        timeout_ms = max(0, min(int(req.get("timeout_ms", cap_ms)), cap_ms))
        tail_n = max(1, min(int(req.get("max_tail_records", 40) or 40), max_tail_records))
        raw_from = req.get("from_seq")
        from_seq = int(raw_from) if raw_from is not None else None
    except (TypeError, ValueError):
        return None, ("bad_request", "cmd_seq, from_seq, lines, quiet_ms, timeout_ms and "
                                     "max_tail_records must be integers")
    until = req.get("until")
    if until is None:
        until = ["exit"] if cmd_seq is not None else ["change"]
    # A bare string is refused, not wrapped: the socket protocol has always
    # required a list, and the shadow tests pin that.  Tools normalise before
    # sending.
    if (not isinstance(until, list) or not until
            or any(u not in WAIT_CONDITIONS for u in until)):
        return None, ("bad_request",
                      f"until must be a non-empty list drawn from {list(WAIT_CONDITIONS)}")
    want = set(until)
    pattern = None
    if "pattern" in want:
        pattern = compile_pattern(req.get("pattern"))
        if pattern is None:
            return None, ("bad_request",
                          "until=['pattern'] needs a safe, non-empty regex in pattern")
    if from_seq is not None and from_seq < 1:
        return None, ("bad_seq", "from_seq must be >= 1")
    return WaitSpec(until=want, cmd_seq=cmd_seq, from_seq=from_seq, pattern=pattern,
                    lines=lines_n, quiet_ms=quiet_ms, timeout_ms=timeout_ms,
                    max_tail_records=tail_n), None


def wait_on_journal(reader: Any, spec: WaitSpec, *, waiter: Waiter,
                    searchable_text: Callable[[Dict[str, Any]], str],
                    stopped: Optional[Callable[[], Optional[str]]] = None,
                    initial_ask: bool = False,
                    drain: int = DEFAULT_DRAIN,
                    now: Callable[[], float] = time.monotonic) -> Dict[str, Any]:
    """Block until a condition in ``spec`` holds; return the new records' tail.

    ``reader`` needs ``read(from_seq, max_records) -> {"records", "next_seq"}``
    and ``bounds() -> {"tail_seq"}`` (``app.shadow.journal.JournalReader``).
    Replays from ``spec.from_seq`` first (default: just past the current
    tail), so an exit that landed between the caller's last read and this
    call fires immediately.
    """
    want = spec.until
    from_seq = spec.from_seq
    if from_seq is None:
        from_seq = (reader.bounds().get("tail_seq") or 0) + 1
    cursor = from_seq
    new_records = 0
    out_lines = 0
    tail: deque = deque(maxlen=spec.max_tail_records)
    exit_seen = False
    result: Dict[str, Any] = {}
    reason: Optional[str] = None
    quiet_s = spec.quiet_ms / 1000.0
    deadline = now() + spec.timeout_ms / 1000.0
    last_append = now()
    if "ask" in want and initial_ask:
        reason = "ask"
    while reason is None:
        waiter.clear()
        while True:
            page = reader.read(cursor, drain)
            recs = page["records"]
            if not recs:
                break
            last_append = now()
            cursor = page["next_seq"]
            for rec in recs:
                new_records += 1
                tail.append(rec)
                t = rec.get("t")
                text = rec.get("text") or ""
                if t == "output" and text:
                    out_lines += text.count("\n") + (0 if text.endswith("\n") else 1)
                mine = t == "exit" and (spec.cmd_seq is None or rec.get("cmd_seq") == spec.cmd_seq)
                exit_seen = exit_seen or mine
                if reason is not None:
                    continue   # first condition wins; keep draining for the tail
                if t == "meta" and rec.get("event") == "exit_session":
                    reason = "session_ended"
                elif mine and "exit" in want:
                    reason = "exit"
                    result.update(exit_code=rec.get("code"), exit_seq=rec.get("seq"))
                elif "ask" in want and t == "meta" and rec.get("event") == "ask":
                    reason = "ask"
                elif spec.pattern is not None and t in ("cmd", "output", "meta"):
                    hay = searchable_text(rec)
                    m = spec.pattern.search(hay)
                    if m:
                        reason = "pattern"
                        s = hay.rfind("\n", 0, m.start()) + 1
                        e = hay.find("\n", m.end())
                        result["matched"] = {
                            "seq": rec.get("seq"),
                            "text": hay[s:(len(hay) if e < 0 else e)][:_MATCH_CONTEXT_CHARS],
                        }
                if reason is None and "lines" in want and out_lines >= spec.lines:
                    reason = "lines"
                if reason is None and "change" in want:
                    reason = "change"
            if len(recs) < drain:
                break
        if reason is not None:
            break
        t_now = now()
        stop_reason = stopped() if stopped is not None else None
        if stop_reason:
            reason = stop_reason
        elif "quiet" in want and t_now - last_append >= quiet_s:
            reason = "quiet"
        elif t_now >= deadline:
            reason = "timeout"
        else:
            wait_s = deadline - t_now
            if "quiet" in want:
                wait_s = min(wait_s, quiet_s - (t_now - last_append))
            waiter.wait(max(0.01, wait_s))
    result.update({
        "reason": reason, "from_seq": from_seq, "next_seq": cursor,
        "new_records": new_records, "output_lines": out_lines,
        "records": list(tail), "tail_truncated": new_records > len(tail),
        "still_running": spec.cmd_seq is not None and not exit_seen,
        "timed_out": reason == "timeout",
    })
    return result
