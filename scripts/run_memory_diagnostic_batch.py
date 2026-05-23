"""Batch memory diagnostic runner.

Runs the extraction pipeline across many conversations and aggregates
metrics so we can spot systematic problems (gate over/under-firing,
layer drift, paraphrase survival, etc.) rather than over-fitting to
one chat.

Each conversation runs in an isolated sandbox (same scheme as
run_memory_diagnostic.py): no real cache or memory store is touched.

Usage:
    python scripts/run_memory_diagnostic_batch.py --count 20
    python scripts/run_memory_diagnostic_batch.py --count 50 --min-turns 8
    python scripts/run_memory_diagnostic_batch.py --seed 42 --count 30 \
        --output /tmp/batch-2026-05-22

Output goes to ~/.ziya/memory-diagnostic/_batch_<timestamp>/ unless
--output is given.  Produces:
  - summary.md   aggregate stats + outlier conversations
  - per-chat/<chat_id>.json  full trace for any chat the user wants to drill into

Designed for unattended runs of dozens of conversations.  Per-chat
sandbox dirs are kept under per-chat/<chat_id>/sandbox/ so they can
be inspected after the fact, but aggregate stats are the primary
output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional
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


# Per-chat trace.  Reset before each chat.  Wrappers below populate it.
_CHAT_TRACE: Dict[str, Any] = {}


def _new_chat_trace(chat_id: str, title: str) -> None:
    _CHAT_TRACE.clear()
    _CHAT_TRACE.update({
        "chat_id": chat_id,
        "title": title,
        "human_turns": 0,
        "salience_hits": 0,
        "windows": [],
        "model_calls": [],
        "gate_decisions": [],
        "comparator_decisions": [],
        "result": None,
        "duration_ms": None,
        "sandbox_proposals": [],
    })


def _wrap_call_service_model(real_fn):
    async def wrapped(category, system_prompt, user_message,
                      max_tokens=2048, temperature=0.2):
        t0 = time.time()
        try:
            response = await real_fn(category, system_prompt, user_message,
                                     max_tokens=max_tokens, temperature=temperature)
            error = None
        except Exception as e:
            response = ""
            error = str(e)
            raise
        finally:
            _CHAT_TRACE["model_calls"].append({
                "category": category,
                "user_chars": len(user_message),
                "response_chars": len(response),
                "duration_ms": int((time.time() - t0) * 1000),
                "error": error,
            })
        return response
    return wrapped


def _wrap_extract_memories(real_fn):
    async def wrapped(stripped, existing, project_name=None, project_path=None):
        result = await real_fn(stripped, existing,
                               project_name=project_name,
                               project_path=project_path)
        _CHAT_TRACE["windows"].append({
            "stripped_chars": len(stripped),
            "candidates_returned": len(result) if isinstance(result, list) else 0,
            "candidate_layers": [c.get("layer") for c in (result or [])],
        })
        return result
    return wrapped


def _wrap_quality_gate(real_fn):
    def wrapped(candidates):
        passed = real_fn(candidates)
        passed_ids = {id(c) for c in passed}
        for c in candidates:
            if id(c) in passed_ids:
                _CHAT_TRACE["gate_decisions"].append({
                    "passed": True,
                    "layer": c.get("layer"),
                    "reason": None,
                    "content": c.get("content", "")[:120],
                })
            else:
                # Diagnose without re-running real_fn (saves cycles)
                reason = _diagnose_gate_rejection(c.get("content", ""))
                _CHAT_TRACE["gate_decisions"].append({
                    "passed": False,
                    "layer": c.get("layer"),
                    "reason": reason,
                    "content": c.get("content", "")[:120],
                })
        return passed
    return wrapped


def _diagnose_gate_rejection(content: str) -> str:
    from app.utils.memory_extractor import (
        MIN_CONTENT_CHARS, MAX_CONTENT_CHARS,
        _DANGLING_REF_RE, _CODE_ARTIFACT_RE, _FILE_REF_RE,
        _CSS_PATTERN_RE, _REFACTORING_RE, _CODE_DESCRIPTION_RE, _CAREER_RE,
    )
    if len(content) < MIN_CONTENT_CHARS:
        return "too_short"
    if len(content) > MAX_CONTENT_CHARS:
        return "too_long"
    if len(_DANGLING_REF_RE.findall(content)) >= 2:
        return "dangling_refs"
    if len(_CODE_ARTIFACT_RE.findall(content)) >= 3:
        return "code_identifiers"
    if len(_FILE_REF_RE.findall(content)) >= 2:
        return "file_refs"
    if _CSS_PATTERN_RE.search(content):
        return "css_layout"
    if _REFACTORING_RE.search(content):
        return "refactoring"
    if _CODE_DESCRIPTION_RE.search(content):
        return "code_description"
    if _CAREER_RE.search(content):
        return "career"
    return "unknown"


def _wrap_compare_memory(real_fn):
    async def wrapped(candidate, similar):
        decision = await real_fn(candidate, similar)
        _CHAT_TRACE["comparator_decisions"].append({
            "candidate_layer": candidate.get("layer"),
            "similar_count": len(similar),
            "action": (decision.get("action") if isinstance(decision, dict)
                       else str(decision)),
        })
        return decision
    return wrapped


async def _run_one(chat, output_dir: Path) -> Dict[str, Any]:
    """Run one chat's extraction in a sandbox.  Returns the chat trace."""
    from app.utils.memory_extractor import (
        run_post_conversation_extraction, _count_salience_hits,
    )
    from app.storage.proposals import ProposalsStore
    from app.storage.memory import MemoryStorage
    from app.services.embedding_service import EmbeddingCache
    from app.utils.paths import get_ziya_home

    _new_chat_trace(chat.chat_id, chat.title)
    messages = [
        {"role": m.get("role"), "content": m.get("content")}
        for m in chat.messages if m.get("content")
    ]
    _CHAT_TRACE["human_turns"] = sum(
        1 for m in messages if m.get("role") in ("human", "user"))
    _CHAT_TRACE["salience_hits"] = _count_salience_hits(messages)

    sandbox = output_dir / "per-chat" / chat.chat_id / "sandbox"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True, exist_ok=True)
    real_active = get_ziya_home() / "memory" / "memories.json"
    if real_active.exists():
        shutil.copy2(real_active, sandbox / "memories.json")
    real_embeds = get_ziya_home() / "memory" / "embeddings.npz"
    if real_embeds.exists():
        shutil.copy2(real_embeds, sandbox / "embeddings.npz")

    sandbox_cache = EmbeddingCache(memory_dir=sandbox)
    sandbox_proposals = ProposalsStore(memory_dir=sandbox)
    sandbox_memory = MemoryStorage(memory_dir=sandbox)

    from app.services import model_resolver
    from app.utils import memory_extractor
    from app.utils import memory_comparator

    real_call = model_resolver.call_service_model
    real_extract = memory_extractor.extract_memories
    real_quality = memory_extractor.quality_gate
    real_compare = memory_comparator.compare_memory

    t0 = time.time()
    try:
        with patch("app.storage.proposals.get_proposals_store",
                   return_value=sandbox_proposals), \
             patch("app.storage.memory.get_memory_storage",
                   return_value=sandbox_memory), \
             patch("app.services.embedding_service.get_embedding_cache",
                   return_value=sandbox_cache), \
             patch("app.mcp.builtin_tools.is_builtin_category_enabled",
                   return_value=True), \
             patch("app.services.model_resolver.call_service_model",
                   side_effect=_wrap_call_service_model(real_call)), \
             patch("app.utils.memory_extractor.extract_memories",
                   side_effect=_wrap_extract_memories(real_extract)), \
             patch("app.utils.memory_extractor.quality_gate",
                   side_effect=_wrap_quality_gate(real_quality)), \
             patch("app.utils.memory_comparator.compare_memory",
                   side_effect=_wrap_compare_memory(real_compare)):
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
         "content": p.get("content", "")}
        for p in sandbox_proposals.list_open()
    ]

    chat_dir = output_dir / "per-chat" / chat.chat_id
    chat_dir.mkdir(parents=True, exist_ok=True)
    (chat_dir / "trace.json").write_text(
        json.dumps(_CHAT_TRACE, indent=2, default=str))
    return dict(_CHAT_TRACE)


# -- aggregate --------------------------------------------------------

def _aggregate(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(traces)
    skipped = [t for t in traces if isinstance(t["result"], dict)
               and t["result"].get("skipped")]
    errored = [t for t in traces if isinstance(t["result"], dict)
               and "error" in t["result"]]
    completed = [t for t in traces if t not in skipped and t not in errored]

    skip_reasons = Counter(t["result"].get("reason", "?") for t in skipped)
    gate_rejection_reasons = Counter()
    layer_distribution = Counter()
    proposals_per_chat = []
    paraphrase_pairs = []
    cross_window_layer_drift = 0
    comparator_actions = Counter()

    for t in completed:
        for d in t["gate_decisions"]:
            if not d["passed"]:
                gate_rejection_reasons[d["reason"]] += 1
        for p in t["sandbox_proposals"]:
            layer_distribution[p["layer"]] += 1
        proposals_per_chat.append(len(t["sandbox_proposals"]))
        for d in t["comparator_decisions"]:
            comparator_actions[d["action"]] += 1

        # Paraphrase detection within sandbox proposals (>50% word overlap
        # in same chat = likely fragment, since intra-batch dedup runs at
        # 60% — the gap between is where survivors hide).
        proposals = t["sandbox_proposals"]
        for i, p1 in enumerate(proposals):
            w1 = set(w.lower() for w in p1["content"].split() if len(w) > 3)
            for p2 in proposals[i + 1:]:
                w2 = set(w.lower() for w in p2["content"].split() if len(w) > 3)
                if not w1 or not w2:
                    continue
                overlap = len(w1 & w2) / min(len(w1), len(w2))
                if overlap >= 0.5:
                    paraphrase_pairs.append({
                        "chat_id": t["chat_id"],
                        "overlap": round(overlap, 2),
                        "p1": p1["content"][:100],
                        "p2": p2["content"][:100],
                        "p1_layer": p1["layer"],
                        "p2_layer": p2["layer"],
                    })

        # Layer drift across windows: same fact tagged differently.
        # Heuristic: if window emits same layer for >1 candidate AND total
        # layer set across windows is >2, suspect drift.
        window_layers = [tuple(w.get("candidate_layers", []))
                         for w in t["windows"] if w.get("candidate_layers")]
        flat = [l for ls in window_layers for l in ls]
        if len(set(flat)) >= 3 and len(flat) >= 4:
            cross_window_layer_drift += 1

    return {
        "total_chats": total,
        "completed": len(completed),
        "skipped": len(skipped),
        "errored": len(errored),
        "skip_reasons": dict(skip_reasons),
        "gate_rejection_reasons": dict(gate_rejection_reasons),
        "layer_distribution": dict(layer_distribution),
        "comparator_actions": dict(comparator_actions),
        "proposals_per_chat": {
            "min": min(proposals_per_chat) if proposals_per_chat else 0,
            "max": max(proposals_per_chat) if proposals_per_chat else 0,
            "mean": (sum(proposals_per_chat) / len(proposals_per_chat))
                    if proposals_per_chat else 0,
            "total": sum(proposals_per_chat),
        },
        "paraphrase_pairs": paraphrase_pairs,
        "cross_window_layer_drift_chats": cross_window_layer_drift,
    }


def _render_summary(traces: List[Dict[str, Any]],
                    agg: Dict[str, Any]) -> str:
    out: List[str] = []
    a = out.append
    a("# Memory Diagnostic Batch Summary")
    a("")
    a(f"- Conversations analyzed: **{agg['total_chats']}**")
    a(f"- Completed: {agg['completed']} | "
      f"Skipped: {agg['skipped']} | Errored: {agg['errored']}")
    a("")
    if agg["skip_reasons"]:
        a("## Skip reasons")
        for r, n in sorted(agg["skip_reasons"].items(), key=lambda x: -x[1]):
            a(f"- {r}: {n}")
        a("")

    a("## Proposals per chat")
    pp = agg["proposals_per_chat"]
    a(f"- Total: {pp['total']}  | Min: {pp['min']}  | "
      f"Max: {pp['max']}  | Mean: {pp['mean']:.1f}")
    a("")

    a("## Layer distribution (across all proposals)")
    total_props = sum(agg["layer_distribution"].values()) or 1
    for layer, count in sorted(agg["layer_distribution"].items(),
                               key=lambda x: -x[1]):
        pct = 100 * count / total_props
        a(f"- {layer}: {count}  ({pct:.1f}%)")
    a("")

    if agg["gate_rejection_reasons"]:
        a("## Gate rejections (across all candidates)")
        for r, n in sorted(agg["gate_rejection_reasons"].items(),
                           key=lambda x: -x[1]):
            a(f"- {r}: {n}")
        a("")

    if agg["comparator_actions"]:
        a("## Comparator actions")
        for action, n in sorted(agg["comparator_actions"].items(),
                                key=lambda x: -x[1]):
            a(f"- {action}: {n}")
        a("")

    a(f"## Layer drift across windows: {agg['cross_window_layer_drift_chats']} "
      "chats had ≥3 distinct layers across windows")
    a("")

    if agg["paraphrase_pairs"]:
        a(f"## Surviving paraphrase pairs: {len(agg['paraphrase_pairs'])}")
        a("")
        a("Within-chat proposal pairs with ≥50% word overlap.  Intra-batch")
        a("dedup runs at 60%, so these slipped through the gap.")
        a("")
        for pair in agg["paraphrase_pairs"][:30]:
            a(f"- **{pair['chat_id'][:8]}**  ovl={pair['overlap']}  "
              f"`{pair['p1_layer']}`/`{pair['p2_layer']}`")
            a(f"  - {pair['p1']}")
            a(f"  - {pair['p2']}")
        a("")

    # Top outliers: chats producing the most proposals (likely noise)
    by_count = sorted(traces,
                      key=lambda t: -len(t.get("sandbox_proposals", [])))
    a("## Top 10 chats by proposal count")
    a("")
    a("| chat_id | turns | proposals | result |")
    a("|---------|-------|-----------|--------|")
    for t in by_count[:10]:
        result = t.get("result", {})
        if isinstance(result, dict):
            r_str = (f"prop={result.get('proposed', 0)} "
                     f"corr={result.get('corroborated', 0)} "
                     f"saved={result.get('saved', 0)}")
        else:
            r_str = str(result)
        a(f"| {t['chat_id'][:8]} | {t['human_turns']} | "
          f"{len(t['sandbox_proposals'])} | {r_str} |")

    return "\n".join(out)


# -- main -------------------------------------------------------------

async def run_batch(args: argparse.Namespace) -> int:
    _bootstrap_plugins()

    from app.utils.memory_eval import iter_random_conversations

    chats = iter_random_conversations(
        sample_size=1_000_000, seed=args.seed, long_quota=0)
    if args.min_turns:
        chats = [c for c in chats if len(c.messages) >= args.min_turns]

    # Dedupe by chat_id (corpus has copies across project dirs)
    seen = set()
    deduped = []
    for c in chats:
        if c.chat_id in seen:
            continue
        seen.add(c.chat_id)
        deduped.append(c)
    chats = deduped

    if args.count and args.count < len(chats):
        # Pick a deterministic slice if seed given, else first N
        chats = chats[:args.count]

    if args.output:
        output_dir = Path(args.output).expanduser()
    else:
        from app.utils.paths import get_ziya_home
        ts = time.strftime("%Y%m%d-%H%M%S")
        output_dir = get_ziya_home() / "memory-diagnostic" / f"_batch_{ts}"
    output_dir.mkdir(parents=True, exist_ok=True)

    _eprint(f"Running {len(chats)} conversations → {output_dir}")
    traces: List[Dict[str, Any]] = []
    t_start = time.time()
    for i, chat in enumerate(chats):
        _eprint(f"[{i+1}/{len(chats)}] {chat.chat_id[:8]} "
                f"({len(chat.messages)} msgs)  {chat.title[:60]}")
        try:
            trace = await _run_one(chat, output_dir)
            traces.append(trace)
        except Exception as e:
            _eprint(f"  FAILED: {e}")
            traces.append({"chat_id": chat.chat_id, "title": chat.title,
                           "result": {"error": str(e)},
                           "sandbox_proposals": [], "human_turns": 0,
                           "salience_hits": 0, "windows": [],
                           "model_calls": [], "gate_decisions": [],
                           "comparator_decisions": [], "duration_ms": 0})

    _eprint(f"Batch complete in {int(time.time() - t_start)}s")
    agg = _aggregate(traces)
    (output_dir / "summary.json").write_text(
        json.dumps({"aggregate": agg, "chats": [
            {"chat_id": t["chat_id"], "title": t.get("title"),
             "human_turns": t["human_turns"],
             "proposal_count": len(t["sandbox_proposals"]),
             "result": t["result"], "duration_ms": t["duration_ms"]}
            for t in traces
        ]}, indent=2, default=str))
    (output_dir / "summary.md").write_text(_render_summary(traces, agg))
    _eprint(f"Wrote {output_dir}/summary.md")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--count", type=int, default=20,
                   help="Max conversations to process (default 20)")
    p.add_argument("--min-turns", type=int, default=4,
                   help="Skip conversations with fewer human/assistant "
                        "messages (default 4)")
    p.add_argument("--seed", type=int, default=42,
                   help="Seed for conversation sampling (default 42)")
    p.add_argument("--output", default=None,
                   help="Output directory")
    args = p.parse_args()
    return asyncio.run(run_batch(args))


if __name__ == "__main__":
    sys.exit(main())
