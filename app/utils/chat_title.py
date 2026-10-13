"""
Placeholder-title handling shared by server-side chat merges.

Mirror of frontend/src/utils/conversationTitle.ts (PLACEHOLDER_TITLES and
mergeTitle); the two sets must agree.  A conversation's real title is
derived in the browser from the first human message, while several backend
writers (tool auto-vivify in context_management, bead/handoff/auto-context
writes) create or re-stamp the record with the "New Conversation"
placeholder and a fresh ``_version``.  A merge that lets a placeholder
replace a real title loses it permanently, because derivation only fires
while no human message exists.
"""

from __future__ import annotations

from typing import Optional

PLACEHOLDER_TITLES = frozenset({
    "New Conversation", "New Ephemeral Chat", "Loading...", "Untitled", "",
})


def is_placeholder_title(title: Optional[str]) -> bool:
    return (title or "") in PLACEHOLDER_TITLES


def merge_title(incoming: Optional[str], existing: Optional[str]) -> Optional[str]:
    """Title to persist when ``incoming`` is merged over ``existing``.

    A real incoming title wins (a rename); a placeholder never replaces a
    real existing title.  When both are placeholders the incoming one wins.
    """
    if not is_placeholder_title(incoming):
        return incoming
    if not is_placeholder_title(existing):
        return existing
    return incoming if incoming is not None else existing
