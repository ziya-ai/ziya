"""
TaskBinding — anchors a launched task card run to a chat.

A binding is a thin three-way pointer: {chat, card, run} plus the
message the card was launched after.  It is not a message and does
not live in Chat.messages; it lives in a separate file keyed by
chat_id.  See design/task-cards.md §UX shape (draft re-write).

Rules:
  - Each launch creates a NEW binding (even of the same card).
  - anchor_message_id is the id of the message the card was
    launched after.  Null means "top of chat" or orphaned after
    message deletion.
  - Bindings are immutable pointers; deleting a binding removes the
    pointer only, not the underlying run or card.
"""

from pydantic import BaseModel, Field
from typing import Optional


class TaskBinding(BaseModel):
    """Binding between a chat and a launched task card run."""
    model_config = {"extra": "allow"}

    id: str = ""
    chat_id: str
    card_id: str
    run_id: str
    # The message this binding is anchored after.  Null when the
    # anchor was removed (message deleted); renderers should show
    # such bindings at the top of the chat with an 'orphaned' flag.
    anchor_message_id: Optional[str] = None
    created_at: int = 0


class TaskBindingCreate(BaseModel):
    """Request body for creating a binding.

    Typically called from the launch path; the card_id identifies
    which card to launch and the anchor_message_id identifies where.
    The server fills in id, chat_id, run_id, and created_at.
    """
    card_id: str
    anchor_message_id: Optional[str] = None
