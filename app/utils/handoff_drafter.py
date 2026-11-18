"""
Draft a conversation's handoff from a separate, small conversation.

"Ask the model to draft it" runs a background turn on the source
conversation's full context.  A conversation that has outgrown its model's
context window cannot run that turn: the request itself is rejected.  The
fallback is a NEW conversation (the "drafter") that starts empty, reads the
source in windows with chat_read / chat_search, and writes the source's
handoff draft with handoff_write(target_conversation_id=<source>).  The user
then commits the continuation from the source's Hand off drawer, as usual.

The drafter record carries ``handoffDraftFor = <source id>``.  That field is
the only thing that lets handoff_write / handoff_read act on a conversation
other than the caller's own (see may_target), and the prelude suppresses the
"draft your own handoff" instruction on a drafter.
"""
import uuid
from typing import Any, Dict, Optional

from app.utils.handoff_prelude import DRAFT_FOR_FIELD

__all__ = ["DRAFT_FOR_FIELD", "drafter_title", "drafter_prompt",
           "new_drafter_record", "may_target"]


def drafter_title(source_title: str) -> str:
    base = (source_title or "").strip() or "Conversation"
    return f"Handoff draft: {base}"


def drafter_prompt(source_id: str, source_title: str, message_count: int) -> str:
    """First message of the drafter conversation.  Pure; tested."""
    title = (source_title or "").strip() or source_id
    sid = source_id
    return (
        f'Conversation "{title}" (id {sid}, {message_count} messages) is too large to '
        "write its own handoff document: asking it to draft one exceeded the model's "
        "context limit. Write that handoff draft from here, by reading the conversation "
        "in pieces. Your own context is limited too, so never load all of it at once and "
        "keep your replies short.\n\n"
        f'1. Call handoff_read(target_conversation_id="{sid}") to see any existing draft.\n'
        f'2. Read with chat_read(chat_id="{sid}", message_start=..., message_end=..., '
        'tool_results="summary", max_total_chars=16000), about 20-40 messages per call. '
        "Read the first few messages (the objective), then the last 40 or so (the current "
        "state), then work backwards through the middle. Use "
        f'chat_search(query=..., conversation_ids=["{sid}"]) to find specific terms instead '
        'of reading everything, and tool_results="full" only for a result that matters.\n'
        "3. Every few windows, save what you have learned: "
        f'handoff_write(target_conversation_id="{sid}", mode="merge", sections={{...}}) with '
        "objective, state, decisions (with messageIndex references), gotchas and "
        "references. Saving as you go keeps the work if your own context runs out. Keep "
        "notes, not copied transcript.\n"
        "4. Read as much as your context allows. Stop when the remaining messages are "
        "unlikely to change the draft or your context is getting full, then make a final "
        "merge so every section is filled.\n"
        "5. Do not create a conversation and do not write files. Finish with a short reply: "
        "which message ranges you read, which you skipped, and the link "
        f"[{title}](ziya://chat/{sid}) so the user can open it and create the continuation "
        "from its Hand off drawer."
    )


def new_drafter_record(src: Dict[str, Any], source_id: str, project_id: str,
                       now: int, drafter_id: Optional[str] = None) -> Dict[str, Any]:
    """Chat record for the drafter: empty, in the source's folder, visible
    wherever the source is.  No lineage fields: it is not a continuation and
    must not share the source's bead tree."""
    return {
        "id": drafter_id or str(uuid.uuid4()),
        "title": drafter_title(src.get("title") or ""),
        "messages": [],
        "createdAt": now,
        "lastActiveAt": now,
        "lastAccessedAt": now,
        "_version": now,
        "projectId": project_id,
        "folderId": src.get("folderId"),
        "groupId": src.get("groupId"),
        "isActive": True,
        "isGlobal": bool(src.get("isGlobal")),
        "contextIds": [],
        "skillIds": [],
        "additionalFiles": [],
        "additionalPrompt": None,
        DRAFT_FOR_FIELD: source_id,
    }


def may_target(caller: Optional[Dict[str, Any]], target_id: Optional[str]) -> bool:
    """Whether the caller's record may read/write ``target_id``'s draft."""
    if not caller or not target_id:
        return False
    if caller.get("id") == target_id:
        return True
    return caller.get(DRAFT_FOR_FIELD) == target_id
