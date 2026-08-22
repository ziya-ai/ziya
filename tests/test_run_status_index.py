"""
Tests for the project-wide run-status index.

Two things here are load-bearing and easy to get wrong, so each has its own
class:

  1. Counting is per attempt-LINEAGE.  A card retried twice produces three
     run records for one logical piece of work; counting records would
     report "3 failed" for a card that failed once.

  2. The cache is per FILE, not per directory.  The first implementation
     memoised on the run directory's mtime, which is defeated by the exact
     condition it must survive: a running task heartbeats every 5 s through
     temp+rename, which bumps the DIRECTORY mtime, so the memo never hit
     while anything was running and every poll re-read the whole history.
     ``TestHeartbeatDoesNotDefeatTheCache`` pins that specifically -- it is
     the test that would have caught the original design.
"""

import json
import os
import time

import pytest

from app.utils.run_status_index import (
    KNOWN_STATUSES, LIVE_STATUSES, RunStatusIndexCache,
    build_status_index, cache_for, clear_all_caches,
    collapse_to_newest_attempt, has_live_runs, invalidate_for,
    summarize_run,
)


class _Run:
    """Minimal stand-in for a TaskRun record."""

    def __init__(self, id, status, conv=None, root=None, attempt=1):
        self.id = id
        self.status = status
        self.source_conversation_id = conv
        self.root_run_id = root
        self.attempt = attempt


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_all_caches()
    yield
    clear_all_caches()


@pytest.fixture
def runs_dir(tmp_path):
    d = tmp_path / "task_runs"
    d.mkdir()
    return d


def _write(d, run_id, status, conv="c1", root=None, attempt=1, bulk=0):
    """Write a run file the way the storage layer does.

    Temp file then rename, mirroring ``BaseStorage._write_json``.  This is
    not incidental: an in-place rewrite does NOT move the directory mtime
    while a rename does, and the cache's pre-gate keys on exactly that.  A
    helper that wrote in place would exercise a path production never
    produces, and would make a sound pre-gate look broken.
    """
    payload = {
        "id": run_id, "status": status, "source_conversation_id": conv,
        "root_run_id": root or run_id, "attempt": attempt,
    }
    if bulk:
        payload["block_states"] = {f"b{i}": {"x": "y" * 200} for i in range(bulk)}
    tmp = d / f"{run_id}.tmp"
    tmp.write_text(json.dumps(payload))
    tmp.rename(d / f"{run_id}.json")


def _reader(path):
    """A read_one that parses plain JSON."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class TestSummarize:

    def test_extracts_the_sidebar_fields(self):
        s = summarize_run(_Run("r1", "held", conv="c1", root="r0", attempt=3))
        assert (s.status, s.conversation_id, s.lineage, s.attempt) == (
            "held", "c1", "r0", 3)

    def test_accepts_a_dict_as_well_as_a_model(self):
        s = summarize_run({"id": "r1", "status": "done",
                           "source_conversation_id": "c1"})
        assert s.status == "done" and s.conversation_id == "c1"

    def test_a_record_with_no_status_is_uncountable(self):
        assert summarize_run({"id": "r1"}) is None

    def test_missing_lineage_falls_back_to_the_run_id(self):
        """A pre-lineage record must be its own lineage, not collide with
        every other pre-lineage record under a null key."""
        a = summarize_run(_Run("r1", "done", conv="c1"))
        b = summarize_run(_Run("r2", "done", conv="c1"))
        assert a.lineage == "r1" and b.lineage == "r2"

    def test_a_non_numeric_attempt_does_not_raise(self):
        s = summarize_run({"id": "r1", "status": "done",
                           "source_conversation_id": "c1", "attempt": "oops"})
        assert s.attempt == 1


class TestLineageCollapse:
    """The counting rule, in the direction that matters."""

    def test_a_retried_card_counts_once(self):
        runs = [_Run(f"r{i}", "failed", conv="c1", root="r1", attempt=i)
                for i in (1, 2, 3)]
        assert build_status_index(runs)["c1"] == {"failed": 1}

    def test_the_newest_attempt_decides_the_status(self):
        runs = [
            _Run("r1", "failed", conv="c1", root="r1", attempt=1),
            _Run("r2", "done", conv="c1", root="r1", attempt=2),
        ]
        assert build_status_index(runs)["c1"] == {"done": 1}

    def test_distinct_cards_are_not_collapsed_together(self):
        runs = [
            _Run("r1", "done", conv="c1", root="r1"),
            _Run("r2", "held", conv="c1", root="r2"),
        ]
        assert build_status_index(runs)["c1"] == {"done": 1, "held": 1}

    def test_a_tie_resolves_to_one_entry(self):
        runs = [_Run("r1", "done", conv="c1", root="rL", attempt=1),
                _Run("r2", "failed", conv="c1", root="rL", attempt=1)]
        assert sum(build_status_index(runs)["c1"].values()) == 1

    def test_lineages_collapse_within_a_conversation_only(self):
        """A colliding pre-lineage id in another conversation must not
        suppress a real run."""
        runs = [_Run("shared", "done", conv="c1", root="shared"),
                _Run("shared", "held", conv="c2", root="shared")]
        idx = build_status_index(runs)
        assert idx["c1"] == {"done": 1} and idx["c2"] == {"held": 1}


class TestIndexShape:

    def test_groups_by_conversation(self):
        runs = [_Run("r1", "done", conv="c1"), _Run("r2", "held", conv="c2")]
        assert set(build_status_index(runs)) == {"c1", "c2"}

    def test_runs_with_no_conversation_are_omitted(self):
        """They belong to no row, so counting them inflates nothing
        visible."""
        assert build_status_index([_Run("r1", "done", conv=None)]) == {}

    def test_an_unknown_status_is_kept_under_its_own_key(self):
        """A newer writer's status must not be silently dropped."""
        idx = build_status_index([_Run("r1", "teleported", conv="c1")])
        assert idx["c1"] == {"teleported": 1}

    def test_empty_input(self):
        assert build_status_index([]) == {}


class TestLiveDetection:

    @pytest.mark.parametrize("status", LIVE_STATUSES)
    def test_live_statuses_are_live(self, status):
        assert has_live_runs({"c1": {status: 1}})

    @pytest.mark.parametrize(
        "status", ["done", "partial", "failed", "cancelled", "held"])
    def test_terminal_statuses_are_not(self, status):
        assert not has_live_runs({"c1": {status: 1}})

    def test_held_is_not_live(self):
        """Explicit because it is the tempting mistake: a held run is
        stopped and only a human moves it, so polling it would never end."""
        assert "held" in KNOWN_STATUSES
        assert "held" not in LIVE_STATUSES
        assert not has_live_runs({"c1": {"held": 5}})

    def test_a_zero_count_is_not_live(self):
        assert not has_live_runs({"c1": {"running": 0}})


class TestPerFileCache:

    def test_reads_every_file_on_the_first_scan(self, runs_dir):
        for i in range(3):
            _write(runs_dir, f"r{i}", "done")
        cache = RunStatusIndexCache(str(runs_dir))
        idx = cache.get(_reader)
        assert idx["c1"] == {"done": 3}
        assert cache.reads_last_scan == 3

    def test_reads_nothing_when_no_file_changed(self, runs_dir):
        for i in range(3):
            _write(runs_dir, f"r{i}", "done")
        cache = RunStatusIndexCache(str(runs_dir))
        cache.get(_reader)
        cache.get(_reader)
        assert cache.reads_last_scan == 0

    def test_reads_only_the_file_that_changed(self, runs_dir):
        for i in range(5):
            _write(runs_dir, f"r{i}", "done")
        cache = RunStatusIndexCache(str(runs_dir))
        cache.get(_reader)
        time.sleep(0.01)
        _write(runs_dir, "r2", "held")
        idx = cache.get(_reader)
        assert cache.reads_last_scan == 1, (
            "changing one run re-read %d files; the whole point is that it "
            "reads one" % cache.reads_last_scan)
        assert idx["c1"] == {"done": 4, "held": 1}

    def test_picks_up_a_new_run(self, runs_dir):
        _write(runs_dir, "r1", "done")
        cache = RunStatusIndexCache(str(runs_dir))
        cache.get(_reader)
        _write(runs_dir, "r2", "running")
        assert cache.get(_reader)["c1"] == {"done": 1, "running": 1}

    def test_forgets_a_deleted_run(self, runs_dir):
        _write(runs_dir, "r1", "done")
        _write(runs_dir, "r2", "held")
        cache = RunStatusIndexCache(str(runs_dir))
        cache.get(_reader)
        os.unlink(runs_dir / "r2.json")
        assert cache.get(_reader)["c1"] == {"done": 1}

    def test_a_missing_directory_is_empty_not_an_error(self, tmp_path):
        cache = RunStatusIndexCache(str(tmp_path / "nope"))
        assert cache.get(_reader) == {}

    def test_an_unreadable_file_does_not_break_the_index(self, runs_dir):
        _write(runs_dir, "r1", "done")
        (runs_dir / "bad.json").write_text("{not json")
        cache = RunStatusIndexCache(str(runs_dir))
        assert cache.get(_reader)["c1"] == {"done": 1}

    def test_an_unreadable_file_is_not_retried_every_scan(self, runs_dir):
        """A corrupt or undecryptable record must not cost a read forever."""
        (runs_dir / "bad.json").write_text("{not json")
        cache = RunStatusIndexCache(str(runs_dir))
        cache.get(_reader)
        cache.get(_reader)
        assert cache.reads_last_scan == 0

    def test_non_json_files_are_ignored(self, runs_dir):
        _write(runs_dir, "r1", "done")
        (runs_dir / "r1.tmp").write_text("partial write in flight")
        cache = RunStatusIndexCache(str(runs_dir))
        assert cache.get(_reader)["c1"] == {"done": 1}
        assert cache.reads_last_scan == 1

    def test_invalidate_forces_a_full_reread(self, runs_dir):
        _write(runs_dir, "r1", "done")
        cache = RunStatusIndexCache(str(runs_dir))
        cache.get(_reader)
        cache.invalidate()
        cache.get(_reader)
        assert cache.reads_last_scan == 1

    def test_built_at_is_stamped(self, runs_dir):
        _write(runs_dir, "r1", "done")
        cache = RunStatusIndexCache(str(runs_dir))
        cache.get(_reader)
        assert cache.built_at > 0


class TestHeartbeatDoesNotDefeatTheCache:
    """The regression that killed the per-directory design.

    ``record_activity`` writes a heartbeat every 5 s for each running task,
    through ``_write_json``'s temp-file-plus-rename -- and a rename bumps the
    DIRECTORY mtime.  A directory-scoped memo therefore missed on every poll
    while anything was running, which is exactly when the sidebar polls, so
    each poll re-read the entire run history (measured: 176 ms for 134
    records, 569 ms for 500).
    """

    def test_a_heartbeat_on_one_run_costs_one_read(self, runs_dir):
        for i in range(20):
            _write(runs_dir, f"r{i}", "done", bulk=4)
        _write(runs_dir, "live", "running", bulk=4)
        cache = RunStatusIndexCache(str(runs_dir))
        cache.get(_reader)

        for _ in range(3):
            time.sleep(0.01)
            # Exactly what _write_json does: temp file, then rename over.
            tmp = runs_dir / "live.tmp"
            tmp.write_text(json.dumps({
                "id": "live", "status": "running",
                "source_conversation_id": "c1", "root_run_id": "live",
                "attempt": 1, "last_activity_at": time.time(),
            }))
            tmp.rename(runs_dir / "live.json")
            cache.get(_reader)
            assert cache.reads_last_scan == 1, (
                "a heartbeat re-read %d of 21 files; a per-directory memo "
                "would have re-read all of them"
                % cache.reads_last_scan)

    def test_the_directory_mtime_moves_even_so(self, runs_dir):
        """Documents the filesystem fact the original design got wrong, so
        nobody reintroduces a directory-scoped memo."""
        _write(runs_dir, "r1", "done")
        before = os.stat(runs_dir).st_mtime
        time.sleep(0.01)
        tmp = runs_dir / "r1.tmp"
        tmp.write_text('{"id":"r1","status":"done"}')
        tmp.rename(runs_dir / "r1.json")
        assert os.stat(runs_dir).st_mtime != before, (
            "temp+rename did not move the directory mtime on this platform; "
            "the per-file cache is still correct, but the rationale in the "
            "module docstring should be revisited")


class TestPreGateAssumption:
    """The pre-gate trusts one filesystem fact; pin it explicitly.

    ``get`` skips all per-file stats when the directory mtime is unchanged.
    That is only sound because every way the storage layer mutates a run --
    create, delete, and rewrite-via-rename -- moves the directory mtime.  If
    a platform ever breaks that, the sidebar would go stale silently, so this
    fails loudly instead.
    """

    def test_rename_over_moves_the_directory_mtime(self, runs_dir):
        _write(runs_dir, "r1", "done")
        before = os.stat(runs_dir).st_mtime
        time.sleep(0.02)
        _write(runs_dir, "r1", "held")          # temp+rename, as storage does
        assert os.stat(runs_dir).st_mtime != before, (
            "rename-over did not move the directory mtime on this platform; "
            "the pre-gate in RunStatusIndexCache.get would skip real changes"
        )

    def test_create_moves_the_directory_mtime(self, runs_dir):
        _write(runs_dir, "r1", "done")
        before = os.stat(runs_dir).st_mtime
        time.sleep(0.02)
        _write(runs_dir, "r2", "done")
        assert os.stat(runs_dir).st_mtime != before

    def test_delete_moves_the_directory_mtime(self, runs_dir):
        _write(runs_dir, "r1", "done")
        _write(runs_dir, "r2", "done")
        before = os.stat(runs_dir).st_mtime
        time.sleep(0.02)
        os.unlink(runs_dir / "r2.json")
        assert os.stat(runs_dir).st_mtime != before

    def test_an_idle_poll_skips_the_per_file_stats(self, runs_dir):
        """The saving the pre-gate exists for: 6.1 ms of stats for 203 files
        versus 0.026 ms for one directory stat."""
        for i in range(5):
            _write(runs_dir, f"r{i}", "done")
        cache = RunStatusIndexCache(str(runs_dir))
        cache.get(_reader)
        scans_after_build = cache.scans
        for _ in range(10):
            cache.get(_reader)
        assert cache.scans == scans_after_build, (
            "the directory was re-scanned %d times while idle"
            % (cache.scans - scans_after_build))


class TestReaderContract:
    """The cache takes a PER-FILE reader, and a wrong one fails silently.

    The read site wraps ``read_one`` in a broad except -- correctly, so one
    corrupt or undecryptable record cannot break the whole index -- which
    also swallows a TypeError from a reader with the wrong signature.  Every
    file then summarises to None and the index comes back EMPTY with no
    error anywhere, so every gear on every unopened row is simply absent.

    That is what happened when the endpoint passed the zero-arg ``list``.
    These pin the contract from both sides.
    """

    def test_a_zero_arg_reader_yields_an_empty_index(self, runs_dir):
        """Documents the failure mode rather than the fix, so the silence is
        visible to the next reader."""
        _write(runs_dir, "r1", "held", conv="c1")
        cache = RunStatusIndexCache(str(runs_dir))
        idx = cache.get(lambda: [])          # wrong arity, as ``list`` is
        assert idx == {}, (
            "a wrong-arity reader produced a non-empty index; the silent "
            "failure this test documents may have changed shape"
        )

    def test_a_per_file_reader_yields_the_real_index(self, runs_dir):
        _write(runs_dir, "r1", "held", conv="c1")
        cache = RunStatusIndexCache(str(runs_dir))
        assert cache.get(_reader) == {"c1": {"held": 1}}

    def test_the_storage_reader_satisfies_the_contract(self, tmp_path):
        """The actual object the endpoint passes.  A unit test of the cache
        alone cannot catch the endpoint handing it the wrong callable, which
        is exactly how this shipped broken."""
        from app.storage.task_runs import TaskRunStorage
        proj = tmp_path / "proj"
        (proj / "task_runs").mkdir(parents=True)
        rec = {
            "id": "r1", "card_id": "c", "status": "held",
            "source_conversation_id": "conv1", "root_run_id": "r1",
            "attempt": 1, "created_at": 1, "updated_at": 1,
        }
        tmp = proj / "task_runs" / "r1.tmp"
        tmp.write_text(json.dumps(rec))
        tmp.rename(proj / "task_runs" / "r1.json")

        storage = TaskRunStorage(proj)
        assert hasattr(storage, "read_run_file"), (
            "TaskRunStorage has no per-file reader; the status-index "
            "endpoint has nothing correct to pass to the cache"
        )
        cache = RunStatusIndexCache(str(storage.runs_dir))
        assert cache.get(storage.read_run_file) == {"conv1": {"held": 1}}

    def test_the_storage_reader_returns_none_for_a_bad_file(self, tmp_path):
        from app.storage.task_runs import TaskRunStorage
        proj = tmp_path / "proj"
        (proj / "task_runs").mkdir(parents=True)
        storage = TaskRunStorage(proj)
        assert storage.read_run_file(str(proj / "task_runs" / "nope.json")) is None


class TestSharedRegistry:
    """The memo only works if it outlives the storage object.

    ``TaskRunStorage`` is constructed per use -- a dozen call sites, one per
    HTTP request -- so a cache held on the instance would be born empty on
    every poll and never hit.
    """

    def test_same_dir_returns_the_same_cache(self, tmp_path):
        assert cache_for(str(tmp_path)) is cache_for(str(tmp_path))

    def test_different_dirs_are_isolated(self, tmp_path):
        d1 = tmp_path / "p1"; d1.mkdir()
        d2 = tmp_path / "p2"; d2.mkdir()
        assert cache_for(str(d1)) is not cache_for(str(d2))

    def test_the_memo_survives_across_lookups(self, runs_dir):
        """The property an instance-scoped cache cannot have."""
        _write(runs_dir, "r1", "done")
        cache_for(str(runs_dir)).get(_reader)
        cache_for(str(runs_dir)).get(_reader)   # a second request
        assert cache_for(str(runs_dir)).reads_last_scan == 0

    def test_invalidate_for_reaches_the_shared_cache(self, runs_dir):
        _write(runs_dir, "r1", "running")
        cache_for(str(runs_dir)).get(_reader)
        invalidate_for(str(runs_dir))
        cache_for(str(runs_dir)).get(_reader)
        assert cache_for(str(runs_dir)).reads_last_scan == 1

    def test_invalidate_for_an_unknown_dir_is_a_noop(self, tmp_path):
        """Writers call this unconditionally, including for projects nothing
        has polled yet."""
        invalidate_for(str(tmp_path / "never_seen"))


class TestMemoryFootprint:
    """The cache must not become the payload it exists to avoid."""

    def test_a_summary_is_far_smaller_than_a_run_record(self, runs_dir):
        import sys
        from app.utils.run_status_index import _Summary
        s = _Summary("done", "c1", "r1", 1)
        # __slots__, so no per-instance dict.
        assert not hasattr(s, "__dict__")
        size = sys.getsizeof(s)
        assert size < 200, f"summary is {size} bytes; expected well under 200"

    def test_bulk_of_a_run_record_is_not_retained(self, runs_dir):
        """Only the summary is kept -- the block states and iteration
        summaries that make records ~108 KB are dropped after counting."""
        _write(runs_dir, "r1", "done", bulk=50)
        cache = RunStatusIndexCache(str(runs_dir))
        cache.get(_reader)
        entry = next(iter(cache._entries.values()))
        summary = entry[2]
        assert summary is not None
        assert not hasattr(summary, "block_states")
        assert set(summary.__slots__) == {
            "status", "conversation_id", "lineage", "attempt"}
