#!/usr/bin/env python3
"""Tier A: a dated snapshot of the memory store's shape.  Zero model calls.

Appends one JSON line per run to .ziya/outputs/memory_audit.jsonl so the
series can be queried with ``analyze`` (dataset_register the file) and a
policy change can be read as a before/after on the same fields:

  * active memories by layer / learned_from
  * promoted-proposal corroboration histogram and ATTRIBUTION RATE — the
    share of corroboration bumps credited to a named conversation.  Measured
    0.8% on 2026-10-24: the active-memory counter was bumped by the
    originating conversation re-extracting itself, so the number was a turn
    counter, not evidence.  The distinct-conversation guard (ProposalsStore
    has it; the memory-side bump sites do not yet) should drive this toward
    100%.
  * proposal log: open / promoted / archived, promoted by reason and layer,
    archived by reason, open by layer (which layers are decaying out)
  * live-use proxy: promoted proposals that ever earned a response_match
    signal (a loaded memory showed up in a reply)

Must be run with the plugins initialised (encryption provider) and NOT from
/tmp (a stray /tmp/attr.py shadows the attrs package there).

    python3 scripts/memory_audit_snapshot.py            # append a snapshot
    python3 scripts/memory_audit_snapshot.py --print    # also pretty-print
"""
from __future__ import annotations

import collections
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_PRINT = "--print" in sys.argv
sys.argv = ["memory_audit_snapshot"]

OUT = ROOT / ".ziya" / "outputs" / "memory_audit.jsonl"


def snapshot() -> dict:
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    from app.storage.memory import get_memory_storage
    from app.storage.proposals import get_proposals_store

    mems = get_memory_storage().list_memories()
    promoted = [m for m in mems if m.learned_from == "promoted_from_proposal"]
    bumps = sum(m.corroborations or 0 for m in promoted)
    attributed = sum(len(set(m.corroborated_by or [])) for m in promoted)
    hist = collections.Counter(min(m.corroborations or 0, 10) for m in promoted)

    rows = get_proposals_store().list_all()
    by_status = collections.Counter(r.get("status") for r in rows)
    prom = [r for r in rows if r.get("status") == "promoted"]
    arch = [r for r in rows if r.get("status") == "archived"]
    opn = [r for r in rows if r.get("status") == "open"]

    def has_signal(r, name):
        sig = r.get("signals") or {}
        if isinstance(sig, dict):
            return bool(sig.get(name))
        return any((s.get("name") == name) for s in sig if isinstance(s, dict))

    # Live-use is read from the MEMORY store, not from promoted proposal rows:
    # prune_terminal keeps only the newest 500 terminal rows, so the promoted
    # rows old enough to have earned a response_match are exactly the ones it
    # deletes.  The first two snapshots read 11 -> 2 off proposal rows while
    # nothing about use had changed.
    used = sum(1 for r in prom if has_signal(r, "response_match"))  # kept for continuity; pruned source
    used_mem_loaded = sum(1 for m in promoted if (getattr(m, "retrieval_loaded_count", 0) or 0) > 0)
    used_mem_importance = sum(1 for m in promoted if (m.importance or 0) > 0.5)
    prom_corr_zero = sum(1 for r in prom if (r.get("corroborations") or 0) == 0)
    # Cross-process race evidence: ProposalsStore._append is read-decrypt-rewrite
    # under an in-process lock only.  Two processes proposing the same hash
    # before seeing each other leave two `record` events for one id.
    events = get_proposals_store()._read_lines()
    rec_ids = [e["id"] for e in events if e.get("kind") == "record"]
    duplicate_record_events = len(rec_ids) - len(set(rec_ids))

    return {
        "ts": int(time.time() * 1000),
        "git_head": os.popen("git -C %s rev-parse --short HEAD" % ROOT).read().strip(),
        "memories_total": len(mems),
        "memories_by_layer": dict(collections.Counter(m.layer for m in mems)),
        "memories_by_learned_from": dict(collections.Counter(m.learned_from for m in mems)),
        "promoted_memories": len(promoted),
        "promoted_corroboration_hist_capped10": {str(k): v for k, v in sorted(hist.items())},
        "corroboration_bumps_total": bumps,
        "corroboration_bumps_attributed": attributed,
        "corroboration_attribution_rate": round(attributed / bumps, 4) if bumps else None,
        "proposals_total": len(rows),
        "proposals_by_status": dict(by_status),
        "promoted_by_layer": dict(collections.Counter(r.get("layer") for r in prom)),
        "promoted_with_zero_corroborations": prom_corr_zero,
        "promoted_ever_used_response_match": used,
        "promoted_memories_ever_loaded": used_mem_loaded,
        "promoted_memories_importance_above_default": used_mem_importance,
        "duplicate_record_events": duplicate_record_events,
        "archived_by_reason": dict(collections.Counter(r.get("archive_reason") for r in arch)),
        "open_by_layer": dict(collections.Counter(r.get("layer") for r in opn)),
    }


def main() -> int:
    snap = snapshot()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "a") as f:
        f.write(json.dumps(snap) + "\n")
    if _PRINT:
        print(json.dumps(snap, indent=1))
    else:
        print(f"snapshot appended to {OUT}: memories={snap['memories_total']} "
              f"attribution={snap['corroboration_attribution_rate']} "
              f"open={snap['proposals_by_status'].get('open')} "
              f"used={snap['promoted_ever_used_response_match']}/{len([1])*snap['proposals_by_status'].get('promoted',0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
