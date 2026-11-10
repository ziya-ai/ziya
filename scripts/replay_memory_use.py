"""Downstream-use replay for a lifecycle-simulation arm.

The form-judged A/Bs (comparator, decision layer) say whether a memory is
well formed; nothing so far says whether memories in an arm's store get USED
by the conversations that follow.  This replays that, offline, against the
real later-phase chats and their real assistant replies:

  for each later chat C (chronological sim, see run_memory_lifecycle_simulation.py):
    available = active sandbox memories whose SOURCE chat predates C
                (never C itself) -- what a live store would have held then
    core      = _select_core_memories(available)      the always-injected tier
    search    = store.search(each human turn) ∩ available, top-k per turn
                                                       what memory_search could surface
    score each loaded memory against C's assistant replies with the same
    sliding-window max-cosine oracle apply_feedback uses (feedback.py), at
    several thresholds, and record best_cos per (chat, memory)

Outputs in <arm>/:
  use_replay.jsonl           one row per (chat, memory, kind)
  use_replay_summary.md      use rates by kind, layer, threshold
  use_calibration.jsonl      (--calibrate N) blind Opus verdicts on a
                             cosine-stratified sample: did the memory plausibly
                             inform the matched reply excerpt?  Calibrates the
                             0.55 threshold before any arm comparison is trusted.

Caveat stated up front: in a sandbox every memory has importance 0.5, so the
core tier's non-priority fill is store order, i.e. a near-random sample under
the 2000-char budget.  The core numbers are a baseline for "inject N memories
blind"; the search numbers are the retrievability ceiling.

Usage:
    python scripts/replay_memory_use.py /tmp/ex_armA [--search-k 3] [--calibrate 40]
"""
import argparse
import asyncio
import json
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("ZIYA_USER_CODEBASE_DIR", str(_ROOT))
os.environ.setdefault("ZIYA_LOAD_INTERNAL_PLUGINS", "1")

THRESHOLDS = (0.45, 0.50, 0.55, 0.60, 0.70)
MAX_HUMAN_TURNS_SEARCHED = 6
QUERY_CHARS = 300

CALIBRATION_SYSTEM = """You audit whether a stored memory informed an assistant reply.
Each item shows a MEMORY and an EXCERPT of the assistant's reply from a later
conversation.  Judge whether the memory's specific content is reflected in the
excerpt (pick one):
- yes: the excerpt states, applies or relies on the memory's specific fact/rule.
- partial: same specific subject, but the excerpt does not use the memory's claim.
- no: different subject, or only generic topical overlap.
Output ONLY a JSON array in input order: [{"id": "...", "verdict": "<yes|partial|no>"}]"""


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _assistant_text(chat) -> str:
    return "\n".join(_text(m.get("content")) for m in chat.messages
                     if m.get("role") in ("assistant", "ai"))


def _human_turns(chat) -> List[str]:
    out = [_text(m.get("content")).strip() for m in chat.messages
           if m.get("role") in ("human", "user")]
    return [t[:QUERY_CHARS] for t in out if t][:MAX_HUMAN_TURNS_SEARCHED]


def available_memories(memories, chat_id: str, chat_created: int,
                       created_by_chat: Dict[str, int]) -> list:
    """Memories a live store would have held when ``chat_id`` happened: those
    whose source chat is dated strictly before it, excluding its own."""
    out = []
    for m in memories:
        src = m.learned_from_conversation
        if not src or src == chat_id:
            continue
        src_created = created_by_chat.get(src)
        if src_created is None or src_created >= chat_created:
            continue
        out.append(m)
    return out


def score_loaded(loaded_ids, cache, window_vecs, cosine) -> Dict[str, float]:
    """best_cos per memory id; ids without a cached vector are omitted."""
    out = {}
    for mid in loaded_ids:
        vec = cache.get(mid)
        if vec is None:
            continue
        out[mid] = max((cosine(vec, wv) for wv in window_vecs), default=0.0)
    return out


def summarize(rows: List[Dict[str, Any]]) -> str:
    lines = ["# Downstream-use replay", ""]
    by_kind = defaultdict(list)
    for r in rows:
        for k in r["kinds"]:
            by_kind[k].append(r)
    lines.append("| kind | loaded (chat×memory) | distinct memories | " +
                 " | ".join(f"used@{t}" for t in THRESHOLDS) + " |")
    lines.append("|" + "---|" * (3 + len(THRESHOLDS)))
    for k in ("core", "search"):
        rs = by_kind.get(k, [])
        n = len(rs) or 1
        cells = [f"{sum(1 for r in rs if r['best_cos'] >= t)} ({100*sum(1 for r in rs if r['best_cos'] >= t)/n:.1f}%)"
                 for t in THRESHOLDS]
        lines.append(f"| {k} | {len(rs)} | {len({r['memory_id'] for r in rs})} | " + " | ".join(cells) + " |")
    lines += ["", "## Use@0.55 by layer (core ∪ search, distinct chat×memory)", "",
              "| layer | loaded | used | rate |", "|---|---|---|---|"]
    by_layer = defaultdict(list)
    for r in rows:
        by_layer[r["layer"]].append(r)
    for layer, rs in sorted(by_layer.items(), key=lambda x: -len(x[1])):
        u = sum(1 for r in rs if r["best_cos"] >= 0.55)
        lines.append(f"| {layer} | {len(rs)} | {u} | {100*u/len(rs):.1f}% |")
    chats = {r["chat_id"] for r in rows}
    ever = {r["memory_id"] for r in rows if r["best_cos"] >= 0.55}
    lines += ["", f"Chats replayed: {len(chats)}.  Distinct memories ever used@0.55: {len(ever)} "
              f"of {len({r['memory_id'] for r in rows})} ever loaded.",
              "Core fill in a sandbox is store order (all importance 0.5): treat core as a blind-injection baseline."]
    return "\n".join(lines)


async def calibrate(rows, memories_by_id, windows_by_chat, n: int, out: Path, seed: int = 7):
    """Blind Opus verdicts on a cosine-stratified sample of loaded memories."""
    from app.services.model_resolver import call_service_model
    rng = random.Random(seed)
    bins = [(0.40, 0.50), (0.50, 0.55), (0.55, 0.60), (0.60, 0.70), (0.70, 1.01)]
    per_bin = max(1, n // len(bins))
    sample = []
    for lo, hi in bins:
        pool = [r for r in rows if lo <= r["best_cos"] < hi]
        rng.shuffle(pool)
        sample.extend(pool[:per_bin])
    items = []
    for i, r in enumerate(sample):
        items.append({"id": f"c{i:03d}", "memory_id": r["memory_id"], "chat_id": r["chat_id"],
                      "best_cos": r["best_cos"], "layer": r["layer"],
                      "memory": memories_by_id[r["memory_id"]].content,
                      "excerpt": windows_by_chat[r["chat_id"]][r["best_window"]]})
    verdicts = {}
    for i in range(0, len(items), 8):
        chunk = items[i:i + 8]
        msg = "\n\n".join(f"ITEM {b['id']}\nMEMORY: {b['memory']}\nEXCERPT: {b['excerpt']}" for b in chunk)
        try:
            raw = await call_service_model(category="memory_eval", system_prompt=CALIBRATION_SYSTEM,
                                           user_message=msg, max_tokens=800, temperature=0.0)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            for v in json.loads(raw):
                if isinstance(v, dict):
                    verdicts[v.get("id")] = str(v.get("verdict", "")).lower().strip()
        except Exception as e:  # noqa: BLE001
            print(f"  calibration batch {i // 8 + 1}: FAILED {e!r}", file=sys.stderr)
    with out.open("w") as f:
        for it in items:
            f.write(json.dumps({**it, "verdict": verdicts.get(it["id"], "unparsed")}) + "\n")
    lines = ["", "## Oracle calibration (blind Opus, cosine bins)", "",
             "| cos bin | n | yes | partial | no |", "|---|---|---|---|---|"]
    for lo, hi in bins:
        its = [it for it in items if lo <= it["best_cos"] < hi]
        c = Counter(verdicts.get(it["id"], "unparsed") for it in its)
        lines.append(f"| [{lo:.2f},{hi:.2f}) | {len(its)} | {c['yes']} | {c['partial']} | {c['no']} |")
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("arm_dir")
    ap.add_argument("--search-k", type=int, default=3)
    ap.add_argument("--calibrate", type=int, default=0, metavar="N")
    ap.add_argument("--seed", type=int, default=42, help="must match the sim's --seed")
    args = ap.parse_args()
    arm = Path(args.arm_dir).expanduser()
    sandbox = arm / "sandbox"

    from app.plugins import initialize
    initialize()
    from app.storage.memory import MemoryStorage
    from app.services.embedding_service import EmbeddingCache, get_embedding_provider, NoopProvider
    from app.memory.prompt import _select_core_memories
    from app.memory.feedback import _windowize, _cosine
    from app.memory.eval import iter_random_conversations

    provider = get_embedding_provider()
    if isinstance(provider, NoopProvider):
        print("embeddings disabled; nothing to score", file=sys.stderr)
        return 1
    store = MemoryStorage(memory_dir=sandbox)
    cache = EmbeddingCache(memory_dir=sandbox)
    # store.search resolves the embedding cache through the module-level
    # getter, i.e. the LIVE cache, which holds none of the sandbox ids: the
    # semantic arm then reports "cache cold" and silently degrades to the
    # keyword fallback (first run, 2026-11-09).  Point it at the sandbox.
    import app.services.embedding_service as _es
    _es.get_embedding_cache = lambda: cache
    memories = store.list_memories(status="active")
    by_id = {m.id: m for m in memories}

    later_ids = [t["chat_id"] for t in json.loads((arm / "phase2_extraction.json").read_text())
                 if not (isinstance(t.get("result"), dict) and
                         (t["result"].get("skipped") or "error" in t["result"]))]
    chats = {c.chat_id: c for c in iter_random_conversations(sample_size=1_000_000,
                                                               seed=args.seed, long_quota=0)}
    created_by_chat = {cid: c.created_at for cid, c in chats.items()}
    print(f"{len(memories)} active memories; {len(later_ids)} later chats to replay")

    rows: List[Dict[str, Any]] = []
    windows_by_chat: Dict[str, List[str]] = {}
    for n, cid in enumerate(later_ids, 1):
        chat = chats.get(cid)
        if chat is None or not chat.created_at:
            continue
        avail = available_memories(memories, cid, chat.created_at, created_by_chat)
        reply = _assistant_text(chat)
        if not avail or not reply.strip():
            continue
        core, _ = _select_core_memories(avail)
        kinds: Dict[str, set] = defaultdict(set)
        for m in core:
            kinds[m.id].add("core")
        avail_ids = {m.id for m in avail}
        for q in _human_turns(chat):
            try:
                hits = store.search(q, limit=args.search_k * 4)
            except Exception as e:  # noqa: BLE001
                print(f"  search failed: {e}", file=sys.stderr)
                continue
            for m in [h for h in hits if h.id in avail_ids][:args.search_k]:
                kinds[m.id].add("search")
        windows = _windowize(reply)
        windows_by_chat[cid] = windows
        wvecs = []
        for w in windows:
            v = provider.embed_text(w)
            if v is not None:
                wvecs.append((w, v))
        if not wvecs:
            continue
        for mid, ks in kinds.items():
            vec = cache.get(mid)
            if vec is None:
                continue
            scores = [_cosine(vec, v) for _, v in wvecs]
            bi = max(range(len(scores)), key=scores.__getitem__)
            rows.append({"chat_id": cid, "memory_id": mid, "layer": by_id[mid].layer,
                         "kinds": sorted(ks), "best_cos": round(scores[bi], 4),
                         "best_window": windows.index(wvecs[bi][0]),
                         "available": len(avail), "core_size": len(core)})
        print(f"[{n}/{len(later_ids)}] {cid[:8]} avail={len(avail)} core={len(core)} "
              f"loaded={len(kinds)} used@.55={sum(1 for r in rows if r['chat_id']==cid and r['best_cos']>=0.55)}",
              flush=True)

    (arm / "use_replay.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    card = summarize(rows)
    if args.calibrate and rows:
        card += "\n" + await calibrate(rows, by_id, windows_by_chat, args.calibrate,
                                       arm / "use_calibration.jsonl")
    (arm / "use_replay_summary.md").write_text(card + "\n")
    print("\n" + card)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
