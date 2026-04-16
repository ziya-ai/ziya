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
from typing import Any, Dict, List, Optional, Set
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
) -> List[Dict[str, Any]]:
    """Ask the service model to cluster memories into thematic domains.

    Returns list of domain dicts with 'handle', 'tags', 'memory_ids'.
    """
    if not memories:
        return []

    mem_lines = []
    for m in memories:
        tags = ", ".join(m.get("tags", []))
        mem_lines.append(
            f"[{m['id']}] ({m.get('layer', '?')}) {m.get('content', '')}"
            + (f"  tags: {tags}" if tags else "")
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

    mem_lines = [
        f"[{m['id']}] ({m.get('layer', '?')}) {m.get('content', '')}"
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
        mem_lines = [
            f"[{m['id']}] ({m.get('layer', '?')}) created={m.get('created', '?')} "
            f"last_accessed={m.get('last_accessed', '?')} importance={m.get('importance', 0.5):.2f}\n"
            f"  {m.get('content', '')}\n"
            f"  tags: {', '.join(m.get('tags', []))}"
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

    # Build existing domain info for the model
    existing_domain_info = [
        {"id": n.id, "handle": n.handle, "tags": n.tags,
         "memory_count": len(n.memory_refs)}
        for n in existing_nodes if n.parent is None
    ]

    to_organize = orphans if existing_nodes else all_memories
    mem_dicts = [m.model_dump() for m in to_organize]

    # Cluster in batches
    all_domains: List[Dict[str, Any]] = []
    for i in range(0, len(mem_dicts), CLUSTER_BATCH_SIZE):
        batch = mem_dicts[i:i + CLUSTER_BATCH_SIZE]
        domains = await cluster_memories(batch, existing_domain_info or None)
        all_domains.extend(domains)

    if not all_domains:
        return {"status": "failed", "message": "Clustering returned no results"}

    # Merge domains with same handle across batches
    merged: Dict[str, Dict] = {}
    for d in all_domains:
        handle = d.get("handle", "Unknown")
        if handle in merged:
            merged[handle]["memory_ids"].extend(d.get("memory_ids", []))
            merged[handle]["tags"] = list(
                set(merged[handle]["tags"]) | set(d.get("tags", []))
            )
        else:
            merged[handle] = {
                "handle": handle,
                "tags": d.get("tags", []),
                "memory_ids": list(d.get("memory_ids", [])),
            }

    nodes_created = 0
    nodes_updated = 0
    memories_placed = 0

    for domain in merged.values():
        handle = domain["handle"]
        tags = domain.get("tags", [])
        memory_ids = domain.get("memory_ids", [])
        if not memory_ids:
            continue

        # Check for a matching existing domain
        matched_node_id = _find_matching_node(handle, tags, existing_nodes)

        if matched_node_id and matched_node_id in existing_node_map:
            node = existing_node_map[matched_node_id]
            existing_refs = set(node.memory_refs)
            node.memory_refs = list(existing_refs | set(memory_ids))
            node.tags = list(set(node.tags) | set(tags))
            store.save_mindmap_node(node)
            nodes_updated += 1
            target_id = matched_node_id
        else:
            node_id = _make_node_id(handle)
            suffix = 0
            while store.get_mindmap_node(node_id):
                suffix += 1
                node_id = f"{_make_node_id(handle)}_{suffix}"

            node = MindMapNode(
                id=node_id, handle=handle, parent=None,
                tags=tags, memory_refs=memory_ids,
            )
            store.save_mindmap_node(node)
            existing_node_map[node_id] = node
            existing_nodes.append(node)
            nodes_created += 1
            target_id = node_id

        for mid in memory_ids:
            mem = store.get(mid)
            if mem:
                mem.scope.domain_node = target_id
                store.save(mem)
                memories_placed += 1

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

    results = {"cleanup": {}, "bootstrap": {}, "relations": {}, "cross_links": [], "divisions": []}

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

    # Phase 2: Extract relations
    try:
        results["relations"] = await extract_all_relations(store)
    except Exception as e:
        logger.error(f"Relation extraction failed: {e}")
        results["relations"] = {"status": "error", "error": str(e)}

    from app.utils.memory_maintenance import discover_cross_links, maybe_divide_node
    try:
        for node in store.list_mindmap_nodes():
            results["cross_links"].extend(discover_cross_links(store, node.id))
    except Exception as e:
        logger.error(f"Cross-link discovery failed: {e}")

    try:
        for node in store.list_mindmap_nodes():
            results["divisions"].extend(maybe_divide_node(store, node.id))
    except Exception as e:
        logger.error(f"Cell division failed: {e}")

    return results


def should_auto_organize(store) -> bool:
    """Check if auto-organization should trigger based on orphan count."""
    nodes = store.list_mindmap_nodes()
    if not nodes:
        memories = store.list_memories(status="active")
        return len(memories) >= AUTO_ORGANIZE_ORPHAN_THRESHOLD

    all_refs: Set[str] = set()
    for n in nodes:
        all_refs.update(n.memory_refs)
    memories = store.list_memories(status="active")
    orphan_count = sum(1 for m in memories if m.id not in all_refs)
    return orphan_count >= AUTO_ORGANIZE_ORPHAN_THRESHOLD


def _find_matching_node(handle, tags, existing_nodes):
    """Find an existing root node matching by handle words + tag overlap."""
    if not existing_nodes:
        return None
    handle_words = set(handle.lower().split())
    tag_set = set(t.lower() for t in tags)
    best_id, best_score = None, 0
    for node in existing_nodes:
        if node.parent is not None:
            continue
        node_words = set(node.handle.lower().split())
        node_tags = set(t.lower() for t in node.tags)
        score = len(handle_words & node_words) * 2 + len(tag_set & node_tags) * 3
        if score > best_score and score >= 4:
            best_score = score
            best_id = node.id
    return best_id


def _make_node_id(handle: str) -> str:
    """Generate a stable node ID from a domain handle."""
    clean = "".join(c if c.isalnum() or c == " " else "" for c in handle.lower().strip())
    clean = clean.replace(" ", "_")[:30]
    return f"domain_{clean}" if clean else f"domain_{int(time.time())}"
