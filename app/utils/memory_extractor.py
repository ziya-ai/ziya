"""
Post-conversation memory extraction.

After a substantive conversation completes, this module:
1. Strips tool results, code blocks, and diffs from the message history
2. Sends the compressed discourse to a small/cheap model
3. Extracts domain facts, decisions, vocabulary, and lessons
4. Deduplicates against the existing memory store
5. Auto-saves low-risk categories, proposes high-stakes ones

Runs as a fire-and-forget background task — never blocks the user.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger


# Categories that are auto-saved without user approval when confidence is "high".
# "high" means the user explicitly stated the fact (not inferred by the model).
# All other combinations go through the proposal flow for user review.
AUTO_SAVE_LAYERS = {"lexicon", "preference"}

# Layers that auto-save ONLY when confidence is "high"
CONDITIONAL_AUTO_SAVE_LAYERS = {"domain_context", "architecture",
                                 "negative_constraint", "process"}

# Minimum conversation turns (human messages) to trigger extraction
MIN_HUMAN_TURNS = 3

# Maximum characters to send to the extraction model (~8K tokens)
MAX_EXTRACTION_INPUT_CHARS = 24_000

# Maximum characters per message after truncation
MAX_MESSAGE_CHARS = 1500

EXTRACTION_SYSTEM_PROMPT = """\
You are a knowledge extraction system. Your job is to find DURABLE KNOWLEDGE \
worth remembering across sessions. You must be extremely selective.

GATE 1 — THE NEXT-SESSION TEST (apply to every candidate, no exceptions):
"Would someone starting a BRAND NEW conversation — with ZERO knowledge of \
what was built, edited, debugged, or discussed today — find this useful?"
If NO → discard. Most conversations produce 0-2 extractable facts, not 5-10.

GATE 2 — SELF-CONTAINMENT (apply to every candidate that passes Gate 1):
A reader with NO surrounding context must understand the memory. It must not \
contain unresolved references like "the document", "the system", "the PR", \
"this component", "the API", "the bug", or "the issue" without a specific \
proper name. If you cannot name it → discard.

GATE 3 — NOT A SESSION ARTIFACT:
Reject anything that is primarily about the CURRENT TASK being performed:
- Editing instructions ("the doc should include X")
- Bug symptoms being actively debugged ("button X is not visible")
- CSS/layout/config fixes to uncommitted code
- Current implementation decisions about transient work products
- TODO items or next steps for work in progress
Extract ONLY the underlying domain truth, if one exists.

REJECT examples (common failures to avoid):
- "The document should avoid political language" → session editing instruction
- "The mute button is not visible due to a regression" → transient bug state
- "Removing marginRight: '8px' fixes the spacing" → transient code fix
- "The document should include per-phase goals" → editing instruction
- "The windowing logic initializes on first mount causing X" → debugging artifact

ACCEPT examples:
- "Component A handles packet forwarding; Component B handles routing policy" → durable system knowledge
- "The ingestion pipeline uses TCAM-based queue routing with per-queue byte counters" → durable architecture fact
- "Exponential backoff with jitter is required for retries to Service X" → durable operational pattern

Output format — for each extracted fact, a JSON object with:
- "content": Distilled principle or fact (1-2 sentences, self-contained)
- "layer": One of the layers below
- "tags": 2-4 lowercase keyword tags (NOT 5+, be selective)
- "confidence": "high" (user explicitly stated) or "medium" (inferred from discussion)

Layers:
- domain_context: What a system/project IS (factual, durable descriptions)
- architecture: How something is structured or built (durable design facts)
- lexicon: Vocabulary, acronyms, disambiguations
- decision: What was chosen and WHY (must name the specific decision)
- negative_constraint: What was tried and rejected, or explicitly ruled out
- preference: User's working style, tool preferences, communication style
- process: How things get done (durable workflows/conventions, NOT current-task instructions)
- active_thread: Current work in progress (use ONLY if nothing more durable applies)

Additional rules:
- Prefer the user's own words over assistant's paraphrasing
- Negative constraints (tried-and-rejected) are high value — always extract these
- If the user corrected the assistant, the correction itself is high-value
- Prefer ONE comprehensive memory over multiple fragments about the same entity
- Do NOT extract meta-commentary about the AI tool itself
- Maximum 2-4 tags per memory. More tags = less findable, not more.
- When in doubt, DO NOT EXTRACT. Silence is better than noise.

Output a JSON array only. No markdown, no explanation. [] if nothing qualifies."""


def strip_conversation(messages: List[Dict[str, Any]]) -> str:
    """Strip tool results, code blocks, and diffs from conversation messages.

    Preserves the discourse — what was discussed, decided, taught —
    while removing artifacts that burn tokens without carrying
    memory-worthy information.
    """
    parts: list[str] = []

    for msg in messages:
        role = msg.get("role", msg.get("type", ""))
        content = msg.get("content", "")

        # Handle list-of-blocks content (Bedrock format)
        if isinstance(content, list):
            text_parts = [
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            content = "\n".join(text_parts)
        elif not isinstance(content, str):
            continue

        if not content.strip():
            continue

        # Normalize role names
        if role in ("human", "user"):
            role_label = "User"
        elif role in ("assistant", "ai"):
            role_label = "Assistant"
        else:
            continue  # Skip system prompts, tool results

        cleaned = _strip_artifacts(content)
        if not cleaned.strip():
            continue

        # Truncate long messages — the gist is enough
        if len(cleaned) > MAX_MESSAGE_CHARS:
            cleaned = cleaned[:MAX_MESSAGE_CHARS] + "..."

        parts.append(f"{role_label}: {cleaned}")

    result = "\n\n".join(parts)

    # Final budget enforcement — keep the end (conclusions live there)
    if len(result) > MAX_EXTRACTION_INPUT_CHARS:
        result = (
            "...[earlier conversation truncated]...\n\n"
            + result[-MAX_EXTRACTION_INPUT_CHARS:]
        )

    return result


def _strip_artifacts(text: str) -> str:
    """Remove code blocks, diffs, tool blocks, and other non-discourse content."""
    # Tool result blocks (3 or 4 backtick variants)
    text = re.sub(r'````tool:[^\n]*\n[\s\S]*?````', '[tool result omitted]', text)
    text = re.sub(r'```tool:[^\n]*\n[\s\S]*?```', '[tool result omitted]', text)

    # HTML tool block comments
    text = re.sub(
        r'<!-- TOOL_BLOCK_START:[^>]+-->[\s\S]*?<!-- TOOL_BLOCK_END:[^>]+-->',
        '[tool result omitted]', text,
    )
    text = re.sub(r'<!-- TOOL_MARKER:[^>]+-->', '', text)

    # Diff blocks
    text = re.sub(r'```diff\n[\s\S]*?```', '[diff omitted]', text)
    text = re.sub(
        r'^diff --git .*?(?=\n\n|\Z)', '[diff omitted]',
        text, flags=re.MULTILINE | re.DOTALL,
    )

    # Fenced code blocks — note the language for context
    def _replace_code(m):
        lang = m.group(1) or ""
        return f"[{lang} code omitted]" if lang else "[code omitted]"
    text = re.sub(r'````(\w*)\n[\s\S]*?````', _replace_code, text)
    text = re.sub(r'```(\w*)\n[\s\S]*?```', _replace_code, text)

    # Base64 blobs
    text = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=]{100,}', '[binary data omitted]', text)

    # REWIND markers
    text = re.sub(r'<!-- REWIND_MARKER:[^>]+-->', '', text)

    # Collapse excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


async def extract_memories(
    stripped_conversation: str,
    existing_memories: List[Dict[str, Any]],
    project_name: Optional[str] = None,
    project_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Call the extraction model to identify memory candidates.

    Uses the Bedrock Converse API with Nova Lite to keep costs low.
    """
    if not stripped_conversation.strip():
        return []

    try:
        # Brief summary of existing memories so the model avoids re-extracting
        existing_summary = _summarize_existing(existing_memories)

        user_content = stripped_conversation

        if existing_summary:
            user_content += (
                "\n\n---\nAlready known (do NOT re-extract these):\n"
                + existing_summary
            )

        from app.services.model_resolver import call_service_model
        output_text = await call_service_model(
            category="memory_extraction",
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            user_message=user_content,
            max_tokens=2048,
            temperature=0.2,
        )

        if not output_text.strip():
            return []

        # Strip markdown fences if the model wrapped the response
        output_text = output_text.strip()
        if output_text.startswith("```"):
            output_text = re.sub(r'^```\w*\n?', '', output_text)
            output_text = re.sub(r'\n?```$', '', output_text)

        candidates = json.loads(output_text)
        if not isinstance(candidates, list):
            logger.warning("Memory extraction: non-list response: %s", type(candidates))
            return []

        logger.info(
            f"🧠 Memory extraction: {len(candidates)} candidate(s) from "
            f"{len(stripped_conversation)} chars of conversation"
        )
        return candidates

    except json.JSONDecodeError as e:
        logger.warning(f"Memory extraction: JSON parse failed: {e}")
        return []
    except Exception as e:
        logger.warning(f"Memory extraction failed (non-fatal): {e}")
        return []


# -- Quality scoring infrastructure -----------------------------------------
#
# Philosophy: the extraction MODEL does semantic filtering (session artifact
# vs durable knowledge).  Code only enforces structural invariants that are
# objectively verifiable — no regex-based NLU.

# Self-containment: penalizes unresolved references like "the document"
# This IS a structural check — it tests for a syntactic pattern (determiner +
# generic noun without a following proper name), not semantic meaning.
_DANGLING_REF_RE = re.compile(
    r'\b(?:the|this|that)\s+(?:document|system|PR|bug|issue|component|API|'
    r'service|module|function|method|class|page|button|feature)\b'
    r'(?!\s+(?:titled|named|called|"' + "|'" + r'|#|\w+\.\w+))',
    re.IGNORECASE,
)

# Structural limits
MAX_TAGS = 5
MIN_CONTENT_CHARS = 20
MAX_CONTENT_CHARS = 500


def quality_gate(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Structural quality gate — enforces objectively verifiable invariants.

    Semantic filtering (session artifact vs durable knowledge) is the
    extraction model's responsibility.  This gate only catches what
    code can verify without understanding meaning:
    - Dangling references (syntactic self-containment)
    - Length bounds (too short = not self-contained, too long = not distilled)
    - Tag count cap
    """
    passed = []
    for c in candidates:
        content = c.get("content", "")
        tags = c.get("tags", [])

        # Structural: cap tags
        if len(tags) > MAX_TAGS:
            c["tags"] = tags[:MAX_TAGS]

        # Structural: length bounds
        if len(content) < MIN_CONTENT_CHARS:
            logger.info(f"🧠 Quality gate REJECT (too short, {len(content)} chars): {content[:80]}")
            continue
        if len(content) > MAX_CONTENT_CHARS:
            logger.info(f"🧠 Quality gate REJECT (too long, {len(content)} chars): {content[:80]}")
            continue

        # Structural: dangling references (2+ = hard reject, 1 = warning-only)
        dangling_hits = len(_DANGLING_REF_RE.findall(content))
        if dangling_hits >= 2:
            logger.info(f"🧠 Quality gate REJECT ({dangling_hits} dangling refs): {content[:80]}")
            continue

        passed.append(c)

    if len(candidates) != len(passed):
        logger.info(f"🧠 Quality gate: {len(candidates)} → {len(passed)} passed")

    return passed


def deduplicate(
    candidates: List[Dict[str, Any]],
    existing_memories: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Filter out candidates that substantially overlap with existing memories."""
    if not existing_memories:
        return candidates

    unique = []
    for candidate in candidates:
        content = candidate.get("content", "").lower()
        tags = set(t.lower() for t in candidate.get("tags", []))

        is_duplicate = False
        for existing in existing_memories:
            ex_content = existing.get("content", "").lower()
            ex_tags = set(t.lower() for t in existing.get("tags", []))

            tag_overlap = len(tags & ex_tags)
            content_words = set(w for w in content.split() if len(w) > 3)
            existing_words = set(w for w in ex_content.split() if len(w) > 3)
            word_overlap = len(content_words & existing_words)

            if tag_overlap >= 3 and word_overlap >= 3:
                is_duplicate = True
                break
            if len(content) > 20 and content in ex_content:
                is_duplicate = True
                break
            if len(ex_content) > 20 and ex_content in content:
                is_duplicate = True
                break

        if not is_duplicate:
            unique.append(candidate)

    if len(candidates) != len(unique):
        logger.info(f"🧠 Dedup: {len(candidates)} → {len(unique)} unique")

    return unique


async def run_post_conversation_extraction(
    messages: List[Dict[str, Any]],
    conversation_id: Optional[str] = None,
    project_name: Optional[str] = None,
    project_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Orchestrate post-conversation memory extraction.

    Fire-and-forget after a conversation stream completes.
    """
    from app.mcp.builtin_tools import is_builtin_category_enabled
    if not is_builtin_category_enabled("memory"):
        return {"skipped": True, "reason": "memory_disabled"}

    human_turns = sum(
        1 for m in messages
        if m.get("role", m.get("type", "")) in ("human", "user")
    )
    if human_turns < MIN_HUMAN_TURNS:
        return {"skipped": True, "reason": f"too_few_turns ({human_turns})"}

    stripped = strip_conversation(messages)
    if len(stripped) < 200:
        return {"skipped": True, "reason": "too_short_after_stripping"}

    # Load existing memories for dedup
    try:
        from app.storage.memory import get_memory_storage
        store = get_memory_storage()
        existing = [m.model_dump() for m in store.list_memories(status="active")]
    except Exception as e:
        logger.warning(f"Memory extraction: could not load existing: {e}")
        existing = []

    candidates = await extract_memories(stripped, existing, project_name, project_path)
    if not candidates:
        return {"extracted": 0, "saved": 0, "proposed": 0}

    # Quality gate: programmatic filter for self-containment and session artifacts
    candidates = quality_gate(candidates)
    if not candidates:
        return {"extracted": 0, "saved": 0, "proposed": 0, "all_rejected_by_gate": True}

    unique = deduplicate(candidates, existing)
    if not unique:
        return {"extracted": len(candidates), "saved": 0, "proposed": 0,
                "all_duplicates": True}

    # Derive a short project label for scope tagging
    _project_label = project_name or (
        project_path.rstrip("/").split("/")[-1] if project_path else None)

    saved = 0
    proposed = 0

    try:
        from app.storage.memory import get_memory_storage
        from app.models.memory import Memory, MemoryProposal
        from app.utils.memory_comparator import find_similar_memories, compare_memory
        store = get_memory_storage()

        for candidate in unique:
            layer = candidate.get("layer", "domain_context")
            content = candidate.get("content", "").strip()
            tags = candidate.get("tags", [])
            confidence = candidate.get("confidence", "medium")

            if not content:
                continue

            # LLM-guided comparison: find similar existing memories and
            # ask the service model whether to ADD, UPDATE, or NOOP.
            # The keyword dedup above catches exact/near-exact duplicates;
            # this catches semantic duplicates, contradictions, and
            # consolidation opportunities that keyword matching misses.
            similar = find_similar_memories(candidate, existing)
            if similar:
                try:
                    decision = await compare_memory(candidate, similar)
                except Exception as cmp_err:
                    logger.warning(f"Memory comparison failed (fail-open → ADD): {cmp_err}")
                    decision = {"action": "ADD"}

                action = decision.get("action", "ADD").upper()

                if action == "NOOP":
                    logger.info(f"🧠 NOOP: Skipping duplicate: {content[:60]}")
                    continue
                elif action == "UPDATE":
                    target_id = decision.get("target_id")
                    if target_id:
                        target_mem = store.get(target_id)
                        if target_mem:
                            target_mem.content = content
                            target_mem.tags = list(set(target_mem.tags + tags))
                            target_mem.last_accessed = __import__('time').strftime("%Y-%m-%d")
                            store.save(target_mem)
                            saved += 1
                            logger.info(f"🧠 UPDATE: Replaced {target_id} with: {content[:60]}")
                            continue
                # Fall through to ADD for action == "ADD" or failed UPDATE

            should_auto_save = (
                (layer in AUTO_SAVE_LAYERS)
                or (layer in CONDITIONAL_AUTO_SAVE_LAYERS and confidence == "high")
            )
            if should_auto_save:
                memory = Memory(
                    content=content, layer=layer, tags=tags,
                    learned_from="auto_extraction",
                )
                if _project_label:
                    memory.scope.project_paths = [project_path or _project_label]
                store.save(memory)
                saved += 1
                try:
                    from app.utils.memory_maintenance import run_post_save_maintenance
                    run_post_save_maintenance(memory.id)
                except Exception:
                    pass
            else:
                proposal = MemoryProposal(
                    content=content, layer=layer, tags=tags,
                    learned_from="auto_extraction",
                    conversation_id=conversation_id,
                )
                if _project_label:
                    proposal.scope = {"project_paths": [project_path or _project_label]}
                store.add_proposal(proposal)
                proposed += 1

    except Exception as e:
        logger.error(f"Memory extraction save/propose failed: {e}")
        return {"extracted": len(candidates), "saved": saved,
                "proposed": proposed, "error": str(e)}

    logger.info(
        f"🧠 Extraction complete: {len(candidates)} extracted, "
        f"{len(candidates) - len(unique)} dupes, "
        f"{saved} auto-saved, {proposed} proposed"
    )

    return {
        "extracted": len(candidates),
        "deduplicated": len(candidates) - len(unique),
        "saved": saved,
        "proposed": proposed,
    }


def _summarize_existing(
    memories: List[Dict[str, Any]], max_chars: int = 2000,
) -> str:
    """Brief summary of existing memories for the extraction model's dedup context."""
    if not memories:
        return ""

    lines = []
    total = 0
    for m in memories:
        content = m.get("content", "")
        tags = ", ".join(m.get("tags", []))
        line = f"- [{m.get('layer', '?')}] {content}"
        if tags:
            line += f" ({tags})"
        if total + len(line) > max_chars:
            lines.append(f"...and {len(memories) - len(lines)} more")
            break
        lines.append(line)
        total += len(line)

    return "\n".join(lines)
