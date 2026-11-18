"""Recognise the API error for a signed thinking block whose prefix changed.

A signed ``thinking`` block is bound to the system prompt and the messages
before it.  If either is rewritten after the block was produced, the API
rejects the whole request:

    messages.N.content.M: Invalid `signature` in `thinking` block. The block
    is bound to a different conversation. ... Content before this block
    differs from when it was created ...

The tool loop retries once with echoed thinking removed
(``StreamingToolExecutor._strip_thinking_blocks``), which is a request shape
the API accepts.
"""
from typing import Any


def is_thinking_signature_error(message: Any) -> bool:
    """True when ``message`` is the API's thinking-signature rejection."""
    if not isinstance(message, str):
        return False
    low = message.lower()
    return "signature" in low and "thinking" in low and "invalid" in low
