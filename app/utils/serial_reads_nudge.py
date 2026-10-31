"""Nudge a model that issues one read-only tool call per message.

The per-turn batch runner (app/tool_batch.py) overlaps every read-only call
that arrives in the same assistant message, and the audit log shows it does:
two calls in one message start within milliseconds of each other.  The log
also shows the runner rarely gets the chance.  Over 2026-10-15..20, 77% of
tool batches held a single call, 1,468 of them a read-only call issued in
the message right after another lone read-only call, and the median model
round-trip between batches was 9.3 s against sub-second tools.  The time is
in round-trips, and only the model can remove them by putting more calls
into each message.

The system prompt already says to batch independent calls.  This module is
the feedback loop for when that does not land: it watches the shape of each
iteration's tool calls and, on the second consecutive message holding
exactly one read-only call, produces a note the executor attaches to that
iteration's tool-result turn (via ``_append_to_last_user_turn``, the same
channel as the "already shown" note).  Any iteration with several calls, or
with a non-read call, resets the streak, so a model that is genuinely
chaining dependent reads is told once and then left alone until it does it
again.

Pure: no I/O, no executor state beyond the counter it owns, so the policy
is testable on its own.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Tuple

# Streak length at which the first note fires, and how often it repeats
# while the streak continues (2, 5, 8, ...).  Repeating keeps a long serial
# run from being nudged only once at its start; spacing keeps the note from
# becoming noise the model learns to skip.
FIRST_NOTE_AT = 2
REPEAT_EVERY = 3


def should_note(streak: int) -> bool:
    """True when a streak of ``streak`` single-read messages gets a note."""
    return streak >= FIRST_NOTE_AT and (streak - FIRST_NOTE_AT) % REPEAT_EVERY == 0


def serial_reads_note(streak: int) -> str:
    return (
        f"[System: This is the {_ordinal(streak)} consecutive message in which "
        "you issued exactly one read-only tool call. Each such message costs a "
        "full model round-trip (typically 5-20 s) while the tool itself took "
        "well under a second. Ziya runs every read-only call from ONE message "
        "concurrently and returns the results together, so reads issued one "
        "per message are the slowest possible way to gather information. "
        "Before your next call, list every read you will need regardless of "
        "this result (files to open, greps, searches, lookups, status checks) "
        "and issue them all in the same message. Serialize only a call whose "
        "arguments depend on a result you do not have yet.]"
    )


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class SerialReadTracker:
    """Counts consecutive iterations whose tool calls were a single read.

    ``observe`` takes the (normalized tool name, args) pairs executed in one
    iteration and returns the note to attach to that iteration's tool-result
    turn, or ``None``.  Classification is ``app.tool_batch.is_read_only_call``,
    the same predicate the batch runner uses, so the note is only ever about
    calls that would in fact have overlapped.
    """

    def __init__(self) -> None:
        self.streak = 0
        self.notes_sent = 0

    def observe(self, calls: Iterable[Tuple[str, Optional[dict]]]) -> Optional[str]:
        calls = list(calls)
        if len(calls) == 1 and _is_read_only(*calls[0]):
            self.streak += 1
        else:
            self.streak = 0
            return None
        if should_note(self.streak):
            self.notes_sent += 1
            return serial_reads_note(self.streak)
        return None


def _is_read_only(name: str, args: Optional[dict]) -> bool:
    try:
        from app.tool_batch import is_read_only_call
        return bool(is_read_only_call(name, args or {}))
    except Exception:  # noqa: BLE001 — a classifier error must never affect the turn
        return False
