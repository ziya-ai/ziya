"""Shape the error chunk the stream emits for a non-retryable provider error.

Until 2026-10-19 the StreamingToolExecutor emitted every non-auth provider
failure as ``{'type': 'error', 'content': message}`` with no ``error`` key,
so the server relayed ``error_type: 'unknown'`` and the frontend fell back
to its generic collapsed "Error Details (Click to expand)" block.  That is
the right fallback for a genuinely unknown failure, but some provider
messages are specific enough to deserve a dedicated banner, and the
frontend can only choose one if the chunk is tagged.

The first such case is a *model server memory ceiling*: a local runtime
(oMLX's prefill memory guard, llama.cpp / Ollama OOM) refusing a prompt
because loading it would exceed the RAM the server is allowed.  It reads
like a context-size error but the remedy is different -- the model's
context window is fine; the machine's memory is not -- so it must not be
labelled ``context_size_error`` either.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

MODEL_MEMORY_ERROR = "model_memory_error"

# Lower-cased substrings.  Observed 2026-10-19 (oMLX 0.x): "oMLX prefill
# memory guard rejected this prompt: Prefill would require ~112.59 GB peak
# ... but the prefill limit is 110.42 GB ... Close other apps to free RAM
# ... raise memory_guard_tier ... or reduce context length."
_MODEL_MEMORY_INDICATORS = (
    "memory guard",
    "memory_guard",
    "prefill would require",
    "free ram",
    "out of memory",
    "insufficient memory",
    "failed to allocate",
    "cuda out of memory",
    "metal out of memory",
)


def is_model_memory_error_text(text: Optional[str]) -> bool:
    """True when ``text`` says the model server ran out of memory for this
    prompt (as opposed to the prompt exceeding the model's context window)."""
    lowered = (text or "").lower()
    return any(ind in lowered for ind in _MODEL_MEMORY_INDICATORS)


def build_provider_error_chunk(message: str) -> Dict[str, Any]:
    """The chunk to yield for a non-retryable, non-auth provider error.

    A recognised message is tagged so ``server.py`` relays a specific
    ``error_type`` and the frontend can render its banner; anything else
    keeps the untagged legacy shape, which the server maps to ``unknown``.
    """
    if is_model_memory_error_text(message):
        return {
            "type": "error",
            "error": MODEL_MEMORY_ERROR,
            "error_type": MODEL_MEMORY_ERROR,
            "content": message,
            "can_retry": True,
        }
    return {"type": "error", "content": message}
