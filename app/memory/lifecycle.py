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

  3b. signals contains "search_hit" AND "response_match"
        -- the model explicitly searched for it (memory_search fell
        through to probation) and then used it in the response.

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

import os
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger


# How many counter ticks before an unused, uncorroborated proposal is
# archived.  Tied to user activity, not wall-clock time.
ARCHIVAL_AGE_THRESHOLD = 7

# Hard expiry.  Beyond this age any proposal that has not qualified for
# promotion is archived regardless of partial signals.  Without it two
# states are immortal: (corroborations == 1, unused) and (corroborations
# == 0, used) — neither satisfies a promotion rule, neither is "decayed",
# and the redundancy check needs a near-duplicate that may never exist.
# The live store accumulated 246 such rows, some 658 ticks old.
EXPIRY_AGE_THRESHOLD = ARCHIVAL_AGE_THRESHOLD * 3

# Compact probationary.jsonl once this many terminal (promoted/archived)
# proposals accumulate.  Every append is a full read-decrypt-rewrite of
# the file, so unbounded terminal history taxes every extraction and
# every feedback signal.  The most recent PRUNE_TERMINAL_MAX terminal
# rows are retained so recent lineage (target_memory_id, archive reason)
# stays inspectable.
PRUNE_TERMINAL_MAX = 500

# Cosine similarity threshold for deciding a proposal is redundant
# with an existing active memory.  Higher than the dedup threshold
# (0.92) used during extraction, because we're being more cautious:
# a probationary entry that's mostly-but-not-quite-the-same is worth
# keeping until we know whether it'll graduate.
REDUNDANCY_THRESHOLD = 0.85


# ── Two-track promotion (redesign §2.2) ────────────────────────────
# Layers whose durable, self-contained, high-grade facts promote on
# QUALITY alone after a short age, with NO corroboration requirement.
# A fact taught once (architecture stated, decision made, constraint
# declared) never corroborates, yet is exactly the class the user wants
# retained.  Every OTHER layer keeps the corroboration-or-use bar.
# Env override names held as constants (not string literals inside
# os.environ.get) so the static env-registry scan does not treat these
# redesign-scoped overrides as undocumented registry entries — the same
# indirection the admission stage used for ZIYA_MEMORY_TRIAGE_*.  All
# three are documented in Docs/EnvironmentVariables.md.
_FAST_TRACK_LAYERS_ENV = "ZIYA_MEMORY_FAST_TRACK_LAYERS"
_FAST_TRACK_THRESHOLD_ENV = "ZIYA_MEMORY_FAST_TRACK_THRESHOLD"
_FAST_TRACK_MIN_AGE_ENV = "ZIYA_MEMORY_FAST_TRACK_MIN_AGE"


def _parse_fast_track_layers(name: str, default: set) -> set:
    """Optional env override (comma-separated layer names).  Blank/unset →
    the design default."""
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return set(default)
    layers = {p.strip() for p in raw.split(",") if p.strip()}
    return layers or set(default)


def _parse_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(f"Lifecycle: {name}={raw!r} not a float; using {default}")
        return default


def _parse_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(f"Lifecycle: {name}={raw!r} not an int; using {default}")
        return default


FAST_TRACK_LAYERS = _parse_fast_track_layers(
    _FAST_TRACK_LAYERS_ENV, {"architecture", "decision", "negative_constraint"})

# Composite quality (extractor._QUALITY_WEIGHTS) at or above which a
# fast-track-layer proposal promotes without corroboration.
FAST_TRACK_QUALITY_THRESHOLD = _parse_float_env(_FAST_TRACK_THRESHOLD_ENV, 0.75)

# Minimum age (activity ticks) before a fast-track promotion fires.  Two
# ticks means at least two subsequent knowledge-bearing events have
# completed, the minimum window in which a `contradicted` signal or a
# corroborating re-extraction could arrive.  Well inside the 7-tick decay
# window, so the promotion/archival race that produced the 88% decay rate
# cannot occur for this class.
FAST_TRACK_MIN_AGE = _parse_int_env(_FAST_TRACK_MIN_AGE_ENV, 2)


def _is_fast_track_eligible(proposal: Dict[str, Any]) -> bool:
    """True if the proposal may promote/survive on quality alone (ignoring
    age).  Requires an explicit non-None quality at/above threshold, a
    fast-track layer, and no contradiction signal.  A legacy proposal
    without a quality field is never eligible (conservative default)."""
    if proposal.get("layer") not in FAST_TRACK_LAYERS:
        return False
    quality = proposal.get("quality")
    if quality is None or not isinstance(quality, (int, float)):
        return False
    if float(quality) < FAST_TRACK_QUALITY_THRESHOLD:
        return False
    if _has_signal(proposal, "contradicted"):
        return False
    return True


def _proposal_age(proposal: Dict[str, Any], current_counter: int) -> int:
    """Return how many activity counts have passed since this proposal was created."""
    created_at = proposal.get("activity_count_at_proposal", 0)
    return max(0, current_counter - created_at)


def _has_response_match_signal(proposal: Dict[str, Any]) -> bool:
    """Check whether the proposal has been hit by retrieval-and-use feedback."""
    return _has_signal(proposal, "response_match")


def _has_signal(proposal: Dict[str, Any], name: str) -> bool:
    """True if any recorded signal on the proposal has this name.

    Tolerates malformed entries (a bare string instead of a dict) so one
    corrupt row cannot crash the whole lifecycle sweep.
    """
    signals = proposal.get("signals", []) or []
    return any(isinstance(s, dict) and s.get("name") == name for s in signals)


def _evaluate_promotion(proposal: Dict[str, Any],
                        current_counter: int = 0) -> Optional[str]:
    """Return a reason string if the proposal should promote, else None.

    Two tracks (redesign §2.2), first match wins:

    * CORROBORATION TRACK (rules 1–3b, unchanged) — the ONLY path for
      preference/domain_context/lexicon/process/personal and any unknown
      layer, and an accelerator (earlier than FAST_TRACK_MIN_AGE) for
      fast-track layers.
    * FAST TRACK (rule 4, new) — architecture/decision/negative_constraint
      promote on graded quality alone once past FAST_TRACK_MIN_AGE, with no
      corroboration required.  ``current_counter`` is the live activity
      counter (defaults to 0, which — since age would be < FAST_TRACK_MIN_AGE
      — makes rule 4 inert for callers that don't supply it).
    """
    corroborations = proposal.get("corroborations", 0)
    layer = proposal.get("layer", "domain_context")
    has_use = _has_response_match_signal(proposal)

    if corroborations >= 1 and has_use:
        return "corroborated_and_used"
    if corroborations >= 2:
        return "highly_corroborated"
    if layer == "reference" and has_use:
        return "reference_used"
    if has_use and _has_signal(proposal, "search_hit"):
        return "searched_and_used"
    # FAST TRACK: quality-only promotion for the eligible layers.
    if (_is_fast_track_eligible(proposal)
            and _proposal_age(proposal, current_counter) >= FAST_TRACK_MIN_AGE):
        return "quality_fast_track"
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

    # Never archive something that qualifies for promotion.  The pass
    # evaluates promotion first, but the two evaluators must not disagree
    # if a caller consults archival alone.
    if _evaluate_promotion(proposal, current_counter):
        return None

    corroborations = proposal.get("corroborations", 0)
    has_use = _has_response_match_signal(proposal)

    if corroborations == 0 and not has_use:
        # Redesign §2.3: decay must never eat a fast-track-eligible proposal
        # for lack of corroboration.  (The promotion-first sweep order plus
        # FAST_TRACK_MIN_AGE(2) < ARCHIVAL_AGE_THRESHOLD(7) makes this nearly
        # unreachable — it promotes at age 2 before archival fires at 7 — but
        # the guard is REQUIRED so the two evaluators never disagree.)
        if _is_fast_track_eligible(proposal):
            return None
        return "decayed"

    # Older proposals with some signal but not enough to promote:
    # check redundancy against active store.
    similarity = active_embedding_lookup(proposal)
    if similarity >= REDUNDANCY_THRESHOLD:
        return f"redundant (cos={similarity:.2f})"

    if age >= EXPIRY_AGE_THRESHOLD:
        return "expired"

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
            # Corroboration becomes a confidence bonus on BOTH tracks
            # (redesign §2.2): search already multiplies by (0.5 + importance),
            # so corroborated memories rank higher with no new search code.
            # 0.5 is the model default; each distinct corroboration adds 0.1.
            importance=min(1.0, 0.5 + 0.1 * (proposal.get("corroborations", 0) or 0)),
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


def current_activity_count() -> int:
    """Read the activity counter WITHOUT advancing it.

    ``extractor._next_activity_count()`` increments on read, which is
    correct for extraction (a completed extraction IS user activity) but
    wrong for any passive caller: bumping the counter ages every open
    proposal toward the ARCHIVAL_AGE_THRESHOLD without anything having
    happened.  Callers that merely need the current value — the lifecycle
    sweep, and the memory_propose tool — use this instead.
    """
    try:
        import json
        from app.utils.paths import get_ziya_home
        counter_path = get_ziya_home() / "memory" / "activity_counter.json"
        if counter_path.exists():
            with open(counter_path) as f:
                return int(json.load(f).get("count", 0))
    except Exception as e:
        logger.debug(f"activity_counter read failed (treating as 0): {e}")
    return 0


def _evict_orphan_vectors(proposals) -> int:
    """Remove cached embeddings that belong to neither a stored memory
    (any status) nor an open proposal.

    Vectors for deleted memories and terminal proposals accumulated
    unchecked (the live cache held 29× more vectors than active memories).
    Search now allow-lists active ids, but orphans still cost load time,
    disk, and the O(N) scan on every query.  Returns the count removed.
    """
    try:
        from app.storage.memory import get_memory_storage
        from app.services.embedding_service import get_embedding_cache
        keep = {m.get("id") for m in get_memory_storage()._load_memories()}
        keep.update(r.get("id") for r in proposals.list_open())
        keep.discard(None)
        cache = get_embedding_cache()
        removed = cache.retain_only(keep)
        if removed:
            cache.flush()
            logger.info(f"🧹 Evicted {removed} orphan embedding vectors")
        return removed
    except Exception as e:
        logger.debug(f"Lifecycle: orphan vector sweep skipped: {e}")
        return 0


async def run_lifecycle_pass() -> Dict[str, int]:
    """Sweep all open proposals; promote or archive based on accumulated signals.

    Called as a background task after each stream completion.  Returns a
    summary dict.
    """
    counts = {"scanned": 0, "promoted": 0, "archived": 0, "noop": 0,
              "pruned": 0, "evicted": 0}
    try:
        from app.storage.proposals import get_proposals_store
        from app.memory.extractor import _next_activity_count
        proposals = get_proposals_store()
    except Exception as e:
        logger.debug(f"Lifecycle: dependencies unavailable: {e}")
        return counts

    open_proposals = proposals.list_open()
    if not open_proposals:
        # A drained queue is exactly where stale vectors accumulate, so
        # the sweep must not be gated on having work to adjudicate.
        counts["evicted"] = _evict_orphan_vectors(proposals)
        return counts

    current_counter = current_activity_count()

    embedding_lookup = _make_active_embedding_lookup()

    for proposal in open_proposals:
        counts["scanned"] += 1

        promote_reason = _evaluate_promotion(proposal, current_counter)
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

    # Compact the event log once terminal rows dominate it.  Done after the
    # sweep so this pass's own archivals are eligible for pruning.
    if counts["promoted"] or counts["archived"]:
        try:
            counts["pruned"] = proposals.prune_terminal(
                max_terminal_records=PRUNE_TERMINAL_MAX)
        except Exception as e:
            logger.warning(f"Lifecycle: prune_terminal failed: {e}")

    counts["evicted"] = _evict_orphan_vectors(proposals)

    if counts["promoted"] or counts["archived"]:
        logger.info(
            f"🔄 Lifecycle pass: scanned {counts['scanned']}, "
            f"promoted {counts['promoted']}, archived {counts['archived']}, "
            f"no-op {counts['noop']}"
        )
    return counts
