"""Blind judge for comparator UPDATE events.

Reads ``updates.jsonl`` from a memory dir (a lifecycle-simulation sandbox, or
the live ``~/.ziya/memory``), fetches the superseded (archived) row and the
live row from that store, and asks the ``memory_eval`` service model to
classify each UPDATE without seeing any metadata:

  correction             new text corrects a fact the old text got wrong
  refinement             same fact, new text is more precise / complete
  paraphrase             same fact, no new information (should have been NOOP)
  distinct_facts_merged  old and new state DIFFERENT facts; the old one is lost
  unrelated              old and new are about different subjects

``distinct_facts_merged`` + ``unrelated`` is the data-loss rate of the
comparator's UPDATE path.  Verdicts append to ``update_verdicts.jsonl``
beside the log; re-runs skip judged events.

Usage:
    python scripts/judge_memory_updates.py <memory_dir> [--batch 8]
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("ZIYA_USER_CODEBASE_DIR", str(_ROOT))
os.environ.setdefault("ZIYA_LOAD_INTERNAL_PLUGINS", "1")

VERDICTS = ("correction", "refinement", "paraphrase", "distinct_facts_merged", "unrelated")

SYSTEM = """You audit an automatic memory store for an engineering assistant.
Each item shows a stored memory's OLD text and the NEW text that replaced it.
Classify the replacement (pick exactly one):

- correction: NEW corrects something OLD stated wrongly about the same fact.
- refinement: same fact; NEW is more precise, more complete, or better worded, and loses nothing.
- paraphrase: same fact, same information; NEW adds nothing.
- distinct_facts_merged: OLD and NEW state different facts about a related subject.
  A reader who needed OLD can no longer find it. (This is the costly case; call it
  whenever OLD contains any claim NEW does not preserve.)
- unrelated: OLD and NEW are about different subjects entirely.

Output ONLY a JSON array in input order:
[{"event_id": "...", "verdict": "<category>", "lost": "<claim from OLD that NEW drops, or empty>"}]"""


def _parse(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("memory_dir")
    ap.add_argument("--batch", type=int, default=8)
    args = ap.parse_args()

    from app.plugins import initialize
    initialize()
    from app.storage.memory import MemoryStorage
    from app.memory.extractor import UPDATE_LOG_FILENAME
    from app.services.model_resolver import call_service_model

    mem_dir = Path(args.memory_dir).expanduser()
    log = mem_dir / UPDATE_LOG_FILENAME
    out = mem_dir / "update_verdicts.jsonl"
    if not log.exists():
        print(f"no {log}", file=sys.stderr)
        return 1
    events = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    done = set()
    if out.exists():
        done = {json.loads(l)["event_id"] for l in out.read_text().splitlines() if l.strip()}

    store = MemoryStorage(memory_dir=mem_dir)
    by_id = {r.get("id"): r for r in store._load_memories()}

    items = []
    for ev in events:
        eid = f"{ev['memory_id']}:{ev['superseded_id']}"
        if eid in done:
            continue
        old, new = by_id.get(ev["superseded_id"]), by_id.get(ev["memory_id"])
        if not old or not new:
            print(f"skip {eid}: row missing", file=sys.stderr)
            continue
        items.append({"event_id": eid, "old": old["content"], "new": new["content"],
                      "old_layer": ev.get("old_layer"), "candidate_layer": ev.get("candidate_layer"),
                      "top_similarity": ev.get("top_similarity")})
    print(f"{len(items)} UPDATE events to judge ({len(done)} already done)")

    for i in range(0, len(items), args.batch):
        batch = items[i:i + args.batch]
        msg = "\n\n".join(f"EVENT {b['event_id']}\nOLD: {b['old']}\nNEW: {b['new']}" for b in batch)
        try:
            raw = await call_service_model(category="memory_eval", system_prompt=SYSTEM,
                                           user_message=msg, max_tokens=2500, temperature=0.0)
            verdicts = {v.get("event_id"): v for v in _parse(raw) if isinstance(v, dict)}
        except Exception as e:  # noqa: BLE001
            print(f"batch {i}: FAILED {e!r}", file=sys.stderr)
            continue
        with out.open("a") as f:
            for b in batch:
                v = verdicts.get(b["event_id"])
                if not v:
                    continue
                verdict = str(v.get("verdict", "")).lower().strip()
                f.write(json.dumps({
                    "event_id": b["event_id"],
                    "verdict": verdict if verdict in VERDICTS else "unparsed",
                    "lost": v.get("lost", ""), "old_layer": b["old_layer"],
                    "candidate_layer": b["candidate_layer"], "top_similarity": b["top_similarity"],
                }) + "\n")
        print(f"batch {i // args.batch + 1}/{(len(items) + args.batch - 1) // args.batch} ok", flush=True)
    print(f"verdicts → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
