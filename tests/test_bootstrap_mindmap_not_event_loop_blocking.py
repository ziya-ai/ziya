"""bootstrap_mindmap must not persist memory placements on the asyncio loop.

Attribution (two stall watchdog reports, 2026-10-09): the event loop was
inside ``store.save(mem)`` -- once in ``json.dumps`` of the whole memories
file, once in ``filelock``'s ``time.sleep`` poll -- under ``_place_domain``
under ``bootstrap_mindmap`` under the scheduler's ``memory_organize`` system
job.  ``_place_domain`` is synchronous and, for every memory it places,
called ``store.save()``, which rewrites, encrypts and fsyncs the ENTIRE
memories file (2,512 entries on the reporting install).  Thousands of full
file rewrites inline on the loop; nothing served while each one ran.

Two properties, both of which fail on the unpatched code:

  1. The loop stays responsive while placements persist (the store's write
     is stubbed to sleep, standing in for serialize+encrypt+write).
  2. Placement uses one batched ``save_many`` per domain, not a ``save`` per
     memory -- the quadratic cost is the reason each stall was long.
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

from app.memory import organizer
from app.models.memory import MindMapNode


class _Store:
    def __init__(self, memories):
        self._mem = {m.id: m for m in memories}
        self.nodes = {}
        self.save_calls = 0
        self.save_many_calls = 0

    def list_memories(self, status="active"):
        return list(self._mem.values())

    def list_mindmap_nodes(self):
        return list(self.nodes.values())

    def get_mindmap_node(self, node_id):
        return self.nodes.get(node_id)

    def save_mindmap_node(self, node):
        self.nodes[node.id] = node
        return node

    def get(self, mid):
        return self._mem.get(mid)

    def save(self, memory):
        self.save_calls += 1
        time.sleep(0.05)  # one full-file rewrite
        return memory

    def save_many(self, memories):
        self.save_many_calls += 1
        time.sleep(0.05)  # one full-file rewrite for the whole batch


def _mem(i):
    return SimpleNamespace(
        id=f"m{i}", content=f"memory {i}", tags=["t"],
        scope=SimpleNamespace(domain_node=None),
        model_dump=lambda i=i: {"id": f"m{i}", "content": f"memory {i}", "tags": ["t"]},
    )


@pytest.mark.asyncio
async def test_bootstrap_places_memories_off_the_loop_in_one_write_per_domain(monkeypatch):
    memories = [_mem(i) for i in range(12)]
    store = _Store(memories)

    async def fake_cluster(batch, existing, batch_target=1):
        return [{"handle": "test domain", "tags": ["t"], "memory_ids": [m["id"] for m in batch]}]

    monkeypatch.setattr(organizer, "cluster_memories", fake_cluster)

    ticks = 0
    stop = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    t = asyncio.create_task(ticker())
    result = await organizer.bootstrap_mindmap(store)
    stop.set()
    await t

    # Positive: the placement happened.
    assert result["status"] == "success"
    assert result["memories_placed"] == 12
    assert all(m.scope.domain_node for m in memories)
    assert len(store.nodes) == 1
    # One batched write per domain, not one write per memory.
    assert store.save_many_calls == 1
    assert store.save_calls == 0
    # And the loop was never parked for it.
    assert ticks >= 3, f"event loop starved during placement: {ticks} ticks"
