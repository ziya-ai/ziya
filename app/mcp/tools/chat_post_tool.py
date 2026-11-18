"""chat_post: post a message from the current conversation into another one.

Thin wrapper over app/utils/chat_post.py (design/conversation-post.md).
Unlike the conversation_organize tools this one DOES add a message to a
record: one server-authored ``peer`` message on the target, never on the
source, and it never edits or removes an existing message.
"""
from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field

from app.mcp.tools.base import BaseMCPTool
from app.mcp.tools.chat_history_tools import _resolve_scope
from app.utils.chat_post import MAX_POST_CHARS, ChatPostError, post_to_chat
from app.utils.ziya_links import chat_link


class ChatPostInput(BaseModel):
    chat_id: str = Field(..., description=(
        "conversationId to post INTO (from chat_list / chat_search).  Any "
        "project.  Not the current conversation."))
    text: str = Field(..., description=(
        f"The message (markdown, up to {MAX_POST_CHARS} characters).  Write it to "
        "stand alone: the reader has none of this conversation's context."))
    mode: str = Field("context", description=(
        "'context' (default): append only; that conversation's model reads it on "
        "its next turn.  'submit': also get a turn to follow.  If that "
        "conversation has a turn running, the post is delivered into it as live "
        "feedback; if not, a turn is started there and its answer is saved to "
        "that conversation (read it later with chat_read)."))


class ChatPostTool(BaseMCPTool):
    name: str = "chat_post"
    description: str = (
        "Post a message from this conversation into another conversation.  It "
        "appears in that conversation's transcript marked as coming from this "
        "one, with a link back.  Use it to hand a finding or a request to a "
        "thread that owns the related work.  This conversation's record is not "
        "changed.  A turn that was itself driven by a submitted post cannot "
        "use mode='submit'."
    )
    InputSchema = ChatPostInput
    # The consent op of a call is its mode.  Ungated by default; setting
    # consent.tools.chat_post.ops.submit to "ask" in permissions.json puts a
    # submit behind the consent prompt.  See also chat_post.submit_policy.
    consent_op_key = "mode"

    async def execute(self, **kwargs) -> Dict[str, Any]:
        kwargs.pop("conversation_id", None)
        scope = _resolve_scope()
        try:
            out = await post_to_chat(
                scope.get("conversation_id"), kwargs.get("chat_id"),
                kwargs.get("text"), kwargs.get("mode") or "context")
        except ChatPostError as e:
            return {"error": True, "message": str(e)}
        target = out["target"]
        out["link"] = chat_link(target["conversationId"], target["messageIndex"])
        return out
