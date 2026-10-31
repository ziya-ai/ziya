"""Answer a tool-consent request from the terminal (``ziya chat`` / ``ziya ask``).

The consent gate (app/utils/consent_gate.py) parks a tool call in the
consent ledger and waits up to 24 h for a human answer through
``POST /api/consent/{id}``.  In the browser, ConsentPanel renders the
``consent_opened`` event and posts that answer.  The CLI stream handler
dropped the event, so in ``ziya chat`` a ``file_write`` outside the write
policy (or any ``ask``-mode tool such as ``git commit``) printed
"Executing file write..." and then waited with nothing on screen until
the user pressed ^C.  Observed 2026-10-27, three turns in a row.

This module shows the request and records the answer in the same ledger
the gate is polling, so the gate continues exactly as it would after a
browser approval.  When there is no terminal to ask (stdin is not a TTY,
e.g. ``git diff | ziya ask``) it rejects immediately, so the model gets a
refusal instead of the turn hanging.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable, Dict, List, Optional

ANSWERED_BY = "terminal"
DIFF_LINES_SHOWN = 80
_ARGS_CAP = 1200


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def describe(event: Dict[str, Any]) -> List[str]:
    """The lines to show for a ``consent_opened`` event (no colour codes
    beyond the diff, so the content is testable)."""
    payload = event.get("payload") or {}
    tool = payload.get("tool") or "tool"
    op = payload.get("op") or ""
    label = f"{tool} {op}".strip() if op not in ("", "*") else tool
    lines = [f"Approval needed: {label}"]
    outside = payload.get("outside_policy") or []
    if outside:
        lines.append("  Outside the write policy:")
        for o in outside[:20]:
            reason = f" ({o.get('reason')})" if o.get("reason") else ""
            lines.append(f"    {o.get('path')}{reason}")
    preflight = payload.get("preflight") or {}
    diff = preflight.get("diff") if isinstance(preflight, dict) else None
    if isinstance(diff, str) and diff.strip():
        dl = diff.rstrip("\n").split("\n")
        lines.extend(dl[:DIFF_LINES_SHOWN])
        if len(dl) > DIFF_LINES_SHOWN:
            lines.append(f"  … {len(dl) - DIFF_LINES_SHOWN} more diff lines")
    else:
        args = payload.get("args") or {}
        if args:
            blob = json.dumps(args, default=str, indent=1)
            if len(blob) > _ARGS_CAP:
                blob = blob[:_ARGS_CAP] + " …"
            lines.append("  Arguments:")
            lines.extend("    " + ln for ln in blob.split("\n"))
    return lines


def _paint(line: str) -> str:
    if line.startswith("+") and not line.startswith("+++"):
        return _c("32", line)
    if line.startswith("-") and not line.startswith("---"):
        return _c("31", line)
    if line.startswith("@@"):
        return _c("36", line)
    return line


def answer_from_terminal(
    event: Dict[str, Any],
    *,
    input_fn: Optional[Callable[[str], str]] = None,
    isatty: Optional[Callable[[], bool]] = None,
    out: Callable[[str], None] = print,
    ledger: Any = None,
) -> Dict[str, Any]:
    """Prompt for one ``consent_opened`` event and record the answer.

    Returns ``{"decision", "scope", "recorded"}``.  Never raises: a failure
    to record still returns, and the gate keeps waiting (the user can ^C).
    """
    if input_fn is None:
        from app.utils.interruptible_input import interruptible_input as input_fn
    if isatty is None:
        isatty = lambda: bool(getattr(sys.stdin, "isatty", lambda: False)())  # noqa: E731
    if ledger is None:
        from app.utils.consent_gate import ledger as _ledger
        ledger = _ledger()

    request_id = event.get("request_id")
    scopes = list(event.get("scopes") or ["once"])
    for ln in describe(event):
        out(_paint(ln) if not ln.startswith("Approval needed") else _c("33", "⏸ " + ln))

    decision, scope, note = "reject", "once", ""
    if not isatty():
        note = "no terminal to ask for approval (stdin is not a TTY)"
        out(_c("33", f"  Refused: {note}."))
    else:
        offer_conv = "conversation" in scopes
        prompt = ("  Allow? [y]es once / [a]lways in this conversation / [n]o: "
                  if offer_conv else "  Allow? [y]es once / [n]o: ")
        try:
            reply = (input_fn(prompt) or "").strip().lower()
        except (KeyboardInterrupt, EOFError):
            reply = ""
            note = "cancelled at the terminal"
        if reply in ("y", "yes"):
            decision = "approve"
        elif offer_conv and reply in ("a", "always"):
            decision, scope = "approve", "conversation"
        elif not note:
            note = "declined at the terminal"

    recorded = False
    if request_id:
        try:
            recorded = ledger.record_answer(
                request_id, decision, scope=scope,
                answer=note, answered_by=ANSWERED_BY,
            ) is not None
        except Exception as e:  # noqa: BLE001 -- display path must not crash the stream
            out(_c("31", f"  Could not record the answer: {e}"))
    return {"decision": decision, "scope": scope, "recorded": recorded}
