"""Blind judge + scorecard for comparator A/B arms of the lifecycle simulation.

Reads ``phase2_extraction.json`` from each arm directory (written by
``run_memory_lifecycle_simulation.py`` with the full-text trace) and, for
every comparator decision that had at least one similar existing memory,
asks the ``memory_eval`` model to classify the RELATIONSHIP between the
candidate and its nearest existing memory.  The judge never sees the
action the comparator took, nor which arm it is scoring.

Relationship labels:
  same_claim_restated   same fact, no new information
  same_claim_corrected  same fact, candidate fixes a wrong value/mechanism
  same_claim_refined    same fact, candidate more precise, nothing lost
  different_claim       different fact about the same topic/system
  unrelated             different subject

Correctness is derived per action:
  UPDATE  ok if corrected|refined; DATA_LOSS if different|unrelated; pointless if restated
  ADD     ok if different|unrelated; MISSED_DEDUP if restated; MISSED_UPDATE if corrected|refined
  NOOP    ok if restated; DATA_LOSS otherwise

Verdicts append to ``<arm>/comparator_verdicts.jsonl`` (resumable); the
scorecard prints per arm and is written to ``<first arm>/../comparator_ab_scorecard.md``.

Usage:
    python scripts/judge_comparator_ab.py /tmp/ab_armA /tmp/ab_armB [--batch 8]
"""
import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("ZIYA_USER_CODEBASE_DIR", str(_ROOT))
os.environ.setdefault("ZIYA_LOAD_INTERNAL_PLUGINS", "1")

LABELS = ("same_claim_restated", "same_claim_corrected", "same_claim_refined",
          "different_claim", "unrelated")

SYSTEM = """You audit an automatic memory store for an engineering assistant.
Each item shows an EXISTING stored memory and a NEW candidate memory that was
compared against it.  Classify their relationship (pick exactly one):

- same_claim_restated: both assert the same specific fact; NEW adds nothing.
- same_claim_corrected: same specific fact; NEW gives a different value, mechanism
  or conclusion for it (one of them must be wrong).
- same_claim_refined: same specific fact; NEW is more precise or complete and
  preserves everything EXISTING said.
- different_claim: a different fact about the same system, component, bug or
  topic.  A mechanism description, a symptom, a root cause, a decision, a
  proposal and a constraint about one component are DIFFERENT claims.  So is a
  broader statement that drops specifics EXISTING stated.
- unrelated: different subject entirely.

Be strict: same topic is not same claim.  Output ONLY a JSON array in input
order: [{"id": "...", "relation": "<label>", "note": "<one short sentence>"}]"""


def _derive(action, relation):
    if action == "UPDATE":
        if relation in ("same_claim_corrected", "same_claim_refined"):
            return "ok"
        return "pointless" if relation == "same_claim_restated" else "DATA_LOSS"
    if action == "ADD":
        if relation in ("different_claim", "unrelated"):
            return "ok"
        return "MISSED_DEDUP" if relation == "same_claim_restated" else "MISSED_UPDATE"
    if action == "NOOP":
        return "ok" if relation == "same_claim_restated" else "DATA_LOSS"
    return "unknown"


def _items(arm_dir: Path):
    traces = json.loads((arm_dir / "phase2_extraction.json").read_text())
    out = []
    for t in traces:
        for i, d in enumerate(t.get("comparator_decisions", [])):
            sims = d.get("similar") or []
            if not sims or not d.get("candidate_content"):
                continue
            # UPDATE: judge against the memory actually replaced; otherwise the
            # nearest (highest cosine, else first) existing memory.
            target = None
            if d.get("action") == "UPDATE" and d.get("target_id"):
                target = next((s for s in sims if s.get("id") == d["target_id"]), None)
            if target is None:
                target = max(sims, key=lambda s: (s.get("similarity") or -1))
            out.append({
                "id": f"{t.get('chat_id', '?')[:8]}:{i}",
                "action": d.get("action"),
                "candidate_layer": d.get("candidate_layer"),
                "existing_layer": target.get("layer"),
                "similarity": target.get("similarity"),
                "existing": target.get("content", ""),
                "candidate": d["candidate_content"],
            })
    return out


def _parse(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


async def judge_arm(arm_dir: Path, batch: int):
    from app.services.model_resolver import call_service_model
    out = arm_dir / "comparator_verdicts.jsonl"
    done = {}
    if out.exists():
        for l in out.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                done[r["id"]] = r
    items = _items(arm_dir)
    todo = [it for it in items if it["id"] not in done]
    print(f"[{arm_dir.name}] {len(items)} decisions with similar; {len(todo)} to judge")
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        msg = "\n\n".join(
            f"ITEM {b['id']}\nEXISTING ({b['existing_layer']}): {b['existing']}\n"
            f"NEW ({b['candidate_layer']}): {b['candidate']}" for b in chunk)
        try:
            raw = await call_service_model(category="memory_eval", system_prompt=SYSTEM,
                                           user_message=msg, max_tokens=2500, temperature=0.0)
            by = {v.get("id"): v for v in _parse(raw) if isinstance(v, dict)}
        except Exception as e:  # noqa: BLE001
            print(f"  batch {i // batch + 1}: FAILED {e!r}", file=sys.stderr)
            continue
        with out.open("a") as f:
            for b in chunk:
                v = by.get(b["id"])
                if not v:
                    continue
                rel = str(v.get("relation", "")).strip().lower()
                rec = {**b, "relation": rel if rel in LABELS else "unparsed",
                       "note": v.get("note", "")}
                rec["outcome"] = _derive(rec["action"], rec["relation"])
                done[rec["id"]] = rec
                f.write(json.dumps(rec) + "\n")
        print(f"  batch {i // batch + 1}/{(len(todo) + batch - 1) // batch} ok", flush=True)
    return list(done.values())


def scorecard(arms):
    lines = ["# Comparator A/B scorecard", ""]
    hdr = "| arm | decisions w/ similar | UPDATE | UPDATE data-loss | ADD | ADD missed-dedup | ADD missed-update | NOOP | NOOP data-loss | total data-loss | total missed-merge |"
    lines += [hdr, "|" + "---|" * (hdr.count("|") - 1)]
    for name, recs in arms.items():
        by_act = Counter(r["action"] for r in recs)
        oc = Counter((r["action"], r["outcome"]) for r in recs)
        dl = oc[("UPDATE", "DATA_LOSS")] + oc[("NOOP", "DATA_LOSS")]
        mm = oc[("ADD", "MISSED_DEDUP")] + oc[("ADD", "MISSED_UPDATE")]
        n = len(recs) or 1
        lines.append(
            f"| {name} | {len(recs)} | {by_act['UPDATE']} | {oc[('UPDATE','DATA_LOSS')]} "
            f"| {by_act['ADD']} | {oc[('ADD','MISSED_DEDUP')]} | {oc[('ADD','MISSED_UPDATE')]} "
            f"| {by_act['NOOP']} | {oc[('NOOP','DATA_LOSS')]} "
            f"| {dl} ({100*dl/n:.1f}%) | {mm} ({100*mm/n:.1f}%) |")
    lines += ["", "data-loss = a distinct fact was overwritten (UPDATE) or discarded (NOOP).",
              "missed-merge = a same-claim candidate was kept as a separate ADD (near-duplicate, recoverable).",
              "Arms are unpaired: extraction is re-run per arm, so candidate sets differ slightly."]
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("arms", nargs="+")
    ap.add_argument("--batch", type=int, default=8)
    args = ap.parse_args()
    from app.plugins import initialize
    initialize()
    results = {}
    for a in args.arms:
        d = Path(a).expanduser()
        results[d.name] = await judge_arm(d, args.batch)
    card = scorecard(results)
    print("\n" + card)
    out = Path(args.arms[0]).expanduser().parent / "comparator_ab_scorecard.md"
    out.write_text(card + "\n")
    print(f"\nscorecard → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
