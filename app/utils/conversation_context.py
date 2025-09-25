"""
Conversation context manager for tracking conversation state across requests.

This module provides a way to track conversation_id and other context
information across the request lifecycle.
"""

import threading
from typing import Optional
from contextlib import contextmanager

# Thread-local storage for conversation context
_context = threading.local()


def set_conversation_id(conversation_id: str) -> None:
    """Set the conversation ID for the current thread."""
    _context.conversation_id = conversation_id


def get_conversation_id() -> Optional[str]:
    """Get the conversation ID for the current thread."""
    return getattr(_context, 'conversation_id', None)


def clear_conversation_context() -> None:
    """Clear the conversation context for the current thread."""
    if hasattr(_context, 'conversation_id'):
        delattr(_context, 'conversation_id')


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
