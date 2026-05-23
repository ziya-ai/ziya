"""
LLM-guided memory comparison — decides ADD / UPDATE / NOOP for new memories.

When a new memory candidate arrives (from extraction or tool-initiated save),
this module compares it against the most similar existing memories using a
cheap service model call.  This replaces keyword-based deduplication with
semantic understanding, handling contradictions, supersession, paraphrases,
and consolidation that keyword matching cannot detect.

Inspired by Mem0's two-phase pipeline (extract → compare) and FadeMem's
LLM-guided conflict resolution.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger


COMPARE_PROMPT = """\
You are a memory deduplication system. Compare a NEW memory candidate \
against EXISTING memories and decide what action to take.

Actions:
- ADD: The new memory is genuinely new information not covered by any existing memory.
- UPDATE <id>: The new memory supersedes, corrects, or consolidates an existing memory. \
  The existing memory should be replaced with the new content. Return the ID of the memory to replace.
- NOOP: The new memory is a duplicate or subset of an existing memory. Discard it.

Rules:
- If the new memory CONTRADICTS an existing memory, choose UPDATE (newer info wins).
- If the new memory is a more complete version of an existing memory, choose UPDATE.
- If the new memory adds detail to a different aspect of the same topic, choose ADD (both are valuable).
- If the new memory says essentially the same thing in different words, choose NOOP.
- When choosing UPDATE, pick the single most relevant existing memory to replace.

Respond with ONLY a JSON object: {"action": "ADD"} or {"action": "UPDATE", "target_id": "<id>"} \
or {"action": "NOOP"}. No explanation."""


def find_similar_memories(
    candidate: Dict[str, Any],
    existing: List[Dict[str, Any]],
    top_n: int = 5,
    min_similarity: float = 0.55,
) -> List[Dict[str, Any]]:
    """Find the most similar existing memories.

    Uses embedding similarity when available, falls back to tag + word
    overlap scoring.

    ``min_similarity`` filters embedding-based hits below the cosine
    threshold.  Without it, ``cache.search`` returns the top-K unconditionally
    even when the active store has nothing topically related to the candidate
    — wasting an LLM comparator call on guaranteed-ADD decisions.
    Empirically, scores below ~0.55 are random co-occurrence (e.g. an MCP
    infra fact "matching" a React performance fact); 0.55+ starts capturing
    genuine topical overlap.
    """
    if not existing:
        return []

    # Try embedding-based similarity first
    try:
        from app.services.embedding_service import (
            get_embedding_provider, get_embedding_cache,
            NoopProvider, embed_and_cache
        )
        provider = get_embedding_provider()
        if not isinstance(provider, NoopProvider):
            # Embed the candidate
            vec = provider.embed_text(candidate.get("content", ""))
            if vec is not None:
                cache = get_embedding_cache()
                # Cache may contain both m_* (active) and prop_* (probationary)
                # entries.  Only m_* are valid as "existing memories" the
                # comparator decides against — prop_* would let a candidate
                # match its OWN freshly-embedded probationary proposal at
                # ~0.98 cosine, falsely reporting itself as a similar memory.
                # Bump top_k to leave headroom for prop_* filtering.
                results = cache.search(vec, top_k=top_n * 4)
                if results:
                    # Filter by minimum cosine AND restrict to m_* keys.
                    # Without the m_* gate, candidates re-discover their own
                    # prop_* embeddings and the comparator wastes calls
                    # disambiguating self-matches.
                    above_threshold = [
                        (mid, score) for mid, score in results
                        if score >= min_similarity
                        and isinstance(mid, str)
                        and mid.startswith("m_")
                    ][:top_n]
                    if above_threshold:
                        active_ids = {mid for mid, _ in above_threshold}
                        matched = [m for m in existing
                                   if m.get("id") in active_ids]
                        if matched:
                            return matched
                    # No active-memory hits cleared the threshold — fall
                    # through to keyword scoring.
    except Exception as e:
        logger.debug(f"Embedding similarity unavailable, using keyword fallback: {e}")

    # Fallback: tag + word overlap (original logic)
    cand_tags = set(t.lower() for t in candidate.get("tags", []))
    cand_words = set(
        w.lower() for w in candidate.get("content", "").split() if len(w) > 3
    )

    # Generic English filler words that two unrelated technical facts will
    # routinely share (e.g. "system", "with", "from", "the", "this") and
    # which create false matches when the score floor is just `total > 0`.
    # Excluding them sharpens the keyword-fallback signal considerably.
    _STOPWORDS = {
        "this", "that", "with", "from", "have", "been", "their", "them",
        "they", "these", "those", "also", "more", "than", "such", "when",
        "where", "what", "which", "while", "system", "systems", "type",
        "types", "case", "cases", "user", "users", "data",
    }
    cand_words = cand_words - _STOPWORDS

    # Minimum overlap required to even be a candidate.  Two genuinely
    # unrelated facts typically share 0-1 content words once stopwords
    # are excluded; a single tag like "design" or "system" is too
    # generic to count as topical similarity on its own.
    #   - Tag-only path:    require >= 2 tags in common.
    #   - Word-only path:   require >= 2 content words in common.
    #   - Hybrid path:      1 tag + 1 word counts.
    # This prevents single-tag matches like "design" from triggering
    # the LLM comparator across totally unrelated technical domains.
    MIN_KEYWORD_OVERLAP = 2

    scored = []
    for mem in existing:
        if mem.get("status", "active") != "active":
            continue
        mem_tags = set(t.lower() for t in mem.get("tags", []))
        mem_words = set(
            w.lower() for w in mem.get("content", "").split() if len(w) > 3
        ) - _STOPWORDS
        tag_score = len(cand_tags & mem_tags)
        word_score = len(cand_words & mem_words)
        # Combined-evidence floor: total signal must be >= 2.
        # A single matching tag (tag_score=1, word_score=0) → total=2 looks
        # like signal but in practice means "shared a generic vocabulary
        # bucket" — bumped from total>=1 (the original logic) to total>=3
        # so a lone tag isn't enough.
        if tag_score == 0 and word_score < MIN_KEYWORD_OVERLAP:
            continue
        if tag_score >= 1 and word_score == 0 and tag_score < 2:
            continue
        total = tag_score * 2 + word_score
        if total >= 3:
            scored.append((total, mem))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:top_n]]


async def compare_memory(
    candidate: Dict[str, Any],
    similar: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Ask the service model to classify: ADD / UPDATE / NOOP.

    Returns {"action": "ADD"}, {"action": "UPDATE", "target_id": "..."}, or {"action": "NOOP"}.
    Falls back to ADD on any error (fail-open: better to have a near-dupe than lose knowledge).
    """
    existing_text = "\n".join(
        f"- [{m.get('id', '?')}] ({m.get('layer', '?')}) {m.get('content', '')}"
        for m in similar
    )
    user_msg = (
        f"NEW MEMORY:\n({candidate.get('layer', '?')}) {candidate.get('content', '')}\n"
        f"Tags: {', '.join(candidate.get('tags', []))}\n\n"
        f"EXISTING MEMORIES:\n{existing_text}"
    )

    try:
        from app.services.model_resolver import call_service_model
        raw = await call_service_model(
            category="memory_comparison",
            system_prompt=COMPARE_PROMPT,
            user_message=user_msg,
            max_tokens=100,
            temperature=0.0,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        result = json.loads(raw)
        action = result.get("action", "ADD").upper()
        if action not in ("ADD", "UPDATE", "NOOP"):
            return {"action": "ADD"}
        return result
    except Exception as e:
        logger.warning(f"Memory comparison failed (fail-open → ADD): {e}")
        return {"action": "ADD"}
