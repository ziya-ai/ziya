"""
Regression coverage for PenPal #144 [CWE-667]: MemoryStorage performed its
read-modify-write of memories.json / proposals.json with no synchronization.

Under ``uvicorn --workers N`` (genuinely parallel processes) — or when the
``await``-yielding background ``reorganize()`` task interleaves with request
handlers on one event loop — two writers each read the same snapshot and the
last write silently discards the others' changes (lost memories/proposals),
and the fixed ``.tmp`` name also collides.

Fix: a reentrant, cross-process per-file lock (``filelock`` is_singleton keyed
on the data file's realpath) applied via the ``_rmw_locked`` decorator to every
RMW method. Reentrant so any same-file RMW nested in another re-enters; the one
cross-file path (approve_proposal proposals->memories) has no reverse, so no
lock-order deadlock.

The headline test spawns real subprocesses (the --workers N analogue) and
asserts no writes are lost. A pure-Python negative control reproduces the
lost-write race on an unsynchronized RMW to prove the assertion is non-vacuous.
"""
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from app.storage.memory import MemoryStorage, _memory_file_lock_144
from app.models.memory import MemoryProposal


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path))
    return MemoryStorage()


class TestReentrancyAndCrossFile:
    def test_same_file_lock_is_reentrant(self, store):
        # Nested acquire of the SAME file's lock on one thread must re-enter,
        # not deadlock on the 10s timeout.
        f = store._memories_file
        start = time.monotonic()
        with _memory_file_lock_144(f):
            with _memory_file_lock_144(f):
                pass
        assert time.monotonic() - start < 5

    def test_approve_proposal_cross_file_no_deadlock(self, store):
        # approve_proposal holds the proposals lock and calls save() which takes
        # the memories lock — two distinct locks; must complete, not deadlock.
        prop = MemoryProposal(content="x", layer="project", tags=[], learned_from="t")
        store.add_proposal(prop)
        start = time.monotonic()
        approved = store.approve_proposal(prop.id)
        assert time.monotonic() - start < 8
        assert approved is not None
        assert any(m.content == "x" for m in store.list_memories())


class TestConcurrentProcessesNoLostWrite:
    """The headline race: parallel PROCESSES (uvicorn --workers N analogue)."""

    def test_multiprocess_saves_all_survive(self, tmp_path):
        worker = tmp_path / "w.py"
        worker.write_text(
            "import sys, os, time, random\n"
            "sys.path.insert(0, sys.argv[1]); os.environ['ZIYA_HOME']=sys.argv[2]\n"
            "from app.storage.memory import MemoryStorage\n"
            "from app.models.memory import Memory\n"
            "st=MemoryStorage()\n"
            "for i in range(int(sys.argv[4])):\n"
            "    st.save(Memory(content=f'w{sys.argv[3]}-m{i}', layer='project', tags=[], learned_from='t'))\n"
            "    time.sleep(random.uniform(0,0.003))\n"
        )
        # The app package root is two levels up from this test file's dir at
        # runtime; derive it from the imported module instead.
        import app
        app_root = os.path.dirname(os.path.dirname(os.path.abspath(app.__file__)))
        home = str(tmp_path / "ziyahome")
        n_workers, per = 4, 15
        procs = [
            subprocess.Popen(
                [sys.executable, str(worker), app_root, home, str(w), str(per)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for w in range(n_workers)
        ]
        for p in procs:
            p.wait()

        monkey_home = os.environ.get("ZIYA_HOME")
        os.environ["ZIYA_HOME"] = home
        try:
            survived = len(MemoryStorage().list_memories())
        finally:
            if monkey_home is not None:
                os.environ["ZIYA_HOME"] = monkey_home
        assert survived == n_workers * per, (
            f"lost writes under concurrent processes: {survived}/{n_workers*per}"
        )


class TestNegativeControlUnlockedRace:
    def test_unlocked_rmw_loses_writes(self, tmp_path):
        """An unsynchronized read-modify-write (the pre-fix pattern) loses
        writes under thread contention — proving the lock is load-bearing."""
        data_file = tmp_path / "c.json"
        data_file.write_text(json.dumps({"items": []}))
        n = 12
        barrier = threading.Barrier(n)

        def unlocked_append(i):
            try:
                barrier.wait()
                d = json.loads(data_file.read_text())
                time.sleep(0.002)
                d["items"].append(i)
                tmp = data_file.with_suffix(f".tmp.{i}")
                tmp.write_text(json.dumps(d))
                tmp.rename(data_file)
            except Exception:  # noqa: BLE001
                pass

        threads = [threading.Thread(target=unlocked_append, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        survived = len(json.loads(data_file.read_text())["items"])
        assert survived < n, f"control too weak: {survived}/{n} survived"
