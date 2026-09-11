"""
Memory organizer — LLM-powered clustering, relation extraction, and mind-map bootstrap.

This module solves the bootstrap problem: the mind-map requires existing nodes
for placement to work, but nothing ever creates the initial nodes.  The organizer
analyzes the full memory corpus and builds structure from scratch.

Operations:
  - cluster_memories: group memories into thematic domains via LLM
  - extract_relations: identify supports/contradicts/elaborates between memories
  - bootstrap_mindmap: create mind-map nodes from clusters
  - reorganize: full pipeline (cluster → place → relate → cross-link → divide)

Called from:
  - POST /api/v1/memory/organize (manual trigger)
  - run_post_save_maintenance (auto-trigger when orphan count exceeds threshold)
"""

import json
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import defaultdict

from app.utils.logging_utils import logger

# Auto-organize when this many memories lack a mind-map placement
AUTO_ORGANIZE_ORPHAN_THRESHOLD = 15

# Maximum memories per LLM clustering call
CLUSTER_BATCH_SIZE = 40

# Maximum memories per cleanup review call
CLEANUP_BATCH_SIZE = 30

# Maximum memories per relation extraction call
RELATION_BATCH_SIZE = 20

# ── §3 ORGANIZATION — global reorganize with fixed depth and occupancy ──
# Every node must hold at least this many memory_refs (roots count descendants);
# below it the node folds into its parent (children) or best sibling (roots).
MIN_NODE_OCCUPANCY = 2
# Hard bounds on the root (domain) count.  MIN_ROOTS is a floor for divide
# eagerness, never a splitting trigger; MAX_ROOTS is enforced by merging.
MAX_ROOTS = 24
MIN_ROOTS = 3
# Target ~ this many memories per node; drives N_target = round(active/this).
TARGET_MEMBERS_PER_NODE = 4
# Two roots merge when their normalized handle-word Jaccard >= this, OR their
# tag Jaccard >= this AND they share at least one non-generic handle word.
ROOT_MERGE_HANDLE_JACCARD = 0.5
ROOT_MERGE_TAG_JACCARD = 0.5
# Generic words dropped from the OVERLAP count (they still count for Jaccard
# denominators, so "Design System" and "Memory System" don't fuse on "system").
_GENERIC_HANDLE_WORDS = {
    "design", "system", "architecture", "memory", "memories",
    "management", "misc", "general",
}
# Lazily-created catch-all root for memories with no sibling overlap.
_GENERAL_ROOT_ID = "domain_general"
_GENERAL_ROOT_HANDLE = "General"


def _n_target(active_count: int) -> int:
    """Target TOTAL node count ≈ memories / TARGET_MEMBERS_PER_NODE, clamped to
    [MIN_ROOTS, MAX_ROOTS]."""
    return max(MIN_ROOTS, min(MAX_ROOTS,
                              round(active_count / TARGET_MEMBERS_PER_NODE)))


def _normalize_handle_words(handle: str) -> Set[str]:
    """Lowercase handle words, alnum-stripped, with a cheap plural fold
    (trailing 's' dropped from words of length >= 4: "Diagrams" ≈ "diagram")."""
    words: Set[str] = set()
    for w in (handle or "").lower().split():
        w = "".join(c for c in w if c.isalnum())
        if not w:
            continue
        if len(w) >= 4 and w.endswith("s"):
            w = w[:-1]
        words.add(w)
    return words


def _jaccard(a: Set[str], b: Set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0

CLUSTER_SYSTEM_PROMPT = """\
You are a knowledge organization system. Given a list of stored memories \
(facts, decisions, vocabulary, lessons), group them into thematic DOMAINS.

Rules:
- Create 3-12 domains depending on the breadth of topics.
- Each domain gets a concise handle (2-5 words, like "Network Architecture" or "AI Tooling").
- Each domain gets 2-4 lowercase tags that characterize it.
- Assign every memory to exactly one domain (by its ID).
- If existing domains are provided, prefer assigning to those over creating new ones. \
  Only create a new domain when no existing domain fits.
- Domains should be meaningful groupings, not just layer categories. \
  "Packet Routing Design" is good. "Architecture Memories" is bad.

Output a JSON object:
{
  "domains": [
    {
      "handle": "Domain Name",
      "tags": ["tag1", "tag2"],
      "memory_ids": ["m_abc", "m_def"]
    }
  ]
}

No markdown fences, no explanation. JSON only."""


RELATION_SYSTEM_PROMPT = """\
You are a knowledge relationship analyzer. Given a set of memories within the \
same domain, identify meaningful relationships between them.

Relationship types:
- supports: Memory A provides evidence or reinforcement for Memory B
- contradicts: Memory A conflicts with or supersedes Memory B
- elaborates: Memory A adds detail or nuance to Memory B
- depends_on: Memory A assumes or requires the knowledge in Memory B

Rules:
- Only identify relationships where the connection is substantive and useful.
- A memory can have multiple relationships.
- Prefer fewer, high-quality relationships over exhaustive weak ones.
- Contradictions are high-value — always identify these.

Output a JSON array of relationships:
[
  {"source": "m_abc", "target": "m_def", "type": "elaborates"},
  {"source": "m_ghi", "target": "m_abc", "type": "contradicts"}
]

No markdown fences, no explanation. JSON only. Empty array [] if no relationships."""


CLEANUP_SYSTEM_PROMPT = """\
You are a memory quality reviewer. Given a list of stored memories, identify \
any that should be REMOVED because they are:

1. Session artifacts: debugging notes, CSS fixes, TODO items, editing instructions \
   that only made sense during one conversation
2. Duplicates or near-duplicates: two memories saying the same thing in different words \
   (keep the better-worded one, remove the other)
3. Dangling references: memories that reference "the document", "the system", "the bug" \
   without naming what they refer to — making them useless out of context
4. Stale/obsolete: information that was likely true at one point but is probably outdated \
   (e.g. "currently working on X" from months ago)
5. Too vague to be useful: memories so generic they provide no actionable knowledge

For duplicates, also identify which memory to KEEP (the more complete or better-worded one) \
and which to MERGE into it (content from the duplicate that adds useful detail).

Output a JSON object:
{
  "remove": ["m_id1", "m_id2"],
  "merge": [
    {"keep": "m_id1", "absorb": "m_id2", "merged_content": "improved combined text"}
  ],
  "reasons": {"m_id1": "session artifact — CSS debugging note", "m_id2": "duplicate of m_id1"}
}

Rules:
- Be conservative. When in doubt, KEEP the memory.
- Only flag memories you are confident are junk or duplicates.
- Merging should improve clarity, not just concatenate.
- Empty arrays/objects are fine if nothing needs cleanup.

No markdown fences, no explanation. JSON only."""


async def cluster_memories(
    memories: List[Dict[str, Any]],
    existing_domains: Optional[List[Dict[str, Any]]] = None,
    batch_target: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Ask the service model to cluster memories into thematic domains.

    Returns list of domain dicts with 'handle', 'tags', 'memory_ids'.

    ``batch_target`` (§3.2) hints the number of domains to aim for in this
    batch and, together with the LIVE ``existing_domains`` inventory, biases
    the model toward reusing established domains instead of minting parallels.
    """
    if not memories:
        return []

    from app.memory.prompt import encode_memory_for_prompt
    mem_lines = []
    for m in memories:
        mem_lines.append(
            f"[{m['id']}] ({m.get('layer', '?')}) "
            + encode_memory_for_prompt(m.get('content', ''), m.get('tags', []))
        )

    user_msg = "MEMORIES TO ORGANIZE:\n" + "\n".join(mem_lines)

    if existing_domains:
        domain_lines = []
        for d in existing_domains:
            tags = ", ".join(d.get("tags", []))
            count = d.get("memory_count", 0)
            domain_lines.append(
                f"- {d.get('handle', '?')} (tags: {tags}, {count} existing memories)"
            )
        user_msg += "\n\nEXISTING DOMAINS (prefer these):\n" + "\n".join(domain_lines)

    if batch_target:
        user_msg += (
            f"\n\nAim for about {max(1, int(batch_target))} domains for this batch; "
            "strongly prefer existing domains."
        )

    try:
        from app.services.model_resolver import call_service_model
        raw = await call_service_model(
            category="memory_organization",
            system_prompt=CLUSTER_SYSTEM_PROMPT,
            user_message=user_msg,
            max_tokens=2048,
            temperature=0.3,
        )

        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(raw)
        domains = result.get("domains", [])

        if not isinstance(domains, list):
            logger.warning("Memory clustering: non-list domains response")
            return []

        logger.info(
            f"🗺️ Clustering: organized {len(memories)} memories into "
            f"{len(domains)} domains"
        )
        return domains

    except json.JSONDecodeError as e:
        logger.warning(f"Memory clustering: JSON parse failed: {e}")
        return []
    except Exception as e:
        logger.warning(f"Memory clustering failed: {e}")
        return []


async def extract_relations(
    memories: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Ask the service model to identify relationships between memories.

    Returns list of relation dicts with 'source', 'target', 'type'.
    """
    if len(memories) < 2:
        return []

    from app.memory.prompt import encode_memory_for_prompt
    mem_lines = [
        f"[{m['id']}] ({m.get('layer', '?')}) "
        + encode_memory_for_prompt(m.get('content', ''), m.get('tags', []))
        for m in memories
    ]
    user_msg = "MEMORIES:\n" + "\n".join(mem_lines)

    try:
        from app.services.model_resolver import call_service_model
        raw = await call_service_model(
            category="memory_organization",
            system_prompt=RELATION_SYSTEM_PROMPT,
            user_message=user_msg,
            max_tokens=1024,
            temperature=0.2,
        )

        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        relations = json.loads(raw)
        if not isinstance(relations, list):
            return []

        valid_types = {"supports", "contradicts", "elaborates", "depends_on"}
        valid_ids = {m["id"] for m in memories}
        validated = [
            r for r in relations
            if (r.get("type") in valid_types
                and r.get("source") in valid_ids
                and r.get("target") in valid_ids
                and r.get("source") != r.get("target"))
        ]

        logger.info(
            f"🔗 Relations: found {len(validated)} relationships "
            f"among {len(memories)} memories"
        )
        return validated

    except json.JSONDecodeError as e:
        logger.warning(f"Relation extraction: JSON parse failed: {e}")
        return []
    except Exception as e:
        logger.warning(f"Relation extraction failed: {e}")
        return []


async def cleanup_corpus(store) -> Dict[str, Any]:
    """Review existing memories and remove/merge junk.

    Runs an LLM pass over the full corpus in batches to identify
    session artifacts, duplicates, dangling references, and stale entries.

    Returns stats on what was cleaned up.
    """
    all_memories = store.list_memories(status="active")
    if len(all_memories) < 5:
        return {"status": "too_few", "removed": 0, "merged": 0}

    mem_dicts = [m.model_dump() for m in all_memories]
    total_removed = 0
    total_merged = 0
    all_reasons: Dict[str, str] = {}

    for i in range(0, len(mem_dicts), CLEANUP_BATCH_SIZE):
        batch = mem_dicts[i:i + CLEANUP_BATCH_SIZE]
        from app.memory.prompt import encode_memory_for_prompt
        mem_lines = [
            f"[{m['id']}] ({m.get('layer', '?')}) created={m.get('created', '?')} "
            f"last_accessed={m.get('last_accessed', '?')} "
            f"importance={m.get('importance', 0.5):.2f}\n"
            f"  {encode_memory_for_prompt(m.get('content', ''), m.get('tags', []))}"
            for m in batch
        ]
        user_msg = "MEMORIES TO REVIEW:\n\n" + "\n\n".join(mem_lines)

        try:
            from app.services.model_resolver import call_service_model
            raw = await call_service_model(
                category="memory_organization",
                system_prompt=CLEANUP_SYSTEM_PROMPT,
                user_message=user_msg,
                max_tokens=2048,
                temperature=0.1,
            )

            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            result = json.loads(raw)

            # Process merges first (before removing, since merge targets may be in remove list)
            for merge_op in result.get("merge", []):
                keep_id = merge_op.get("keep")
                absorb_id = merge_op.get("absorb")
                merged_content = merge_op.get("merged_content", "")
                if keep_id and absorb_id and merged_content:
                    keep_mem = store.get(keep_id)
                    if keep_mem:
                        keep_mem.content = merged_content
                        absorb_mem = store.get(absorb_id)
                        if absorb_mem:
                            keep_mem.tags = list(set(keep_mem.tags + absorb_mem.tags))
                        store.save(keep_mem)
                        store.delete(absorb_id)
                        total_merged += 1
                        logger.info(
                            f"🧹 MERGE: {absorb_id} → {keep_id}: {merged_content[:60]}"
                        )

            # Process removals
            merged_ids = {m.get("absorb") for m in result.get("merge", []) if m.get("absorb")}
            for mem_id in result.get("remove", []):
                if mem_id in merged_ids:
                    continue  # Already handled by merge
                reason = result.get("reasons", {}).get(mem_id, "flagged by cleanup")
                if store.delete(mem_id):
                    total_removed += 1
                    all_reasons[mem_id] = reason
                    logger.info(f"🧹 REMOVE: {mem_id} — {reason}")

        except json.JSONDecodeError as e:
            logger.warning(f"Cleanup batch JSON parse failed: {e}")
        except Exception as e:
            logger.warning(f"Cleanup batch failed: {e}")

    logger.info(
        f"🧹 Corpus cleanup: {total_removed} removed, {total_merged} merged "
        f"from {len(all_memories)} total"
    )
    return {
        "status": "success",
        "reviewed": len(all_memories),
        "removed": total_removed,
        "merged": total_merged,
        "reasons": all_reasons,
    }


async def bootstrap_mindmap(store) -> Dict[str, Any]:
    """Create mind-map structure from the current memory corpus.

    Clusters all unplaced memories into domains, creates nodes,
    and places memories under them.
    """
    from app.models.memory import MindMapNode

    all_memories = store.list_memories(status="active")
    if not all_memories:
        return {"status": "empty", "message": "No memories to organize"}

    existing_nodes = store.list_mindmap_nodes()
    existing_node_map = {n.id: n for n in existing_nodes}

    # Find orphan memories
    all_refs: Set[str] = set()
    for n in existing_nodes:
        all_refs.update(n.memory_refs)
    orphans = [m for m in all_memories if m.id not in all_refs]

    if not orphans and existing_nodes:
        return {"status": "organized", "message": "All memories are already placed"}

    to_organize = orphans if existing_nodes else all_memories
    mem_dicts = [m.model_dump() for m in to_organize]

    nodes_created = 0
    nodes_updated = 0
    memories_placed = 0

    # §3.2: cluster in batches, but process each batch's domains IMMEDIATELY and
    # refresh the live root inventory between batches, so a domain minted by an
    # earlier batch is offered to (and reused by) later batches instead of a
    # parallel root being created.  ``existing_nodes``/``existing_node_map`` are
    # mutated in place by ``_place_domain`` so they stay live.
    per_batch_target = max(1, CLUSTER_BATCH_SIZE // TARGET_MEMBERS_PER_NODE)
    saw_domains = False
    for i in range(0, len(mem_dicts), CLUSTER_BATCH_SIZE):
        batch = mem_dicts[i:i + CLUSTER_BATCH_SIZE]
        # Refresh the domain inventory from the LIVE root set every batch.
        existing_domain_info = [
            {"id": n.id, "handle": n.handle, "tags": n.tags,
             "memory_count": len(n.memory_refs)}
            for n in existing_nodes if n.parent is None
        ]
        batch_hint = max(1, len(batch) // TARGET_MEMBERS_PER_NODE)
        domains = await cluster_memories(
            batch, existing_domain_info or None, batch_target=batch_hint)
        for domain in domains:
            saw_domains = True
            c, u, p = _place_domain(store, domain, existing_nodes,
                                    existing_node_map)
            nodes_created += c
            nodes_updated += u
            memories_placed += p

    if not saw_domains:
        return {"status": "failed", "message": "Clustering returned no results"}

    logger.info(
        f"🗺️ Bootstrap: {nodes_created} created, {nodes_updated} updated, "
        f"{memories_placed} placed"
    )
    return {
        "status": "success",
        "domains_created": nodes_created,
        "domains_updated": nodes_updated,
        "memories_placed": memories_placed,
    }


def _place_domain(store, domain, existing_nodes, existing_node_map):
    """Place one clustered domain into the live mind-map, reusing a matching
    existing root when one exists (§3.2/§3.3).  Mutates ``existing_nodes`` and
    ``existing_node_map`` in place so subsequent calls see this root.

    Returns ``(nodes_created, nodes_updated, memories_placed)``.
    """
    from app.models.memory import MindMapNode

    handle = domain.get("handle", "Unknown")
    tags = domain.get("tags", [])
    memory_ids = list(domain.get("memory_ids", []))
    if not memory_ids:
        return (0, 0, 0)

    created = updated = placed = 0
    matched_node_id = _find_matching_node(handle, tags, existing_nodes)

    if matched_node_id and matched_node_id in existing_node_map:
        node = existing_node_map[matched_node_id]
        node.memory_refs = list(set(node.memory_refs) | set(memory_ids))
        node.tags = list(set(node.tags) | set(tags))
        store.save_mindmap_node(node)
        updated = 1
        target_id = matched_node_id
    elif (existing := store.get_mindmap_node(_make_node_id(handle))) is not None \
            and existing.parent is None \
            and " ".join(existing.handle.lower().split()) == " ".join(handle.lower().split()):
        # Exact-handle root the fuzzy matcher missed — merge, don't twin.
        existing.memory_refs = list(set(existing.memory_refs) | set(memory_ids))
        existing.tags = list(set(existing.tags) | set(tags))
        store.save_mindmap_node(existing)
        existing_node_map[existing.id] = existing
        if existing not in existing_nodes:
            existing_nodes.append(existing)
        updated = 1
        target_id = existing.id
    else:
        node_id = _make_node_id(handle)
        suffix = 0
        while store.get_mindmap_node(node_id):
            suffix += 1
            node_id = f"{_make_node_id(handle)}_{suffix}"
        node = MindMapNode(id=node_id, handle=handle, parent=None,
                           tags=tags, memory_refs=memory_ids)
        store.save_mindmap_node(node)
        existing_node_map[node_id] = node
        existing_nodes.append(node)
        created = 1
        target_id = node_id

    for mid in memory_ids:
        mem = store.get(mid)
        if mem:
            mem.scope.domain_node = target_id
            store.save(mem)
            placed += 1
    return (created, updated, placed)


async def extract_all_relations(store) -> Dict[str, Any]:
    """Extract relations for all memories grouped by domain."""
    nodes = store.list_mindmap_nodes()
    if not nodes:
        return {"status": "no_nodes", "relations_found": 0}

    total_relations = 0
    for node in nodes:
        if len(node.memory_refs) < 2:
            continue
        memories = [store.get(mid) for mid in node.memory_refs]
        memories = [m for m in memories if m]
        if len(memories) < 2:
            continue

        mem_dicts = [m.model_dump() for m in memories]
        for i in range(0, len(mem_dicts), RELATION_BATCH_SIZE):
            batch = mem_dicts[i:i + RELATION_BATCH_SIZE]
            relations = await extract_relations(batch)
            for rel in relations:
                source_mem = store.get(rel["source"])
                if source_mem:
                    rt = rel["type"]
                    if rt not in source_mem.relations:
                        source_mem.relations[rt] = []
                    if rel["target"] not in source_mem.relations[rt]:
                        source_mem.relations[rt].append(rel["target"])
                        store.save(source_mem)
                        total_relations += 1

    logger.info(f"🔗 Relation extraction: {total_relations} relations stored")
    return {"status": "success", "relations_found": total_relations}


async def reorganize(store=None) -> Dict[str, Any]:
    """Full reorganization: cluster → place → relate → cross-link → divide."""
    if store is None:
        from app.storage.memory import get_memory_storage
        store = get_memory_storage()

    results = {
        "cleanup": {},
        "bootstrap": {},
        "consolidate": {},
        "relations": {},
        "cross_links": [],
        "divisions": [],
        "rem": {},
    }

    # Phase 0: Clean up junk before organizing
    try:
        results["cleanup"] = await cleanup_corpus(store)
    except Exception as e:
        logger.error(f"Corpus cleanup failed: {e}")
        results["cleanup"] = {"status": "error", "error": str(e)}

    # Phase 1: Cluster and place
    try:
        results["bootstrap"] = await bootstrap_mindmap(store)
    except Exception as e:
        logger.error(f"Bootstrap failed: {e}")
        results["bootstrap"] = {"status": "error", "error": str(e)}

    # Phase 1.5: Consolidation — deterministic, no LLM calls.  Enforces the
    # §3 invariants (depth <= 2, occupancy >= 2, bounded root count, single
    # parent, zero empties) that bootstrap alone cannot guarantee.
    try:
        results["consolidate"] = consolidate_mindmap(store)
    except Exception as e:
        logger.error(f"Consolidation failed: {e}")
        results["consolidate"] = {"status": "error", "error": str(e)}

    # Phase 2: Extract relations
    try:
        results["relations"] = await extract_all_relations(store)
    except Exception as e:
        logger.error(f"Relation extraction failed: {e}")
        results["relations"] = {"status": "error", "error": str(e)}

    # Phase 3: REM — synthesis + staleness on mature nodes
    try:
        from app.memory.rem import rem_phase
        results["rem"] = await rem_phase(store)
    except Exception as e:
        logger.error(f"REM phase failed: {e}")
        results["rem"] = {"status": "error", "error": str(e)}

    from app.memory.maintenance import (
        discover_cross_links, discover_cross_links_by_embedding, maybe_divide_node,
        stamp_interference_scores,
    )
    try:
        # PenPal #53: load the node list once and share a branch-id cache across
        # the whole pass so each discover_cross_links call is O(candidates), not
        # O(n) disk reads — turning the loop from O(n^2) I/O into O(n).
        _nodes = store.list_mindmap_nodes()
        _branch_cache: Dict[str, Any] = {}
        for node in _nodes:
            results["cross_links"].extend(
                discover_cross_links(store, node.id, node_list=_nodes,
                                     branch_cache=_branch_cache))
    except Exception as e:
        logger.error(f"Cross-link discovery failed: {e}")

    # Option C: embedding-centroid cross-links — catches semantically related
    # domains that share no literal tags.  Shared centroid cache makes this
    # O(N) centroid computations instead of O(N^2) across the node loop.
    try:
        _centroids: Dict[str, Any] = {}
        for node in store.list_mindmap_nodes():
            results["cross_links"].extend(
                discover_cross_links_by_embedding(store, node.id, _centroids))
    except Exception as e:
        logger.error(f"Embedding cross-link discovery failed: {e}")

    try:
        for node in store.list_mindmap_nodes():
            results["divisions"].extend(maybe_divide_node(store, node.id))
    except Exception as e:
        logger.error(f"Cell division failed: {e}")

    # Interference (redundancy) scoring — stamps Memory.interference_score so
    # the opportunistic-decay gate can age out redundant memories on the
    # accelerated window.  Embedding-native; no-ops when embeddings disabled.
    try:
        results["interference"] = stamp_interference_scores(store)
    except Exception as e:
        logger.error(f"Interference scoring failed: {e}")
        results["interference"] = {"status": "error", "error": str(e)}

    # Append summary to bounded history log for the Memory Browser UI.
    try:
        from app.memory.organize_history import append_organize_result
        append_organize_result(results)
    except Exception as e:
        logger.warning(f"organize_history append failed (non-fatal): {e}")

    return results


def should_auto_organize(store) -> bool:
    """Trigger auto-organization on structure debt, not just orphans (§3.5).

    Fires when ANY of: orphan count reaches the threshold (original rule);
    the root count has exploded past ``max(12, 2 * N_target)``; or more than
    30% of nodes are hollow (0 refs and 0 children).
    """
    nodes = store.list_mindmap_nodes()
    memories = store.list_memories(status="active")
    if not nodes:
        return len(memories) >= AUTO_ORGANIZE_ORPHAN_THRESHOLD

    all_refs: Set[str] = set()
    for n in nodes:
        all_refs.update(n.memory_refs)
    orphan_count = sum(1 for m in memories if m.id not in all_refs)
    if orphan_count >= AUTO_ORGANIZE_ORPHAN_THRESHOLD:
        return True

    roots = sum(1 for n in nodes if n.parent is None)
    n_target = _n_target(len(memories))
    if roots > 0 and roots > max(12, 2 * n_target):
        return True

    empty = sum(1 for n in nodes if not n.memory_refs and not n.children)
    empty_fraction = empty / len(nodes) if nodes else 0.0
    if empty_fraction > 0.3:
        return True

    return False


def _find_matching_node(handle, tags, existing_nodes):
    """Find an existing root node matching by handle words + tag overlap (§3.3).

    Acceptance widened beyond the original ``score >= 4``: a root also matches
    when its normalized handle-word Jaccard >= ROOT_MERGE_HANDLE_JACCARD, or its
    tag Jaccard >= ROOT_MERGE_TAG_JACCARD AND it shares >= 1 non-generic handle
    word.  Generic words are dropped from the overlap COUNT but kept in the
    Jaccard denominators.  Best match wins by score, Jaccard as tiebreak.
    """
    if not existing_nodes:
        return None
    handle_words = _normalize_handle_words(handle)
    content_words = handle_words - _GENERIC_HANDLE_WORDS
    tag_set = set(t.lower() for t in tags)
    candidates: List[Tuple[int, float, str]] = []
    for node in existing_nodes:
        if node.parent is not None:
            continue
        node_words = _normalize_handle_words(node.handle)
        node_content = node_words - _GENERIC_HANDLE_WORDS
        node_tags = set(t.lower() for t in node.tags)
        shared_content = content_words & node_content
        score = len(shared_content) * 2 + len(tag_set & node_tags) * 3
        h_jac = _jaccard(handle_words, node_words)
        t_jac = _jaccard(tag_set, node_tags)
        accept = (
            score >= 4
            or h_jac >= ROOT_MERGE_HANDLE_JACCARD
            or (t_jac >= ROOT_MERGE_TAG_JACCARD and len(shared_content) >= 1)
        )
        if accept:
            candidates.append((score, max(h_jac, t_jac), node.id))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]


def _make_node_id(handle: str) -> str:
    """Generate a stable node ID from a domain handle."""
    clean = "".join(c if c.isalnum() or c == " " else "" for c in handle.lower().strip())
    clean = clean.replace(" ", "_")[:30]
    return f"domain_{clean}" if clean else f"domain_{int(time.time())}"


# ── §3.4 Consolidation pass (deterministic, no LLM) ─────────────────────

def _descendant_ref_count(store, node, node_by_id) -> int:
    """Total memory_refs on a node plus its direct children (depth <= 2)."""
    total = len(node.memory_refs)
    for cid in node.children:
        child = node_by_id.get(cid)
        if child is not None:
            total += len(child.memory_refs)
    return total


def _repoint_scope(store, old_id: str, new_id: str) -> int:
    """Re-point every active memory whose ``scope.domain_node`` is ``old_id``
    to ``new_id``.  Returns the number of memories touched."""
    changed = 0
    for m in store.list_memories(status="active"):
        if m.scope.domain_node == old_id:
            m.scope.domain_node = new_id
            store.save(m)
            changed += 1
    return changed


def _merge_roots(store, survivor_id: str, absorbed_id: str) -> None:
    """Fold root ``absorbed_id`` into ``survivor_id``: union refs/tags/
    cross_links, reparent the absorbed root's children onto the survivor,
    re-point memory scopes, then delete the absorbed node."""
    surv = store.get_mindmap_node(survivor_id)
    other = store.get_mindmap_node(absorbed_id)
    if surv is None or other is None or survivor_id == absorbed_id:
        return
    surv.memory_refs = list(dict.fromkeys(surv.memory_refs + other.memory_refs))
    surv.tags = list(dict.fromkeys(surv.tags + other.tags))
    surv.cross_links = [
        x for x in dict.fromkeys(surv.cross_links + other.cross_links)
        if x not in (survivor_id, absorbed_id)
    ]
    for cid in list(other.children):
        child = store.get_mindmap_node(cid)
        if child is not None:
            child.parent = survivor_id
            store.save_mindmap_node(child)
            if cid not in surv.children:
                surv.children.append(cid)
    surv.access_count = max(surv.access_count, other.access_count)
    surv.last_accessed = max(surv.last_accessed or "", other.last_accessed or "")
    # Empty the absorbed node's own refs/children so delete_mindmap_node has
    # nothing left to reparent to the survivor's (None) grandparent.
    other.children = []
    other.memory_refs = []
    store.save_mindmap_node(surv)
    store.save_mindmap_node(other)
    _repoint_scope(store, absorbed_id, survivor_id)
    store.delete_mindmap_node(absorbed_id)


def _root_merge_qualifies(a, b) -> bool:
    """§3.3 similarity gate between two roots."""
    aw, bw = _normalize_handle_words(a.handle), _normalize_handle_words(b.handle)
    at = set(t.lower() for t in a.tags)
    bt = set(t.lower() for t in b.tags)
    h_jac = _jaccard(aw, bw)
    t_jac = _jaccard(at, bt)
    shared_content = (aw - _GENERIC_HANDLE_WORDS) & (bw - _GENERIC_HANDLE_WORDS)
    return (h_jac >= ROOT_MERGE_HANDLE_JACCARD
            or (t_jac >= ROOT_MERGE_TAG_JACCARD and len(shared_content) >= 1))


def _pick_survivor(store, a, b, node_by_id):
    """Survivor = larger (self+descendant) ref count; tie -> smaller id."""
    sa = _descendant_ref_count(store, a, node_by_id)
    sb = _descendant_ref_count(store, b, node_by_id)
    if sa > sb or (sa == sb and a.id <= b.id):
        return a.id, b.id
    return b.id, a.id


def _merge_similar_roots(store) -> int:
    """Merge every root pair meeting the §3.3 Jaccard criterion, iterating to
    a fixed point.  Deterministic."""
    merged = 0
    while True:
        all_nodes = store.list_mindmap_nodes()
        node_by_id = {n.id: n for n in all_nodes}
        roots = [n for n in all_nodes if n.parent is None]
        pair = None
        for i in range(len(roots)):
            for j in range(i + 1, len(roots)):
                if _root_merge_qualifies(roots[i], roots[j]):
                    pair = (roots[i], roots[j])
                    break
            if pair:
                break
        if pair is None:
            break
        surv_id, absorbed_id = _pick_survivor(store, pair[0], pair[1], node_by_id)
        _merge_roots(store, surv_id, absorbed_id)
        merged += 1
    return merged


def _merge_similar_roots_by_embedding(store) -> int:
    """Optional embedding improvement (§3.4 step 2): merge root pairs whose
    member-centroid cosine >= 0.80.  No-op (returns 0) when embeddings are
    disabled or unavailable."""
    try:
        from app.services.embedding_service import (
            get_embedding_provider, get_embedding_cache, NoopProvider,
        )
        provider = get_embedding_provider()
        if isinstance(provider, NoopProvider):
            return 0
        cache = get_embedding_cache()
        from app.memory.maintenance import _node_centroid
        import numpy as np
    except Exception:
        return 0

    merged = 0
    while True:
        all_nodes = store.list_mindmap_nodes()
        node_by_id = {n.id: n for n in all_nodes}
        roots = [n for n in all_nodes if n.parent is None]
        centroids = {}
        for r in roots:
            try:
                centroids[r.id] = _node_centroid(cache, r)
            except Exception:
                centroids[r.id] = None
        pair = None
        for i in range(len(roots)):
            ci = centroids.get(roots[i].id)
            if ci is None:
                continue
            for j in range(i + 1, len(roots)):
                cj = centroids.get(roots[j].id)
                if cj is None:
                    continue
                if float(np.dot(ci, cj)) >= 0.80:
                    pair = (roots[i], roots[j])
                    break
            if pair:
                break
        if pair is None:
            break
        surv_id, absorbed_id = _pick_survivor(store, pair[0], pair[1], node_by_id)
        _merge_roots(store, surv_id, absorbed_id)
        merged += 1
    return merged


def _ensure_general_root(store) -> str:
    """Return the catch-all root id, creating it if absent."""
    node = store.get_mindmap_node(_GENERAL_ROOT_ID)
    if node is None:
        from app.models.memory import MindMapNode
        node = MindMapNode(id=_GENERAL_ROOT_ID, handle=_GENERAL_ROOT_HANDLE,
                           parent=None, tags=[], memory_refs=[])
        store.save_mindmap_node(node)
    return _GENERAL_ROOT_ID


def _best_sibling(victim, siblings):
    """Best sibling to fold ``victim`` into: highest tag-Jaccard, tiebreak
    handle overlap then larger root.  None when EVERY sibling scores 0 on both
    tags and non-generic handle words."""
    vt = set(t.lower() for t in victim.tags)
    vw = _normalize_handle_words(victim.handle) - _GENERIC_HANDLE_WORDS
    best, best_key = None, None
    for s in siblings:
        st = set(t.lower() for t in s.tags)
        sw = _normalize_handle_words(s.handle) - _GENERIC_HANDLE_WORDS
        t_jac = _jaccard(vt, st)
        h_overlap = len(vw & sw)
        if t_jac <= 0 and h_overlap <= 0:
            continue
        key = (t_jac, h_overlap, len(s.memory_refs))
        if best_key is None or key > best_key:
            best_key, best = key, s
    return best


def _fold_low_occupancy(store) -> Tuple[int, int]:
    """§3.4 step 3.  Fold under-occupied children into their parent and
    under-occupied roots into the best sibling (or the general root).
    Returns (children_folded, roots_folded)."""
    children_folded = 0
    roots_folded = 0

    # Children: any non-root below occupancy folds refs up into its parent.
    for node in [n for n in store.list_mindmap_nodes() if n.parent is not None]:
        cur = store.get_mindmap_node(node.id)
        if cur is None or cur.parent is None:
            continue
        if len(cur.memory_refs) >= MIN_NODE_OCCUPANCY:
            continue
        parent = store.get_mindmap_node(cur.parent)
        if parent is None:
            continue
        parent.memory_refs = list(dict.fromkeys(parent.memory_refs + cur.memory_refs))
        parent.tags = list(dict.fromkeys(parent.tags + cur.tags))
        store.save_mindmap_node(parent)
        _repoint_scope(store, cur.id, parent.id)
        cur.memory_refs = []
        cur.children = []
        store.save_mindmap_node(cur)
        store.delete_mindmap_node(cur.id)
        children_folded += 1

    # Roots: total (self + descendants) below occupancy folds into a sibling.
    while True:
        all_nodes = store.list_mindmap_nodes()
        node_by_id = {n.id: n for n in all_nodes}
        roots = [n for n in all_nodes if n.parent is None]
        victim = None
        for r in roots:
            if r.id == _GENERAL_ROOT_ID:
                continue  # catch-all is exempt from folding
            if _descendant_ref_count(store, r, node_by_id) < MIN_NODE_OCCUPANCY:
                victim = r
                break
        if victim is None:
            break
        siblings = [r for r in roots if r.id != victim.id]
        target = _best_sibling(victim, siblings)
        target_id = target.id if target is not None else _ensure_general_root(store)
        if target_id == victim.id:
            break
        _merge_roots(store, target_id, victim.id)
        roots_folded += 1
    return children_folded, roots_folded


def _enforce_root_bound(store) -> int:
    """§3.4 step 4.  While root count > MAX_ROOTS, merge the most-similar root
    pair (max tag-Jaccard, tiebreak smallest combined size) regardless of the
    similarity threshold.  Returns the number of merges performed."""
    merges = 0
    while True:
        all_nodes = store.list_mindmap_nodes()
        node_by_id = {n.id: n for n in all_nodes}
        roots = [n for n in all_nodes if n.parent is None]
        if len(roots) <= MAX_ROOTS:
            break
        best_pair, best_key = None, None
        for i in range(len(roots)):
            at = set(t.lower() for t in roots[i].tags)
            for j in range(i + 1, len(roots)):
                bt = set(t.lower() for t in roots[j].tags)
                t_jac = _jaccard(at, bt)
                combined = (_descendant_ref_count(store, roots[i], node_by_id)
                            + _descendant_ref_count(store, roots[j], node_by_id))
                key = (t_jac, -combined)
                if best_key is None or key > best_key:
                    best_key, best_pair = key, (roots[i], roots[j])
        if best_pair is None:
            break
        surv_id, absorbed_id = _pick_survivor(store, best_pair[0], best_pair[1],
                                              node_by_id)
        _merge_roots(store, surv_id, absorbed_id)
        merges += 1
    return merges


def _enforce_single_parent(store) -> int:
    """Guarantee every memory is referenced by exactly one node, preferring the
    node named by its ``scope.domain_node`` (else the lexicographically-first
    holder).  Also re-points ``scope.domain_node`` at the winning owner.
    Returns the number of nodes whose ref list was rewritten."""
    nodes = store.list_mindmap_nodes()
    scope_by_mem = {m.id: m.scope.domain_node
                    for m in store.list_memories(status="active")}
    placements: Dict[str, List[str]] = defaultdict(list)
    for n in nodes:
        for mid in n.memory_refs:
            if n.id not in placements[mid]:
                placements[mid].append(n.id)

    owners: Dict[str, str] = {}
    for mid, node_ids in placements.items():
        if len(node_ids) == 1:
            owners[mid] = node_ids[0]
            continue
        pref = scope_by_mem.get(mid)
        owners[mid] = pref if pref in node_ids else sorted(node_ids)[0]

    fixed = 0
    for n in nodes:
        new_refs = list(dict.fromkeys(
            mid for mid in n.memory_refs if owners.get(mid) == n.id))
        if new_refs != n.memory_refs:
            n.memory_refs = new_refs
            store.save_mindmap_node(n)
            fixed += 1

    for m in store.list_memories(status="active"):
        owner = owners.get(m.id)
        if owner and m.scope.domain_node != owner:
            m.scope.domain_node = owner
            store.save(m)
    return fixed


def consolidate_mindmap(store) -> Dict[str, Any]:
    """Deterministic Phase 1.5 (§3.4).  Runs on every ``reorganize`` after
    bootstrap; enforces the §3.1 invariants without any LLM call:

      1. ``store.repair_mindmap()`` — dangling refs, identical-handle root
         merge, unplaced placement, empty-leaf prune.
      2. similar-root merge (keyword/tag Jaccard), then an optional
         embedding-centroid merge (no-op when embeddings disabled).
      3. occupancy fold (children -> parent, small roots -> sibling/general).
      4. hard root-bound enforcement (root count <= MAX_ROOTS).
      5. single-parent enforcement + a final repair pass.
    """
    stats: Dict[str, Any] = {
        "status": "success", "repaired": {}, "roots_merged": 0,
        "roots_merged_embedding": 0, "children_folded": 0, "roots_folded": 0,
        "bound_merges": 0, "reparented": 0,
    }
    try:
        stats["repaired"] = store.repair_mindmap()
    except Exception as e:
        logger.warning(f"consolidate: repair_mindmap failed: {e}")

    stats["roots_merged"] = _merge_similar_roots(store)
    try:
        stats["roots_merged_embedding"] = _merge_similar_roots_by_embedding(store)
    except Exception as e:
        logger.debug(f"consolidate: embedding merge skipped: {e}")

    cf, rf = _fold_low_occupancy(store)
    stats["children_folded"] = cf
    stats["roots_folded"] = rf

    stats["bound_merges"] = _enforce_root_bound(store)
    stats["reparented"] = _enforce_single_parent(store)

    # De-duplicating double-parented memories above can drop a node below
    # occupancy; fold once more so the occupancy invariant holds post-dedup.
    cf2, rf2 = _fold_low_occupancy(store)
    stats["children_folded"] += cf2
    stats["roots_folded"] += rf2

    try:
        store.repair_mindmap()
    except Exception as e:
        logger.debug(f"consolidate: final repair skipped: {e}")

    # Final structure summary for diagnostics.
    nodes = store.list_mindmap_nodes()
    roots = [n for n in nodes if n.parent is None]
    stats["node_count"] = len(nodes)
    stats["root_count"] = len(roots)
    stats["empty_nodes"] = sum(
        1 for n in nodes if not n.memory_refs and not n.children)
    logger.info(
        f"🧭 Consolidate: {stats['node_count']} nodes / {stats['root_count']} "
        f"roots / {stats['empty_nodes']} empty "
        f"(merged {stats['roots_merged']}, folded {cf}c/{rf}r, "
        f"bound {stats['bound_merges']})"
    )
    return stats
