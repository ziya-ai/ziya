"""
recall_tributary — return a tool hunt that replay folded behind a tb-… handle.

design/tributary-fold.md, slice 3.  The handle in a replayed placeholder
is the content address of the exact record substring that ran from the
turn's first tool block to its last.  This tool re-reads the calling
conversation's own record (same resolution as the handoff tools) and
hands the span back through app.utils.tributary_recall, so it arrives in
the inert ‹tool_result› form rather than the renderer's fence form.

Scoped to the calling conversation: a handle issued elsewhere is reported
as not found, never as an error the model could read as a verdict on the
earlier work.  Read-only; results are past tool output, so the tool is not
in the high-trust set.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.mcp.tools.context_management import _resolve_chat_for_request
from app.utils.tributary_recall import DEFAULT_MAX_CHARS, MODES, recall


class RecallTributaryInput(BaseModel):
    handle: str = Field(..., description=(
        "The tb-… handle from a '[Ziya: tributary tb-… folded on replay …]' "
        "placeholder in this conversation's history."))
    mode: str = Field("full", description=(
        "'full' (default): the whole span — calls, results and the notes "
        "between them — re-encoded as result envelopes.  'commands': one "
        "label per tool call, so the calls can be re-run against current "
        "state instead of re-reading a stale result.  'results': each call's "
        "label followed by its body."))
    grep: Optional[str] = Field(None, description=(
        "Case-insensitive regex; keep only matching lines of the rendered text."))
    max_chars: int = Field(DEFAULT_MAX_CHARS, description=(
        "Cap on returned text; a truncation note says how much was cut."))


class RecallTributaryTool(BaseMCPTool):
    name: str = "recall_tributary"
    description: str = (
        "Bring back a tool hunt that this conversation's replayed history shows "
        "folded behind a tb-… handle.  Returns the exact span from the record "
        "(the message may since have been muted; it still resolves).  mode="
        "'commands' gives just the calls made, 'results' just their bodies, "
        "'full' everything; grep= slices lines.  Handles are scoped to the "
        "conversation they were issued in.  Conclusions you drew when the span "
        "was in view stand; recall is for when you need the calls or results "
        "again, not a re-check of them."
    )
    InputSchema = RecallTributaryInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("conversation_id", None)
        ctx = await asyncio.to_thread(_resolve_chat_for_request, kwargs)
        if not ctx.get("ok"):
            return {"found": False, "error": True, "message": ctx.get("error")}
        return recall(
            ctx["chat_data"], handle=str(kwargs.get("handle", "")),
            mode=str(kwargs.get("mode") or "full"),
            grep=kwargs.get("grep") or None,
            max_chars=int(kwargs.get("max_chars") or DEFAULT_MAX_CHARS),
        )
