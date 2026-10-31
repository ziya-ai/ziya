"""
Cost of one conversation, including the Task Card runs anchored in it.

The Info tab asked the ledger for ``conversation_id = <chat id>`` and got
only the chat's own turns: task runs wrote their rows with
``conversation_id = run_id`` (the executor's stand-in id), so a
conversation whose only spend was a run read "no usage recorded".

Two joins close that gap, and both are used so old and new runs agree:

* Rows written since the fix carry the anchoring conversation's id (the
  executor puts the run's ``source_conversation_id`` into the usage
  attribution; see app/cost/meter.py), so a plain conversation filter now
  finds them.  They are told apart from chat turns by ``source == "task"``.
* The chat's TaskBinding file names every run launched from it, including
  runs whose rows predate the fix.  Each bound run is rolled up by
  ``run_id`` regardless of what its rows say for conversation.

Totals are summed from the chat group and the per-run groups, never from a
second conversation-wide query, so a run found by both joins is counted
once.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.utils.logging_utils import logger

_SUM_KEYS = ("records", "estimate_records", "actual_records", "priced_records",
             "unknown_records", "partial_records")


def sum_groups(groups: Iterable[Dict[str, Any]], key: str = "__sum__") -> Dict[str, Any]:
    """Add rollup groups together with the same null-vs-zero rules the
    ledger uses: cost is None only when NO group could be priced."""
    out: Dict[str, Any] = {k: 0 for k in _SUM_KEYS}
    out.update({"key": key, "dims": {}, "estimate_dims": {}, "actual_dims": {},
                "provenance": {}, "unpriced_dims": set(), "first_ts": None, "last_ts": None})
    eff: Optional[float] = None
    lst: Optional[float] = None
    for g in groups:
        for k in _SUM_KEYS:
            out[k] += int(g.get(k, 0) or 0)
        for dk in ("dims", "estimate_dims", "actual_dims"):
            for dim, n in (g.get(dk) or {}).items():
                out[dk][dim] = out[dk].get(dim, 0) + int(n or 0)
        for p, n in (g.get("provenance") or {}).items():
            out["provenance"][p] = out["provenance"].get(p, 0) + int(n or 0)
        out["unpriced_dims"].update(g.get("unpriced_dims") or [])
        if g.get("effective_cost") is not None:
            eff = (eff or 0.0) + float(g["effective_cost"])
        if g.get("list_cost") is not None:
            lst = (lst or 0.0) + float(g["list_cost"])
        for bound, pick in (("first_ts", min), ("last_ts", max)):
            v = g.get(bound)
            if v is not None:
                out[bound] = v if out[bound] is None else pick(out[bound], v)
    out["effective_cost"] = round(eff, 6) if eff is not None else None
    out["list_cost"] = round(lst, 6) if lst is not None else None
    out["unpriced_dims"] = sorted(out["unpriced_dims"])
    out["has_estimates"] = out["estimate_records"] > 0
    out["is_partial"] = out["partial_records"] > 0 or out["unknown_records"] > 0
    return out


def _bound_run_ids(project_dir: Optional[Path], conversation_id: str) -> List[str]:
    if not project_dir:
        return []
    try:
        from app.storage.task_bindings import TaskBindingStorage
        ids = [b.run_id for b in TaskBindingStorage(project_dir).list_for_chat(conversation_id)
               if b.run_id]
    except Exception as e:  # noqa: BLE001 - a missing bindings file is not an error
        logger.debug("conversation_cost: bindings unavailable: %s", e)
        return []
    seen: List[str] = []
    for rid in ids:
        if rid not in seen:
            seen.append(rid)
    return seen


def _run_facts(project_dir: Optional[Path], run_id: str) -> Dict[str, Any]:
    """Status, card name and launch ordinal for one run; empty on failure."""
    facts: Dict[str, Any] = {"run_id": run_id}
    if not project_dir:
        return facts
    try:
        from app.storage.task_runs import TaskRunStorage
        storage = TaskRunStorage(project_dir)
        run = storage.get(run_id)
        if not run:
            facts["missing"] = True
            return facts
        facts.update({
            "card_id": run.card_id, "status": run.status, "attempt": run.attempt,
            "started_at": run.started_at, "completed_at": run.completed_at,
            "wall_ms": (int((run.completed_at or 0) - run.started_at)
                        if run.started_at and run.completed_at else None),
        })
        # The Nth launch of this card, counting attempt lineages once, so
        # the Info tab can say "run #3" instead of showing a hex id.
        roots: List[Any] = []
        for r in sorted(storage.list(card_id=run.card_id), key=lambda r: (r.created_at, r.id)):
            root = r.root_run_id or r.id
            if root not in roots:
                roots.append(root)
        mine = run.root_run_id or run.id
        facts["ordinal"] = roots.index(mine) + 1 if mine in roots else None
        try:
            from app.storage.task_cards import TaskCardStorage
            card = TaskCardStorage(project_dir).get(run.card_id)
            snap = run.card_snapshot or {}
            facts["card_name"] = (card.name if card else None) or snap.get("name") or run.card_id
        except Exception:  # noqa: BLE001
            facts["card_name"] = (run.card_snapshot or {}).get("name") or run.card_id
    except Exception as e:  # noqa: BLE001
        logger.debug("conversation_cost: run facts failed for %s: %s", run_id, e)
    return facts


def conversation_cost(conversation_id: str, *, project_dir: Optional[Path] = None,
                      ledger: Any = None) -> Dict[str, Any]:
    """``{conversation_id, chat, runs: [...], totals}`` — see module doc.

    ``chat`` is the rollup of this conversation's own turns (every source
    but ``task``); each ``runs`` entry is one Task Card run with its facts
    and a ``cost`` group; ``totals`` is the sum of chat and runs.
    """
    if ledger is None:
        from app.storage.usage_ledger import get_ledger
        ledger = get_ledger()

    by_source = ledger.rollup(by="source", conversation_id=conversation_id)
    chat = sum_groups([g for g in by_source.get("groups", []) if g["key"] != "task"], key="chat")

    run_ids: List[str] = []
    # Runs whose rows name this conversation (post-fix), then bound runs
    # (covers pre-fix rows).  Order: first seen.
    for rec in ledger.query(conversation_id=conversation_id, source="task"):
        if rec.run_id and rec.run_id not in run_ids:
            run_ids.append(rec.run_id)
    for rid in _bound_run_ids(project_dir, conversation_id):
        if rid not in run_ids:
            run_ids.append(rid)

    runs: List[Dict[str, Any]] = []
    for rid in run_ids:
        facts = _run_facts(project_dir, rid)
        facts["cost"] = ledger.rollup(by="status", run_id=rid).get("totals")
        runs.append(facts)
    runs.sort(key=lambda r: (r.get("started_at") or 0, r["run_id"]))

    totals = sum_groups([chat] + [r["cost"] for r in runs if r.get("cost")], key="__total__")
    return {
        "conversation_id": conversation_id,
        "chat": chat,
        "runs": runs,
        "totals": totals,
        "catalog_version": by_source.get("catalog_version"),
        "plan": by_source.get("plan"),
    }
