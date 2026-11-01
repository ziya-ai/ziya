"""reorganize()'s synchronous phases must not run on the asyncio event loop.

Attribution (stall watchdog, 2026-10-09, after bootstrap_mindmap was moved
off the loop): the loop was next caught in ``store.save()`` under
``extract_all_relations`` (one full-file rewrite per relation found) and
under ``maintenance.maybe_divide_node`` called inline from ``reorganize``.
The filelock then timed out ("could not be acquired") -- the quadratic write
pattern hitting its own ceiling.  ``consolidate_mindmap``, the cross-link
loops and ``stamp_interference_scores`` are the same shape: synchronous,
store-writing, called inline from the coroutine.

Both tests fail on the unpatched code.
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

from app.memory import organizer


class _Store:
    def __init__(self, nodes, mems):
        self.nodes = nodes
        self._mem = mems
        self.save_calls = 0
        self.save_many_calls = 0

    def list_mindmap_nodes(self):
        return list(self.nodes)

    def list_memories(self, status="active"):
        return list(self._mem.values())

    def get(self, mid):
        return self._mem.get(mid)

    def save(self, m):
        self.save_calls += 1
        time.sleep(0.05)

    def save_many(self, ms):
        self.save_many_calls += 1
        time.sleep(0.05)


async def _with_ticker(coro):
    ticks = 0
    stop = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    t = asyncio.create_task(ticker())
    result = await coro
    stop.set()
    await t
    return result, ticks


@pytest.mark.asyncio
async def test_extract_all_relations_batches_writes_off_the_loop(monkeypatch):
    mems = {f"m{i}": SimpleNamespace(id=f"m{i}", relations={},
                                     model_dump=lambda i=i: {"id": f"m{i}", "content": "c"})
            for i in range(6)}
    node = SimpleNamespace(id="n", memory_refs=list(mems))
    store = _Store([node], mems)

    async def fake_extract(batch):
        return [{"source": "m0", "target": f"m{i}", "type": "elaborates"} for i in range(1, 6)]

    monkeypatch.setattr(organizer, "extract_relations", fake_extract)

    result, ticks = await _with_ticker(organizer.extract_all_relations(store))

    assert result == {"status": "success", "relations_found": 5}
    assert mems["m0"].relations == {"elaborates": ["m1", "m2", "m3", "m4", "m5"]}
    assert store.save_many_calls == 1 and store.save_calls == 0
    assert ticks >= 3, f"event loop starved while relations were saved: {ticks} ticks"


@pytest.mark.asyncio
async def test_reorganize_runs_sync_phases_off_the_loop(monkeypatch):
    import app.memory.maintenance as mt
    import app.memory.rem as rem
    import app.memory.organize_history as oh

    nodes = [SimpleNamespace(id="a"), SimpleNamespace(id="b")]
    store = _Store(nodes, {})

    async def _ok(*a, **k):
        return {"status": "success"}

    def _slow(*a, **k):
        time.sleep(0.15)
        return {"status": "success"}

    def _slow_list(*a, **k):
        time.sleep(0.1)
        return []

    monkeypatch.setattr(organizer, "cleanup_corpus", _ok)
    monkeypatch.setattr(organizer, "bootstrap_mindmap", _ok)
    monkeypatch.setattr(organizer, "extract_all_relations", _ok)
    monkeypatch.setattr(rem, "rem_phase", _ok)
    monkeypatch.setattr(organizer, "consolidate_mindmap", _slow)
    monkeypatch.setattr(mt, "discover_cross_links", _slow_list)
    monkeypatch.setattr(mt, "discover_cross_links_by_embedding", _slow_list)
    monkeypatch.setattr(mt, "maybe_divide_node", _slow_list)
    monkeypatch.setattr(mt, "stamp_interference_scores", _slow)
    monkeypatch.setattr(oh, "append_organize_result", lambda r: None)

    result, ticks = await _with_ticker(organizer.reorganize(store))

    # Positive: every phase ran and reported.
    assert result["consolidate"] == {"status": "success"}
    assert result["interference"] == {"status": "success"}
    assert result["cross_links"] == [] and result["divisions"] == []
    # ~0.9 s of synchronous phase work must not park the loop (~90 ticks offloaded).
    assert ticks >= 20, f"event loop starved during reorganize sync phases: {ticks} ticks"
