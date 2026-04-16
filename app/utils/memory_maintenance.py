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

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from collections import Counter

from app.utils.logging_utils import logger

# Thresholds (tunable)
CELL_DIVISION_THRESHOLD = 12   # memories per node before considering split
CELL_DIVISION_MIN_CLUSTER = 4  # minimum memories sharing a tag to form a child
STALE_DAYS = 90                # days since last access → stale
CROSS_LINK_MIN_OVERLAP = 2     # minimum shared tags to create a cross-link
CLEANUP_INTERVAL_HOURS = 24    # minimum hours between periodic cleanup runs
CLEANUP_MIN_MEMORIES = 10      # don't bother cleaning if fewer than this
CLEANUP_MIN_NEW_SINCE = 5      # only clean if this many memories added since last run
AUTO_LINK_TOP_K = 5            # how many similar memories to consider for linking
AUTO_LINK_MIN_SIMILARITY = 0.75  # minimum cosine similarity to create a link
AUTO_LINK_STRONG_SIMILARITY = 0.88  # above this = "elaborates" relation
TAG_ENRICHMENT_MIN_SIMILARITY = 0.80  # minimum to propagate tags


def run_post_save_maintenance(memory_id: str) -> Dict[str, any]:
    """Run after every memory_save.  Lightweight — O(nodes × tags)."""
    from app.storage.memory import get_memory_storage
    store = get_memory_storage()

    results: Dict[str, any] = {"placed": None, "divided": [], "cross_linked": [],
                                "linked": [], "enriched": []}

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

    # 4. Auto-link: find semantically similar memories and create relations
    try:
        linked = _auto_link_by_embedding(store, memory)
        results["linked"] = linked
    except Exception as e:
        logger.debug(f"Auto-link failed (non-fatal): {e}")

    # 5. Enrich: propagate tags to related memories
    try:
        enriched = _enrich_related_tags(store, memory)
        results["enriched"] = enriched
    except Exception as e:
        logger.debug(f"Tag enrichment failed (non-fatal): {e}")

    # 6. Auto-trigger reorganization if orphan count exceeds threshold
    try:
        from app.utils.memory_organizer import should_auto_organize
        if should_auto_organize(store):
            from app.utils.memory_organizer import reorganize
            logger.info("🗺️ Auto-organize triggered: orphan threshold exceeded")
            # Fire-and-forget with error logging — don't block the save
            async def _bg_organize():
                try:
                    await reorganize(store)
                    logger.info("🗺️ Auto-organize completed successfully")
                except Exception as e:
                    logger.error(f"🗺️ Auto-organize failed: {e}")
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_bg_organize())
            except RuntimeError:
                logger.debug("No running event loop for auto-organize — skipping")
    except Exception as e:
        logger.debug(f"Auto-organize check failed (non-fatal): {e}")

    # 5. Periodic cleanup — runs at most once per CLEANUP_INTERVAL_HOURS
    try:
        _maybe_periodic_cleanup(store)
    except Exception as e:
        logger.debug(f"Periodic cleanup check failed (non-fatal): {e}")

    return results


def _get_cleanup_state_file() -> Path:
    """Path to the cleanup gate state file."""
    from app.utils.paths import get_ziya_home
    return get_ziya_home() / "memory" / ".cleanup_state.json"


def _read_cleanup_state() -> Dict[str, any]:
    """Read the last cleanup timestamp and memory count."""
    state_file = _get_cleanup_state_file()
    if not state_file.exists():
        return {"last_run": 0, "memory_count_at_last_run": 0}
    try:
        return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {"last_run": 0, "memory_count_at_last_run": 0}


def _write_cleanup_state(memory_count: int) -> None:
    """Record that cleanup just ran."""
    state_file = _get_cleanup_state_file()
    try:
        state_file.write_text(json.dumps({
            "last_run": time.time(),
            "memory_count_at_last_run": memory_count,
        }))
    except OSError as e:
        logger.debug(f"Could not write cleanup state: {e}")


def _maybe_periodic_cleanup(store) -> None:
    """Check if periodic cleanup is due and fire it if so.

    Gates:
    1. At least CLEANUP_INTERVAL_HOURS since last run
    2. At least CLEANUP_MIN_MEMORIES active memories
    3. At least CLEANUP_MIN_NEW_SINCE new memories since last run
    4. Not already running (via organize_task_status)
    """
    state = _read_cleanup_state()
    hours_since = (time.time() - state["last_run"]) / 3600

    if hours_since < CLEANUP_INTERVAL_HOURS:
        return

    active_count = len(store.list_memories(status="active"))
    if active_count < CLEANUP_MIN_MEMORIES:
        return

    new_since = active_count - state.get("memory_count_at_last_run", 0)
    if new_since < CLEANUP_MIN_NEW_SINCE:
        return

    # Check if organize is already running
    try:
        from app.api.memory import _organize_task_status
        if _organize_task_status.get("running"):
            return
    except ImportError:
        pass

    logger.info(
        f"🧹 Periodic cleanup triggered: {hours_since:.0f}h since last run, "
        f"{new_since} new memories since last cleanup"
    )

    async def _bg_cleanup():
        try:
            from app.utils.memory_organizer import cleanup_corpus
            result = await cleanup_corpus(store)
            _write_cleanup_state(len(store.list_memories(status="active")))
            removed = result.get("removed", 0)
            merged = result.get("merged", 0)
            if removed or merged:
                logger.info(f"🧹 Periodic cleanup complete: {removed} removed, {merged} merged")
            else:
                logger.debug("🧹 Periodic cleanup: nothing to clean")
        except Exception as e:
            logger.error(f"🧹 Periodic cleanup failed: {e}")

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_bg_cleanup())
    except RuntimeError:
        logger.debug("No running event loop for periodic cleanup — skipping")


def _auto_link_by_embedding(store, memory) -> List[Tuple[str, str, str]]:
    """Find semantically similar memories and create typed relations.

    For each similar memory found via embedding cosine similarity:
    - >0.88: "elaborates" (very similar = likely adds detail to same topic)
    - 0.75–0.88: "supports" (related but distinct perspectives)

    Returns list of (source_id, relation_type, target_id) triples created.
    Only creates links that don't already exist.
    """
    try:
        from app.services.embedding_service import (
            get_embedding_provider, get_embedding_cache, NoopProvider
        )
        provider = get_embedding_provider()
        if isinstance(provider, NoopProvider):
            return []

        cache = get_embedding_cache()
        vec = cache.get(memory.id)
        if vec is None:
            # Memory was just saved but not yet embedded (race condition)
            vec = provider.embed_text(memory.content)
            if vec is None:
                return []

        # Find similar memories, excluding self
        similar = cache.search(vec, top_k=AUTO_LINK_TOP_K + 1,
                               exclude_ids={memory.id})
        if not similar:
            return []

        created: List[Tuple[str, str, str]] = []
        for target_id, score in similar:
            if score < AUTO_LINK_MIN_SIMILARITY:
                break  # Sorted descending, everything after is lower

            # Determine relation type based on similarity strength
            if score >= AUTO_LINK_STRONG_SIMILARITY:
                rel_type = "elaborates"
            else:
                rel_type = "supports"

            # Add forward link: new memory → existing
            if rel_type not in memory.relations:
                memory.relations[rel_type] = []
            if target_id not in memory.relations[rel_type]:
                memory.relations[rel_type].append(target_id)
                created.append((memory.id, rel_type, target_id))

            # Add reverse link: existing → new memory
            target_mem = store.get(target_id)
            if target_mem:
                reverse_type = "elaborates" if rel_type == "elaborates" else "supports"
                if reverse_type not in target_mem.relations:
                    target_mem.relations[reverse_type] = []
                if memory.id not in target_mem.relations[reverse_type]:
                    target_mem.relations[reverse_type].append(memory.id)
                    store.save(target_mem)

        if created:
            store.save(memory)
            logger.info(
                f"🔗 AUTO_LINK: {memory.id} linked to {len(created)} memories "
                f"({', '.join(f'{t}→{tid[:10]}' for _, t, tid in created[:3])})"
            )
        return created

    except Exception as e:
        logger.debug(f"Auto-link failed: {e}")
        return []


def _enrich_related_tags(store, memory) -> List[str]:
    """Propagate tags from a new memory to highly similar existing memories.

    When a new memory shares >0.80 cosine similarity with an existing memory,
    any tags on the new memory that the existing memory lacks are added.
    This enables tag-based queries to find related content that was tagged
    differently across sessions.

    Returns list of memory IDs whose tags were enriched.
    """
    if not memory.tags:
        return []

    try:
        from app.services.embedding_service import (
            get_embedding_provider, get_embedding_cache, NoopProvider
        )
        provider = get_embedding_provider()
        if isinstance(provider, NoopProvider):
            return []

        cache = get_embedding_cache()
        vec = cache.get(memory.id)
        if vec is None:
            return []

        similar = cache.search(vec, top_k=AUTO_LINK_TOP_K,
                               exclude_ids={memory.id})
        if not similar:
            return []

        new_tags = set(t.lower() for t in memory.tags)
        enriched_ids = []

        for target_id, score in similar:
            if score < TAG_ENRICHMENT_MIN_SIMILARITY:
                break

            target_mem = store.get(target_id)
            if not target_mem:
                continue

            existing_tags = set(t.lower() for t in target_mem.tags)
            new_for_target = new_tags - existing_tags

            if new_for_target and len(existing_tags) < 6:
                # Add up to 2 new tags to avoid tag explosion
                to_add = list(new_for_target)[:2]
                target_mem.tags = list(set(target_mem.tags + to_add))
                store.save(target_mem)
                enriched_ids.append(target_id)

        if enriched_ids:
            logger.info(
                f"🏷️ TAG_ENRICH: {memory.id} propagated tags to {len(enriched_ids)} "
                f"similar memories"
            )
        return enriched_ids

    except Exception as e:
        logger.debug(f"Tag enrichment failed: {e}")
        return []


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
