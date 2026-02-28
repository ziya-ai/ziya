"""
Conversation context manager for tracking conversation state across requests.

This module provides a way to track conversation_id and other context
information across the request lifecycle.
"""

import contextvars
from typing import Optional
from contextlib import contextmanager

# Async-safe per-task storage for conversation context
_conversation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'conversation_id', default=None
)


def set_conversation_id(conversation_id: str) -> None:
    """Set the conversation ID for the current async task."""
    _conversation_id.set(conversation_id)


def get_conversation_id() -> Optional[str]:
    """Get the conversation ID for the current async task."""
    return _conversation_id.get()


def clear_conversation_context() -> None:
    """Clear the conversation context for the current async task."""
    _conversation_id.set(None)


@contextmanager
def conversation_context(conversation_id: str):
    """Context manager for setting conversation ID."""
    old_id = get_conversation_id()
    set_conversation_id(conversation_id)
    try:
        yield
    finally:
        if old_id is not None:
            set_conversation_id(old_id)
        else:
            clear_conversation_context()
