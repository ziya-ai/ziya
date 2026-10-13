"""Iteration attempts (design/typed-findings.md, step 6f).

A gather contract with ``rerun_missing: 1`` re-runs a short iteration once.
The re-run is recorded as a second ``IterationSummary`` for the same index
with ``attempt: 2`` (a missing ``attempt`` means 1), and its artifact is
written to its own file, so the first attempt stays on record.

Every reader that asks "what was iteration N's result" must take the
HIGHEST attempt for that index, or a resume would bank a failed first
attempt whose re-run passed (or count both as work).  This is that rule,
in one place.  Identity is ``(pass_key, index)``: ``index`` is unique only
within one pass of an enclosing loop.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple


def _get(s: Any, key: str, default: Any = None) -> Any:
    if isinstance(s, dict):
        return s.get(key, default)
    return getattr(s, key, default)


def attempt_of(summary: Any) -> int:
    try:
        return int(_get(summary, "attempt", 1) or 1)
    except (TypeError, ValueError):
        return 1


def latest_attempts(summaries: Iterable[Any]) -> List[Any]:
    """One summary per ``(pass_key, index)``: the highest attempt.

    Ties keep the later record.  Order is that of each kept record's
    first appearance, so callers that relied on list order are unchanged
    when there are no re-runs.
    """
    best: Dict[Tuple[Any, Any], Any] = {}
    order: List[Tuple[Any, Any]] = []
    for s in summaries or []:
        key = (_get(s, "pass_key"), _get(s, "index"))
        if key not in best:
            order.append(key)
            best[key] = s
        elif attempt_of(s) >= attempt_of(best[key]):
            best[key] = s
    return [best[k] for k in order]
