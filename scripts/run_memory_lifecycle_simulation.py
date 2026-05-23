"""Memory lifecycle simulation — extracts on a SEED set of conversations,
force-promotes the surviving proposals to active memories, then runs
extraction on a LATER set against that seeded sandbox so the comparator
(NOOP/UPDATE/ADD) and corroboration paths actually fire.

Without this, every batch run starts with an empty sandbox, so:
  * `compare_memory` is never called (no `existing_memories` to compare)
  * Embedding-dedup against active memories never triggers (only the
    proposal-paraphrase drop path runs)
  * Corroboration accumulation never observable

This script splits the corpus into two phases:
  Phase 1 (SEED): N chats run through extraction.  After all are done,
                  every proposal is force-promoted to an active memory
                  (skipping the corroboration/age requirements).
  Phase 2 (LATER): M chats run through extraction against the seeded
                  sandbox.  Comparator decisions, embedding-dedup hits,
                  and corroboration bumps are recorded.

Splits:
  --split random         Random partition (default; tests "does extraction
                         respect ANY existing memories?")
  --split clustered      Embed first user message of each chat, cluster
                         by similarity, take 1 chat per cluster as seed
                         and the rest as later (tests "does extraction
                         recognize related-topic conversations?")

Usage:
    python scripts/run_memory_lifecycle_simulation.py --seed-count 10 --later-count 10
    python scripts/run_memory_lifecycle_simulation.py --seed-count 15 --later-count 15 --split clustered

Output: ~/.ziya/memory-diagnostic/_lifecycle_<timestamp>/
  - phase1_extraction.json     per-seed-chat traces
  - phase1_promotions.json     what got promoted to active
  - phase2_extraction.json     per-later-chat traces with comparator data
  - summary.md                 head-to-head comparison
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def _bootstrap_plugins():
    os.environ.setdefault("ZIYA_LOAD_INTERNAL_PLUGINS", "1")
    try:
        from app.plugins import initialize as init_plugins
        init_plugins()
    except Exception as e:
        _eprint(f"plugin init failed (continuing): {e}")


# Per-chat trace; reset before each chat extraction.
_CHAT_TRACE: Dict[str, Any] = {}


def _new_chat_trace(chat_id: str, title: str) -> None:
    _CHAT_TRACE.clear()
    _CHAT_TRACE.update({
        "chat_id": chat_id,
        "title": title,
        "human_turns": 0,
        "salience_hits": 0,
        "windows": [],
        "comparator_decisions": [],
        "embedding_dedup_events": [],  # active-vs-proposal match, scores, action
        "corroboration_sink": [],       # m_* IDs that received corroboration credit
        "result": None,
        "duration_ms": None,
        "sandbox_proposals": [],
    })


# ─── wrappers ────────────────────────────────────────────────────────

def _wrap_extract_memories(real_fn):
    async def wrapped(stripped, existing, project_name=None, project_path=None):
        result = await real_fn(stripped, existing,
                               project_name=project_name,
                               project_path=project_path)
        _CHAT_TRACE["windows"].append({
            "stripped_chars": len(stripped),
            "existing_count": len(existing or []),
            "candidates_returned": len(result) if isinstance(result, list) else 0,
            "candidate_layers": [c.get("layer") for c in (result or [])],
        })
        return result
    return wrapped


def _wrap_compare_memory(real_fn):
    async def wrapped(candidate, similar):
        decision = await real_fn(candidate, similar)
        _CHAT_TRACE["comparator_decisions"].append({
            "candidate_layer": candidate.get("layer"),
            "candidate_content": candidate.get("content", "")[:120],
            "similar_count": len(similar),
            "similar_ids": [s.get("id") for s in similar[:3]],
            "action": (decision.get("action") if isinstance(decision, dict)
                       else str(decision)),
            "target_id": (decision.get("target_id")
                          if isinstance(decision, dict) else None),
        })
        return decision
    return wrapped


# Embedding-dedup observation: hook the cache.search call site indirectly
# via patching deduplicate to capture the corroboration_sink it produces.
def _wrap_deduplicate(real_fn):
    def wrapped(candidates, existing, corroboration_sink=None):
        # Inject our own sink so we can see who got corroborated
        sink = corroboration_sink if corroboration_sink is not None else []
        result = real_fn(candidates, existing, corroboration_sink=sink)
        _CHAT_TRACE["corroboration_sink"].extend(sink)
        # Record: how many candidates went in, how many came out
        _CHAT_TRACE["embedding_dedup_events"].append({
            "in": len(candidates),
            "out": len(result),
            "dropped": len(candidates) - len(result),
            "corroborated_active_memories": len(sink),
        })
        return result
    return wrapped


# ─── seeded sandbox machinery ────────────────────────────────────────

def _make_sandbox(output_dir: Path, name: str):
    """Create an isolated sandbox dir with fresh proposals/memories/embeddings."""
    from app.storage.proposals import ProposalsStore
    from app.storage.memory import MemoryStorage
    from app.services.embedding_service import EmbeddingCache

    sandbox = output_dir / name
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True, exist_ok=True)

    cache = EmbeddingCache(memory_dir=sandbox)
    proposals = ProposalsStore(memory_dir=sandbox)
    memory_store = MemoryStorage(memory_dir=sandbox)
    return sandbox, cache, proposals, memory_store


async def _extract_one(chat, proposals_store, memory_store, cache,
                        record_dedup: bool = False) -> Dict[str, Any]:
    """Run extraction on one chat against the given sandbox stores."""
    from app.utils.memory_extractor import (
        run_post_conversation_extraction, _count_salience_hits,
    )
    from app.utils import memory_extractor
    from app.utils import memory_comparator

    _new_chat_trace(chat.chat_id, chat.title)
    messages = [
        {"role": m.get("role"), "content": m.get("content")}
        for m in chat.messages if m.get("content")
    ]
    _CHAT_TRACE["human_turns"] = sum(
        1 for m in messages if m.get("role") in ("human", "user"))
    _CHAT_TRACE["salience_hits"] = _count_salience_hits(messages)

    real_extract = memory_extractor.extract_memories
    real_compare = memory_comparator.compare_memory
    real_dedup = memory_extractor.deduplicate

    patches = [
        patch("app.storage.proposals.get_proposals_store",
              return_value=proposals_store),
        patch("app.storage.memory.get_memory_storage",
              return_value=memory_store),
        patch("app.services.embedding_service.get_embedding_cache",
              return_value=cache),
        patch("app.mcp.builtin_tools.is_builtin_category_enabled",
              return_value=True),
        patch("app.utils.memory_extractor.extract_memories",
              side_effect=_wrap_extract_memories(real_extract)),
        patch("app.utils.memory_comparator.compare_memory",
              side_effect=_wrap_compare_memory(real_compare)),
    ]
    if record_dedup:
        patches.append(patch("app.utils.memory_extractor.deduplicate",
                             side_effect=_wrap_deduplicate(real_dedup)))

    t0 = time.time()
    try:
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = await run_post_conversation_extraction(
                messages, conversation_id=chat.chat_id, project_path=None)
        _CHAT_TRACE["result"] = result
    except Exception as e:
        _CHAT_TRACE["result"] = {"error": str(e)}
    finally:
        _CHAT_TRACE["duration_ms"] = int((time.time() - t0) * 1000)

    _CHAT_TRACE["sandbox_proposals"] = [
        {"id": p.get("id"), "layer": p.get("layer"),
         "tags": p.get("tags", []),
         "content": p.get("content", ""),
         "corroborations": p.get("corroborations", 0)}
        for p in proposals_store.list_open()
    ]
    return dict(_CHAT_TRACE)


def _force_promote_all(proposals_store, memory_store, cache) -> List[Dict[str, Any]]:
    """Force-promote every open proposal in the sandbox to an active memory.

    This bypasses the normal corroboration/age requirements — useful for
    seeding a sandbox with a known active store so we can test how
    extraction behaves against it.

    Embeds promoted memories directly into the SANDBOX cache (passed as
    \`\`cache\`\`), bypassing \`\`embed_and_cache\`\` which would route to whatever
    \`\`get_embedding_cache()\`\` returns at call time.  Without this, calls
    here run OUTSIDE the patch context (between phases) and leak
    embeddings into the real ~/.ziya/memory/ cache.
    """
    from app.models.memory import Memory
    from app.services.embedding_service import get_embedding_provider, NoopProvider
    provider = get_embedding_provider()
    promoted: List[Dict[str, Any]] = []
    for prop in proposals_store.list_open():
        memory = Memory(
            content=prop["content"],
            layer=prop.get("layer", "domain_context"),
            tags=prop.get("tags", []) or [],
            learned_from="seed_phase_force_promote",
            status="active",
            corroborations=prop.get("corroborations", 0),
            corroborated_by=list(prop.get("corroborated_by", []) or []),
            learned_from_conversation=prop.get("conversation_id"),
        )
        scope_data = prop.get("scope") or {}
        if scope_data.get("project_paths"):
            memory.scope.project_paths = list(scope_data["project_paths"])
        # MemoryStorage.save calls embed_and_cache internally — that uses
        # the real get_embedding_cache (we're outside the patch context
        # here), which would leak into ~/.ziya/memory/.  Apply the patch
        # locally for the duration of save().
        with patch("app.services.embedding_service.get_embedding_cache",
                   return_value=cache):
            memory_store.save(memory)
        # Direct embed into sandbox cache under the m_* ID so cache.search
        # returns m_* matches in the later phase.
        if not isinstance(provider, NoopProvider):
            try:
                vec = provider.embed_text(memory.content)
                if vec is not None:
                    cache.put(memory.id, vec)
            except Exception as e:
                _eprint(f"  embed failed for {memory.id}: {e}")
        proposals_store.mark_promoted(prop["id"], target_memory_id=memory.id)
        promoted.append({
            "memory_id": memory.id,
            "from_proposal": prop["id"],
            "layer": memory.layer,
            "content": memory.content[:120],
        })
    cache.flush()
    return promoted


# ─── splitting strategies ────────────────────────────────────────────

def _split_random(chats, seed_count: int, later_count: int, rng_seed: int):
    import random
    rng = random.Random(rng_seed)
    pool = list(chats)
    rng.shuffle(pool)
    if seed_count + later_count > len(pool):
        seed_count = max(1, len(pool) // 2)
        later_count = len(pool) - seed_count
        _eprint(f"Adjusted to seed={seed_count} later={later_count}")
    return pool[:seed_count], pool[seed_count:seed_count + later_count]


def _first_user_text(chat, max_chars: int = 600) -> str:
    """Extract first user message text for clustering."""
    for m in chat.messages:
        if m.get("role") in ("human", "user"):
            content = m.get("content", "")
            if isinstance(content, list):
                # Bedrock content blocks
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if isinstance(content, str) and content.strip():
                return content[:max_chars]
    return chat.title or ""


def _split_clustered(chats, seed_count: int, later_count: int):
    """Cluster chats by first-user-message embedding similarity.

    For each cluster of 2+ chats: take 1 as seed, the rest go to later.
    Singletons go to whichever bucket needs filling.

    Goal: maximize topical overlap between seed and later phases so the
    comparator actually has work to do.
    """
    try:
        from app.services.embedding_service import get_embedding_provider, NoopProvider
        provider = get_embedding_provider()
        if isinstance(provider, NoopProvider):
            _eprint("WARNING: clustered split needs a real embedder; "
                    "got Noop. Falling back to random split.")
            return _split_random(chats, seed_count, later_count, 42)
    except Exception as e:
        _eprint(f"WARNING: clustered split failed init: {e}. "
                "Falling back to random.")
        return _split_random(chats, seed_count, later_count, 42)

    import numpy as np
    vecs: List[np.ndarray] = []
    keep: List[Any] = []
    for c in chats:
        text = _first_user_text(c)
        if not text:
            continue
        try:
            v = provider.embed_text(text)
            if v is not None:
                vecs.append(v)
                keep.append(c)
        except Exception:
            continue
    if not vecs:
        return _split_random(chats, seed_count, later_count, 42)

    matrix = np.stack(vecs)
    # Greedy clustering: O(n²) but fine for n ≤ 200.
    threshold = 0.55  # cosine; chosen empirically — high enough to require
                     # genuine topical overlap, low enough to find clusters.
    n = len(keep)
    cluster_id = [-1] * n
    next_cid = 0
    for i in range(n):
        if cluster_id[i] != -1:
            continue
        cluster_id[i] = next_cid
        for j in range(i + 1, n):
            if cluster_id[j] != -1:
                continue
            sim = float(np.dot(matrix[i], matrix[j]) /
                        (np.linalg.norm(matrix[i]) * np.linalg.norm(matrix[j]) + 1e-9))
            if sim >= threshold:
                cluster_id[j] = next_cid
        next_cid += 1

    # Group into clusters
    clusters: Dict[int, List[Any]] = {}
    for idx, cid in enumerate(cluster_id):
        clusters.setdefault(cid, []).append(keep[idx])
    multi = [c for c in clusters.values() if len(c) >= 2]
    singletons = [c[0] for c in clusters.values() if len(c) == 1]
    _eprint(f"Clustered: {len(multi)} multi-chat clusters, "
            f"{len(singletons)} singletons (threshold={threshold})")
    if not multi:
        _eprint("WARNING: no topical clusters found. Falling back to random.")
        return _split_random(chats, seed_count, later_count, 42)

    seed: List[Any] = []
    later: List[Any] = []
    for cluster in multi:
        seed.append(cluster[0])
        later.extend(cluster[1:])
    # Top up from singletons if either bucket is short
    while len(seed) < seed_count and singletons:
        seed.append(singletons.pop())
    while len(later) < later_count and singletons:
        later.append(singletons.pop())
    seed = seed[:seed_count]
    later = later[:later_count]
    return seed, later


# ─── aggregate / report ──────────────────────────────────────────────

def _aggregate_phase(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    completed = [t for t in traces if not (
        isinstance(t["result"], dict) and (
            t["result"].get("skipped") or "error" in t["result"]))]
    skipped = [t for t in traces if isinstance(t["result"], dict)
               and t["result"].get("skipped")]
    proposals_total = sum(len(t.get("sandbox_proposals", []))
                           for t in completed)
    comparator_actions = Counter()
    embedding_dedup_drops = 0
    embedding_dedup_corroborations = 0  # m_* matches caught by deduplicate()
    result_corroborated = 0  # bumps from comparator ADD-with-similar path
    result_saved = 0          # UPDATE path writes
    for t in traces:
        for d in t.get("comparator_decisions", []):
            comparator_actions[d["action"]] += 1
        for ev in t.get("embedding_dedup_events", []):
            embedding_dedup_drops += ev.get("dropped", 0)
            embedding_dedup_corroborations += ev.get(
                "corroborated_active_memories", 0)
        # Pull authoritative corroboration/saved counts from extraction result.
        # The deduplicate() sink only sees paraphrase-of-active hits; the ADD
        # path inside run_post_conversation_extraction also bumps when
        # find_similar_memories returns matches. Both count toward "active
        # memory got a corroboration signal this conversation."
        result = t.get("result")
        if isinstance(result, dict) and not result.get("skipped"):
            result_corroborated += result.get("corroborated", 0) or 0
            result_saved += result.get("saved", 0) or 0
    return {
        "total": len(traces),
        "completed": len(completed),
        "skipped": len(skipped),
        "proposals_total": proposals_total,
        "comparator_actions": dict(comparator_actions),
        "embedding_dedup_drops": embedding_dedup_drops,
        "embedding_dedup_corroborations": embedding_dedup_corroborations,
        "result_corroborated_total": result_corroborated,
        "result_saved_total": result_saved,
    }


def _render_summary(seed_traces, later_traces,
                    seed_agg, later_agg, promoted, args) -> str:
    out: List[str] = []
    a = out.append
    a("# Memory Lifecycle Simulation Summary")
    a("")
    a(f"- Split mode: **{args.split}**")
    a(f"- Seed chats: {seed_agg['total']} "
      f"({seed_agg['completed']} completed, {seed_agg['skipped']} skipped)")
    a(f"- Later chats: {later_agg['total']} "
      f"({later_agg['completed']} completed, {later_agg['skipped']} skipped)")
    a(f"- Force-promoted to active: **{len(promoted)}**")
    a("")
    a("## Phase 1 (SEED) — extraction against EMPTY sandbox")
    a("")
    a(f"- Proposals produced: {seed_agg['proposals_total']}")
    a(f"- Comparator actions: {seed_agg['comparator_actions'] or '(none — empty sandbox, no compare calls)'}")
    a(f"- Embedding-dedup drops: {seed_agg['embedding_dedup_drops']}")
    a(f"- Active-memory corroboration hits (dedup path): {seed_agg['embedding_dedup_corroborations']}")
    a(f"- Active-memory corroborations (per result): {seed_agg['result_corroborated_total']}")
    a(f"- Active-memory UPDATE writes: {seed_agg['result_saved_total']}")
    a("")
    a("## Phase 2 (LATER) — extraction against SEEDED sandbox")
    a("")
    a(f"- Proposals produced: {later_agg['proposals_total']}")
    a(f"- Comparator actions: {later_agg['comparator_actions'] or '(none)'}")
    a(f"- Embedding-dedup drops (proposal-paraphrase): {later_agg['embedding_dedup_drops']}")
    a(f"- Active-memory corroboration hits (dedup path): {later_agg['embedding_dedup_corroborations']}")
    a(f"- Active-memory corroborations (per result): {later_agg['result_corroborated_total']}")
    a(f"- Active-memory UPDATE writes: {later_agg['result_saved_total']}")
    a("")

    # The interesting comparison: do later-phase chats produce DIFFERENT
    # comparator action distributions than what an empty sandbox would?
    a("## Lifecycle behavior under seeded conditions")
    a("")
    if not later_agg["comparator_actions"]:
        a("⚠️  Comparator NEVER fired in later phase. Either:")
        a("    - No proposals survived embedding-dedup against active memories")
        a("    - `find_similar_memories` returned empty (active store too small "
          "or topically disjoint)")
        a("    - Extraction skipped on every chat (check skip rate)")
    else:
        total = sum(later_agg["comparator_actions"].values())
        for action, n in sorted(later_agg["comparator_actions"].items(),
                                key=lambda x: -x[1]):
            pct = 100 * n / total
            a(f"- {action}: {n} ({pct:.1f}%)")
    a("")

    total_corr = (later_agg["embedding_dedup_corroborations"]
                  + later_agg["result_corroborated_total"])
    if total_corr > 0:
        a(f"✓ Active-memory corroboration is firing "
          f"({total_corr} total hits across {later_agg['total']} later chats: "
          f"{later_agg['embedding_dedup_corroborations']} via embedding-dedup, "
          f"{later_agg['result_corroborated_total']} via comparator ADD-with-similar). "
          f"The seeded sandbox is exercising lifecycle corroboration paths "
          f"that empty-sandbox runs cannot.")
    else:
        a("⚠️  No active-memory corroboration hits in later phase. The "
          "topical overlap between seed and later chats may be too low — "
          "try `--split clustered` if you used random.")
    if later_agg["result_saved_total"] > 0:
        a("")
        a(f"✓ UPDATE writes fired {later_agg['result_saved_total']} times — "
          f"the comparator chose to replace existing active memories with "
          f"newer/more-complete versions from the later phase.")
    a("")

    # Seed-phase paraphrase pairs (control)
    a("## Seed promotions (sample)")
    a("")
    for p in promoted[:10]:
        a(f"- `{p['memory_id'][:10]}` [{p['layer']}]  {p['content']}")
    if len(promoted) > 10:
        a(f"- ... and {len(promoted) - 10} more")
    a("")

    # Top later-phase chats by interesting comparator activity
    interesting = [t for t in later_traces if t.get("comparator_decisions")]
    if interesting:
        a("## Later-phase chats with comparator decisions (sample)")
        a("")
        a("| chat_id | turns | proposals | NOOP | UPDATE | ADD |")
        a("|---------|-------|-----------|------|--------|-----|")
        for t in interesting[:10]:
            actions = Counter(d["action"]
                              for d in t["comparator_decisions"])
            a(f"| {t['chat_id'][:8]} | {t['human_turns']} | "
              f"{len(t['sandbox_proposals'])} | "
              f"{actions.get('NOOP', 0)} | "
              f"{actions.get('UPDATE', 0)} | "
              f"{actions.get('ADD', 0)} |")
        a("")

    return "\n".join(out)


# ─── main ────────────────────────────────────────────────────────────

async def run_sim(args: argparse.Namespace) -> int:
    _bootstrap_plugins()

    from app.utils.memory_eval import iter_random_conversations
    chats = iter_random_conversations(
        sample_size=1_000_000, seed=args.seed, long_quota=0)
    if args.min_turns:
        chats = [c for c in chats if len(c.messages) >= args.min_turns]

    # Dedupe by chat_id
    seen, deduped = set(), []
    for c in chats:
        if c.chat_id in seen:
            continue
        seen.add(c.chat_id)
        deduped.append(c)
    chats = deduped
    _eprint(f"Loaded {len(chats)} unique conversations from corpus")

    # Split
    if args.split == "clustered":
        seed_chats, later_chats = _split_clustered(
            chats, args.seed_count, args.later_count)
    else:
        seed_chats, later_chats = _split_random(
            chats, args.seed_count, args.later_count, args.seed)

    if not seed_chats or not later_chats:
        _eprint("ERROR: not enough chats for split")
        return 1

    if args.output:
        output_dir = Path(args.output).expanduser()
    else:
        from app.utils.paths import get_ziya_home
        ts = time.strftime("%Y%m%d-%H%M%S")
        output_dir = get_ziya_home() / "memory-diagnostic" / f"_lifecycle_{ts}"
    output_dir.mkdir(parents=True, exist_ok=True)
    _eprint(f"Output → {output_dir}")
    _eprint(f"Seed chats: {len(seed_chats)} | Later chats: {len(later_chats)}")

    # Single sandbox shared across both phases
    sandbox, cache, proposals, memory_store = _make_sandbox(
        output_dir, "sandbox")

    # ── Phase 1: SEED ──────────────────────────────────────────────
    _eprint("\n=== Phase 1: SEED extraction (empty sandbox) ===")
    seed_traces: List[Dict[str, Any]] = []
    t_phase1 = time.time()
    for i, chat in enumerate(seed_chats):
        _eprint(f"[seed {i+1}/{len(seed_chats)}] {chat.chat_id[:8]} "
                f"({len(chat.messages)} msgs)  {chat.title[:50]}")
        try:
            trace = await _extract_one(
                chat, proposals, memory_store, cache,
                record_dedup=False)  # no active memories in seed phase
            seed_traces.append(trace)
        except Exception as e:
            _eprint(f"  FAILED: {e}")
    _eprint(f"Phase 1 done in {int(time.time() - t_phase1)}s")

    # ── Force-promote all surviving proposals ──────────────────────
    promoted = _force_promote_all(proposals, memory_store, cache)
    _eprint(f"\nForce-promoted {len(promoted)} proposals to active memories")

    # ── Phase 2: LATER ─────────────────────────────────────────────
    _eprint("\n=== Phase 2: LATER extraction (seeded sandbox) ===")
    later_traces: List[Dict[str, Any]] = []
    t_phase2 = time.time()
    for i, chat in enumerate(later_chats):
        _eprint(f"[later {i+1}/{len(later_chats)}] {chat.chat_id[:8]} "
                f"({len(chat.messages)} msgs)  {chat.title[:50]}")
        try:
            trace = await _extract_one(
                chat, proposals, memory_store, cache, record_dedup=True)
            later_traces.append(trace)
        except Exception as e:
            _eprint(f"  FAILED: {e}")
    _eprint(f"Phase 2 done in {int(time.time() - t_phase2)}s")

    # Aggregate + write
    seed_agg = _aggregate_phase(seed_traces)
    later_agg = _aggregate_phase(later_traces)

    (output_dir / "phase1_extraction.json").write_text(
        json.dumps(seed_traces, indent=2, default=str))
    (output_dir / "phase1_promotions.json").write_text(
        json.dumps(promoted, indent=2, default=str))
    (output_dir / "phase2_extraction.json").write_text(
        json.dumps(later_traces, indent=2, default=str))
    (output_dir / "summary.json").write_text(json.dumps({
        "seed_aggregate": seed_agg,
        "later_aggregate": later_agg,
        "promoted_count": len(promoted),
        "args": vars(args),
    }, indent=2, default=str))
    summary_md = _render_summary(seed_traces, later_traces,
                                 seed_agg, later_agg, promoted, args)
    (output_dir / "summary.md").write_text(summary_md)
    _eprint(f"\nWrote {output_dir}/summary.md")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed-count", type=int, default=10,
                   help="How many conversations to use for seeding (Phase 1)")
    p.add_argument("--later-count", type=int, default=10,
                   help="How many conversations to run against seeded sandbox (Phase 2)")
    p.add_argument("--split", choices=["random", "clustered"], default="random",
                   help="How to partition the corpus. 'random' for "
                        "framework validation, 'clustered' for topical "
                        "overlap (uses embeddings — needs Bedrock provider).")
    p.add_argument("--min-turns", type=int, default=4)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output", default=None)
    args = p.parse_args()
    return asyncio.run(run_sim(args))


if __name__ == "__main__":
    sys.exit(main())
