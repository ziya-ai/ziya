"""
Replay evidence of saved beads after the assistant turns that created them.

Bead tools are internal: the frontend drops their tool_start/tool_display
events, so a reply that said "parking X" is replayed with no call behind
it.  The bead directive treats an unbacked "parked" as a false claim, so on
the next turn the model concluded its call never happened, apologised, and
created the bead again (.ziya/diagnostics/ziya-beads-phantom-failure-2026-11-09.md).

For each assistant turn in which beads were created, this module appends
one line to the USER message that follows it, sourced from the bead STORE
rather than from message text::

    ‹bead_record›Bead store: 2 bead(s) created in the reply before this
    message were saved: "fix CSS layout" [bead_1cd], "review auth" [bead_048].‹/bead_record›

Why the following user message and not the reply itself: a line at the end
of the model's own earlier reply reads as something the model wrote, and it
copied the pattern into new replies, where it reached the browser as plain
text (2026-11-11).  On a user message it reads as injected context, like
the "Pending Beads" list.  For the most recent reply the following user
message is the current question, which is not in ``chat_history``; that
record is returned separately for the caller to append there.

Why the store and not a marker the frontend writes into the message: text
in ``message.content`` cannot be told apart from text the model wrote, so a
persisted marker would be forgeable evidence.  The store is written only by
the bead tools.  For the same reason any ``‹bead_record`` lookalike in
assistant text (the model imitating the line) is removed when it is a
complete record and defanged otherwise.  The frontend hides the same spans
on display (frontend/src/utils/beadRecordStrip.ts).

Attribution is by time, not ``Bead.message_index``: that index counts the
persisted record, which includes muted messages the frontend leaves out of
``chat_history``, and is documented as approximate.  A bead created while
the model answered human turn k has ``created_at`` after human k's
``_timestamp`` and before human k+1's, so its record goes on human k+1
(or on the current question for the last window), provided the window
holds an assistant message at all.  Human messages without a timestamp, or
with timestamps out of order, disable attribution (no record is better than
a wrong one).  Bead times are server clock and message times browser clock;
on the usual localhost setup they are the same clock.

Only creation facts (content, id) are listed, never the current status, so
an earlier message's bytes do not change when a bead is later completed and
the prompt-cache prefix stays valid.
"""
from __future__ import annotations

import bisect
import re
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger

OPEN = "\u2039bead_record\u203a"
CLOSE = "\u2039/bead_record\u203a"
MAX_LISTED = 12

_ASSISTANT = frozenset({"ai", "assistant"})
_HUMAN = frozenset({"human", "user"})
# Either bracket alphabet, so neither "<bead_record>" nor "‹bead_record›"
# written by the model survives replay as a record.
_LOOKALIKE_RE = re.compile(r"[<\u2039](\s*/?\s*bead_record\b)", re.IGNORECASE)


def _role(msg: Dict[str, Any]) -> Optional[str]:
    t = msg.get("type") or msg.get("role")
    if t in _ASSISTANT:
        return "assistant"
    if t in _HUMAN:
        return "human"
    return None


def defang(text: str) -> str:
    """Neutralise record lookalikes so text can never open or close one."""
    return _LOOKALIKE_RE.sub(r"[\1", text) if text else text


# A complete single-line record the model wrote in its own reply, in either
# bracket alphabet, with the blank lines that precede it.  The real record
# is rebuilt from the store on every request, so a typed one carries no
# information; leaving it (even defanged) replays an example to copy.
_SPAN_RE = re.compile(
    r"\n{0,2}[<\u2039]\s*bead_record\s*[>\u203a][^\n]*?"
    r"[<\u2039]\s*/\s*bead_record\s*[>\u203a]",
    re.IGNORECASE,
)


def strip_record_spans(text: str) -> str:
    """Remove complete model-written records; fragments are left for defang."""
    return _SPAN_RE.sub("", text) if text else text


def _scrub(text: str) -> str:
    return defang(strip_record_spans(text))


def _scrub_content(content: Any) -> Any:
    if isinstance(content, str):
        out = _scrub(content)
        return content if out == content else out
    if isinstance(content, list):
        changed = False
        blocks = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                t = _scrub(b["text"])
                if t != b["text"]:
                    b = {**b, "text": t}
                    changed = True
            blocks.append(b)
        return blocks if changed else content
    return content


def _record_line(beads: List[Any]) -> str:
    listed = beads[:MAX_LISTED]
    parts = [f'"{defang(str(b.content))}" [{str(b.id)[:8]}]' for b in listed]
    more = len(beads) - len(listed)
    if more > 0:
        parts.append(f"+{more} more")
    return (f"\n\n{OPEN}Bead store: {len(beads)} bead(s) created in the reply "
            f"before this message were saved: {', '.join(parts)}.{CLOSE}")


def _append(content: Any, line: str) -> Any:
    if isinstance(content, str):
        return content + line
    if isinstance(content, list):
        return list(content) + [{"type": "text", "text": line}]
    return content


def attribute_beads(history: List[Any], beads: List[Any]) -> Dict[int, List[Any]]:
    """Map the index of the human message FOLLOWING a turn -> beads it created.

    The last window has no following message in *history*; its beads are
    keyed by ``len(history)``, meaning the current question.
    """
    humans: List[tuple] = []
    for i, m in enumerate(history):
        if isinstance(m, dict) and _role(m) == "human":
            ts = m.get("_timestamp")
            if not isinstance(ts, (int, float)) or isinstance(ts, bool):
                return {}
            humans.append((float(ts), i))
    if not humans:
        return {}
    times = [t for t, _ in humans]
    if any(b < a for a, b in zip(times, times[1:])):
        return {}

    out: Dict[int, List[Any]] = {}
    for bead in sorted(beads, key=lambda b: getattr(b, "created_at", 0) or 0):
        created = getattr(bead, "created_at", None)
        if not isinstance(created, (int, float)):
            continue
        k = bisect.bisect_right(times, float(created)) - 1
        if k < 0:
            continue  # before the first timed human message
        start = humans[k][1]
        end = humans[k + 1][1] if k + 1 < len(humans) else len(history)
        # A window with no reply (a retried or unanswered question) has no
        # turn to attribute to.
        if any(isinstance(history[j], dict) and _role(history[j]) == "assistant"
               for j in range(start + 1, end)):
            out.setdefault(end, []).append(bead)
    return out


def annotate_bead_history_with_tail(
        history: List[Any], conversation_id: Optional[str],
        beads: Optional[List[Any]] = None) -> Tuple[List[Any], str]:
    """Return (history copy with records on user messages, record for the
    current question).

    Assistant text is scrubbed of record lookalikes.  *beads* overrides the
    store lookup (tests).  Input is not mutated; with nothing to change,
    the original list is returned.  The second value is "" when the most
    recent reply created no beads.
    """
    if not history:
        return history, ""
    if beads is None:
        beads = []
        if conversation_id:
            try:
                from app.storage.beads import load_bead_tree
                beads = load_bead_tree(conversation_id=conversation_id).beads
            except Exception as e:  # noqa: BLE001 -- never fail a turn on this
                logger.debug(f"📿 bead history record: store unavailable: {e}")
                beads = []
    targets = attribute_beads(history, beads) if beads else {}
    tail_beads = targets.pop(len(history), None)
    tail = _record_line(tail_beads) if tail_beads else ""

    out: List[Any] = []
    changed = False
    for i, m in enumerate(history):
        if isinstance(m, dict):
            role = _role(m)
            content = original = m.get("content", "")
            if role == "assistant":
                content = _scrub_content(content)
            elif role == "human" and i in targets:
                content = _append(content, _record_line(targets[i]))
            if content is not original:
                m = {**m, "content": content}
                changed = True
        out.append(m)
    return (out if changed else history), tail


def annotate_bead_history(history: List[Any], conversation_id: Optional[str],
                          beads: Optional[List[Any]] = None) -> List[Any]:
    """History half of :func:`annotate_bead_history_with_tail`."""
    return annotate_bead_history_with_tail(history, conversation_id, beads)[0]
