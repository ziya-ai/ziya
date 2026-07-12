"""
Regression coverage for PenPal #129 [CWE-667]: ChatGroupStorage performed
its read-modify-write cycle (create/update/delete/reorder + the inline
bulk_sync_groups / set_chat_group_global handlers) with no synchronization,
so concurrent writers under ``uvicorn --workers N`` (or if the sync I/O is
ever made async) each read the same _groups.json snapshot and the last
writer silently discards the others' changes — lost group metadata,
colliding order values, corrupted global-flag state.

Fix: an fcntl.flock-based ``_exclusive_lock()`` context manager on
ChatGroupStorage, wrapping every mutator's full R-M-W (and applied once
around the whole loop of the two inline API handlers).

These tests drive the REAL ChatGroupStorage against a tmp project dir.
The negative control reproduces the lost-write race with an unsynchronized
read-modify-write to prove the lock is what closes it (non-vacuous).
"""
import threading
import time
from pathlib import Path

import pytest

from app.storage.groups import ChatGroupStorage
from app.models.group import ChatGroupCreate


@pytest.fixture
def storage(tmp_path):
    return ChatGroupStorage(tmp_path)


class TestExclusiveLockPrimitive:
    def test_lock_exists_and_is_context_manager(self, storage):
        # The CM must acquire and release without error.
        with storage._exclusive_lock():
            pass
        # Re-acquirable after release (fresh fd each time).
        with storage._exclusive_lock():
            pass

    def test_lock_serializes_cross_thread(self, storage):
        events = []

        def worker(tag):
            with storage._exclusive_lock():
                events.append(f"{tag}-enter")
                time.sleep(0.3)
                events.append(f"{tag}-exit")

        ta = threading.Thread(target=worker, args=("A",))
        tb = threading.Thread(target=worker, args=("B",))
        ta.start()
        time.sleep(0.05)
        tb.start()
        ta.join()
        tb.join()

        # Whichever ran first must fully complete before the other enters:
        # no interleaving of enter/exit.
        assert events in (
            ["A-enter", "A-exit", "B-enter", "B-exit"],
            ["B-enter", "B-exit", "A-enter", "A-exit"],
        ), events

    def test_lock_file_is_separate_from_data_file(self, storage):
        # The lock target must NOT be _groups.json itself (its atomic rename
        # cycle would race the lock target).
        assert storage._lock_path != storage.groups_file
        assert str(storage._lock_path).endswith(".lock")


class TestConcurrentMutationsNoLostWrite:
    def test_concurrent_creates_all_survive(self, storage):
        """N concurrent create() calls must all persist (the headline race)."""
        n = 12
        barrier = threading.Barrier(n)
        errors = []

        def create_one(i):
            try:
                barrier.wait()  # maximize contention
                storage.create(ChatGroupCreate(name=f"group-{i}"))
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=create_one, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], errors
        survived = storage.list()
        assert len(survived) == n, (
            f"expected {n} groups to survive concurrent create, got {len(survived)}"
        )
        # Order values must be unique (no colliding order the report warned of).
        orders = [g.order for g in survived]
        assert len(set(orders)) == len(orders), f"colliding order values: {orders}"

    def test_concurrent_creates_no_deadlock(self, storage):
        """create() never nests _exclusive_lock (calls only _read/_write), so
        concurrent calls must complete well under the flock has no timeout —
        use a wall-clock ceiling to catch a regression that introduces
        nested-fd self-deadlock."""
        t0 = time.time()
        threads = [
            threading.Thread(target=lambda i=i: storage.create(ChatGroupCreate(name=f"g{i}")))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert time.time() - t0 < 10.0


class TestNegativeControlUnlockedRace:
    """Proves the race is real: an unsynchronized read-modify-write mirroring
    the PRE-FIX code loses writes under the same contention the lock survives."""

    def test_unlocked_rmw_loses_writes(self, tmp_path):
        import json

        data_file = tmp_path / "counter.json"
        data_file.write_text(json.dumps({"items": []}))

        n = 12
        barrier = threading.Barrier(n)

        def unlocked_append(i):
            # read-modify-write with NO lock (the pre-fix lost-write pattern).
            # Per-thread temp name + atomic rename so this control isolates the
            # LOST-WRITE race specifically — it does not also trip the shared
            # fixed-temp-name collision (a separate defect the lock also fixes);
            # any residual error is swallowed so the assertion measures only
            # how many writes survived, and the control never emits a spurious
            # unhandled-thread-exception traceback.
            try:
                barrier.wait()  # force interleave
                d = json.loads(data_file.read_text())
                time.sleep(0.002)  # widen the window
                d["items"].append(i)
                tmp = data_file.with_suffix(f".tmp.{i}")
                tmp.write_text(json.dumps(d))
                tmp.rename(data_file)
            except Exception:  # noqa: BLE001 — control thread errors are irrelevant here
                pass

        threads = [threading.Thread(target=unlocked_append, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        survived = len(json.loads(data_file.read_text())["items"])
        # The whole point: without a lock, writes are LOST (survived < n).
        # (If this ever equals n the control is too weak to prove anything.)
        assert survived < n, (
            f"negative control did not demonstrate the race: {survived}/{n} survived"
        )
