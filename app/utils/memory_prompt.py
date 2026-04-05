"""
Dynamic prompt generation for the structured memory system.

Called per-request from precision_prompt_system.py.  Loads existing
memories from the flat store and formats them as a compact context
block the model can reference during conversation.  Also provides
behavioral guidance so the model knows when to search, save, and
propose memories proactively.

Phase 0: loads the entire flat store (suitable for ≤~80 memories).
Phase 1 will introduce progressive loading via mind-map handles.
"""

import os
from typing import Optional

from app.utils.logging_utils import logger


def get_memory_prompt_section() -> str:
    """
    Build the memory context block for the system prompt.

    Returns an empty string if the memory category is disabled or
    no memories exist.
    """
    from app.mcp.builtin_tools import is_builtin_category_enabled
    if not is_builtin_category_enabled("memory"):
        return ""

    try:
        from app.storage.memory import get_memory_storage
        store = get_memory_storage()
        memories = store.list_memories(status="active")
        pending_count = len(store.list_proposals())
        mindmap_nodes = store.list_mindmap_nodes()
    except Exception as e:
        logger.debug(f"Could not load memories for prompt: {e}")
        return ""

    # ── Progressive loading (Phase 1) ──────────────────────────────
    # If a mind-map exists, load Level 0 handles (~500 tokens) instead
    # of dumping every memory (~40 tokens each × N).  The model uses
    # memory_context / memory_expand to go deeper when needed.
    if mindmap_nodes:
        root_nodes = store.get_root_nodes()
        lines = [
            "",
            "## Persistent Memory",
            "",
            _BEHAVIORAL_GUIDANCE,
            "",
            "### Domain Overview (use `memory_context`/`memory_expand` for detail)",
            "",
        ]
        for r in root_nodes:
            child_count = len(r.children)
            mem_count = len(r.memory_refs)
            lines.append(f"- **{r.handle}** — `{r.id}` ({mem_count} memories, {child_count} sub-topics)")
        lines.append("")
        lines.append(f"*{len(memories)} total memories across {len(root_nodes)} domains.*")
        if pending_count > 0:
            lines.append(f"*{pending_count} memory proposal(s) awaiting user review.*")
        return "\n".join(lines)

    # ── Flat dump (Phase 0 fallback) ───────────────────────────────
    # No mind-map configured — load all memories directly.
    lines = [
        "",
        "## Persistent Memory",
        "",
        _BEHAVIORAL_GUIDANCE,
    ]

    if memories:
        lines.append("")
        lines.append("### Known Facts")
        lines.append("")

        # Group by layer for readability
        by_layer: dict[str, list] = {}
        for m in memories:
            by_layer.setdefault(m.layer, []).append(m)

        # Render each layer compactly
        _LAYER_LABELS = {
            "domain_context": "Domain",
            "architecture": "Architecture",
            "lexicon": "Vocabulary",
            "decision": "Decisions",
            "active_thread": "Active Work",
            "process": "Process",
            "preference": "Preferences",
            "negative_constraint": "Lessons (avoid)",
        }

        for layer_key in [
            "preference", "lexicon", "domain_context", "architecture",
            "decision", "negative_constraint", "active_thread", "process",
        ]:
            items = by_layer.get(layer_key, [])
            if not items:
                continue
            label = _LAYER_LABELS.get(layer_key, layer_key)
            lines.append(f"**{label}:**")
            for m in items:
                tag_str = f" [{', '.join(m.tags)}]" if m.tags else ""
                lines.append(f"- {m.content}{tag_str}")
            lines.append("")
    else:
        lines.append("")
        lines.append(
            "No memories stored yet. As you learn about the user's domain, "
            "use `memory_propose` to suggest facts worth retaining."
        )

    if pending_count > 0:
        lines.append(f"*{pending_count} memory proposal(s) awaiting user review.*")

    return "\n".join(lines)


_BEHAVIORAL_GUIDANCE = """You have a persistent memory system that retains knowledge across sessions.

**Behavior rules:**
- DO NOT announce what you remember. Simply be informed — use memories silently to give better answers.
- When the user teaches you domain facts, vocabulary, architecture decisions, or lessons learned, use `memory_propose` to suggest saving them. Batch proposals at natural pauses (topic shifts, before tool calls), not after every sentence.
- When the user explicitly says "/remember" or "save this", use `memory_save` directly.
- When conversation touches topics that may have prior context, use `memory_search` to check.
- Negative constraints (things tried and rejected) are especially valuable — always propose saving these.
- A memory earns its place only if removing it would force the user to re-explain something next session.
- Content should be distilled principles and facts, not raw conversation transcript."""
