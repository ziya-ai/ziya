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
) -> List[Dict[str, Any]]:
    """Find the most similar existing memories.

    Uses embedding similarity when available, falls back to tag + word
    overlap scoring.
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
                results = cache.search(vec, top_k=top_n)
                if results:
                    result_ids = {mid for mid, _ in results}
                    matched = [m for m in existing if m.get("id") in result_ids]
                    if matched:
                        return matched
                    # Embedding hits didn't overlap with provided existing list — fall through
    except Exception as e:
        logger.debug(f"Embedding similarity unavailable, using keyword fallback: {e}")

    # Fallback: tag + word overlap (original logic)
    cand_tags = set(t.lower() for t in candidate.get("tags", []))
    cand_words = set(
        w.lower() for w in candidate.get("content", "").split() if len(w) > 3
    )

    scored = []
    for mem in existing:
        if mem.get("status", "active") != "active":
            continue
        mem_tags = set(t.lower() for t in mem.get("tags", []))
        mem_words = set(
            w.lower() for w in mem.get("content", "").split() if len(w) > 3
        )
        tag_score = len(cand_tags & mem_tags)
        word_score = len(cand_words & mem_words)
        total = tag_score * 2 + word_score  # weight tags higher
        if total > 0:
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
