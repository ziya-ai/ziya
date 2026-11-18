"""Event-loop stall while memory organize runs (watchdog dump, 2026-11-17).

The dump showed the loop inside ``task_scheduler._read_state`` (a 11 KB
file), one worker thread CPU-bound in ``MemoryStorage._rank_placement``
(called from ``repair_mindmap``) and five ``GET /jobs`` workers inside
``os.path.realpath``.  A thread that makes a syscall releases the GIL and
must win it back from the CPU-bound thread, which costs up to a switch
interval each time.  Measured on the reporting machine:

  * rebalance pass of repair_mindmap: 6324 memories x 222 nodes, ~170 s of
    pure Python (27 ms per memory); with a per-pass node index 0.34 s for
    all 6324, identical rankings;
  * JobManager.list with 215 jobs: 4 ms idle, 1.3 s beside one spinning
    thread (two realpath calls per job, an lstat per path component);
  * scheduler enumeration: 0.11 s idle, 1.5 s beside one spinning thread,
    on the event loop.

Three corrections, one test group each:
  1. the scheduler's per-tick file reads run off the loop;
  2. JobManager.list resolves paths once, not twice per job per poll;
  3. repair_mindmap builds the per-node ranking inputs once per pass.
"""
import asyncio
import json
import os
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents import task_scheduler as ts


# ── 1. scheduler file reads are off the event loop ──────────────────────

@pytest.mark.asyncio
async def test_scheduler_enumeration_runs_off_the_event_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    loop_thread = threading.get_ident()
    seen = []

    def fake_enumerate():
        seen.append(threading.get_ident())
        return []

    with patch.object(ts, "_enumerate_scheduled_cards", side_effect=fake_enumerate), \
         patch.object(ts, "_TICK_INTERVAL_S", 0.02):
        task = ts.start_scheduler()
        try:
            for _ in range(100):
                if seen:
                    break
                await asyncio.sleep(0.02)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert seen, "the fire pass never enumerated"
    assert all(t != loop_thread for t in seen)


def test_tick_card_reads_state_off_the_event_loop():
    seen = []
    far = 2**62

    def fake_read(project_id):
        seen.append((project_id, threading.get_ident()))
        return {"card-1": {"next_fire_at": far, "fires_so_far": 0}}

    target = SimpleNamespace(project_id="p1", card_id="card-1", card_name="c",
                             block=SimpleNamespace(schedule_max_runs=None, id="b"))
    main = threading.get_ident()
    with patch.object(ts, "_read_state", side_effect=fake_read), \
         patch.object(ts, "_write_state") as wrote, \
         patch.object(ts, "_fire_one") as fired:
        asyncio.run(ts._tick_card(target, 1000))
    assert [p for p, _ in seen] == ["p1"]          # the state was read, once
    assert seen[0][1] != main
    assert not wrote.called and not fired.called   # not due: nothing else happened


# ── 2. JobManager.list does not resolve paths per job ───────────────────

def _job_dir(root, job_id, project_root):
    d = root / ".ziya" / "jobs" / job_id
    d.mkdir(parents=True)
    (d / "job.json").write_text(json.dumps(
        {"job_id": job_id, "project_root": str(project_root), "started_at": 1.0}))
    (d / "status.json").write_text(json.dumps({"state": "exited", "exit_code": 0}))


def test_job_list_resolves_paths_once_not_per_job(tmp_path, monkeypatch):
    from app.jobs.manager import JobManager
    root = tmp_path / "proj"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    for i in range(40):
        _job_dir(root, f"j{i:02d}", root)
    _job_dir(root, "foreign", other)        # recorded under another project root
    link = tmp_path / "link"
    link.symlink_to(root)

    mgr = JobManager()
    assert len(mgr.list(str(root))) == 40   # scan + first list

    calls = []
    real = os.path.realpath

    def counting(p, *a, **k):
        calls.append(p)
        return real(p, *a, **k)

    monkeypatch.setattr(os.path, "realpath", counting)
    listed = mgr.list(str(link))            # a symlinked root is the same project
    assert sorted(j["job_id"] for j in listed) == [f"j{i:02d}" for i in range(40)]
    # Was 2 per job + 1 (83 here); the poll hits this every few seconds.
    assert len(calls) <= 3, f"{len(calls)} realpath calls for one list"


# ── 3. repair_mindmap ranks against one per-pass node index ─────────────

@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_EMBEDDING_PROVIDER", "none")
    import app.services.embedding_service as _es
    monkeypatch.setattr(_es, "_provider", None)
    monkeypatch.setattr(_es, "_cache", None)
    from app.storage.memory import MemoryStorage
    return MemoryStorage(memory_dir=tmp_path / "memory")


def _populate(storage):
    """Three roots and a child, members from two projects, so that tag
    overlap, the parent-tag subtraction and the provenance gate all bear
    on the ranking."""
    from app.models.memory import Memory, MemoryScope, MindMapNode
    X, Y = "/w/project-x", "/w/project-y"
    mems = []
    for i in range(30):
        proj = X if i % 3 else Y
        tags = [["net", "bgp"], ["net", "queue"], ["ui", "bench"], ["ui", "net"], ["bgp"]][i % 5]
        m = Memory(content=f"fact {i}", tags=tags, scope=MemoryScope(project_paths=[proj]))
        storage.save(m)
        mems.append(m)
    ids = [m.id for m in mems]
    storage.save_mindmap_node(MindMapNode(id="net", handle="Net", tags=["net", "bgp", "queue"],
                                          memory_refs=ids[0:12], children=["net_bgp"]))
    storage.save_mindmap_node(MindMapNode(id="net_bgp", handle="BGP", parent="net",
                                          tags=["net", "bgp"], memory_refs=ids[12:16]))
    storage.save_mindmap_node(MindMapNode(id="ui", handle="UI", tags=["ui", "bench"],
                                          memory_refs=ids[16:26]))
    storage.save_mindmap_node(MindMapNode(id="untagged", handle="Misc", tags=[],
                                          memory_refs=ids[26:30]))
    # A node whose members all come from a third project that shares no
    # node with X or Y (so it is a collection of its own): provenance-
    # incompatible for every other memory that shares its "bgp" tag.
    z_only = []
    for i in range(6):
        m = Memory(content=f"z fact {i}", tags=["zz"],
                   scope=MemoryScope(project_paths=["/elsewhere/project-z"]))
        storage.save(m)
        z_only.append(m.id)
    storage.save_mindmap_node(MindMapNode(id="zonly", handle="Z only", tags=["bgp", "zz"],
                                          memory_refs=z_only))
    return mems


def test_indexed_ranking_equals_unindexed(storage):
    mems = _populate(storage)
    nodes = storage._load_mindmap()
    rows = storage._load_memories()
    proj_by_id, coll = storage._provenance(rows, nodes)
    index = storage._placement_index(nodes, proj_by_id)
    ranked = [storage._rank_placement(m, nodes, proj_by_id, coll) for m in mems]
    assert any(len(r) > 1 for r in ranked)                         # the ranking is exercised
    assert any(not compat for r in ranked for _s, compat, _n in r)  # and so is the gate
    assert ranked == [storage._rank_placement(m, nodes, proj_by_id, coll, _index=index)
                      for m in mems]


def test_repair_builds_the_node_index_once_per_rebalance_pass(storage):
    from app.storage.memory import MemoryStorage
    mems = _populate(storage)
    built = []
    ranked_with = []
    real_index = MemoryStorage._placement_index
    real_rank = MemoryStorage._rank_placement

    def spy_index(self, nodes, proj_by_id):
        built.append(1)
        return real_index(self, nodes, proj_by_id)

    def spy_rank(self, memory, nodes, proj_by_id, coll=None, _index=None):
        ranked_with.append(_index is not None)
        return real_rank(self, memory, nodes, proj_by_id, coll, _index=_index)

    with patch.object(MemoryStorage, "_placement_index", spy_index), \
         patch.object(MemoryStorage, "_rank_placement", spy_rank):
        storage.repair_mindmap()
    # Every placed memory is ranked in the rebalance pass; those calls
    # share one index instead of rebuilding it (or its inputs) per memory.
    assert sum(ranked_with) >= len(mems) - 4
    assert len(built) <= 1 + (len(ranked_with) - sum(ranked_with))
