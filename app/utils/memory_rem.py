"""
REM phase — higher-order abstraction synthesis and staleness detection.

Runs as a phase in `memory_organizer.reorganize()` between relation
extraction and cross-link discovery.  Two activities, both gated by
maturity:

  1. Synthesis (#1): for mature mind-map nodes, ask the LLM whether a
     non-obvious principle emerges from the memories that no individual
     memory states explicitly.  Output goes to ProposalsStore with
     learned_from="rem_synthesis"; promotion happens via the normal
     corroboration/use signals so bad abstractions self-correct via the
     90-day decay path if they never get retrieved.

  2. Staleness (#3): for top-importance memories in mature nodes, ask
     whether each is still consistent with the rest of the corpus and
     recent activity.  Memories the LLM marks stale AND where another
     memory in the same node contradicts it flip to status="contested";
     the contradiction gate prevents fabricated staleness.

Contested memories are excluded from system-prompt injection but remain
findable via memory_search (with a [contested] marker) so the model can
reason about them.  Resurrection happens in memory_feedback when a
contested memory matches the assistant's response.

Inspired by:
  - SCM (arXiv 2604.20943) NREM/REM phase split
  - Continuum Memory Architecture higher-order abstraction
  - STALE benchmark for staleness detection in agent memory
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.utils.logging_utils import logger

# -- Maturity gate ---------------------------------------------------

# Minimum number of active memories in a node before REM operates.
_MATURITY_MIN_MEMORIES = 4

# Minimum number of distinct learned_from sources across the node's
# memories.  Prevents abstracting over a single extraction batch.
_MATURITY_MIN_SOURCES = 2

# Oldest memory in the node must be at least this old (seconds).
# Stable patterns are abstracted; in-flight thinking is not.
_MATURITY_MIN_AGE_SECONDS = 30 * 86400

# -- Synthesis ------------------------------------------------------

# Skip synthesis if a recent rem_synthesis memory or proposal already
# elaborates on >=80% of the node's current memory set.  Idempotency
# without mutating the MindMapNode model.
_SYNTHESIS_OVERLAP_THRESHOLD = 0.8
_SYNTHESIS_COOLDOWN_SECONDS = 30 * 86400

SYNTHESIS_PROMPT = """\
You are a memory abstraction system.  Given a set of memories that share \
a topic, identify whether they collectively instantiate a single \
non-obvious principle that no individual memory states explicitly.

Reject as null (return {"synthesis": null}) if:
- The only common thread is the topic name itself
- Memories are independent facts that happen to share tags
- The "principle" you would write is a paraphrase of one source memory
- The pattern is obvious from any single memory in isolation

Accept (return {"synthesis": "...", "rationale": "..."}) only if:
- A pattern emerges across MULTIPLE memories that no single memory states
- A reader of the abstracted principle in a brand new conversation \
(zero context about today) would find it useful
- The principle would not need to be re-derived next session

Be strict.  Most mature nodes will return null.  Returning a vague \
abstraction is worse than returning nothing.

Output ONLY a JSON object: {"synthesis": "...", "rationale": "..."} \
or {"synthesis": null, "rationale": "..."}.  No markdown, no explanation."""


# -- Staleness ------------------------------------------------------

# Top-K highest-importance memories per node to evaluate for staleness.
_STALENESS_TOP_K = 3

STALENESS_PROMPT = """\
You are a memory staleness detector.  Given a set of CANDIDATE memories \
from a knowledge domain plus the broader CONTEXT of related memories, \
determine whether each candidate is still likely true.

For each candidate, output one verdict:
- "true" — candidate is consistent with context, no contradictions
- "false" — candidate contradicts more recent context or has been superseded
- "unknown" — insufficient information to judge (DEFAULT for ambiguous cases)

Be conservative.  Return "false" only when you can point to a specific \
contradicting memory in the context.  When in doubt, return "unknown".

Output ONLY a JSON object:
{"verdicts": [{"id": "...", "verdict": "true|false|unknown", "rationale": "..."}]}

No markdown, no explanation outside the JSON."""


# -- Maturity gate --------------------------------------------------

def _is_mature(node, store) -> Tuple[bool, str]:
    """Whether a mind-map node qualifies for REM operations.

    Returns (is_mature, reason).  reason is empty when mature.
    """
    if not node or not node.memory_refs:
        return False, "no_memory_refs"
    memories = []
    for mid in node.memory_refs:
        m = store.get(mid)
        if m and m.status == "active":
            memories.append(m)
    if len(memories) < _MATURITY_MIN_MEMORIES:
        return False, f"too_few_memories ({len(memories)} < {_MATURITY_MIN_MEMORIES})"
    sources = {m.learned_from for m in memories if m.learned_from}
    if len(sources) < _MATURITY_MIN_SOURCES:
        return False, f"too_few_sources ({len(sources)} < {_MATURITY_MIN_SOURCES})"
    now = time.time()
    oldest_age = 0
    for m in memories:
        try:
            t = time.mktime(time.strptime(m.created, "%Y-%m-%d"))
            age = now - t
            if age > oldest_age:
                oldest_age = age
        except (ValueError, TypeError):
            continue
    if oldest_age < _MATURITY_MIN_AGE_SECONDS:
        return False, f"too_young (oldest={int(oldest_age / 86400)}d)"
    return True, ""


def _should_synthesize(node, store) -> bool:
    """Idempotency check: skip if a recent synthesis already covers this node.

    Compares this node's memory_refs against `relations.elaborates` on
    existing rem_synthesis memories.  >=80% overlap within the cooldown
    window means we already synthesized recently.
    """
    current_set = set(node.memory_refs)
    if not current_set:
        return False
    cutoff = time.time() - _SYNTHESIS_COOLDOWN_SECONDS
    for mem in store.list_memories():
        if mem.learned_from != "rem_synthesis":
            continue
        try:
            created_t = time.mktime(time.strptime(mem.created, "%Y-%m-%d"))
        except (ValueError, TypeError):
            continue
        if created_t < cutoff:
            continue
        elaborates = set(mem.relations.get("elaborates", []) or [])
        if not elaborates:
            continue
        overlap = len(current_set & elaborates) / max(len(current_set), 1)
        if overlap >= _SYNTHESIS_OVERLAP_THRESHOLD:
            logger.debug(
                f"REM: skipping synthesis for node {node.id} — "
                f"existing {mem.id} elaborates on {overlap:.0%} of memories"
            )
            return False
    return True


# -- Synthesis ------------------------------------------------------

async def synthesize_node(node, store) -> Optional[str]:
    """Run synthesis on a mature node.  Returns proposal id on creation,
    None if the LLM returned null or the call failed.
    """
    memories = []
    for mid in node.memory_refs:
        m = store.get(mid)
        if m and m.status == "active":
            memories.append(m)
    if not memories:
        return None

    mem_lines = []
    for m in memories:
        tag_str = f" [{', '.join(m.tags)}]" if m.tags else ""
        mem_lines.append(f"- ({m.layer}){tag_str} {m.content}")
    user_msg = (
        f"DOMAIN: {node.handle}\n\n"
        f"MEMORIES:\n" + "\n".join(mem_lines)
    )

    try:
        from app.services.model_resolver import call_service_model
        raw = await call_service_model(
            category="memory_organization",
            system_prompt=SYNTHESIS_PROMPT,
            user_message=user_msg,
            max_tokens=400,
            temperature=0.2,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        result = json.loads(raw)
    except Exception as e:
        logger.warning(f"REM synthesis failed for node {node.id}: {e}")
        return None

    synthesis = result.get("synthesis")
    rationale = result.get("rationale", "")
    if not synthesis or not isinstance(synthesis, str) or len(synthesis.strip()) < 20:
        logger.debug(f"REM: no synthesis for node {node.id} — {rationale[:120]}")
        return None

    # Write to ProposalsStore.  Earns promotion via corroboration/use
    # signals — same path as any other proposal.
    try:
        from app.storage.proposals import get_proposals_store
        from app.models.memory import MemoryProposal, MemoryScope
        from app.utils.memory_extractor import _next_activity_count

        ps = get_proposals_store()
        # Tag union from source memories, capped at 4
        all_tags: List[str] = []
        for m in memories:
            for t in m.tags:
                if t not in all_tags:
                    all_tags.append(t)
        proposal = MemoryProposal(
            content=synthesis.strip(),
            layer="domain_context",
            tags=all_tags[:4],
            learned_from="rem_synthesis",
            scope=MemoryScope(domain_node=node.id),
        )
        # Carry the source ids for later relations.elaborates wiring
        # by tunneling through conversation_id as JSON.
        proposal.conversation_id = json.dumps({
            "rem_source_ids": [m.id for m in memories],
            "rem_rationale": rationale[:500],
        })
        activity = _next_activity_count()
        ps.add(proposal, activity_count=activity)
        logger.info(
            f"🌙 REM SYNTHESIS for {node.id} ({len(memories)} sources): "
            f"{synthesis[:80]}... | rationale: {rationale[:120]}"
        )
        return proposal.id
    except Exception as e:
        logger.warning(f"REM synthesis store-write failed: {e}")
        return None


# -- Staleness ------------------------------------------------------

async def detect_staleness(node, store) -> List[str]:
    """Run staleness detection on top-K importance memories in this node.

    Returns list of memory ids flipped to contested.  A memory is flipped
    only when (a) the LLM verdict is "false" AND (b) at least one other
    memory in the node contradicts it (text overlap on key tokens) — the
    contradiction gate prevents fabricated staleness.
    """
    from app.utils.memory_feedback import is_labile

    all_memories = []
    for mid in node.memory_refs:
        m = store.get(mid)
        if m and m.status == "active":
            all_memories.append(m)
    if len(all_memories) < _MATURITY_MIN_MEMORIES:
        return []

    candidates = []
    for m in all_memories:
        if (m.retrieval_loaded_count or 0) < 1:
            continue
        if is_labile(m.id):
            continue
        candidates.append(m)
    candidates.sort(key=lambda m: m.importance or 0, reverse=True)
    candidates = candidates[:_STALENESS_TOP_K]
    if not candidates:
        return []

    candidate_ids = {c.id for c in candidates}
    context_memories = [m for m in all_memories if m.id not in candidate_ids]
    if not context_memories:
        return []

    cand_lines = [f"- [{m.id}] ({m.layer}) {m.content}" for m in candidates]
    ctx_lines = [f"- ({m.layer}) {m.content}" for m in context_memories]
    user_msg = (
        f"DOMAIN: {node.handle}\n\n"
        f"CANDIDATES (judge each):\n" + "\n".join(cand_lines) + "\n\n"
        f"CONTEXT (other memories in this domain):\n" + "\n".join(ctx_lines)
    )

    try:
        from app.services.model_resolver import call_service_model
        raw = await call_service_model(
            category="memory_organization",
            system_prompt=STALENESS_PROMPT,
            user_message=user_msg,
            max_tokens=600,
            temperature=0.0,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        result = json.loads(raw)
    except Exception as e:
        logger.warning(f"REM staleness failed for node {node.id}: {e}")
        return []

    verdicts = result.get("verdicts", []) or []
    contested_ids: List[str] = []
    candidate_by_id = {c.id: c for c in candidates}
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        mid = v.get("id")
        verdict = (v.get("verdict") or "").lower()
        rationale = v.get("rationale", "")
        if verdict != "false" or mid not in candidate_by_id:
            continue
        target = candidate_by_id[mid]
        # Contradiction gate: at least one context memory must share
        # significant content overlap with the target (same topic) AND
        # be more recently created.
        target_words = {w.lower() for w in target.content.split() if len(w) > 4}
        contradicting = None
        for ctx_mem in context_memories:
            ctx_words = {w.lower() for w in ctx_mem.content.split() if len(w) > 4}
            overlap = len(target_words & ctx_words)
            if overlap < 3:
                continue
            try:
                t_target = time.mktime(time.strptime(target.created, "%Y-%m-%d"))
                t_ctx = time.mktime(time.strptime(ctx_mem.created, "%Y-%m-%d"))
                if t_ctx > t_target:
                    contradicting = ctx_mem
                    break
            except (ValueError, TypeError):
                continue
        if not contradicting:
            logger.info(
                f"REM: LLM marked {mid} stale but no contradicting evidence — "
                f"leaving status unchanged. Rationale: {rationale[:120]}"
            )
            continue
        target.status = "contested"
        store.save(target)
        contested_ids.append(mid)
        logger.info(
            f"🌙 REM CONTESTED {mid} (in node {node.id}) — superseded by "
            f"{contradicting.id}. Rationale: {rationale[:120]}"
        )
    return contested_ids


# -- Top-level orchestration ---------------------------------------

async def rem_phase(store) -> Dict[str, Any]:
    """Run synthesis and staleness on every mature mind-map node.

    Per-node errors are isolated; one failing node does not abort the
    rest.  Returns summary counts for the organize-history log.
    """
    nodes = store.list_mindmap_nodes()
    summary = {
        "nodes_evaluated": 0,
        "nodes_mature": 0,
        "syntheses_created": 0,
        "syntheses_skipped_cooldown": 0,
        "memories_contested": 0,
        "syntheses": [],
        "contested": [],
        "errors": [],
    }
    for node in nodes:
        summary["nodes_evaluated"] += 1
        try:
            mature, reason = _is_mature(node, store)
            if not mature:
                logger.debug(f"REM: skipping {node.id} — {reason}")
                continue
            summary["nodes_mature"] += 1

            if _should_synthesize(node, store):
                pid = await synthesize_node(node, store)
                if pid:
                    summary["syntheses_created"] += 1
                    summary["syntheses"].append({"node_id": node.id, "proposal_id": pid})
            else:
                summary["syntheses_skipped_cooldown"] += 1

            contested_ids = await detect_staleness(node, store)
            summary["memories_contested"] += len(contested_ids)
            for cid in contested_ids:
                summary["contested"].append({"node_id": node.id, "memory_id": cid})
        except Exception as e:
            logger.error(f"REM phase node {node.id} failed: {e}")
            summary["errors"].append({"node_id": node.id, "error": str(e)})
    if summary["syntheses_created"] or summary["memories_contested"]:
        logger.info(
            f"🌙 REM phase: {summary['nodes_mature']}/{summary['nodes_evaluated']} mature, "
            f"{summary['syntheses_created']} syntheses, "
            f"{summary['memories_contested']} contested"
        )
    return summary