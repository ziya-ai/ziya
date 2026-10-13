"""Closing the Bedrock stream early must not leave urllib3 to die noisily.

After ``message_stop`` the provider yields ``StreamEnd`` and stays suspended
inside its read loop; consumers (task_executor, the CLI) break there and,
since the aclosing fix, close the generator at once.  That throws
GeneratorExit at the yield with the HTTP body still open past the last
event, and the botocore / urllib3 generators are then finalized with the
body un-drained.  urllib3's ``_error_catcher`` takes its not-clean-exit
branch and calls ``_original_response.close()`` on a response whose socket
file is already gone::

    Exception ignored in: <generator object HTTPResponse.stream at 0x...>
    ...
    ValueError: I/O operation on closed file.

printed to stderr in the middle of the sweep's terminal output, once per
model call.  The CancelledError branch already avoids this by calling
``stream_body.close()`` BEFORE the frame unwinds (the http.client response
is then marked closed, so urllib3's later close is a no-op); GeneratorExit
needs the same.  The fake body below reproduces urllib3's contract: its
iterator raises on finalization unless ``close()`` ran first.
"""

import asyncio
import json
import sys
from contextlib import aclosing
from types import SimpleNamespace

import pytest

from app.providers.bedrock import BedrockProvider


def _chunk(obj):
    return {"chunk": {"bytes": json.dumps(obj).encode()}}


class _Body:
    """botocore EventStream stand-in with urllib3's finalization behaviour."""

    def __init__(self, events):
        self._events = events
        self.closed = False

    def close(self):
        self.closed = True

    def __iter__(self):
        drained = False
        try:
            for e in self._events:
                yield e
            # Reached only when the consumer reads past the last event; a
            # consumer that stops at StreamEnd leaves the chunked terminator
            # unread, exactly like the real body.
            drained = True
        finally:
            # urllib3's _error_catcher: a body that exited cleanly (drained)
            # needs no cleanup; one finalized un-drained closes the original
            # response, which raises if nothing closed the body first.
            if not drained and not self.closed:
                raise ValueError("I/O operation on closed file.")


def _provider():
    p = BedrockProvider.__new__(BedrockProvider)
    p.model_id = "m"
    p.model_config = {}
    return p


def _config():
    return SimpleNamespace(thinking=None)


EVENTS = [
    _chunk({"type": "message_start", "message": {"usage": {"input_tokens": 1}}}),
    _chunk({"type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}}),
    _chunk({"type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "hi"}}),
    _chunk({"type": "content_block_stop", "index": 0}),
    _chunk({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 1}}),
    _chunk({"type": "message_stop"}),
]


@pytest.fixture
def unraisable(monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "unraisablehook", lambda u: seen.append(u))
    return seen


@pytest.mark.asyncio
async def test_prompt_close_after_stream_end_closes_body_quietly(unraisable):
    from app.providers.base import StreamEnd
    body = _Body(EVENTS)
    gen = _provider()._parse_stream({"body": body}, _config())
    async with aclosing(gen) as s:
        async for ev in s:
            if isinstance(ev, StreamEnd):
                break
    # The body was closed by the provider, in order, before anything was
    # finalized -- so urllib3's finalizer has nothing to complain about.
    assert body.closed, "stream_body.close() did not run on GeneratorExit"
    del gen, body
    import gc
    gc.collect()
    await asyncio.sleep(0)
    assert unraisable == [], [str(u.exc_value) for u in unraisable]


@pytest.mark.asyncio
async def test_exhausted_stream_still_closes_body(unraisable):
    """Positive counterpart: run the generator to exhaustion (no early
    break) and the body is closed the same way."""
    body = _Body(EVENTS)
    async for _ in _provider()._parse_stream({"body": body}, _config()):
        pass
    assert body.closed
    import gc
    gc.collect()
    assert unraisable == []
