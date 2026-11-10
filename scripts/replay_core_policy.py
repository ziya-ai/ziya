"""Paired A/B/C of core-tier selection policies, on a lifecycle-sim arm.

replay_memory_use.py showed the always-injected tier is degenerate: the
2000-char budget is filled with priority layers in STORE ORDER, so the same
8-9 negative_constraints go to every turn.  This replays each later chat with
three selection policies over the SAME available set, scores each policy's
injected set against the chat's real assistant replies with the feedback.py
oracle, and reports them side by side -- a paired design, unlike the
extraction arms.

  A  current: priority layers (preference, negative_constraint, lexicon)
     first, store order; fill with the rest by importance.  _select_core_memories.
  B  priority layers ranked by importance desc, then retrieval_used_count
     desc; rest by importance.  In a sandbox every memory has importance
     0.5 and use 0, so B == A there; it is reported to put that on record.
  C  relevance: cosine of each available memory to the chat's human turns
     (max over the first MAX_HUMAN_TURNS turns), fill the budget by score,
     any layer except `personal`.

Outputs in <arm>/: core_policy.jsonl (one row per chat, policy, memory),
core_policy_summary.md.  --judge N: blind Opus yes/partial/no on N
used@0.60 hits per policy, to check the oracle is not favouring C merely
because C selects by the same embedding it is scored with.

Usage:
    python scripts/replay_core_policy.py /tmp/ex_armA [--judge 20]
"""
import argparse
import asyncio
import importlib.util
import json
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("ZIYA_USER_CODEBASE_DIR", str(_ROOT))
os.environ.setdefault("ZIYA_LOAD_INTERNAL_PLUGINS", "1")

POLICIES = ("A_store_order", "B_ranked_priority", "C_relevance")
THRESHOLDS = (0.55, 0.60, 0.70)


def _load_replay_module():
    spec = importlib.util.spec_from_file_location(
        "replay_memory_use", _ROOT / "scripts" / "replay_memory_use.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _fill(ordered, budget: int) -> list:
    """Greedy fill in the given order, same cost model as _select_core_memories."""
    out = []
    for m in ordered:
        cost = len(m.content) + 20
        if budget >= cost:
            out.append(m)
            budget -= cost
    return out


def policy_b(memories, budget: int, priority_layers, excluded) -> list:
    pri = [m for m in memories if m.layer in priority_layers]
    rest = [m for m in memories if m.layer not in priority_layers and m.layer not in excluded]
    pri.sort(key=lambda m: (m.importance, getattr(m, "retrieval_used_count", 0) or 0), reverse=True)
    rest.sort(key=lambda m: m.importance, reverse=True)
    return _fill(pri + rest, budget)


def policy_c(memories, query_vecs: List[np.ndarray], vec_of, budget: int, excluded) -> list:
    """Rank by max cosine to any query vector; unembedded memories are skipped."""
    scored = []
    for m in memories:
        if m.layer in excluded:
            continue
        v = vec_of(m.id)
        if v is None or not query_vecs:
            continue
        scored.append((max(float(np.dot(v, q)) for q in query_vecs), m))
    scored.sort(key=lambda t: t[0], reverse=True)
    return _fill([m for _, m in scored], budget)


def summarize(rows: List[Dict[str, Any]]) -> str:
    by_pol = defaultdict(list)
    for r in rows:
        by_pol[r["policy"]].append(r)
    lines = ["# Core-tier selection policy A/B/C (paired)", "",
             "| policy | injected (chat×memory) | distinct memories | " +
             " | ".join(f"used@{t}" for t in THRESHOLDS) + " | chats with ≥1 used@0.60 |",
             "|" + "---|" * (4 + len(THRESHOLDS))]
    chats = sorted({r["chat_id"] for r in rows})
    for p in POLICIES:
        rs = by_pol.get(p, [])
        n = len(rs) or 1
        cells = [f"{sum(1 for r in rs if r['best_cos'] >= t)} ({100*sum(1 for r in rs if r['best_cos'] >= t)/n:.1f}%)"
                 for t in THRESHOLDS]
        hit_chats = len({r["chat_id"] for r in rs if r["best_cos"] >= 0.60})
        lines.append(f"| {p} | {len(rs)} | {len({r['memory_id'] for r in rs})} | "
                     + " | ".join(cells) + f" | {hit_chats}/{len(chats)} |")
    # Paired per-chat comparison, A vs C on used@0.60 count.
    per = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r["best_cos"] >= 0.60:
            per[r["chat_id"]][r["policy"]] += 1
    wins = Counter()
    for c in chats:
        a, cc = per[c]["A_store_order"], per[c]["C_relevance"]
        wins["C>A" if cc > a else "A>C" if a > cc else "tie"] += 1
    lines += ["", f"Paired per chat, used@0.60 count, C vs A: {dict(wins)}",
              "", "## C: layers of injected memories", ""]
    lay = Counter(r["layer"] for r in by_pol.get("C_relevance", []))
    lines.append(", ".join(f"{k} {v}" for k, v in lay.most_common()))
    same_ab = all({r["memory_id"] for r in by_pol["A_store_order"] if r["chat_id"] == c}
                  == {r["memory_id"] for r in by_pol["B_ranked_priority"] if r["chat_id"] == c}
                  for c in chats)
    lines += ["", f"B selected the identical set to A on every chat: {same_ab} "
              "(expected in a sandbox: all importance 0.5, all use counts 0)."]
    return "\n".join(lines)


async def judge(rows, by_id, windows_by_chat, n: int, out: Path, system: str, seed: int = 11):
    from app.services.model_resolver import call_service_model
    rng = random.Random(seed)
    items = []
    for p in POLICIES:
        pool = [r for r in rows if r["policy"] == p and r["best_cos"] >= 0.60]
        rng.shuffle(pool)
        for r in pool[:n]:
            items.append({"id": f"{p[0]}{len(items):03d}", "policy": p, "memory_id": r["memory_id"],
                          "chat_id": r["chat_id"], "best_cos": r["best_cos"],
                          "memory": by_id[r["memory_id"]].content,
                          "excerpt": windows_by_chat[r["chat_id"]][r["best_window"]]})
    verdicts = {}
    for i in range(0, len(items), 8):
        chunk = items[i:i + 8]
        msg = "\n\n".join(f"ITEM {b['id']}\nMEMORY: {b['memory']}\nEXCERPT: {b['excerpt']}" for b in chunk)
        try:
            raw = await call_service_model(category="memory_eval", system_prompt=system,
                                           user_message=msg, max_tokens=800, temperature=0.0)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            for v in json.loads(raw):
                if isinstance(v, dict):
                    verdicts[v.get("id")] = str(v.get("verdict", "")).lower().strip()
        except Exception as e:  # noqa: BLE001
            print(f"  judge batch {i // 8 + 1}: FAILED {e!r}", file=sys.stderr)
    with out.open("w") as f:
        for it in items:
            f.write(json.dumps({**it, "verdict": verdicts.get(it["id"], "unparsed")}) + "\n")
    lines = ["", "## Blind Opus on used@0.60 hits, per policy", "",
             "| policy | n | yes | partial | no |", "|---|---|---|---|---|"]
    for p in POLICIES:
        its = [it for it in items if it["policy"] == p]
        c = Counter(verdicts.get(it["id"], "unparsed") for it in its)
        lines.append(f"| {p} | {len(its)} | {c['yes']} | {c['partial']} | {c['no']} |")
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("arm_dir")
    ap.add_argument("--judge", type=int, default=0, metavar="N")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    arm = Path(args.arm_dir).expanduser()
    sandbox = arm / "sandbox"
    rp = _load_replay_module()

    from app.plugins import initialize
    initialize()
    from app.storage.memory import MemoryStorage
    from app.services.embedding_service import EmbeddingCache, get_embedding_provider, NoopProvider
    from app.memory.prompt import (_select_core_memories, CORE_TOKEN_BUDGET_CHARS,
                                   CORE_PRIORITY_LAYERS, EXCLUDED_FROM_CORE_LAYERS)
    from app.memory.feedback import _windowize, _cosine
    from app.memory.eval import iter_random_conversations

    provider = get_embedding_provider()
    if isinstance(provider, NoopProvider):
        print("embeddings disabled", file=sys.stderr)
        return 1
    store = MemoryStorage(memory_dir=sandbox)
    cache = EmbeddingCache(memory_dir=sandbox)
    memories = store.list_memories(status="active")
    by_id = {m.id: m for m in memories}
    later_ids = [t["chat_id"] for t in json.loads((arm / "phase2_extraction.json").read_text())
                 if not (isinstance(t.get("result"), dict) and
                         (t["result"].get("skipped") or "error" in t["result"]))]
    chats = {c.chat_id: c for c in iter_random_conversations(sample_size=1_000_000,
                                                               seed=args.seed, long_quota=0)}
    created = {cid: c.created_at for cid, c in chats.items()}
    print(f"{len(memories)} active memories; {len(later_ids)} later chats")

    rows: List[Dict[str, Any]] = []
    windows_by_chat: Dict[str, List[str]] = {}
    for n, cid in enumerate(later_ids, 1):
        chat = chats.get(cid)
        if chat is None or not chat.created_at:
            continue
        avail = rp.available_memories(memories, cid, chat.created_at, created)
        reply = rp._assistant_text(chat)
        turns = rp._human_turns(chat)
        if not avail or not reply.strip() or not turns:
            continue
        qvecs = [v for v in (provider.embed_text(t) for t in turns) if v is not None]
        sel = {
            "A_store_order": _select_core_memories(avail)[0],
            "B_ranked_priority": policy_b(avail, CORE_TOKEN_BUDGET_CHARS, CORE_PRIORITY_LAYERS,
                                          EXCLUDED_FROM_CORE_LAYERS),
            "C_relevance": policy_c(avail, qvecs, cache.get, CORE_TOKEN_BUDGET_CHARS,
                                    EXCLUDED_FROM_CORE_LAYERS),
        }
        windows = _windowize(reply)
        windows_by_chat[cid] = windows
        wvecs = [(w, v) for w, v in ((w, provider.embed_text(w)) for w in windows) if v is not None]
        if not wvecs:
            continue
        line = []
        for pol, mems in sel.items():
            used = 0
            for m in mems:
                vec = cache.get(m.id)
                if vec is None:
                    continue
                scores = [_cosine(vec, v) for _, v in wvecs]
                bi = max(range(len(scores)), key=scores.__getitem__)
                rows.append({"chat_id": cid, "policy": pol, "memory_id": m.id, "layer": m.layer,
                             "best_cos": round(scores[bi], 4), "best_window": bi,
                             "available": len(avail)})
                used += scores[bi] >= 0.60
            line.append(f"{pol[0]}={len(mems)}/{used}")
        print(f"[{n}/{len(later_ids)}] {cid[:8]} avail={len(avail)} " + " ".join(line), flush=True)

    (arm / "core_policy.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    card = summarize(rows)
    if args.judge and rows:
        card += "\n" + await judge(rows, by_id, windows_by_chat, args.judge,
                                   arm / "core_policy_judge.jsonl", rp.CALIBRATION_SYSTEM)
    (arm / "core_policy_summary.md").write_text(card + "\n")
    print("\n" + card)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
