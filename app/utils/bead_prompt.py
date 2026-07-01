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
- The words "parked"/"park"/"I've parked" in your response text are a CLAIM \
that you called `bead_create` (status="parked"). If you write any of them \
without having actually made that tool call, you are misrepresenting an \
action you did not take. Either make the `bead_create` call or do not use \
the word. The same binding applies to saying a thread is "tracked".
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
    """Return the bead system-prompt directive if beads are enabled.

    The directive is *instructions* — teaching the model that beads exist
    and when to use them.  It is gated ONLY on the category being enabled
    and on true global ephemeral mode.  It deliberately does NOT depend on
    whether the current conversation's chat record is resolvable/on-disk:
    doing so silently stripped the model's only instruction to use beads
    whenever ``_resolve_chat_storage()`` hiccuped (unregistered project
    path, ContextVar timing, browser-mode resolution), which read to the
    user as "beads never activate".  The actual persistence decision lives
    in the tool's ``execute()`` (``_is_ephemeral_context``), which skips the
    write cleanly when a chat genuinely can't persist.
    """
    from app.mcp.builtin_tools import is_builtin_category_enabled
    if not is_builtin_category_enabled("beads"):
        logger.debug("📿 bead directive: suppressed (category disabled)")
        return ""
    if _is_global_ephemeral():
        logger.debug("📿 bead directive: suppressed (global ephemeral mode)")
        return ""
    return BEAD_DIRECTIVE


def get_bead_status_summary(turn_count: int = 0) -> str:
    """Return a compact summary of the current bead tree for injection.

    Only included when there are parked beads — gives the model awareness
    of pending threads without bloating the prompt for simple conversations.

    When there are *no* beads yet but the conversation has become multi-turn
    (``turn_count`` >= 3), emit a brief live nudge instead of staying silent.
    The static directive is buried deep in a long system prompt by the time a
    session gets busy; this puts the trigger conditions back into recent
    context every turn — precisely when threads are most likely to be dropped.
    Single-shot / short exchanges stay quiet, preserving the original design.
    """
    from app.mcp.builtin_tools import is_builtin_category_enabled
    if not is_builtin_category_enabled("beads"):
        return ""
    if _is_global_ephemeral():
        return ""

    try:
        from app.storage.beads import load_bead_tree
        tree = load_bead_tree()
        parked = tree.parked_beads if tree.beads else []
        if not parked:
            # No pending threads. Stay silent for short conversations, but
            # for ongoing ones surface a one-line reminder so the trigger
            # conditions aren't lost at the bottom of a long static prompt.
            if turn_count >= 3:
                return (
                    "\n\n### Bead check: no threads tracked yet. "
                    "If this turn you flagged an aside, noticed an unrelated "
                    "issue ('noticed in passing'), or listed options the user "
                    "didn't all pick, park a bead now before it's lost."
                )
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


def _is_global_ephemeral() -> bool:
    """Whether global ephemeral mode is active (CLI --ephemeral / ZIYA_EPHEMERAL=1).

    This is the ONLY condition under which the bead *directive* is
    suppressed.  In global ephemeral mode nothing persists for the whole
    session, so teaching the model to track threads would only produce
    tool calls that no-op.

    Per-conversation persistability is deliberately NOT checked here — that
    decision belongs to the tool's ``execute()`` (``_is_ephemeral_context``),
    so a transient project-resolution failure never strips the model's
    instructions.  Coupling the directive to chat-resolvability was the
    cause of the "beads never activate even deep in conversations" bug: any
    ``_resolve_chat_storage()`` hiccup (unregistered project path, ContextVar
    timing, browser-mode resolution) silently removed the only instruction
    telling the model that beads exist.
    """
    # Both names are checked: the server sets ZIYA_EPHEMERAL_MODE
    # (app/main.py), while ZIYA_EPHEMERAL is the documented manual override.
    for var in ("ZIYA_EPHEMERAL", "ZIYA_EPHEMERAL_MODE"):
        if os.environ.get(var, "").lower() in ("1", "true", "yes"):
            logger.debug(f"📿 bead directive gate: global ephemeral mode active ({var})")
            return True
    return False
