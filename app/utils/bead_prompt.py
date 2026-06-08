"""
Bead system prompt injection — teaches the model to track subtasks silently.

Injected into the system message when the beads category is enabled.
The model calls bead_create/bead_complete/bead_status as part of its
internal workflow; the user never sees these calls or their output.
"""
import os
from typing import Optional

from app.utils.logging_utils import logger


BEAD_DIRECTIVE = """\

## Task Tree (Beads)

You have an internal task-tracking system called "beads". Use it to maintain \
a tree of what you're working on so you never lose track of threads.

**Rules:**
- Call `bead_create` when you identify a new subtask or the user mentions \
something you'll need to come back to later. Use status="parked" for things \
you're noting for later, status="active" to switch to working on them now.
- Call `bead_complete` when you finish a subtask. This resumes the parent.
- Call `bead_status` at the start of a conversation to see what's pending.
- These tools are invisible to the user. Do NOT mention beads in your responses.
- Create beads proactively when you notice multiple threads developing. \
It's better to have a bead you don't need than to lose track of a thread.
- If the user says something like "let's come back to that" or "also, ...", \
that's a signal to park a bead.
- Keep bead content short (2-8 words): "fix CSS layout", "review auth flow", \
"investigate memory leak".

**When to create beads:**
- User asks multiple questions → bead per question
- You identify prerequisite subtasks → active bead for prereq, parked for main
- User pivots topic mid-stream → park the old topic, active the new
- "While we're at it..." / "also..." / "btw..." → parked bead for the aside
- **You notice an unrelated bug or issue while working on the requested \
task** ("While solving this, I also noticed:" / "noticed in passing:" / \
"this also looks suspicious:") → park a bead immediately. The user did \
not ask for this work, so don't fix it now, but the observation is real \
and would otherwise be lost when the conversation moves on.
- **You present a numbered list of next-steps / options the user must \
choose between** → park a bead for each item the user does NOT pick. \
The user picking #1 means #2-#N are unfollowed branches; without beads \
they're lost the moment the conversation moves on.

**When NOT to create beads:**
- Simple single-shot questions
- The conversation has only one clear thread
- A topic is fully resolved in a single exchange
- Numbered steps of a SINGLE task ("first do A, then B, then C" is one \
bead, not three) — beads track distinct threads, not sequential steps
- Enumerated considerations or trade-offs ("here are things to think \
about") that aren't committed work
"""


def get_bead_directive() -> str:
    """Return the bead system-prompt directive if beads are enabled."""
    from app.mcp.builtin_tools import is_builtin_category_enabled
    if not is_builtin_category_enabled("beads"):
        return ""
    if _is_ephemeral():
        return ""
    return BEAD_DIRECTIVE


def get_bead_status_summary() -> str:
    """Return a compact summary of the current bead tree for injection.

    Only included when there are parked beads — gives the model awareness
    of pending threads without bloating the prompt for simple conversations.
    """
    from app.mcp.builtin_tools import is_builtin_category_enabled
    if not is_builtin_category_enabled("beads"):
        return ""
    if _is_ephemeral():
        return ""

    try:
        from app.storage.beads import load_bead_tree
        tree = load_bead_tree()
        if not tree.beads:
            return ""
        parked = tree.parked_beads
        if not parked:
            return ""

        lines = ["\n\n### Pending Beads (parked threads):"]
        for b in parked:
            hint = f" — {b.context_hint}" if b.context_hint else ""
            lines.append(f"- ⏸ {b.content}{hint}")
        active = tree.active_bead
        if active:
            lines.append(f"\nCurrently working on: {active.content}")
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"Bead status summary failed (non-fatal): {e}")
        return ""


def _is_ephemeral() -> bool:
    """Whether the current context is ephemeral (no bead persistence).

    Returns True when:
      - Global ephemeral mode is active (CLI --ephemeral / ZIYA_EPHEMERAL=1)
      - The current conversation has no backing chat record (frontend
        ephemeral conversations are never pushed to the server, so the
        chat record doesn't exist on disk)

    In either case, beads would have nowhere to persist — so we suppress
    the prompt directive entirely, preventing the model from wasting tool
    calls on state that won't survive.
    """
    # Global ephemeral mode (CLI --ephemeral flag)
    if os.environ.get("ZIYA_EPHEMERAL", "").lower() in ("1", "true", "yes"):
        return True

    # Per-conversation check: does the chat record exist?
    try:
        from app.context import get_conversation_id_or_none
        conv_id = get_conversation_id_or_none()
        if not conv_id:
            return True  # No conversation context → can't persist beads
        from app.storage.beads import _resolve_chat_storage
        storage, _ = _resolve_chat_storage()
        chat = storage.get(conv_id)
        return chat is None
    except (ValueError, ImportError):
        return True  # Can't resolve storage → treat as ephemeral
