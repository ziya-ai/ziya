"""backfill_embeddings must not block the asyncio event loop.

Attribution (py-spy dump of a live server, 2026-10-08): the MainThread was
inside ``ssl.read`` under ``botocore`` under ``embed_text`` under
``backfill_embeddings`` under ``_initialize_memory_background`` -- i.e. the
event loop itself was parked in a synchronous Bedrock HTTP call, once per
memory lacking a vector (thousands on a mature install).  While it is parked
no request is served at all: not /chats, not any config GET, not the folder
progress poll.  That is the startup "limbo" users attributed to the folder
scan, which was not even running in the dumps.

The test drives a provider whose embed_text sleeps synchronously and checks
that a concurrently scheduled coroutine keeps ticking.  Against the
unpatched code the ticker is starved for the whole backfill and the
assertion fails.
"""
import asyncio
import time

import pytest

from app.services import embedding_service as es


class _SlowProvider:
    """Stands in for the Bedrock provider: each call blocks the calling thread."""

    def __init__(self, per_call_s: float):
        self.per_call_s = per_call_s
        self.calls = 0

    def embed_text(self, _content):
        self.calls += 1
        time.sleep(self.per_call_s)
        return [0.0, 1.0]


class _Cache:
    def __init__(self):
        self.vectors = {}
        self.flushes = 0

    def put(self, mid, vec):
        self.vectors[mid] = vec

    def flush(self):
        self.flushes += 1


@pytest.mark.asyncio
async def test_backfill_keeps_event_loop_responsive(monkeypatch):
    provider = _SlowProvider(per_call_s=0.05)
    cache = _Cache()
    monkeypatch.setattr(es, "get_embedding_provider", lambda: provider)
    monkeypatch.setattr(es, "get_embedding_cache", lambda: cache)
    monkeypatch.setattr(es, "BACKFILL_BATCH_DELAY", 0)

    items = [(f"m{i}", f"content {i}") for i in range(12)]  # ~0.6 s of blocking calls

    ticks = 0
    stop = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    t = asyncio.create_task(ticker())
    count = await es.backfill_embeddings(items)
    stop.set()
    await t

    # Positive: the backfill actually did its work.
    assert count == 12
    assert provider.calls == 12
    assert set(cache.vectors) == {f"m{i}" for i in range(12)}
    assert cache.flushes >= 1
    # The property under test: 12 x 50 ms of blocking provider calls must not
    # starve the loop.  A loop that only resumes at the between-batch sleep
    # (BACKFILL_BATCH_SIZE=20 -> never, for 12 items) sees ~1 tick.
    assert ticks >= 20, f"event loop starved during backfill: {ticks} ticks"
