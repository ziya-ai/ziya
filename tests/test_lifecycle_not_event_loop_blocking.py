"""run_lifecycle_pass must not run the memory sweep on the event loop.

The stall watchdog caught the loop here (2026-10-09):

    extractor.run_post_conversation_extraction -> run_lifecycle_pass
      -> _promote_proposal -> embed_and_cache -> bedrock invoke_model
      -> ssl.read

``run_lifecycle_pass`` was ``async def`` with no awaits, so the whole sweep
(proposal-store I/O, one embedding round trip per promotion, prune, vector
eviction) ran ON the loop.  It must hand the sweep to a worker thread.

Behavioural: the real coroutine is driven with the blocking callees swapped
for probes that record their thread.  Against the pre-fix module the probes
run on the loop (main) thread and both tests fail.
"""
import asyncio
import threading

import pytest

from app.memory import lifecycle


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Probe:
    def __init__(self, result):
        self.result = result
        self.thread = None
        self.calls = 0

    def __call__(self, *a, **kw):
        self.thread = threading.current_thread()
        self.calls += 1
        return self.result


def _assert_off_loop(probe):
    assert probe.calls >= 1, "probe never ran -- the seam was not exercised"
    assert probe.thread is not threading.main_thread(), (
        "lifecycle work ran on the event-loop thread; use asyncio.to_thread")


class _FakeStore:
    def __init__(self, open_items):
        self._open = open_items

    def list_open(self):
        return self._open

    def prune_terminal(self, max_terminal_records=None):
        return 0


def test_empty_queue_eviction_runs_off_the_loop(monkeypatch):
    import app.storage.proposals as sp
    monkeypatch.setattr(sp, "get_proposals_store", lambda: _FakeStore([]))
    probe = _Probe(0)
    monkeypatch.setattr(lifecycle, "_evict_orphan_vectors", probe)
    counts = _run(lifecycle.run_lifecycle_pass())
    assert counts["scanned"] == 0
    _assert_off_loop(probe)


def test_promotion_embed_path_runs_off_the_loop(monkeypatch):
    import app.storage.proposals as sp
    import app.utils.memory_clock as mc
    monkeypatch.setattr(sp, "get_proposals_store",
                        lambda: _FakeStore([{"id": "p1", "content": "x"}]))
    monkeypatch.setattr(lifecycle, "current_activity_count", lambda: 0)
    monkeypatch.setattr(mc, "build_context", lambda *_a, **_k: None)
    monkeypatch.setattr(mc, "effective_counter", lambda *_a, **_k: 0)
    monkeypatch.setattr(lifecycle, "_make_active_embedding_lookup", lambda: None)
    monkeypatch.setattr(lifecycle, "_evaluate_promotion", lambda *_a: "test")
    monkeypatch.setattr(lifecycle, "_evict_orphan_vectors", lambda *_a: 0)
    probe = _Probe("m1")
    monkeypatch.setattr(lifecycle, "_promote_proposal", probe)
    counts = _run(lifecycle.run_lifecycle_pass())
    assert counts["promoted"] == 1
    _assert_off_loop(probe)


class _StatefulStore:
    """list_open reflects promotions, like the real projection."""

    def __init__(self):
        self.status = {"p1": "open"}

    def list_open(self):
        return [{"id": pid, "content": "x"}
                for pid, s in self.status.items() if s == "open"]

    def prune_terminal(self, max_terminal_records=None):
        return 0


def test_concurrent_passes_promote_a_proposal_once(monkeypatch):
    """Off the loop, two passes can overlap.  _promote_proposal saves the
    memory, then embeds (seconds), then marks the proposal promoted; two
    passes that both listed it open would each save an active memory.
    _PASS_LOCK must serialize them so the second sees it already promoted."""
    import time
    import app.storage.proposals as sp
    import app.utils.memory_clock as mc
    store = _StatefulStore()
    monkeypatch.setattr(sp, "get_proposals_store", lambda: store)
    monkeypatch.setattr(lifecycle, "current_activity_count", lambda: 0)
    monkeypatch.setattr(mc, "build_context", lambda *_a, **_k: None)
    monkeypatch.setattr(mc, "effective_counter", lambda *_a, **_k: 0)
    monkeypatch.setattr(lifecycle, "_make_active_embedding_lookup", lambda: None)
    monkeypatch.setattr(lifecycle, "_evaluate_promotion", lambda *_a: "test")
    monkeypatch.setattr(lifecycle, "_evict_orphan_vectors", lambda *_a: 0)

    saved = []

    def slow_promote(proposal, reason):
        saved.append(proposal["id"])          # store.save(memory)
        time.sleep(0.2)                       # embed_and_cache round trip
        store.status[proposal["id"]] = "promoted"   # mark_promoted
        return "m-" + proposal["id"]

    monkeypatch.setattr(lifecycle, "_promote_proposal", slow_promote)

    async def both():
        return await asyncio.gather(lifecycle.run_lifecycle_pass(),
                                    lifecycle.run_lifecycle_pass())

    results = _run(both())
    assert sorted(r["promoted"] for r in results) == [0, 1]
    assert saved == ["p1"], f"proposal saved as active memory {len(saved)} times"
