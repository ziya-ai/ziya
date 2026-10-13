"""A run interrupted inside a parallel fan-out must be held at a block
whose retry covers EVERY in-flight branch.

Observed on the audit S7 overnight card after a server restart: four
lanes of a parallel ("Telemetry lanes" > Lane A..D) were running; the
reconciler picked "the running block with the latest started_at" and the
banner offered "Retry Lane D".  A retry targeted inside a parallel is
order-dependent in the executor (one shared resume_skipping flag; the
parallel starts children in body order), so Lanes A-C would have been
replayed as "(skipped on resume: no recorded result)" and only D re-run.

The fix places the hold at the lowest common ancestor of the in-flight
leaves, raised to the outermost enclosing parallel.  A single line of
execution is unchanged (leaf selection, mid-loop naming).
"""

from pathlib import Path

import pytest

from app.models.task_run import TaskRunBlockState, TaskRunCreate
from app.storage.task_runs import TaskRunStorage
from app.utils.resume_targets import concurrent_resume_block


def _n(bid, btype="task", body=None):
    node = {"id": bid, "block_type": btype}
    if body is not None:
        node["body"] = body
    return node


# Shape of the audit S7 card in the screenshot.
S7_ROOT = _n("root", "group", [
    _n("givens"),
    _n("foundation", "parallel", [
        _n("f0"),
        _n("telemetry", "parallel", [
            _n("lane_a"), _n("lane_b"), _n("lane_c"), _n("lane_d"),
        ]),
        _n("lane_e"),
    ]),
    _n("verify"),
    _n("report"),
])

S7_RUNNING = ["root", "foundation", "telemetry",
              "lane_a", "lane_b", "lane_c", "lane_d"]


class TestConcurrentResumeBlock:
    def test_s7_shape_resolves_to_outermost_parallel(self):
        # Not lane_d (loses A-C), and not "telemetry" either: that is
        # itself inside "foundation", so F0 / Lane E would be replayed or
        # re-run depending on body order.
        assert concurrent_resume_block(S7_ROOT, S7_RUNNING) == "foundation"

    def test_single_line_of_execution_defers_to_caller(self):
        # Containers + one leaf is ONE line of execution.
        assert concurrent_resume_block(
            S7_ROOT, ["root", "foundation", "telemetry", "lane_b"]) is None

    def test_nothing_running(self):
        assert concurrent_resume_block(S7_ROOT, []) is None

    def test_ids_not_in_tree_are_ignored(self):
        # e.g. callee blocks of a Call: not placeable in the caller tree.
        assert concurrent_resume_block(S7_ROOT, ["x-callee", "lane_a"]) is None

    def test_lca_without_parallel_is_the_lca(self):
        # Two leaves under a parallel root are under the root itself.
        root = _n("r", "parallel", [_n("a"), _n("b")])
        assert concurrent_resume_block(root, ["r", "a", "b"]) == "r"

    def test_group_inside_parallel_is_raised_to_the_parallel(self):
        root = _n("r", "group", [
            _n("p", "parallel", [
                _n("g", "group", [_n("x"), _n("y")]),
                _n("z"),
            ]),
        ])
        # Hypothetical: two leaves running under group g (which a group
        # would not do, but the rule must still not target inside p).
        assert concurrent_resume_block(root, ["x", "y"]) == "p"

    def test_inside_loop_normalizes_to_outermost_loop(self):
        root = _n("r", "group", [
            _n("loop", "repeat", [
                _n("p", "parallel", [_n("a"), _n("b")]),
            ]),
        ])
        assert concurrent_resume_block(root, ["a", "b"]) == "loop"


# ── storage seam: what the reconciler actually writes ────────────────

@pytest.fixture
def storage(tmp_path: Path) -> TaskRunStorage:
    return TaskRunStorage(tmp_path)


def _seed_s7(storage: TaskRunStorage, with_snapshot: bool = True) -> str:
    run = storage.create(TaskRunCreate(card_id="audit-s7"))
    storage.update_status(run.id, "running")
    if with_snapshot:
        storage.set_card_snapshot(run.id, {"name": "S7", "root": S7_ROOT})
    # Realistic epoch-ms: EpochMs normalization rescales small values on
    # every load/save, which would reorder fake timestamps.
    t = 1_790_000_000_000
    storage.set_block_state(run.id, TaskRunBlockState(
        block_id="givens", block_type="task", status="done",
        started_at=t, completed_at=t + 1))
    for i, bid in enumerate(S7_RUNNING):
        btype = "parallel" if bid in ("foundation", "telemetry") else (
            "group" if bid == "root" else "task")
        storage.set_block_state(run.id, TaskRunBlockState(
            block_id=bid, block_type=btype, status="running",
            started_at=t + 2 + i))
    for bid in ("f0", "lane_e"):
        storage.set_block_state(run.id, TaskRunBlockState(
            block_id=bid, block_type="task", status="failed",
            started_at=t + 2, completed_at=t + 3))
    return run.id


class TestReconcileFanout:
    def test_restart_holds_at_the_fanout_not_one_lane(self, storage):
        run_id = _seed_s7(storage)
        assert storage.reconcile_stale_runs() == 1
        swept = storage.get(run_id)
        assert swept.status == "held"
        assert swept.held_reason == "server_restart"
        assert swept.held_at_block_id == "foundation"
        # Banked work untouched.
        assert swept.block_states["givens"].status == "done"

    def test_force_abort_in_fanout_holds_at_the_fanout(self, storage):
        run_id = _seed_s7(storage)
        out = storage.mark_aborted(run_id)
        assert out.held_reason == "user_abort"
        assert out.held_at_block_id == "foundation"

    def test_without_snapshot_falls_back_to_latest_started(self, storage):
        # Pre-snapshot records cannot be placed in a tree; the prior rule
        # applies (and such runs are not resumable anyway).
        run_id = _seed_s7(storage, with_snapshot=False)
        storage.reconcile_stale_runs()
        assert storage.get(run_id).held_at_block_id == "lane_d"

    def test_single_branch_inside_a_parallel_is_raised_to_the_parallel(self, storage):
        # One lane running is still a target INSIDE a parallel, which the
        # resume path never targets (body-order replay).  The hold names
        # the parallel so the banner's "Retry X" is what actually happens.
        run = storage.create(TaskRunCreate(card_id="serial"))
        storage.update_status(run.id, "running")
        storage.set_card_snapshot(run.id, {"root": S7_ROOT})
        for i, bid in enumerate(["root", "foundation", "telemetry", "lane_b"]):
            storage.set_block_state(run.id, TaskRunBlockState(
                block_id=bid, block_type="task", status="running",
                started_at=1_790_000_000_000 + i * 1000))
        storage.reconcile_stale_runs()
        assert storage.get(run.id).held_at_block_id == "foundation"

    def test_serial_leaf_is_still_the_target(self, storage):
        run = storage.create(TaskRunCreate(card_id="serial"))
        storage.update_status(run.id, "running")
        storage.set_card_snapshot(run.id, {"root": S7_ROOT})
        for i, bid in enumerate(["root", "verify"]):
            storage.set_block_state(run.id, TaskRunBlockState(
                block_id=bid, block_type="task", status="running",
                started_at=1_790_000_000_000 + i * 1000))
        storage.reconcile_stale_runs()
        assert storage.get(run.id).held_at_block_id == "verify"


class TestMarkHeldRaisesOutOfParallel:
    """An infra fault raised by ONE branch names that branch as the block;
    the hold must still be resumable without body-order replay."""

    def _run(self, storage, snapshot=True):
        run = storage.create(TaskRunCreate(card_id="c"))
        storage.update_status(run.id, "running")
        if snapshot:
            storage.set_card_snapshot(run.id, {"root": S7_ROOT})
        return run.id

    def test_branch_fault_holds_at_outermost_parallel(self, storage):
        rid = self._run(storage)
        out = storage.mark_held(rid, reason="authentication_error",
                                block_id="lane_c")
        assert out.held_at_block_id == "foundation"
        assert storage.get(rid).held_at_block_id == "foundation"

    def test_block_outside_any_parallel_is_unchanged(self, storage):
        rid = self._run(storage)
        out = storage.mark_held(rid, reason="throttling_error", block_id="verify")
        assert out.held_at_block_id == "verify"

    def test_without_snapshot_is_unchanged(self, storage):
        rid = self._run(storage, snapshot=False)
        out = storage.mark_held(rid, reason="throttling_error", block_id="lane_c")
        assert out.held_at_block_id == "lane_c"


# ── helpers: where a parallel resume lands, and what it banks ────────

from app.utils.resume_targets import (  # noqa: E402
    REPLAY_MISSING_PREFIX, banked_parallel_children,
    outermost_parallel_ancestor,
)


class TestOutermostParallelAncestor:
    def test_nested_lane_resolves_to_outer(self):
        assert outermost_parallel_ancestor(S7_ROOT, "lane_b") == "foundation"

    def test_direct_child(self):
        assert outermost_parallel_ancestor(S7_ROOT, "f0") == "foundation"

    def test_inner_parallel_itself(self):
        assert outermost_parallel_ancestor(S7_ROOT, "telemetry") == "foundation"

    def test_top_level_parallel_and_serial_blocks(self):
        assert outermost_parallel_ancestor(S7_ROOT, "foundation") is None
        assert outermost_parallel_ancestor(S7_ROOT, "verify") is None
        assert outermost_parallel_ancestor(S7_ROOT, "nope") is None


def _done(summary):
    return {"status": "done", "artifact": {"summary": summary}}


class TestBankedParallelChildren:
    def test_finished_branches_bank_unfinished_do_not(self):
        states = {
            "f0": _done("F0 tables"),
            "lane_a": _done("A found it"),
            "lane_b": {"status": "failed",
                       "artifact": {"summary": "x", "failed": True}},
            # The empty marker a prior resume persisted: not a result.
            "lane_c": {"status": "skipped", "artifact": {
                "summary": f"{REPLAY_MISSING_PREFIX} for lane_c)"}},
            # Banked by a prior resume: still a result, still banked.
            "lane_d": {"status": "skipped", "artifact": {"summary": "D"}},
            "lane_e": {"status": "running"},
            "telemetry": {"status": "running"},
        }
        assert banked_parallel_children(S7_ROOT, "foundation", states) == {
            "f0", "lane_a", "lane_d"}

    def test_a_finished_inner_parallel_banks_whole(self):
        states = {"telemetry": _done("all lanes"), "lane_a": _done("A")}
        assert banked_parallel_children(S7_ROOT, "foundation", states) == {
            "telemetry"}

    def test_target_itself_is_never_banked(self):
        states = {"foundation": _done("F"), "f0": _done("F0")}
        assert banked_parallel_children(S7_ROOT, "foundation", states) == {"f0"}

    def test_children_of_a_sequence_are_not_banked(self):
        # Retrying the root group: Givens is a sequence child, not a
        # parallel branch, so it re-runs; the parallel's branches bank.
        states = {"givens": _done("G"), "lane_a": _done("A")}
        assert banked_parallel_children(S7_ROOT, "root", states) == {"lane_a"}

    def test_loop_bodies_are_not_banked(self):
        root = _n("r", "group", [
            _n("loop", "repeat", [_n("p", "parallel", [_n("a"), _n("b")])]),
        ])
        assert banked_parallel_children(
            root, "r", {"a": _done("A")}) == set()


def test_marker_prefix_matches_the_executor():
    # The executor writes the marker; resume_targets filters on it.  A
    # reworded marker would silently be banked as a real result.
    import inspect
    import app.agents.block_executor as be
    assert REPLAY_MISSING_PREFIX in inspect.getsource(be._replay_artifact)


# ── executor seam: a resumed parallel replays banked branches ────────

import time  # noqa: E402
from unittest.mock import patch  # noqa: E402

import app.agents.block_executor as be  # noqa: E402
from app.models.task_card import Artifact, Block  # noqa: E402


def _blk(node):
    kw = {"id": node["id"], "block_type": node["block_type"], "name": node["id"]}
    if node["block_type"] == "task":
        kw["instructions"] = "go"
    if node.get("body"):
        kw["body"] = [_blk(c) for c in node["body"]]
    return Block(**kw)


class _FakeStorage:
    def __init__(self):
        self.writes = []

    def update_block_status(self, run_id, block_id, status, error=None, artifact=None):
        self.writes.append((block_id, status, artifact))

    def __getattr__(self, name):          # every other storage call is a no-op
        return lambda *a, **k: None


class TestExecutorBanksParallelBranches:
    @pytest.mark.asyncio
    async def test_resume_at_parallel_replays_banked_branches_only(self):
        ran = []

        async def _work(blk, **kw):
            ran.append(blk.id)
            return Artifact(summary=f"ran {blk.id}", created_at=time.time())

        ctx = be.ExecutionContext(run_id="r", project_id="p", project_root="/tmp")
        ctx.resume_from_block_id = "foundation"
        ctx.resume_skipping = True
        ctx.resume_artifacts = {
            "givens": Artifact(summary="G", created_at=time.time()),
            "f0": Artifact(summary="F0", created_at=time.time()),
            "lane_a": Artifact(summary="A done", created_at=time.time()),
        }
        ctx.resume_banked_ids = {"f0", "lane_a"}
        with patch.object(be, "execute_task_block", _work):
            await be.execute_block(_blk(S7_ROOT), ctx)
        # Positive: the unfinished branches and everything downstream ran.
        assert sorted(ran) == sorted(
            ["lane_b", "lane_c", "lane_d", "lane_e", "verify", "report"])
        # The banked branch's own result is what later blocks see.
        assert ctx.artifact_registry["lane_a"].summary == "A done"

    @pytest.mark.asyncio
    async def test_replay_of_a_failed_result_persists_it_as_failed(self):
        # Control flow needs failed cleared (so on_failure=stop does not
        # halt before the target), but the RECORD must keep it, or the
        # next resume would bank a failure as a finished branch.
        ctx = be.ExecutionContext(run_id="r", project_id="p", project_root="/tmp")
        ctx.storage = _FakeStorage()
        ctx.resume_artifacts = {
            "f0": Artifact(summary="broke", failed=True, created_at=time.time())}
        out = await be._replay_artifact(_blk(_n("f0")), ctx)
        assert out.failed is False
        (bid, status, art), = ctx.storage.writes
        assert (bid, status, art.failed) == ("f0", "skipped", True)
