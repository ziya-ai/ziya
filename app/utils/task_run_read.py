"""Read-side view of a task run for the ``task_run_read`` tool.

Run records are encrypted at rest (TaskRunStorage decrypts) and the
live-event journal (``events.jsonl``) is plain JSON lines that only a
browser tail consumed.  Nothing in the tool set exposed either, so a
model — or an operator working through one — diagnosing a failed night
had to reverse-engineer the journal by hand.  ``task_card_launch``'s own
text told the model to "poll the task-run views"; those views did not
exist as tools.

This module builds BOUNDED, payload-free summaries from the storage
layer: a run's identity and status, which block failed and why (the
executor's ``failure_signature`` / ``self_assessment`` / decisions), an
index of its outputs, the ``task_finished`` events from its journal, and
the card's schedule-state (fires, skips, deferrals) when the card is
scheduled.  Summaries are truncated and lists capped so the result is
affordable in a model's context; the tool is for finding out WHAT
happened, not for reading every artifact body.

Pure functions over ``TaskRunStorage`` so they are unit-testable without
the tool/MCP layer.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger

SUMMARY_CHARS = 600
DECISIONS_CAP = 20
EVENTS_CAP = 200
RUNS_LIST_CAP = 50

# Journal event types worth returning to a reader.  Text deltas and tool
# traffic are the bulk of a journal and are what the summaries already
# distil; these carry the verdicts and the holds.
_VERDICT_EVENT_TYPES = frozenset({
    "task_finished", "iteration_completed", "run_held", "run_status",
    "ask_opened", "ask_answered", "block_status",
    # The Until evaluator's per-iteration yes/no and its reason -- the
    # one verdict in a run that is not derivable from the body's output.
    "until_evaluated",
})


def _clip(text: Optional[str], n: int = SUMMARY_CHARS) -> Optional[str]:
    if text is None:
        return None
    text = str(text)
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def _artifact_view(art: Any, *, full_decisions: bool = False) -> Optional[Dict[str, Any]]:
    """The parts of an Artifact a reader needs to judge it, without payloads."""
    if art is None:
        return None
    decisions = list(getattr(art, "decisions", None) or [])
    outputs = list(getattr(art, "outputs", None) or [])
    view: Dict[str, Any] = {
        "failed": bool(getattr(art, "failed", False)),
        "signature": getattr(art, "signature", None),
        "self_assessment": getattr(art, "self_assessment", None),
        "summary": _clip(getattr(art, "summary", "")),
        "decisions": decisions if full_decisions else decisions[-DECISIONS_CAP:],
        "decisions_total": len(decisions),
        "outputs": [
            {
                "name": getattr(o, "name", None),
                "label": getattr(o, "label", None),
                "group": getattr(o, "group", None),
                "part_type": getattr(o, "part_type", None),
                "media_type": getattr(o, "media_type", None),
                "schema_id": getattr(o, "schema_id", None),
            }
            for o in outputs
        ],
        "tokens": getattr(art, "tokens", 0),
        "tool_calls": getattr(art, "tool_calls", 0),
        "duration_ms": getattr(art, "duration_ms", 0),
    }
    return view


def failure_reason_of(art: Any) -> Optional[str]:
    """One line saying why an artifact is failed, mirroring the rule
    task_executor uses for ``task_finished.failure_reason`` so a reader
    of an older run (whose journal predates that field) gets the same
    answer.  ``None`` for a passing artifact."""
    if art is None or not getattr(art, "failed", False):
        return None
    sig = getattr(art, "signature", None) or ""
    sa = getattr(art, "self_assessment", None) or {}
    if sig.startswith("self_assessment_") and sa:
        verdict = sa.get("objective_met", "unknown")
        rationale = (sa.get("rationale") or "").strip()
        return (f"self_assessment objective_met={verdict}"
                + (f": {rationale}" if rationale else ""))
    if sig == "until_condition_unmet":
        # An Until that exhausted its budget (or stopped as unreachable)
        # with the condition unmet.  The exhaustion decision carries the
        # evaluator's last reason; prefer it over the per-iteration ones.
        ds = list(getattr(art, "decisions", None) or [])
        for d in reversed(ds):
            if d.startswith("until condition not satisfied after"):
                return d
        for d in reversed(ds):
            if d.startswith("until condition not satisfied"):
                return d
        return "until condition not satisfied"
    kind = sig.split(":", 1)[0]
    for d in getattr(art, "decisions", None) or []:
        if kind and d.startswith(kind):
            return d
    # A container's failure is one of its children's; the first decision
    # naming a failed child is the most specific thing on record.
    for d in getattr(art, "decisions", None) or []:
        if "failed" in d:
            return d
    return sig or "failed"


def _block_view(block_id: str, st: Any) -> Dict[str, Any]:
    art = getattr(st, "artifact", None)
    its = list(getattr(st, "iteration_summaries", None) or [])
    view: Dict[str, Any] = {
        "block_id": block_id,
        "block_type": getattr(st, "block_type", None),
        "status": getattr(st, "status", None),
        "started_at": getattr(st, "started_at", None),
        "completed_at": getattr(st, "completed_at", None),
        "error": getattr(st, "error", None),
    }
    if art is not None:
        view["failed"] = bool(getattr(art, "failed", False))
        view["signature"] = getattr(art, "signature", None)
        view["self_assessment"] = getattr(art, "self_assessment", None)
        view["failure_reason"] = failure_reason_of(art)
        view["outputs"] = len(getattr(art, "outputs", None) or [])
    if its:
        view["iterations"] = len(its)
        view["iterations_failed"] = sum(
            1 for s in its if getattr(s, "status", None) == "failed")
        planned = getattr(st, "planned_iterations", None)
        if planned is not None:
            view["planned_iterations"] = planned
    return view


def _snapshot_header(run: Any) -> Dict[str, Any]:
    snap = getattr(run, "card_snapshot", None) or {}
    return {
        "card_name": snap.get("name"),
        "card_version": snap.get("version"),
        "has_card_snapshot": bool(snap.get("root")),
        # Set by the scheduler's _fire_one; absent on interactive launches.
        "scheduled_fire": bool(snap.get("schedule_block_id")),
        "schedule_block_id": snap.get("schedule_block_id"),
    }


def run_header(run: Any) -> Dict[str, Any]:
    """Identity + status of a run, one row of a listing."""
    art = getattr(run, "artifact", None)
    h: Dict[str, Any] = {
        "run_id": run.id,
        "card_id": run.card_id,
        "status": run.status,
        "launch_context": getattr(run, "launch_context", None),
        "created_at": getattr(run, "created_at", None),
        "started_at": getattr(run, "started_at", None),
        "completed_at": getattr(run, "completed_at", None),
        "error": _clip(getattr(run, "error", None), 300),
        "held_reason": getattr(run, "held_reason", None),
        "held_at_block_id": getattr(run, "held_at_block_id", None),
        "attempt": getattr(run, "attempt", None),
        "resumed_from_block_id": getattr(run, "resumed_from_block_id", None),
        "parent_run_id": getattr(run, "parent_run_id", None),
        "source_conversation_id": getattr(run, "source_conversation_id", None),
        "total_tokens": getattr(run, "total_tokens", 0),
        "total_tool_calls": getattr(run, "total_tool_calls", 0),
    }
    h.update(_snapshot_header(run))
    if art is not None:
        h["failed"] = bool(getattr(art, "failed", False))
        h["failure_reason"] = failure_reason_of(art)
    return h


def read_journal_events(
    journal_path: Path, *, kinds: Optional[frozenset] = None, cap: int = EVENTS_CAP,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Events of the given kinds from a run's journal (newest last, capped
    to the LAST ``cap``), plus a count of every event type seen.

    The journal is written by the executing process as JSON lines; a
    trailing partial line is skipped, as the relay's own reader does.
    """
    counts: Dict[str, int] = {}
    kept: List[Dict[str, Any]] = []
    if not journal_path.exists():
        return kept, counts
    wanted = _VERDICT_EVENT_TYPES if kinds is None else kinds
    try:
        with open(journal_path, "rb") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                t = str(ev.get("type") or "")
                counts[t] = counts.get(t, 0) + 1
                if t in wanted:
                    kept.append(ev)
    except OSError as e:
        logger.debug(f"task_run_read: journal unreadable {journal_path}: {e}")
    if len(kept) > cap:
        kept = kept[-cap:]
    return kept, counts


def schedule_state_for(project_id: str, card_id: str) -> Optional[Dict[str, Any]]:
    """The scheduler's record for a card (fires, skips, deferrals), or
    None when the card has never been scheduled."""
    try:
        from app.agents.task_scheduler import _read_state
        rec = _read_state(project_id).get(card_id)
    except Exception as e:  # noqa: BLE001 — optional enrichment
        logger.debug(f"task_run_read: schedule state unavailable: {e}")
        return None
    if not rec:
        return None
    out = dict(rec)
    out["run_ids"] = list(rec.get("run_ids", []))[:10]
    out["skips"] = list(rec.get("skips", []))[:10]
    return out


def last_run_summary(storage: Any, run_ids: List[str]) -> Optional[Dict[str, Any]]:
    """Status and failure reason of the newest run in ``run_ids`` that still
    exists, for the schedule-state endpoint.

    ``schedule_state.run_ids`` is newest-first and is not pruned when a run
    is deleted, so entries are tried in order and a missing run is skipped
    rather than reported.  ``None`` when no listed run can be read.  Kept
    to the few fields the schedule editor's status line shows; the full
    picture is one ``task_run_read(run_id=...)`` away.
    """
    for rid in run_ids or []:
        if not rid or any(c in rid for c in ("/", "\\", "\x00")) or ".." in rid:
            continue
        try:
            run = storage.get(rid)
        except Exception as e:  # noqa: BLE001 — an unreadable record is "not there"
            logger.debug(f"task_run_read: last_run read failed for {rid}: {e}")
            continue
        if run is None:
            continue
        art = getattr(run, "artifact", None)
        return {
            "run_id": run.id,
            "status": run.status,
            "created_at": getattr(run, "created_at", None),
            "completed_at": getattr(run, "completed_at", None),
            "failed": bool(getattr(art, "failed", False)) if art is not None else None,
            "failure_reason": failure_reason_of(art) if art is not None else None,
            "held_reason": getattr(run, "held_reason", None),
            "error": _clip(getattr(run, "error", None), 300),
        }
    return None


def list_runs(storage: Any, *, card_id: Optional[str] = None,
              limit: int = 20) -> List[Dict[str, Any]]:
    """Newest-first headers of a project's runs, optionally one card's."""
    try:
        runs = storage.list(card_id=card_id)
    except TypeError:
        runs = [r for r in storage.list() if not card_id or r.card_id == card_id]
    runs.sort(key=lambda r: getattr(r, "created_at", 0) or 0, reverse=True)
    n = max(1, min(int(limit or 20), RUNS_LIST_CAP))
    return [run_header(r) for r in runs[:n]]


def resolve_card_id(card_storage: Any, ref: str) -> Optional[str]:
    """A card id from an id or a (case-insensitive) name; None if unknown."""
    if not ref:
        return None
    try:
        if card_storage.get(ref):
            return ref
    except Exception:  # noqa: BLE001
        pass
    try:
        cards = card_storage.list()
    except Exception:  # noqa: BLE001
        return None
    low = ref.strip().lower()
    for c in cards:
        if (getattr(c, "name", "") or "").strip().lower() == low:
            return c.id
    return None


def summarize_run(
    storage: Any, run: Any, *,
    project_id: Optional[str] = None,
    block_id: Optional[str] = None,
    include_events: bool = True,
) -> Dict[str, Any]:
    """Everything an operator needs to say what a run did and why it
    stopped, bounded.  ``block_id`` adds that block's full artifact and
    iteration summaries."""
    out: Dict[str, Any] = {"run": run_header(run)}
    out["artifact"] = _artifact_view(getattr(run, "artifact", None))

    states = getattr(run, "block_states", None) or {}
    blocks = [_block_view(bid, st) for bid, st in states.items()]
    blocks.sort(key=lambda b: (b.get("started_at") or 0, b["block_id"]))
    out["blocks"] = blocks
    out["failures"] = [
        {k: b.get(k) for k in ("block_id", "block_type", "status", "signature",
                               "failure_reason", "self_assessment", "error")}
        for b in blocks
        if b.get("failed") or b.get("status") == "failed" or b.get("error")
    ]
    pending = getattr(run, "pending_ask", None)
    if pending:
        out["pending_ask"] = pending
    if getattr(run, "held_faults", None):
        out["held_faults"] = run.held_faults

    if block_id:
        st = states.get(block_id)
        if st is None:
            out["block"] = {"block_id": block_id,
                            "error": "no such block in this run's block_states"}
        else:
            detail = _block_view(block_id, st)
            detail["artifact"] = _artifact_view(
                getattr(st, "artifact", None), full_decisions=True)
            detail["iteration_summaries"] = [
                {
                    "index": getattr(s, "index", None),
                    "status": getattr(s, "status", None),
                    "signature": getattr(s, "signature", None),
                    "duration_ms": getattr(s, "duration_ms", 0),
                    "tokens": getattr(s, "tokens", 0),
                }
                for s in (getattr(st, "iteration_summaries", None) or [])
            ]
            out["block"] = detail

    if include_events:
        try:
            events, counts = read_journal_events(storage.journal_path(run.id))
        except Exception as e:  # noqa: BLE001
            events, counts = [], {}
            logger.debug(f"task_run_read: journal read failed for {run.id}: {e}")
        if block_id:
            events = [e for e in events if e.get("block_id") == block_id]
        out["events"] = events
        out["event_counts"] = counts

    if project_id:
        sched = schedule_state_for(project_id, run.card_id)
        if sched is not None:
            out["schedule"] = sched
    return out
