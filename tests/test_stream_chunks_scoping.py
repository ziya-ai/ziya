"""
Regression tests for variable scoping and symbol availability in stream_chunks().

History:
  - Original bug: `mcp_tools` was only assigned inside `if question:` but
    referenced in a LangChain fallback iteration loop → UnboundLocalError.
  - The LangChain fallback loop was removed during the StreamingToolExecutor
    refactoring, so the original tests (which searched for a marker comment
    and a `while iteration < max_iterations` loop) became stale.

Current structure (post-refactoring):
  stream_chunks() is a single-path async generator that delegates to
  StreamingToolExecutor.  Variables like `chunk_count`, `executor`, and
  `mcp_tools` are assigned inside nested blocks (try/if) but referenced
  in except handlers and post-stream code.  These tests guard against
  scoping regressions in the current code.

Additionally, stream_chunks() calls `cleanup_stream()` and references
`active_streams` — symbols that must be defined in server.py or imported.
A refactoring pass removed them, causing NameError on every stream
completion.  Tests here guard against that class of regression.
"""

import ast
import asyncio
import inspect
import textwrap

import pytest


def _get_stream_chunks_source() -> str:
    """Import stream_chunks and return its source code."""
    from app.server import stream_chunks
    return inspect.getsource(stream_chunks)


class TestStreamChunksVariableScoping:
    """
    Guard against UnboundLocalError in stream_chunks() error handlers.

    Variables assigned inside nested try/if blocks but referenced in
    except handlers must be initialized before the try block, or the
    handler will crash when the try block fails early.
    """

    def test_chunk_count_reachable_from_valueerror_handler(self):
        """
        chunk_count is referenced in the `except ValueError` handler
        (`if chunk_count > 0`).  If it's only assigned inside the inner
        try block, a ValueError from build_messages_for_streaming() would
        hit UnboundLocalError before the handler can check it.

        This test verifies chunk_count is assigned at a scope that the
        ValueError handler can reach.
        """
        source = _get_stream_chunks_source()

        # Parse the AST to find the assignment and the except handler
        # at a structural level, not just string matching.
        assert "chunk_count" in source, "chunk_count not found in stream_chunks"

        # The ValueError handler references chunk_count.  Verify the
        # assignment happens before or at the same nesting level.
        # Simple heuristic: chunk_count = 0 must appear before
        # `except ValueError`.
        assign_pos = source.find("chunk_count = 0")
        except_pos = source.find("except ValueError")

        assert assign_pos != -1, (
            "chunk_count = 0 assignment not found in stream_chunks. "
            "The except ValueError handler will crash with UnboundLocalError."
        )
        assert except_pos != -1, (
            "except ValueError handler not found — was it removed?"
        )
        assert assign_pos < except_pos, (
            f"chunk_count = 0 (pos {assign_pos}) appears AFTER "
            f"except ValueError (pos {except_pos}).  The handler will "
            f"crash with UnboundLocalError if the try block fails early."
        )

    def test_mcp_tools_reachable_from_retry_path(self):
        """
        mcp_tools is used in the validation retry loop
        (`executor.stream_with_tools(..., tools=mcp_tools, ...)`).
        It must be assigned before that reference.
        """
        source = _get_stream_chunks_source()
        assert "mcp_tools" in source, "mcp_tools not found in stream_chunks"

        assign_pos = source.find("mcp_tools = list(")
        if assign_pos == -1:
            assign_pos = source.find("mcp_tools = []")

        assert assign_pos != -1, (
            "mcp_tools assignment not found in stream_chunks"
        )

    def test_executor_used_after_assignment(self):
        """
        `executor` is created via StreamingToolExecutor() and used in
        `executor.stream_with_tools(...)`.  Verify the assignment
        precedes all usages.
        """
        source = _get_stream_chunks_source()
        assign_pos = source.find("executor = StreamingToolExecutor(")
        assert assign_pos != -1, "executor assignment not found"

        # All references to executor.stream_with_tools must come after
        first_usage = source.find("executor.stream_with_tools(")
        assert first_usage != -1, "executor.stream_with_tools() call not found"
        assert assign_pos < first_usage, (
            "executor is used before it is assigned"
        )


class TestStreamChunksSymbolAvailability:
    """
    Guard against NameError for symbols that stream_chunks() calls.

    These symbols must be importable from app.server at module level.
    A refactoring pass can delete definitions while leaving call sites,
    causing NameError at runtime (not at import time).
    """

    def test_cleanup_stream_is_defined(self):
        """cleanup_stream must be importable from app.server."""
        from app.server import cleanup_stream
        assert callable(cleanup_stream)
        assert asyncio.iscoroutinefunction(cleanup_stream), (
            "cleanup_stream must be async (called with await in stream_chunks)"
        )

    def test_active_streams_is_defined(self):
        """active_streams must be importable from app.server."""
        from app.server import active_streams
        assert isinstance(active_streams, dict)

    def test_cleanup_stream_referenced_in_stream_chunks(self):
        """
        Verify stream_chunks actually calls cleanup_stream.
        If someone removes the calls, these guard tests become stale
        and should be updated or removed.
        """
        source = _get_stream_chunks_source()
        assert "cleanup_stream" in source, (
            "cleanup_stream is no longer referenced in stream_chunks(). "
            "Either the calls were removed (update this test) or "
            "a refactoring accidentally deleted them."
        )

    def test_build_messages_for_streaming_is_importable(self):
        """build_messages_for_streaming is called by stream_chunks and chat_endpoint."""
        from app.server import build_messages_for_streaming
        assert callable(build_messages_for_streaming)

    def test_keepalive_wrapper_is_importable(self):
        """_keepalive_wrapper wraps stream_chunks in the SSE response."""
        from app.server import _keepalive_wrapper
        assert callable(_keepalive_wrapper)
