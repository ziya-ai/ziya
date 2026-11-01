#!/usr/bin/env python3
"""
Runner for the memory-extraction eval (app/memory/eval.py).

The harness had no entry point, which is why its verdict data was written
once and never refreshed: reproducing a run meant reverse-engineering
plugin initialisation and the ALE key path by hand.

Two things this handles that a bare `python -c` does not:

  1. ``initialize_plugins()`` BEFORE any store access.  The encryption
     provider that derives the KEK is registered by the plugin layer, so
     without it every read of an encrypted chat/memory fails with
     "Cannot decrypt an encrypted file".  Same uid, same key file — the
     only missing step is plugin registration.
  2. Resumable, incrementally-written output.  A full sweep is dozens of
     live model calls and will outlive most shell timeouts; results are
     flushed per conversation so an interrupted run keeps its work and
     ``--resume`` skips what is already done.

Usage:
  # A/B the extraction model against the recorded Opus verdicts
  python3 scripts/memory_eval_ab.py ab --limit 10 \
      --model-a us.anthropic.claude-haiku-4-5-20251001-v1:0 \
      --model-b us.anthropic.claude-sonnet-4-6

  # Re-grade with Opus (expensive; writes to the real verdicts file)
  python3 scripts/memory_eval_ab.py grade --limit 5

  # Just show what the recorded verdicts already say
  python3 scripts/memory_eval_ab.py summary
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

VERDICTS = Path.home() / ".ziya" / "memory-eval" / "verdicts.jsonl"
DEFAULT_AB_OUT = Path.home() / ".ziya" / "memory-eval" / "ab_results.json"


def _init():
    """Plugin init must precede any encrypted-store read.  See module docstring."""
    from app.plugins import initialize as initialize_plugins
    initialize_plugins()


def load_verdicts() -> dict:
    """Latest verdict per chat_id.  The file is append-only, so a chat
    re-evaluated later has several rows; only the last one is current."""
    latest = {}
    if not VERDICTS.exists():
        return latest
    with open(VERDICTS) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                latest[r["chat_id"]] = r
            except Exception:
                continue
    return latest


def load_graded_chats(limit=None, min_msgs=0):
    """Load the chats that already have Opus verdicts, largest first.

    Pairing against recorded verdicts is what makes this an A/B rather
    than two unrelated samples — the ground truth is fixed.
    """
    from app.utils.encryption import is_encrypted, get_encryptor
    verdicts = load_verdicts()
    files = {
        p.stem: p
        for p in (Path.home() / ".ziya" / "projects").glob("*/chats/*.json")
        if not p.name.startswith("_") and not p.name.endswith(".bindings.json")
    }
    enc = get_encryptor()
    out = []
    for cid, v in verdicts.items():
        p = files.get(cid)
        if not p:
            continue
        try:
            raw = p.read_bytes()
            if is_encrypted(raw):
                raw = enc.decrypt(raw)
            data = json.loads(raw)
        except Exception as e:
            print(f"  skip {cid[:8]}: {type(e).__name__}", flush=True)
            continue
        msgs = data.get("messages") or []
        if len(msgs) < min_msgs:
            continue
        out.append({
            "chat_id": cid,
            "title": data.get("title") or v.get("title") or "",
            "messages": msgs,
            "verdict": v,
        })
    out.sort(key=lambda c: -len(c["messages"]))
    return out[:limit] if limit else out


async def extract_with(model_id, messages):
    """Run the production windowed-extraction path with one model pinned.

    Mirrors run_post_conversation_extraction's window loop but never
    writes to a store.  ZIYA_MEMORY_EXTRACTION_MODEL is read per call by
    the resolver (no caching), so setting it here is sufficient.
    """
    from app.memory.extractor import (
        _count_salience_hits, _split_into_topic_windows,
        strip_conversation, extract_memories, quality_gate,
    )
    os.environ["ZIYA_MEMORY_EXTRACTION_MODEL"] = model_id

    windows = _split_into_topic_windows(messages)
    eligible = 0
    cands = []
    for w in windows:
        if _count_salience_hits(w) == 0:
            continue
        ws = strip_conversation(w)
        if len(ws) < 200:
            continue
        eligible += 1
        # Empty dedup context: each model must be judged on the same
        # input.  Threading accumulated candidates makes run B's prompt
        # depend on run A's output.
        cands.extend(await extract_memories(ws, []))
    gated = quality_gate(list(cands))
    return {
        "windows_total": len(windows),
        "windows_eligible": eligible,
        "raw": len(cands),
        "gated": len(gated),
        "contents": [c.get("content", "") for c in gated],
        "layers": Counter(c.get("layer", "?") for c in gated),
    }


async def cmd_ab(args):
    _init()
    chats = load_graded_chats(limit=args.limit, min_msgs=args.min_msgs)
    print(f"paired A/B over {len(chats)} graded conversations", flush=True)

    out_path = Path(args.out)
    results = []
    done = set()
    if args.resume and out_path.exists():
        try:
            results = json.loads(out_path.read_text())
            done = {r["chat_id"] for r in results}
            print(f"  resuming: {len(done)} already done", flush=True)
        except Exception:
            results, done = [], set()

    for i, c in enumerate(chats, 1):
        if c["chat_id"] in done:
            continue
        gold = len(c["verdict"].get("missed") or [])
        print(f"[{i}/{len(chats)}] msgs={len(c['messages'])} gold_missed={gold} "
              f":: {c['title'][:44]}", flush=True)
        row = {"chat_id": c["chat_id"], "title": c["title"],
               "msgs": len(c["messages"]), "gold_missed": gold}
        for label, model in (("a", args.model_a), ("b", args.model_b)):
            try:
                r = await extract_with(model, c["messages"])
            except Exception as e:
                print(f"    {label} FAILED: {type(e).__name__}: {e}", flush=True)
                row[label] = None
                continue
            row[label] = {k: (dict(v) if isinstance(v, Counter) else v)
                          for k, v in r.items()}
            print(f"    {label}: windows={r['windows_eligible']}/{r['windows_total']} "
                  f"raw={r['raw']} gated={r['gated']}", flush=True)
        results.append(row)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=1))

    _report_ab(results, args.model_a, args.model_b)
    print(f"\nwrote {out_path}", flush=True)


def _report_ab(results, model_a, model_b):
    ok = [r for r in results if r.get("a") and r.get("b")]
    if not ok:
        print("\nno complete pairs to report")
        return
    print(f"\n=== A/B totals over {len(ok)} conversations ===")
    for label, model in (("a", model_a), ("b", model_b)):
        raw = sum(r[label]["raw"] for r in ok)
        gated = sum(r[label]["gated"] for r in ok)
        lay = Counter()
        for r in ok:
            lay.update(r[label].get("layers") or {})
        print(f"  {model}")
        print(f"    raw={raw}  post-quality-gate={gated}")
        print(f"    layers: {dict(lay.most_common(6))}")
    print(f"  gold facts Opus found in these: "
          f"{sum(r['gold_missed'] for r in ok)}")


async def cmd_grade(args):
    """Full Opus grading pass — appends to the real verdicts file."""
    _init()
    from app.memory.eval import evaluate_conversation, append_verdict, ChatRecord
    chats = load_graded_chats(limit=args.limit, min_msgs=args.min_msgs)
    print(f"grading {len(chats)} conversations with Opus", flush=True)
    for i, c in enumerate(chats, 1):
        print(f"[{i}/{len(chats)}] {c['title'][:50]}", flush=True)
        rec = ChatRecord(project_id="", chat_id=c["chat_id"],
                         title=c["title"], messages=c["messages"], file_size=0)
        try:
            out = await evaluate_conversation(rec)
            append_verdict(out)
            print(f"    extracted={out.pipeline_extracted_count} "
                  f"proposed={out.pipeline_proposed_count} "
                  f"missed={len(out.missed or [])}", flush=True)
        except Exception as e:
            print(f"    FAILED: {type(e).__name__}: {e}", flush=True)


def cmd_summary(args):
    """Read-only report over recorded verdicts.  No model calls."""
    u = load_verdicts()
    if not u:
        print(f"no verdicts at {VERDICTS}")
        return
    rows = list(u.values())
    sk = [r for r in rows if r.get("pipeline_skipped_reason")]
    ran = [r for r in rows if not r.get("pipeline_skipped_reason")]
    missed_ran = sum(len(r.get("missed") or []) for r in ran)
    extracted = sum(int(r.get("pipeline_extracted_count") or 0) for r in ran)
    print(f"=== recorded verdicts: {len(rows)} unique conversations ===")
    print(f"  skipped (no salience): {len(sk)}  "
          f"(gold facts lost there: {sum(len(r.get('missed') or []) for r in sk)})")
    print(f"  extraction ran:        {len(ran)}")
    if missed_ran + extracted:
        print(f"    missed={missed_ran} extracted={extracted} "
              f"miss_rate={100*missed_ran/(missed_ran+extracted):.1f}%")
    ratings = Counter()
    viol = Counter()
    for r in rows:
        for c in (r.get("candidates") or []):
            ratings[c.get("rating")] += 1
            if c.get("gate_violation"):
                viol[c["gate_violation"]] += 1
    tot = sum(ratings.values())
    if tot:
        junk = ratings[1] + ratings[2]
        good = ratings[4] + ratings[5]
        print(f"  candidates graded={tot}  "
              f"rated1-2={junk} ({100*junk/tot:.0f}%)  "
              f"rated4-5={good} ({100*good/tot:.0f}%)")
        print(f"  gate violations: {dict(viol.most_common())}")
    lay = Counter()
    for r in rows:
        for m in (r.get("missed") or []):
            lay[m.get("suggested_layer")] += 1
    if lay:
        print(f"  MISSED gold by layer: {dict(lay.most_common())}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ab", help="A/B two extraction models on graded chats")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--min-msgs", type=int, default=0)
    p.add_argument("--model-a", default="us.anthropic.claude-haiku-4-5-20251001-v1:0")
    p.add_argument("--model-b", default="us.anthropic.claude-sonnet-4-6")
    p.add_argument("--out", default=str(DEFAULT_AB_OUT))
    p.add_argument("--resume", action="store_true")

    g = sub.add_parser("grade", help="full Opus grading pass (expensive)")
    g.add_argument("--limit", type=int, default=3)
    g.add_argument("--min-msgs", type=int, default=0)

    sub.add_parser("summary", help="report recorded verdicts (no model calls)")

    args = ap.parse_args()
    if args.cmd == "summary":
        cmd_summary(args)
    elif args.cmd == "ab":
        asyncio.run(cmd_ab(args))
    elif args.cmd == "grade":
        asyncio.run(cmd_grade(args))


if __name__ == "__main__":
    main()
