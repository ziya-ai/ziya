"""
Reverse lookup: which run holds a given card, when that card ran as a callee.

A Call block runs its target *inline* in the caller's run, so a six-card
stack (CL0 calling CL1..CL6) produces exactly ONE run record, owned by
CL0.  ``TaskRunStorage.list(card_id=...)`` filters on ``run.card_id``, so
asking it about CL1 returns nothing at all — CL1 has no runs of its own,
and opening CL1 in the deck therefore showed no sign that it was the card
currently holding a study.

Nothing new needs persisting to fix that.  Two facts already on disk make
the lookup exact:

  1. ``run.call_snapshots[<call block id>]`` records the resolved callee
     as ``{"key": "card:<card_id>", "root": <the callee's block tree>}``
     (``block_executor._record_call_audit``).

  2. That tree is ``card.root`` **verbatim** — ``task_call._resolve_card``
     returns the stored card's own root, and ``_assign_block_ids`` is
     fill-only for an existing card, so the callee's blocks carry the SAME
     ids the callee's own card carries.  Verified rather than assumed: a
     held ``held_at_block_id`` of a callee block is found in that callee's
     own id set.

Fact 2 is what makes this worth doing at all.  Because the ids match, a
hold recorded against a callee block can be resolved against the callee's
*own* tree — so opening CL1 directly can show which of CL1's blocks is
held, and which of CL1's blocks are blocked behind it, using the same
``holdChain`` derivation the caller's run map uses.  Without the id
identity the caller's ``held_at_block_id`` would be meaningless in the
callee's frame and the feature would require per-callee run records.

Pure and storage-agnostic: takes already-loaded runs, returns plain data.
The API layer owns the I/O, this owns the matching rule, so a change to
the key format has one place to change rather than three.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

# Statuses worth reporting back to a callee's own view.  A held run is the
# motivating case; 'running' is included because "CL1 is executing right
# now inside a study" is the same question asked at a different moment,
# and answering only when something broke would make the surface appear
# only on failure — teaching users it is an error indicator rather than a
# location indicator.
INTERESTING_STATUSES = ("held", "running", "paused")


def callee_key(card_id: str) -> str:
    """The ``call_snapshots`` key format for a card callee.

    Mirrors ``task_call._resolve_card``'s ``key=f"card:{card.id}"``.  Kept
    here so the one place that constructs it and the one place that
    matches it cannot drift apart.
    """
    return f"card:{card_id}"


def _tree_block_ids(node: Optional[Dict[str, Any]]) -> List[str]:
    """Every block id at or below a raw (dict) block tree.

    Iterative, and guards against a cycle in a hand-edited or truncated
    run file: the server rejects call cycles at execution time, but a
    corrupt record must not hang a lookup that runs on every deck open.
    """
    if not node:
        return []
    out: List[str] = []
    seen: set[int] = set()
    stack: List[Dict[str, Any]] = [node]
    while stack:
        cur = stack.pop()
        if not isinstance(cur, dict) or id(cur) in seen:
            continue
        seen.add(id(cur))
        bid = cur.get("id")
        if bid:
            out.append(bid)
        for child in cur.get("body") or []:
            stack.append(child)
    return out


def find_callee_holds(
    runs: Iterable[Any],
    card_id: str,
    *,
    statuses: Sequence[str] = INTERESTING_STATUSES,
) -> List[Dict[str, Any]]:
    """Runs that invoked ``card_id`` as a callee and are worth surfacing.

    ``runs`` are already-loaded ``TaskRun`` objects (or anything exposing
    the same attributes) — this function does no I/O.

    Each result describes the callee's position in a caller's run:

      run_id, run_status      the CALLER's run and its state
      caller_card_id          who owns that run
      call_block_id           the Call block inside the caller
      callee_root             the callee's block tree as recorded
      held_at_block_id        the held block, or None
      held_in_callee          True when that block is inside THIS callee's
                              subtree — the discriminator that keeps a
                              hold in CL2 from being reported on CL1's
                              page, which would be worse than showing
                              nothing because it points at the wrong card
      held_reason / held_faults / held_gate_reason
                              passed through for the surface to render

    Runs where the same card was called from several Call blocks yield one
    entry per call site: they are genuinely distinct invocations with
    distinct positions, and collapsing them would hide one.
    """
    wanted_key = callee_key(card_id)
    allowed = set(statuses) if statuses else None
    out: List[Dict[str, Any]] = []
    for run in runs:
        status = getattr(run, "status", None)
        if allowed is not None and status not in allowed:
            continue
        snapshots = getattr(run, "call_snapshots", None) or {}
        if not isinstance(snapshots, dict):
            continue
        for call_block_id, snap in snapshots.items():
            if not isinstance(snap, dict):
                continue
            if snap.get("key") != wanted_key:
                continue
            callee_root = snap.get("root")
            held_block = getattr(run, "held_at_block_id", None)
            # A hold anywhere in the run is reported, but only flagged as
            # THIS callee's when the block is actually in its subtree.
            # ``held_in_callee`` is what the surface should gate its
            # per-block markers on; the rest is context.
            held_in_callee = bool(
                held_block and held_block in set(_tree_block_ids(callee_root))
            )
            out.append({
                "run_id": getattr(run, "id", ""),
                "run_status": status,
                "caller_card_id": getattr(run, "card_id", ""),
                "call_block_id": call_block_id,
                "callee_target": snap.get("target"),
                "callee_root": callee_root,
                "held_at_block_id": held_block,
                "held_in_callee": held_in_callee,
                "held_reason": getattr(run, "held_reason", None),
                "held_faults": getattr(run, "held_faults", None),
                "held_gate_reason": getattr(run, "held_gate_reason", None),
                "updated_at": getattr(run, "updated_at", 0) or 0,
            })
    # Most recent first, and a hold ahead of a merely-running peer at the
    # same instant: when a card appears in two studies the broken one is
    # the one the user opened the card to find out about.
    out.sort(
        key=lambda e: (e["run_status"] == "held", e["updated_at"]),
        reverse=True,
    )
    return out


def primary_callee_hold(
    runs: Iterable[Any], card_id: str,
) -> Optional[Dict[str, Any]]:
    """The single most relevant callee context for a card, or None.

    Prefers a run whose hold is inside THIS callee — the case where the
    callee's own tree can be marked up — over a run that is merely held
    somewhere else, which can only be reported as context.
    """
    hits = find_callee_holds(runs, card_id)
    if not hits:
        return None
    for hit in hits:
        if hit["held_in_callee"]:
            return hit
    return hits[0]
