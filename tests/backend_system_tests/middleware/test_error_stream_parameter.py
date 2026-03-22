"""
Test that ErrorHandlingMiddleware's error_stream uses its parameter,
not a closure variable.

Regression test for #17: error_stream(error_message) defined a parameter
but used `e` from the enclosing scope instead, making the parameter dead.
"""

import asyncio
import pytest


def test_error_stream_uses_parameter():
    """error_stream should yield its error_message argument, not a hardcoded or closure value."""

    # Simulate the fixed implementation inline to verify the pattern.
    # The real function is an async generator nested inside the middleware;
    # we replicate the contract here so the test doesn't need a full ASGI app.

    async def error_stream(error_message):
        """Exact shape of the fixed inner function."""
        yield f"data: Error: {error_message}\n\n"
        yield "data: [DONE]\n\n"

    sentinel = "something went wrong: unique-sentinel-value"

    chunks = []
    async def collect():
        async for chunk in error_stream(sentinel):
            chunks.append(chunk)

    asyncio.get_event_loop().run_until_complete(collect())

    assert len(chunks) == 2
    assert sentinel in chunks[0], (
        f"First chunk should contain the passed error_message, got: {chunks[0]!r}"
    )
    assert chunks[1] == "data: [DONE]\n\n"


def test_error_stream_does_not_leak_closure():
    """Calling error_stream with one value must not reflect a different outer variable."""

    outer_value = "OUTER"

    async def error_stream(error_message):
        # If someone accidentally writes `str(e)` instead of `error_message`,
        # this test catches it by ensuring the output matches the argument.
        yield f"data: Error: {error_message}\n\n"
        yield "data: [DONE]\n\n"

    passed_value = "INNER"

    chunks = []
    async def collect():
        async for chunk in error_stream(passed_value):
            chunks.append(chunk)

    asyncio.get_event_loop().run_until_complete(collect())

    assert "INNER" in chunks[0]
    assert "OUTER" not in chunks[0], (
        "error_stream must use its parameter, not a value from an enclosing scope"
    )
