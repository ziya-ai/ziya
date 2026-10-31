"""A local model server's memory ceiling gets its own error tag.

2026-10-19: oMLX's prefill memory guard rejected a prompt.  The provider
raised, the StreamingToolExecutor emitted ``{'type': 'error', 'content': msg}``
with no ``error`` key, server.py relayed ``error_type: 'unknown'``, and the
chat showed the generic collapsed "Error Details (Click to expand)" block
with the raw text.  The chunk is now built by
``app.utils.provider_error_chunks.build_provider_error_chunk`` so the
frontend can render a dedicated banner; everything unrecognised keeps the
legacy shape.
"""
from app.utils.provider_error_chunks import (
    MODEL_MEMORY_ERROR, build_provider_error_chunk, is_model_memory_error_text,
)

OMLX = (
    "oMLX prefill memory guard rejected this prompt: Prefill would require ~112.59 GB "
    "peak (current 108.21 GB + KV+SDPA 4.38 GB) but the prefill limit is 110.42 GB "
    "(dynamic ceiling 113.83 GB - safety margin 3.41 GB). Close other apps to free RAM "
    "(macOS and other apps leave oMLX 113.83 GB of the 125.44 GB static cap, and oMLX "
    "already uses all but 5.62 GB of it), raise memory_guard_tier (safe → balanced → "
    "aggressive), or reduce context length. To continue, set Memory Guard to aggressive, "
    "raise the custom memory guard ceiling, free system memory, or compact/reduce context."
)


def test_omlx_memory_guard_is_tagged():
    assert is_model_memory_error_text(OMLX)
    chunk = build_provider_error_chunk(OMLX)
    assert chunk["type"] == "error"
    assert chunk["error"] == MODEL_MEMORY_ERROR
    assert chunk["error_type"] == MODEL_MEMORY_ERROR
    assert chunk["content"] == OMLX          # raw text kept for "Technical details"
    assert chunk["can_retry"] is True


def test_server_relay_shape_matches():
    """server.py relays error_type from chunk['error']; without that key it
    sends 'unknown', which is exactly the regression being fixed."""
    chunk = build_provider_error_chunk(OMLX)
    assert chunk.get("error", "unknown") == MODEL_MEMORY_ERROR


def test_other_local_runtime_oom_shapes():
    for msg in (
        "llama_decode failed: CUDA out of memory. Tried to allocate 2.00 GiB",
        "failed to allocate buffer for kv cache",
        "Metal out of memory",
    ):
        assert is_model_memory_error_text(msg), msg


def test_unrecognised_errors_keep_legacy_untagged_shape():
    for msg in (
        "Input is too long for requested model.",      # context-size, handled by frontend text match
        "The security token included in the request is invalid.",
        "Bedrock returned an empty response",
        "",
    ):
        assert not is_model_memory_error_text(msg), msg
        assert build_provider_error_chunk(msg) == {"type": "error", "content": msg}


def test_memory_message_is_not_misread_as_auth_or_context_size():
    """The oMLX text mentions 'context length'; neither the backend auth
    classifier nor the frontend's context-size substrings may claim it."""
    from app.utils.infra_errors import is_auth_failure
    assert is_auth_failure(RuntimeError(OMLX), None) is False
    # Mirrors chatApi.ts isContextSizeError text match.
    assert not any(s in OMLX for s in ("too large", "too long", "tokens >", "prompt is too"))
    # Mirrors chatApi.ts isAuthError text match.
    assert not any(s in OMLX for s in ("credential", "mwinit", "AWS credentials"))
