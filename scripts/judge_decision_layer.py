"""Blind judge + scorecard for extraction-prompt A/B arms (decision layer).

Reads ``phase1_extraction.json`` and ``phase2_extraction.json`` from each arm
directory (written by ``run_memory_lifecycle_simulation.py`` with the
full-candidate window trace) and asks the ``memory_eval`` model to classify
EVERY candidate's form, without seeing the layer the extractor assigned or
which arm produced it:

  decision_with_rationale  names a choice AND a rejected alternative or reason
  decision_no_rationale    names a choice but no alternative/reason
  fact                     describes how something is / works (no choice)
  task_status              reports work in flight, an issue, a TODO
  constraint_or_rule       a rule, limit, or thing-not-to-do
  other

Scorecard per arm:
  decision precision   decision_with_rationale / candidates labelled decision
  decision recall      labelled decision / judged decision_with_rationale
  misfiled decisions   judged decision_with_rationale but labelled elsewhere
  per-layer counts     so arm B cannot win by extracting less overall

Verdicts append to ``<arm>/decision_verdicts.jsonl`` (resumable); scorecard to
``<first arm>/../decision_layer_scorecard.md``.

Usage:
    python scripts/judge_decision_layer.py /tmp/ex_armA /tmp/ex_armB [--batch 12]
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("ZIYA_USER_CODEBASE_DIR", str(_ROOT))
os.environ.setdefault("ZIYA_LOAD_INTERNAL_PLUGINS", "1")

FORMS = ("decision_with_rationale", "decision_no_rationale", "fact",
         "task_status", "constraint_or_rule", "other")

SYSTEM = """You audit candidate memories extracted from engineering conversations.
For each item classify the FORM of the statement (pick exactly one):

- decision_with_rationale: states a choice that was made AND names at least one
  rejected alternative OR the specific reason for the choice.
- decision_no_rationale: states that something was chosen/decided/adopted but
  gives no alternative and no reason.
- fact: describes how a system, component, format or process IS or WORKS.
  No choice is asserted, even if the described design was obviously chosen by
  someone at some point.
- task_status: reports work in progress, an open issue, a tracked bug, a TODO,
  or what was done in a session.
- constraint_or_rule: a rule, limit, prohibition, or approach-not-to-use.
- other: none of the above (opinion, preference, vocabulary, personal).

"X uses Y" is a fact, not a decision. "X uses Y rather than Z" or "X uses Y
because W" is a decision_with_rationale. Output ONLY a JSON array in input
order: [{"id": "...", "form": "<label>"}]"""


def _items(arm_dir: Path):
    out = []
    for phase in ("phase1_extraction.json", "phase2_extraction.json"):
        p = arm_dir / phase
        if not p.exists():
            continue
        for t in json.loads(p.read_text()):
            for wi, w in enumerate(t.get("windows", [])):
                for ci, c in enumerate(w.get("candidates", [])):
                    content = (c.get("content") or "").strip()
                    if not content:
                        continue
                    h = hashlib.sha1(content.encode()).hexdigest()[:10]
                    out.append({
                        "id": f"{phase[5]}:{str(t.get('chat_id', '?'))[:8]}:{wi}:{ci}:{h}",
                        "phase": phase[:6],
                        "layer": c.get("layer"),
                        "content": content,
                        "confidence": c.get("confidence"),
                        "durability": c.get("durability"),
                    })
    return out


def _parse(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


async def judge_arm(arm_dir: Path, batch: int):
    from app.services.model_resolver import call_service_model
    out = arm_dir / "decision_verdicts.jsonl"
    done = {}
    if out.exists():
        for l in out.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                done[r["id"]] = r
    items = _items(arm_dir)
    todo = [it for it in items if it["id"] not in done]
    print(f"[{arm_dir.name}] {len(items)} candidates; {len(todo)} to judge")
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        # Layer deliberately omitted from what the judge sees.
        msg = "\n\n".join(f"ITEM {b['id']}\n{b['content']}" for b in chunk)
        try:
            raw = await call_service_model(category="memory_eval", system_prompt=SYSTEM,
                                           user_message=msg, max_tokens=2000, temperature=0.0)
            by = {v.get("id"): v for v in _parse(raw) if isinstance(v, dict)}
        except Exception as e:  # noqa: BLE001
            print(f"  batch {i // batch + 1}: FAILED {e!r}", file=sys.stderr)
            continue
        with out.open("a") as f:
            for b in chunk:
                v = by.get(b["id"])
                if not v:
                    continue
                form = str(v.get("form", "")).strip().lower()
                rec = {**b, "form": form if form in FORMS else "unparsed"}
                done[rec["id"]] = rec
                f.write(json.dumps(rec) + "\n")
        print(f"  batch {i // batch + 1}/{(len(todo) + batch - 1) // batch} ok", flush=True)
    return list(done.values())


def scorecard(arms):
    lines = ["# Decision-layer extraction A/B scorecard", ""]
    lines += ["| arm | candidates | labelled decision | ...with rationale (precision) "
              "| ...no rationale | ...fact | ...task_status | judged rationale-decisions total "
              "| misfiled (judged decision, other label) | decision recall |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for name, recs in arms.items():
        dec = [r for r in recs if r["layer"] == "decision"]
        f = Counter(r["form"] for r in dec)
        all_dwr = [r for r in recs if r["form"] == "decision_with_rationale"]
        misfiled = [r for r in all_dwr if r["layer"] != "decision"]
        n_dec = len(dec) or 1
        prec = f["decision_with_rationale"] / n_dec
        rec_ = f["decision_with_rationale"] / (len(all_dwr) or 1)
        lines.append(
            f"| {name} | {len(recs)} | {len(dec)} | {f['decision_with_rationale']} ({100*prec:.0f}%) "
            f"| {f['decision_no_rationale']} | {f['fact']} | {f['task_status']} "
            f"| {len(all_dwr)} | {len(misfiled)} | {100*rec_:.0f}% |")
    lines += ["", "## Candidates per layer (arm B must not win by extracting less)", ""]
    layers = sorted({r["layer"] for recs in arms.values() for r in recs}, key=str)
    lines.append("| arm | " + " | ".join(str(l) for l in layers) + " |")
    lines.append("|" + "---|" * (len(layers) + 1))
    for name, recs in arms.items():
        c = Counter(r["layer"] for r in recs)
        lines.append(f"| {name} | " + " | ".join(str(c[l]) for l in layers) + " |")
    lines += ["", "## Where judged-decisions landed, by labelled layer", ""]
    for name, recs in arms.items():
        c = Counter(r["layer"] for r in recs if r["form"] == "decision_with_rationale")
        lines.append(f"- {name}: {dict(c)}")
    lines += ["", "## Where labelled-decisions were judged, by form", ""]
    for name, recs in arms.items():
        c = Counter(r["form"] for r in recs if r["layer"] == "decision")
        lines.append(f"- {name}: {dict(c)}")
    lines += ["", "Arms are unpaired: extraction is re-run per arm, so candidate sets differ."]
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("arms", nargs="+")
    ap.add_argument("--batch", type=int, default=12)
    args = ap.parse_args()
    from app.plugins import initialize
    initialize()
    results = {}
    for a in args.arms:
        d = Path(a).expanduser()
        results[d.name] = await judge_arm(d, args.batch)
    card = scorecard(results)
    print("\n" + card)
    out = Path(args.arms[0]).expanduser().parent / "decision_layer_scorecard.md"
    out.write_text(card + "\n")
    print(f"\nscorecard → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
