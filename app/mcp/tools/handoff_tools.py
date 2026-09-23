"""
Handoff draft tools — the standardized interface for the living handoff
document (design/conversation-handoff.md, "the living draft").

As context tightens, models spontaneously start maintaining a handoff file
with a made-up name and location.  These tools give that instinct a home:
the draft lives on the current conversation's chat record (``handoffDraft``),
the drawer reads and edits the same record, and the commit endpoint copies
it into the continuation.

  - handoff_read   : current draft (or none), inherited handoff (if this is a
                     continuation), and the last measured context pressure.
  - handoff_write  : upsert the draft.  ``merge`` (default) overwrites only
                     the named sections; ``replace`` supersedes the document.

Record resolution and the _version bump mirror context_management so a
write here is picked up by the frontend's next sync exactly like a
context_add_file.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.mcp.tools.context_management import _resolve_chat_for_request
from app.utils.handoff_prelude import (
    DRAFT_FIELD, DRAFT_SECTIONS, HANDOFF_FIELD, get_context_pressure,
    merge_draft, nudge_ratio,
)
from app.utils.logging_utils import logger


def _persist(ctx: Dict[str, Any], chat_data: Dict[str, Any]) -> None:
    import time
    now = int(time.time() * 1000)
    chat_data["lastActiveAt"] = now
    chat_data["_version"] = now
    ctx["storage"]._write_json(ctx["chat_file"], chat_data)


# ---------------------------------------------------------------------------
# handoff_read
# ---------------------------------------------------------------------------

class HandoffReadInput(BaseModel):
    """No parameters — reads the current conversation's draft."""


class HandoffReadTool(BaseMCPTool):
    name: str = "handoff_read"
    description: str = (
        "Read this conversation's handoff draft — the document a continuation "
        "conversation will receive if the user hands off.  Returns the draft "
        "(or null), the inherited handoff document if THIS conversation is "
        "itself a continuation, and the context pressure measured on the "
        "previous turn (fraction of the model's limit).  Read before a "
        "merge so you update the right sections."
    )
    InputSchema = HandoffReadInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("conversation_id", None)
        ctx = _resolve_chat_for_request(kwargs)
        if not ctx.get("ok"):
            return {"error": True, "message": ctx.get("error")}
        chat = ctx["chat_data"]
        draft = chat.get(DRAFT_FIELD)
        inherited = chat.get(HANDOFF_FIELD)
        pressure = get_context_pressure(ctx["chat_id"])
        return {
            "conversation_id": ctx["chat_id"],
            "draft": draft if isinstance(draft, dict) else None,
            "inherited_handoff": inherited if isinstance(inherited, dict) else None,
            "predecessor_id": chat.get("branchedFrom") if chat.get("lineageKind") == "handoff" else None,
            "context_pressure": round(pressure, 3) if pressure is not None else None,
            "nudge_ratio": nudge_ratio(),
            "sections": list(DRAFT_SECTIONS),
        }


# ---------------------------------------------------------------------------
# handoff_write
# ---------------------------------------------------------------------------

class HandoffWriteInput(BaseModel):
    sections: Optional[Dict[str, str]] = Field(None, description=(
        "Section-keyed text.  Keys: objective, state, decisions, gotchas, "
        "references.  With mode='merge' only the keys you pass are "
        "overwritten; the rest of the draft is kept.  Put turn/message "
        "references (e.g. 'turn 18', 'messageIndex 41') in `references` so "
        "the continuation can retrieve them with chat_read."))
    document: Optional[str] = Field(None, description=(
        "Whole document (markdown).  Use with mode='replace' when starting a "
        "draft from scratch or when the existing one is wrong.  Under "
        "mode='merge' a bare document is treated as the `state` section."))
    mode: str = Field("merge", description=(
        "'merge' (default): overwrite only the named sections.  'replace': "
        "the document/sections become the whole draft.  Prefer merge for "
        "per-turn updates; do not rewrite wholesale unless it is wrong."))


class HandoffWriteTool(BaseMCPTool):
    name: str = "handoff_write"
    description: str = (
        "Create or update this conversation's handoff draft — the document a "
        "continuation conversation receives when the user hands off.  This "
        "record IS the handoff; never write a handoff to a file.  Start one "
        "when asked, or when the context-pressure prelude suggests it; once "
        "it exists, merge in decisions, state changes, gotchas and "
        "references as they happen (touch only the sections that changed).  "
        "The user can edit the draft at any time and commits the handoff "
        "themselves — this tool never creates a new conversation."
    )
    InputSchema = HandoffWriteInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("conversation_id", None)
        sections = kwargs.get("sections")
        document = kwargs.get("document")
        mode = (kwargs.get("mode") or "merge").strip().lower()
        if not isinstance(sections, dict):
            sections = None
        else:
            sections = {str(k).strip().lower(): v for k, v in sections.items()
                        if isinstance(v, str)}
            unknown = sorted(set(sections) - set(DRAFT_SECTIONS))
            if unknown:
                return {"error": True,
                        "message": (f"Unknown section(s) {unknown}; use "
                                    f"{list(DRAFT_SECTIONS)}.")}
        if not sections and not (isinstance(document, str) and document.strip()):
            return {"error": True,
                    "message": "Provide `sections` and/or a non-empty `document`."}

        ctx = _resolve_chat_for_request(kwargs)
        if not ctx.get("ok"):
            return {"error": True, "message": ctx.get("error")}
        chat = ctx["chat_data"]
        messages = chat.get("messages")
        count = len(messages) if isinstance(messages, list) else 0
        try:
            new_draft = merge_draft(
                chat.get(DRAFT_FIELD), document=document, sections=sections,
                mode=mode, source_message_count=count,
            )
        except ValueError as e:
            return {"error": True, "message": str(e)}

        existed = isinstance(chat.get(DRAFT_FIELD), dict)
        chat[DRAFT_FIELD] = new_draft
        try:
            _persist(ctx, chat)
        except Exception as e:
            logger.error(f"handoff_write: persist failed for {ctx['chat_id'][:8]}: {e}")
            return {"error": True, "message": f"Could not save the handoff draft: {e}"}
        logger.info(f"📝 handoff_write[{ctx['chat_id'][:8]}] mode={mode} "
                    f"{'updated' if existed else 'created'} "
                    f"({len(new_draft['document'])} chars)")
        return {
            "ok": True,
            "conversation_id": ctx["chat_id"],
            "action": "updated" if existed else "created",
            "mode": mode,
            "draft": new_draft,
            "note": ("The draft is saved on this conversation.  The user commits "
                     "the handoff from the UI; keep merging changes as they "
                     "happen."),
        }
