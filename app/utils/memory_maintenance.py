"""
Memory maintenance — Phase 2 automatic structure and upkeep.

Algorithmic routines that keep the memory system organized without
user intervention:

  - Auto-placement: file new memories into the mind-map by tag overlap
  - Cell division: split oversized nodes into focused children
  - Cross-link discovery: connect nodes in different branches that
    share tags
  - Staleness detection: flag memories not accessed recently

Called from memory_save tool (on every save) and from the periodic
review API endpoint.
"""

import time
from typing import Dict, List, Optional, Set, Tuple
from collections import Counter

from app.utils.logging_utils import logger

# Thresholds (tunable)
CELL_DIVISION_THRESHOLD = 12   # memories per node before considering split
CELL_DIVISION_MIN_CLUSTER = 4  # minimum memories sharing a tag to form a child
STALE_DAYS = 90                # days since last access → stale
CROSS_LINK_MIN_OVERLAP = 2     # minimum shared tags to create a cross-link


def run_post_save_maintenance(memory_id: str) -> Dict[str, any]:
    """Run after every memory_save.  Lightweight — O(nodes × tags)."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()

    results: Dict[str, any] = {"placed": None, "divided": [], "cross_linked": []}

    memory = store.get(memory_id)
    if not memory:
        return results

    # 1. Auto-place into best mind-map node
    placed = store.place_memory_in_mindmap(memory)
    results["placed"] = placed

    # 2. Check if the placed node needs cell division
    if placed:
        divided = maybe_divide_node(store, placed)
        results["divided"] = divided

    # 3. Discover cross-links for the node this memory landed in
    target_node_id = placed or memory.scope.domain_node
    if target_node_id:
        links = discover_cross_links(store, target_node_id)
        results["cross_linked"] = links

    return results


def maybe_divide_node(store, node_id: str) -> List[str]:
    """If a node has too many memories, split by tag cluster.

    Returns list of newly created child node IDs.
    """
    from app.models.memory import MindMapNode

    node = store.get_mindmap_node(node_id)
    if not node or len(node.memory_refs) < CELL_DIVISION_THRESHOLD:
        return []

    # Load all memories in this node
    memories = []
    for mid in node.memory_refs:
        m = store.get(mid)
        if m:
            memories.append(m)

    if len(memories) < CELL_DIVISION_THRESHOLD:
        return []

    # Find tag clusters: tags that appear in >= CELL_DIVISION_MIN_CLUSTER memories
    tag_counter: Counter = Counter()
    mem_by_tag: Dict[str, List[str]] = {}
    for m in memories:
        for tag in m.tags:
            t = tag.lower()
            tag_counter[t] += 1
            mem_by_tag.setdefault(t, []).append(m.id)

    # Exclude tags that are already the node's own tags (too broad)
    node_tags = {t.lower() for t in node.tags}
    candidates = [
        (tag, mids)
        for tag, mids in mem_by_tag.items()
        if len(mids) >= CELL_DIVISION_MIN_CLUSTER and tag not in node_tags
    ]

    if not candidates:
        return []

    # Pick the strongest cluster (most memories)
    candidates.sort(key=lambda x: len(x[1]), reverse=True)
    best_tag, best_mids = candidates[0]

    # Don't split if the cluster IS the entire node (nothing would remain)
    if len(best_mids) >= len(memories) - 1:
        return []

    # Create child node
    child_id = f"{node_id}-{best_tag}"
    # Check if this child already exists (idempotent)
    existing = store.get_mindmap_node(child_id)
    if existing:
        return []

    child = MindMapNode(
        id=child_id,
        handle=f"{best_tag.replace('-', ' ').title()} — {len(best_mids)} related memories",
        parent=node_id,
        tags=[best_tag] + list(node_tags)[:3],
        memory_refs=list(set(best_mids)),
    )
    store.save_mindmap_node(child)

    # Remove migrated refs from parent
    remaining_refs = [mid for mid in node.memory_refs if mid not in best_mids]
    node.memory_refs = remaining_refs
    if child_id not in node.children:
        node.children.append(child_id)
    store.save_mindmap_node(node)

    # Update migrated memories' scope.domain_node
    for mid in best_mids:
        m = store.get(mid)
        if m:
            m.scope.domain_node = child_id
            store.save(m)

    logger.info(
        f"🔀 CELL_DIVISION: Split '{node_id}' → new child '{child_id}' "
        f"with {len(best_mids)} memories (tag: {best_tag})"
    )
    return [child_id]


def discover_cross_links(store, node_id: str) -> List[Tuple[str, str]]:
    """Find nodes in other branches that share tags with this node.

    Returns list of (node_id, linked_node_id) pairs that were added.
    """
    node = store.get_mindmap_node(node_id)
    if not node or not node.tags:
        return []

    node_tags = {t.lower() for t in node.tags}
    all_nodes = store.list_mindmap_nodes()
    added: List[Tuple[str, str]] = []

    # Collect ancestor chain to avoid linking within the same branch
    ancestors: Set[str] = set()
    cur = node_id
    for _ in range(20):
        n = store.get_mindmap_node(cur)
        if not n or not n.parent:
            break
        ancestors.add(n.parent)
        cur = n.parent

    descendants: Set[str] = set()
    def _collect_desc(nid: str, depth: int = 0):
        if depth > 20:
            return
        n = store.get_mindmap_node(nid)
        if not n:
            return
        for cid in n.children:
            descendants.add(cid)
            _collect_desc(cid, depth + 1)
    _collect_desc(node_id)

    same_branch = ancestors | descendants | {node_id}

    for other in all_nodes:
        if other.id in same_branch:
            continue
        if other.id in (node.cross_links or []):
            continue
        other_tags = {t.lower() for t in other.tags}
        overlap = node_tags & other_tags
        if len(overlap) >= CROSS_LINK_MIN_OVERLAP:
            node.cross_links = list(set((node.cross_links or []) + [other.id]))
            other.cross_links = list(set((other.cross_links or []) + [node_id]))
            store.save_mindmap_node(node)
            store.save_mindmap_node(other)
            added.append((node_id, other.id))
            logger.info(f"🔗 CROSS_LINK: {node_id} ↔ {other.id} (shared: {overlap})")

    return added


def find_stale_memories(store, days: int = STALE_DAYS) -> List[dict]:
    """Return memories not accessed in the last N days."""
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    stale = []
    for m in store.list_memories(status="active"):
        if m.last_accessed < cutoff:
            stale.append({
                "id": m.id,
                "content": m.content[:100],
                "layer": m.layer,
                "last_accessed": m.last_accessed,
                "tags": m.tags,
            })
    return stale


def get_review_summary(store) -> Dict[str, any]:
    """Build a review summary: stale memories, large nodes, orphan memories."""
    stale = find_stale_memories(store)
    nodes = store.list_mindmap_nodes()
    memories = store.list_memories()

    # Find oversized nodes (close to or exceeding threshold)
    large_nodes = [
        {"id": n.id, "handle": n.handle, "memory_count": len(n.memory_refs)}
        for n in nodes
        if len(n.memory_refs) >= CELL_DIVISION_THRESHOLD - 2
    ]

    # Find orphan memories (not referenced by any mind-map node)
    all_refs: Set[str] = set()
    for n in nodes:
        all_refs.update(n.memory_refs)
    orphans = [
        {"id": m.id, "content": m.content[:100], "tags": m.tags}
        for m in memories
        if m.id not in all_refs
    ]

    return {
        "stale_memories": stale,
        "stale_count": len(stale),
        "large_nodes": large_nodes,
        "orphan_memories": orphans,
        "orphan_count": len(orphans),
        "total_memories": len(memories),
        "total_nodes": len(nodes),
    }
