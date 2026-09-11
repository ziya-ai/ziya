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

import asyncio
import json
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

# Caps to bound process-local growth.  A conversation that never reaches
# end-of-turn feedback (tab closed, crash) leaks its loaded-set otherwise;
# a memory marked labile but never re-checked leaks its expiry entry.
_MAX_TRACKED_CONVERSATIONS = 500
_MAX_LABILE_ENTRIES = 2000

# Window durations.  RETRIEVAL covers "user injected this into context";
# USED covers "user demonstrably referenced this in their response"
# (stronger signal, longer window).
_LABILE_RETRIEVAL_MS = 3_600_000      # 1 hour
_LABILE_USED_MS      = 14_400_000     # 4 hours


# Window config.  Approximate character-based windowing rather than real
# tokenization -- we don't need precise alignment, just enough chunks for
# max-pooling to find the relevant section.
#
# Windows are fact-scale on purpose.  Stored memories average ~300 chars;
# comparing one against an 800-char window meant the paraphrase that would
# match it was diluted by ~500 chars of unrelated text in the same window,
# and on the live store the signal fired on 2 of 29 loaded memories.  A
# window the size of the thing it is compared against is the cheapest
# structural fix; the count cap bounds embedding calls on long responses
# by widening the stride rather than truncating the text.
_WINDOW_CHARS = 300
_WINDOW_STRIDE = 150  # 50% overlap so phrase boundaries don't bisect signal
_MAX_WINDOWS = 80

# Cosine threshold for "used".  0.55 was set by intuition, not data; the
# stats rows written by _record_feedback_stats (surfaced at
# /api/v1/memory/feedback/stats) exist so it can be set from the observed
# best-cosine distribution.  Override with ZIYA_MEMORY_USE_THRESHOLD; the
# override is read at call time so it takes effect without a restart.
_USE_THRESHOLD = 0.55

# Rolling calibration log: one row per (loaded memory, response) scoring.
# Bounded so an active install cannot grow it without limit.
FEEDBACK_STATS_MAX_ROWS = 2000

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
    _prune_stale_state()
    if not conversation_id:
        return
    _loaded_per_conversation[conversation_id].update(memory_ids)
    logger.debug(
        f"📥 Recorded {len(memory_ids)} memory load(s) for conv {conversation_id[:8]}: "
        f"{memory_ids[:3]}{'...' if len(memory_ids) > 3 else ''}"
    )


def _prune_stale_state() -> None:
    """Bound process-local growth of the two tracking dicts.

    - Expired labile windows are dropped (is_labile() only cleans entries
      that happen to be re-checked; never-rechecked ones leak otherwise).
    - If still over cap after expiry-prune, evict oldest-expiry entries.
    - Abandoned conversation loaded-sets (no end-of-turn feedback) are
      evicted oldest-first when over cap.  We can't know recency without
      a timestamp, so eviction is arbitrary-but-bounded — acceptable
      because a dropped set only forfeits one turn's use-credit.
    """
    now = int(time.time() * 1000)
    if _labile_until:
        expired = [mid for mid, exp in _labile_until.items() if exp <= now]
        for mid in expired:
            _labile_until.pop(mid, None)
        if len(_labile_until) > _MAX_LABILE_ENTRIES:
            # Keep the most-recently-expiring (longest-lived) windows.
            keep = sorted(_labile_until.items(), key=lambda kv: kv[1],
                          reverse=True)[:_MAX_LABILE_ENTRIES]
            _labile_until.clear()
            _labile_until.update(keep)
    if len(_loaded_per_conversation) > _MAX_TRACKED_CONVERSATIONS:
        overflow = len(_loaded_per_conversation) - _MAX_TRACKED_CONVERSATIONS
        for cid in list(_loaded_per_conversation.keys())[:overflow]:
            _loaded_per_conversation.pop(cid, None)


def get_loaded_memory_ids(conversation_id: str) -> Set[str]:
    """Return all memory IDs loaded into this conversation so far."""
    return _loaded_per_conversation.get(conversation_id, set()).copy()


def clear_conversation(conversation_id: str) -> None:
    """Drop all loaded-memory tracking for a conversation.  Called after
    feedback application so the dict doesn't grow unboundedly."""
    _loaded_per_conversation.pop(conversation_id, None)


def _resolve_use_threshold(explicit: Optional[float] = None) -> float:
    """Explicit argument > ZIYA_MEMORY_USE_THRESHOLD env > module default."""
    if explicit is not None:
        return float(explicit)
    try:
        from app.config.env_registry import ziya_env
        val = ziya_env("ZIYA_MEMORY_USE_THRESHOLD", default=_USE_THRESHOLD)
        return float(val) if val is not None else _USE_THRESHOLD
    except Exception:
        return _USE_THRESHOLD


def _stats_file():
    from app.utils.paths import get_ziya_home
    return get_ziya_home() / "memory" / "feedback_stats.jsonl"


def _record_feedback_stats(rows: List[Dict[str, Any]]) -> None:
    """Append calibration rows; keep only the newest FEEDBACK_STATS_MAX_ROWS.

    Plain JSONL, not ALE-encrypted: rows hold ids and scores, never
    memory content.  Best-effort -- never raises into the feedback path.
    """
    if not rows:
        return
    try:
        path = _stats_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        existing: List[str] = []
        if path.exists():
            existing = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        new_lines = [json.dumps({"ts": ts, **r}, ensure_ascii=False) for r in rows]
        kept = (existing + new_lines)[-FEEDBACK_STATS_MAX_ROWS:]
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.debug(f"Feedback stats write skipped (non-fatal): {e}")


def load_feedback_stats() -> List[Dict[str, Any]]:
    """Return the rolling calibration rows, oldest first."""
    try:
        path = _stats_file()
        if not path.exists():
            return []
        out = []
        for ln in path.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
        return out
    except Exception as e:
        logger.debug(f"Feedback stats read failed: {e}")
        return []


def feedback_stats_summary() -> Dict[str, Any]:
    """Percentiles of best-cosine and the hit rate at the current threshold.

    This is the number the threshold should be set from: if p90 sits well
    below the threshold the signal is dead; if p50 sits above it the
    signal is noise.
    """
    rows = load_feedback_stats()
    threshold = _resolve_use_threshold()

    def _block(rs: List[Dict[str, Any]]) -> Dict[str, Any]:
        cos = sorted(float(r.get("best_cos", 0.0)) for r in rs)
        if not cos:
            return {"count": 0, "used": 0, "hit_rate": 0.0}
        arr = np.array(cos)
        used = sum(1 for r in rs if r.get("used"))
        return {
            "count": len(cos), "used": used, "hit_rate": used / len(cos),
            "p10": float(np.percentile(arr, 10)), "p50": float(np.percentile(arr, 50)),
            "p90": float(np.percentile(arr, 90)), "max": float(arr.max()),
        }

    kinds = sorted({r.get("kind", "memory") for r in rows})
    return {
        "threshold": threshold,
        **_block(rows),
        "by_kind": {k: _block([r for r in rows if r.get("kind", "memory") == k]) for k in kinds},
    }


def _windowize(text: str, size: int = _WINDOW_CHARS,
               stride: int = _WINDOW_STRIDE) -> List[str]:
    """Slice text into overlapping windows suitable for max-pool scoring.

    Window count is capped at _MAX_WINDOWS by widening the stride (not by
    truncating), so a very long response still has its tail scored.
    """
    if not text or len(text) <= size:
        return [text] if text else []
    needed = (len(text) - size) // stride + 2
    if needed > _MAX_WINDOWS:
        stride = max(stride, -(-(len(text) - size) // (_MAX_WINDOWS - 1)))
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
    use_threshold: Optional[float] = None,
) -> Dict[str, int]:
    """Score loaded memories against the assistant's response and update
    lifecycle counters.  Idempotent for a given conversation -- after
    applying, the conversation's loaded-set is cleared.

    Returns counts: {"loaded": N, "used": M, "errors": K}.
    """
    use_threshold = _resolve_use_threshold(use_threshold)
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

    def _embed_windows() -> List[np.ndarray]:
        # Runs in a worker thread: provider.embed_text is a blocking
        # Bedrock HTTP call, and apply_feedback is dispatched as a
        # fire-and-forget task on the main event loop.  Embedding inline
        # would stall every other coroutine for the duration.
        vecs: List[np.ndarray] = []
        for w in windows:
            try:
                v = provider.embed_text(w)
                if v is not None:
                    vecs.append(v)
            except Exception as e:
                logger.debug(f"Feedback: window embed failed: {e}")
        return vecs

    window_vecs: List[np.ndarray] = await asyncio.to_thread(_embed_windows)
    if not window_vecs:
        _bump_loaded_only(loaded)
        clear_conversation(conversation_id)
        return {"loaded": len(loaded), "used": 0, "errors": 1}

    # Score each loaded memory against best-matching window.
    used_ids: Set[str] = set()
    stats_rows: List[Dict[str, Any]] = []
    for mid in loaded:
        mem_vec = cache.get(mid)
        if mem_vec is None:
            continue
        best = max((_cosine(mem_vec, wv) for wv in window_vecs), default=0.0)
        used = best >= use_threshold
        stats_rows.append({"memory_id": mid, "best_cos": round(best, 4), "used": used,
                           "kind": "memory", "threshold": use_threshold,
                           "n_windows": len(window_vecs)})
        if used:
            used_ids.add(mid)
            logger.debug(f"Feedback: memory {mid[:8]} used (cos={best:.3f})")

    # Apply updates to active store.
    _apply_updates(loaded, used_ids)

    # Score open proposals too — the response may be independently
    # consistent with a probationary entry, which is corroboration
    # signal even if no one explicitly loaded it.
    proposal_signals = _score_open_proposals(window_vecs, use_threshold, stats_rows)
    _record_feedback_stats(stats_rows)

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
                          use_threshold: float,
                          stats_rows: Optional[List[Dict[str, Any]]] = None) -> int:
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
        used = best >= use_threshold
        if stats_rows is not None:
            stats_rows.append({"memory_id": pid, "best_cos": round(best, 4), "used": used,
                               "kind": "proposal", "threshold": use_threshold,
                               "n_windows": len(window_vecs)})
        if used:
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
        mutated = []
        for mid in loaded:
            mem = store.get(mid)
            if not mem:
                continue
            mem.retrieval_loaded_count = (mem.retrieval_loaded_count or 0) + 1
            mem.last_retrieved_at = int(time.time() * 1000)
            if mid in used_ids:
                mem.retrieval_used_count = (mem.retrieval_used_count or 0) + 1
                mem.importance = min(1.0, (mem.importance or 0.5) + _USE_IMPORTANCE_DELTA)
            mutated.append(mem)
        # One batched write instead of N full-file rewrites.
        store.save_many(mutated)
    except Exception as e:
        logger.warning(f"Feedback: failed to persist counter updates: {e}")
