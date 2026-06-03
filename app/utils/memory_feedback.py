"""
Retrieval-feedback signal: detect whether a memory loaded into context
actually got used by the model, and update lifecycle counters accordingly.

The signal closes the lifecycle loop:
  - Memories loaded into context but never used drift toward stale.
  - Memories loaded AND used corroborate as durable knowledge.
  - Probationary entries that receive a single use signal can promote
    (per Diff 7's promotion engine).

Design:

  - Per-conversation set of memory IDs that were loaded into context.
    Stored in a process-local dict keyed by conversation_id.  No
    persistence — feedback always runs synchronously at end-of-turn,
    so a process restart only loses incomplete in-flight conversations.

  - On retrieval (memory_search / memory_context / memory_expand /
    system-prompt injection), call ``record_load(conversation_id, [memory_ids])``.

  - On response completion, call ``apply_feedback(conversation_id,
    response_text)``.  This embeds the response in sliding windows,
    scores each loaded memory's embedding against the windows, and
    updates ``retrieval_loaded_count`` / ``retrieval_used_count`` /
    ``importance`` on each.

  - The threshold (default 0.55) is conservative -- false positives
    (claiming "used" when the memory wasn't actually informative)
    bump importance unfairly, but false negatives (real use missed)
    only delay promotion.  Better to err toward false negatives.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

import numpy as np

from app.utils.logging_utils import logger


# Memory IDs loaded per conversation_id.  In-memory only -- the feedback
# loop runs synchronously at conversation end, so we don't need
# durability across process restarts.
_loaded_per_conversation: Dict[str, Set[str]] = defaultdict(set)


# Reconsolidation labile windows -- per-memory expiry timestamps (Unix ms).
# When a memory is retrieved, it briefly enters a "labile" state during
# which the comparator is biased toward UPDATE over NOOP for partial
# overlaps.  Mirrors the biological reconsolidation window: retrieval
# transiently destabilizes the trace, allowing finer corrections, then
# it re-stabilizes.  Process-local: restart-conservative is the right
# fallback (closes all open windows; nothing wrongly biased).
_labile_until: Dict[str, int] = {}

# Window durations.  RETRIEVAL covers "user injected this into context";
# USED covers "user demonstrably referenced this in their response"
# (stronger signal, longer window).
_LABILE_RETRIEVAL_MS = 3_600_000      # 1 hour
_LABILE_USED_MS      = 14_400_000     # 4 hours


# Window tokenization config.  Approximate character-based windowing
# rather than real tokenization -- we don't need precise alignment,
# just enough chunks for max-pooling to find the relevant section.
_WINDOW_CHARS = 800   # ~200 tokens
_WINDOW_STRIDE = 400  # 50% overlap so phrase boundaries don't bisect signal

# Cosine threshold for "used".  Tuned conservative: at this level,
# unrelated text rarely scores above; on-topic paraphrase reliably does.
# Tunable per-memory in the future based on layer (e.g. lexicon needs
# higher threshold than domain_context).
_USE_THRESHOLD = 0.55

# Importance bump applied when a memory is detected as "used".
_USE_IMPORTANCE_DELTA = 0.05


def mark_labile(memory_ids, duration_ms: int) -> None:
    """Open or extend a reconsolidation window on each memory.

    Extension is max-based: a longer-duration call cannot shrink an
    existing window.  Accepts any iterable of ids; empty input is a no-op.
    """
    if not memory_ids or duration_ms <= 0:
        return
    expiry = int(time.time() * 1000) + duration_ms
    for mid in memory_ids:
        if not mid:
            continue
        prev = _labile_until.get(mid, 0)
        if expiry > prev:
            _labile_until[mid] = expiry


def is_labile(memory_id: str) -> bool:
    """Whether the memory is currently within its reconsolidation window.

    Auto-cleans expired entries on read so the dict can't grow unbounded
    even if mark_labile is called for memories that are never re-checked.
    """
    if not memory_id:
        return False
    now = int(time.time() * 1000)
    expiry = _labile_until.get(memory_id, 0)
    if expiry == 0:
        return False
    if expiry <= now:
        _labile_until.pop(memory_id, None)
        return False
    return True


def record_load(conversation_id: Optional[str], memory_ids: List[str]) -> None:
    """Record that these memories were loaded into a conversation's context.

    Idempotent within a conversation: loading the same memory twice in
    one conversation only counts once for the purposes of feedback,
    since the model only sees the same content once per turn.
    """
    if not memory_ids:
        return
    # Open the reconsolidation window regardless of whether we have a
    # conversation_id -- the labile property is per-memory, not per-conv.
    mark_labile(memory_ids, _LABILE_RETRIEVAL_MS)
    if not conversation_id:
        return
    _loaded_per_conversation[conversation_id].update(memory_ids)
    logger.debug(
        f"📥 Recorded {len(memory_ids)} memory load(s) for conv {conversation_id[:8]}: "
        f"{memory_ids[:3]}{'...' if len(memory_ids) > 3 else ''}"
    )


def get_loaded_memory_ids(conversation_id: str) -> Set[str]:
    """Return all memory IDs loaded into this conversation so far."""
    return _loaded_per_conversation.get(conversation_id, set()).copy()


def clear_conversation(conversation_id: str) -> None:
    """Drop all loaded-memory tracking for a conversation.  Called after
    feedback application so the dict doesn't grow unboundedly."""
    _loaded_per_conversation.pop(conversation_id, None)


def _windowize(text: str, size: int = _WINDOW_CHARS,
               stride: int = _WINDOW_STRIDE) -> List[str]:
    """Slice text into overlapping windows suitable for max-pool scoring."""
    if not text or len(text) <= size:
        return [text] if text else []
    windows = []
    i = 0
    while i < len(text):
        windows.append(text[i:i + size])
        i += stride
    return windows


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity for L2-normalized vectors — dot product.

    BedrockTitanProvider L2-normalizes its outputs (see embedding_service.py),
    so cos(a,b) == dot(a,b) for vectors from this pipeline.
    """
    return float(np.dot(a, b))


async def apply_feedback(
    conversation_id: Optional[str],
    response_text: str,
    use_threshold: float = _USE_THRESHOLD,
) -> Dict[str, int]:
    """Score loaded memories against the assistant's response and update
    lifecycle counters.  Idempotent for a given conversation -- after
    applying, the conversation's loaded-set is cleared.

    Returns counts: {"loaded": N, "used": M, "errors": K}.
    """
    if not conversation_id:
        return {"loaded": 0, "used": 0, "errors": 0}

    loaded = _loaded_per_conversation.get(conversation_id, set())
    if not loaded:
        return {"loaded": 0, "used": 0, "errors": 0}

    if not response_text or not response_text.strip():
        # Nothing to score against; clear and return.  Loaded count is
        # still incremented so next-time-same-memory still gets the
        # "appeared in context" credit.
        _bump_loaded_only(loaded)
        clear_conversation(conversation_id)
        return {"loaded": len(loaded), "used": 0, "errors": 0}

    try:
        from app.services.embedding_service import (
            get_embedding_provider, get_embedding_cache, NoopProvider,
        )
        provider = get_embedding_provider()
        if isinstance(provider, NoopProvider):
            # No embeddings -- bump load count only.
            _bump_loaded_only(loaded)
            clear_conversation(conversation_id)
            return {"loaded": len(loaded), "used": 0, "errors": 0}
        cache = get_embedding_cache()
    except Exception as e:
        logger.debug(f"Feedback: embedding service unavailable: {e}")
        _bump_loaded_only(loaded)
        clear_conversation(conversation_id)
        return {"loaded": len(loaded), "used": 0, "errors": 1}

    # Embed response in sliding windows.  Bedrock Titan handles ~8k tokens
    # per call; a 30-window response is unusual but still cheap.
    windows = _windowize(response_text)
    window_vecs: List[np.ndarray] = []
    for w in windows:
        try:
            v = provider.embed_text(w)
            if v is not None:
                window_vecs.append(v)
        except Exception as e:
            logger.debug(f"Feedback: window embed failed: {e}")
    if not window_vecs:
        _bump_loaded_only(loaded)
        clear_conversation(conversation_id)
        return {"loaded": len(loaded), "used": 0, "errors": 1}

    # Score each loaded memory against best-matching window.
    used_ids: Set[str] = set()
    for mid in loaded:
        mem_vec = cache.get(mid)
        if mem_vec is None:
            continue
        best = max((_cosine(mem_vec, wv) for wv in window_vecs), default=0.0)
        if best >= use_threshold:
            used_ids.add(mid)
            logger.debug(f"Feedback: memory {mid[:8]} used (cos={best:.3f})")

    # Apply updates to active store.
    _apply_updates(loaded, used_ids)

    # Score open proposals too — the response may be independently
    # consistent with a probationary entry, which is corroboration
    # signal even if no one explicitly loaded it.
    proposal_signals = _score_open_proposals(window_vecs, use_threshold)

    clear_conversation(conversation_id)

    if used_ids:
        logger.info(
            f"📤 Feedback for conv {conversation_id[:8]}: "
            f"{len(used_ids)}/{len(loaded)} memories used"
        )
    if proposal_signals:
        logger.info(
            f"📤 Proposal signals for conv {conversation_id[:8]}: "
            f"{proposal_signals} probationary entries received use signals"
        )
    return {"loaded": len(loaded), "used": len(used_ids), "errors": 0}


def _score_open_proposals(window_vecs: List[np.ndarray],
                          use_threshold: float) -> int:
    """Score open proposals against response windows; record signals.

    Returns the number of proposals that received a 'response_match' signal.
    Used by the Diff 7 promotion engine to count independent uses of a
    probationary entry, even when the model didn't explicitly retrieve it.
    """
    try:
        from app.storage.proposals import get_proposals_store
        from app.services.embedding_service import get_embedding_cache
        store = get_proposals_store()
        cache = get_embedding_cache()
    except Exception as e:
        logger.debug(f"Proposal feedback: stores unavailable: {e}")
        return 0

    signaled = 0
    for proposal in store.list_open():
        pid = proposal.get("id")
        if not pid:
            continue
        prop_vec = cache.get(pid)
        if prop_vec is None:
            continue
        best = max((_cosine(prop_vec, wv) for wv in window_vecs), default=0.0)
        if best >= use_threshold:
            store.record_signal(pid, name="response_match",
                                value={"score": round(best, 3)})
            signaled += 1
            logger.debug(f"Feedback: proposal {pid} matched (cos={best:.3f})")
    return signaled


def _bump_loaded_only(memory_ids: Set[str]) -> None:
    """Increment retrieval_loaded_count without trying to detect use."""
    _apply_updates(memory_ids, used_ids=set())


def _apply_updates(loaded: Set[str], used_ids: Set[str]) -> None:
    """Persist counter and importance updates to the active memory store."""
    # Memories the response actually referenced get the longer labile
    # window: a "used" signal is stronger evidence the user is engaged
    # with this fact than mere injection into context.
    if used_ids:
        mark_labile(used_ids, _LABILE_USED_MS)
    try:
        from app.storage.memory import get_memory_storage
        store = get_memory_storage()
        for mid in loaded:
            mem = store.get(mid)
            if not mem:
                continue
            mem.retrieval_loaded_count = (mem.retrieval_loaded_count or 0) + 1
            mem.last_retrieved_at = int(time.time() * 1000)
            if mid in used_ids:
                mem.retrieval_used_count = (mem.retrieval_used_count or 0) + 1
                mem.importance = min(1.0, (mem.importance or 0.5) + _USE_IMPORTANCE_DELTA)
            store.save(mem)
    except Exception as e:
        logger.warning(f"Feedback: failed to persist counter updates: {e}")
