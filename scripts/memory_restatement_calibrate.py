#!/usr/bin/env python3
"""Calibrate the restatement signal against a temporal null.  Zero API calls.

The first scoring pass used the extractor's paraphrase threshold (0.88) and
found a restatement rate of exactly 0.0 for every proposal status.  Its
positive control failed: the ORIGIN conversation, which contains the fact by
construction, averaged cos 0.44-0.51 against the proposal text.  A distilled
one-sentence fact never reaches dedup-level similarity with a raw 1,500-char
user message, so an absolute threshold is the wrong instrument.

This re-scores from the cached embeddings with a paired null per proposal:

  after  = max cos over FOREIGN conversations whose chunk ts > proposed_at
  before = max cos over FOREIGN conversations whose chunk ts < proposed_at

A fact cannot be restated before it was proposed, so `before` is the
best-case similarity that chance plus shared vocabulary produce for THIS
proposal in THIS corpus.  The lift (after - before), and the share of
proposals with after > before + margin, is the restatement signal, grouped
by status.  Also reports where `self` sits, as the attainable ceiling.

Writes .ziya/outputs/memory_restatement_calibrated.jsonl (one row per
proposal) and prints the by-status summary.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.argv = ["memory_restatement_calibrate"]
STATE_DIR = ROOT / ".ziya" / "memory_restatement"
OUT = ROOT / ".ziya" / "outputs" / "memory_restatement_calibrated.jsonl"
MARGIN = 0.05


def main() -> int:
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()
    from app.storage.proposals import get_proposals_store
    from scripts.memory_restatement import load_emb, _h  # noqa: E402

    emb = load_emb()
    chunks = [json.loads(l) for l in (STATE_DIR / "chunks.jsonl").read_text().splitlines() if l.strip()]
    chunks = [c for c in chunks if c["h"] in emb]
    M = np.stack([emb[c["h"]] for c in chunks]).astype(np.float32)
    M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    convs = np.array([c["conv"] for c in chunks])
    tss = np.array([c["ts"] for c in chunks], dtype=np.int64)

    rows_out = []
    agg = collections.defaultdict(lambda: {"n": 0, "lift": [], "after": [], "before": [], "self": [], "pos": 0})
    for r in get_proposals_store().list_all():
        v = emb.get("prop:" + _h(r.get("content") or ""))
        if v is None:
            continue
        v = v / (np.linalg.norm(v) + 1e-9)
        sims = M @ v
        origin = r.get("conversation_id")
        t = int(r.get("proposed_at") or 0)
        foreign = convs != origin
        a = foreign & (tss > t)
        b = foreign & (tss < t)
        if not a.any() or not b.any():
            continue
        after = float(sims[a].max())
        # Size-matched null.  The unmatched version read before=0.52 vs
        # after=0.30: every proposal is post-August (the log was reset), so
        # "before" searched eight months of chunks and "after" a few weeks,
        # and a max over more draws is larger.  Subsample `before` to the
        # `after` count so the two maxima are comparable.
        na = int(a.sum()); bidx = np.flatnonzero(b)
        rng = np.random.default_rng(int(t) & 0xFFFFFFFF)
        draws = [float(sims[rng.choice(bidx, size=min(na, len(bidx)), replace=False)].max()) for _ in range(5)]
        before = float(np.mean(draws))
        selfc = float(sims[convs == origin].max()) if (convs == origin).any() else None
        key = r.get("archive_reason") if r.get("status") == "archived" else r.get("status")
        key = key if key in ("open", "promoted", "decayed") else "other"
        row = {"proposal_id": r.get("id"), "status": key, "layer": r.get("layer"),
               "after": round(after, 4), "before": round(before, 4), "lift": round(after - before, 4),
               "self": round(selfc, 4) if selfc is not None else None,
               "restated": after > before + MARGIN, "content": (r.get("content") or "")[:140]}
        rows_out.append(row)
        g = agg[key]; g["n"] += 1; g["lift"].append(row["lift"]); g["after"].append(after); g["before"].append(before)
        if selfc is not None: g["self"].append(selfc)
        g["pos"] += int(row["restated"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(o) + "\n" for o in rows_out))
    print(f"{'status':<10}{'n':>5}{'mean_before':>13}{'mean_after':>12}{'mean_lift':>11}{'p(after>before+.05)':>21}{'mean_self':>11}")
    for k in ("promoted", "open", "decayed", "other"):
        g = agg.get(k)
        if not g or not g["n"]:
            continue
        print(f"{k:<10}{g['n']:>5}{np.mean(g['before']):>13.3f}{np.mean(g['after']):>12.3f}"
              f"{np.mean(g['lift']):>11.3f}{g['pos']/g['n']:>21.3f}{(np.mean(g['self']) if g['self'] else float('nan')):>11.3f}")
    # Sign test across all: is after > before more often than chance?
    lifts = np.array([o["lift"] for o in rows_out])
    print(f"\nall n={len(lifts)}  after>before: {np.mean(lifts > 0):.3f}  median lift: {np.median(lifts):+.3f}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
