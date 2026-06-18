"""
Memory lifecycle engine — promotes probationary proposals to active
memories, archives stale ones.

Runs as a background task after each conversation stream completes
(server.py:run_lifecycle_pass call).  Sweeps all open proposals,
evaluates promotion/archival conditions, and applies state transitions
through ProposalsStore.mark_promoted / mark_archived.

Promotion rules (in order; first match wins):

  1. corroborations >= 1 AND signals contains "response_match"
        -- corroborated AND used: strongest signal.

  2. corroborations >= 2
        -- 3 distinct conversations have produced this content;
        sufficient evidence even without explicit use signal.

  3. layer == "reference" AND signals contains "response_match"
        -- references have a lower bar; one use is enough.

Archival rules (applied to proposals that don't promote):

  4. age >= 7 AND corroborations == 0 AND no response_match signals
        -- decayed: nothing happened, drop.

  5. age >= 7 AND active store has a near-duplicate (cosine >= 0.85)
        -- redundant: the knowledge is already in the active store.

Anything else stays probationary (the "no-op" path).

The activity counter is shared with extraction (memory_extractor:
_next_activity_count); both increment it.  Each proposal records the
counter value at its creation in ``activity_count_at_proposal``.
Age = current_counter - activity_count_at_proposal.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger


# How many counter ticks before an unused, uncorroborated proposal is
# archived.  Tied to user activity, not wall-clock time.
ARCHIVAL_AGE_THRESHOLD = 7

# Cosine similarity threshold for deciding a proposal is redundant
# with an existing active memory.  Higher than the dedup threshold
# (0.92) used during extraction, because we're being more cautious:
# a probationary entry that's mostly-but-not-quite-the-same is worth
# keeping until we know whether it'll graduate.
REDUNDANCY_THRESHOLD = 0.85


def _proposal_age(proposal: Dict[str, Any], current_counter: int) -> int:
    """Return how many activity counts have passed since this proposal was created."""
    created_at = proposal.get("activity_count_at_proposal", 0)
    return max(0, current_counter - created_at)


def _has_response_match_signal(proposal: Dict[str, Any]) -> bool:
    """Check whether the proposal has been hit by retrieval-and-use feedback."""
    signals = proposal.get("signals", []) or []
    return any(s.get("name") == "response_match" for s in signals)


def _evaluate_promotion(proposal: Dict[str, Any]) -> Optional[str]:
    """Return a reason string if the proposal should promote, else None."""
    corroborations = proposal.get("corroborations", 0)
    layer = proposal.get("layer", "domain_context")
    has_use = _has_response_match_signal(proposal)

    if corroborations >= 1 and has_use:
        return "corroborated_and_used"
    if corroborations >= 2:
        return "highly_corroborated"
    if layer == "reference" and has_use:
        return "reference_used"
    return None


def _evaluate_archival(proposal: Dict[str, Any], current_counter: int,
                        active_embedding_lookup) -> Optional[str]:
    """Return a reason string if the proposal should archive, else None.

    ``active_embedding_lookup`` is a callable taking a proposal dict and
    returning the cosine similarity of the closest active memory (or 0.0
    if no embedding cache available, no active memories, etc.).
    """
    age = _proposal_age(proposal, current_counter)
    if age < ARCHIVAL_AGE_THRESHOLD:
        return None

    corroborations = proposal.get("corroborations", 0)
    has_use = _has_response_match_signal(proposal)

    if corroborations == 0 and not has_use:
        return "decayed"

    # Older proposals with some signal but not enough to promote:
    # check redundancy against active store.
    similarity = active_embedding_lookup(proposal)
    if similarity >= REDUNDANCY_THRESHOLD:
        return f"redundant (cos={similarity:.2f})"

    return None


def _make_active_embedding_lookup():
    """Build the active-store similarity lookup, or a no-op if embeddings
    aren't available.  Returns a callable: proposal -> max cosine."""
    try:
        from app.services.embedding_service import get_embedding_cache
        from app.storage.memory import get_memory_storage
        cache = get_embedding_cache()
        store = get_memory_storage()
        active_ids = [m.id for m in store.list_memories(status="active")]
    except Exception as e:
        logger.debug(f"Lifecycle: redundancy check unavailable: {e}")
        return lambda _proposal: 0.0

    if not active_ids:
        return lambda _proposal: 0.0

    # Pre-fetch active embeddings once per pass.  Worth the upfront cost
    # since we'll compare every probationary proposal against this set.
    active_vectors = []
    for mid in active_ids:
        v = cache.get(mid)
        if v is not None:
            active_vectors.append(v)
    if not active_vectors:
        return lambda _proposal: 0.0

    import numpy as np

    # Stack once into an (N, dim) matrix so each proposal lookup is a single
    # matrix-vector product instead of a Python-level max() over a generator
    # of N dot products.  Cosine semantics are unchanged: the embedding cache
    # stores pre-normalized vectors, so matrix @ prop_vec yields cosine
    # similarities directly.  np.stack copies the row views returned by
    # cache.get, so later cache mutations can't corrupt this snapshot.
    active_matrix = np.stack(active_vectors).astype(np.float32)

    def _lookup(proposal: Dict[str, Any]) -> float:
        pid = proposal.get("id")
        if not pid:
            return 0.0
        prop_vec = cache.get(pid)
        if prop_vec is None:
            return 0.0
        return float(np.max(active_matrix @ prop_vec))

    return _lookup


def _promote_proposal(proposal: Dict[str, Any], reason: str) -> Optional[str]:
    """Promote a probationary proposal to the active memory store.

    Returns the new memory's ID, or None on failure.
    """
    try:
        from app.storage.memory import get_memory_storage
        from app.storage.proposals import get_proposals_store
        from app.models.memory import Memory, MemoryReference
        from app.services.embedding_service import embed_and_cache

        store = get_memory_storage()
        proposals = get_proposals_store()

        memory = Memory(
            content=proposal["content"],
            layer=proposal.get("layer", "domain_context"),
            tags=proposal.get("tags", []) or [],
            learned_from="promoted_from_proposal",
            status="active",
            corroborations=proposal.get("corroborations", 0),
            corroborated_by=list(proposal.get("corroborated_by", []) or []),
            learned_from_conversation=proposal.get("conversation_id"),
        )

        scope_data = proposal.get("scope") or {}
        if scope_data.get("project_paths"):
            memory.scope.project_paths = list(scope_data["project_paths"])

        ref_data = proposal.get("reference")
        if ref_data:
            memory.reference = MemoryReference(**ref_data)

        store.save(memory)
        # Re-embed under the new memory ID so retrieval-feedback can find it.
        try:
            embed_and_cache(memory.id, memory.content)
        except Exception as e:
            logger.debug(f"Lifecycle: re-embed under new ID failed: {e}")

        proposals.mark_promoted(proposal["id"], target_memory_id=memory.id)
        logger.info(
            f"⭐ Promoted {proposal['id']} -> {memory.id} ({reason}): "
            f"{proposal['content'][:60]}"
        )
        return memory.id
    except Exception as e:
        logger.warning(f"Lifecycle: promotion failed for {proposal.get('id')}: {e}")
        return None


def _archive_proposal(proposal: Dict[str, Any], reason: str) -> bool:
    """Mark a probationary proposal as archived."""
    try:
        from app.storage.proposals import get_proposals_store
        proposals = get_proposals_store()
        proposals.mark_archived(proposal["id"], reason=reason)
        logger.debug(
            f"🗑️ Archived {proposal['id']} ({reason}): "
            f"{proposal['content'][:60]}"
        )
        return True
    except Exception as e:
        logger.warning(f"Lifecycle: archive failed for {proposal.get('id')}: {e}")
        return False


async def run_lifecycle_pass() -> Dict[str, int]:
    """Sweep all open proposals; promote or archive based on accumulated signals.

    Called as a background task after each stream completion.  Returns a
    summary dict.
    """
    counts = {"scanned": 0, "promoted": 0, "archived": 0, "noop": 0}
    try:
        from app.storage.proposals import get_proposals_store
        from app.memory.extractor import _next_activity_count
        proposals = get_proposals_store()
    except Exception as e:
        logger.debug(f"Lifecycle: dependencies unavailable: {e}")
        return counts

    open_proposals = proposals.list_open()
    if not open_proposals:
        return counts

    # The lifecycle pass should NOT advance the counter -- it's a passive
    # sweep, not user activity.  Read the current value without bumping it.
    try:
        from pathlib import Path
        import json
        from app.utils.paths import get_ziya_home
        counter_path = get_ziya_home() / "memory" / "activity_counter.json"
        if counter_path.exists():
            with open(counter_path) as f:
                current_counter = int(json.load(f).get("count", 0))
        else:
            current_counter = 0
    except Exception:
        current_counter = 0

    embedding_lookup = _make_active_embedding_lookup()

    for proposal in open_proposals:
        counts["scanned"] += 1

        promote_reason = _evaluate_promotion(proposal)
        if promote_reason:
            if _promote_proposal(proposal, promote_reason):
                counts["promoted"] += 1
            continue

        archive_reason = _evaluate_archival(proposal, current_counter, embedding_lookup)
        if archive_reason:
            if _archive_proposal(proposal, archive_reason):
                counts["archived"] += 1
            continue

        counts["noop"] += 1

    if counts["promoted"] or counts["archived"]:
        logger.info(
            f"🔄 Lifecycle pass: scanned {counts['scanned']}, "
            f"promoted {counts['promoted']}, archived {counts['archived']}, "
            f"no-op {counts['noop']}"
        )
    return counts
