"""A peer the infra gate cancelled must not be banked as done by a resume.

Measured on the memory-replay card, 2026-09-27: a 3-wide parallel fan-out
(sep-baseline, sep-current, sep-current-aa) held when iteration 2 hit an
expired credential.  The gate cancelled iterations 0 and 1 mid-work
(sep-baseline at event 149 of 2,149; sep-current had not started).  Their
synthesized artifacts are ``failed=False`` by design -- the environment
failed, not the work -- and ``_record_iteration`` mapped that to status
``passed``.  ``parallel_replay_indices`` banks passed iterations, so
"retry from" re-ran only iteration 2 and replayed 0 and 1 as finished.

Three seams, each needed:

  A. the executor must PERSIST a cancelled peer as ``cancelled``
     (real ``_record_iteration`` -> summary -> ``parallel_replay_indices``);
  B. a first resume from a source written before A must not bank the
     legacy ``passed`` records whose artifact is the cancellation marker;
  C. a chained resume must not re-bank markers it CARRIED from the
     previous attempt (``resume_iteration_artifacts``), which is the
     state run 8c5deca5 is in.

Existing tests replace ``_record_iteration`` with a recorder and assert
only ``artifact.failed``, which is why none of this was caught.
"""

import asyncio
import json
import time
from typing import Any, Dict, List, Set
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.agents.block_executor as be
from app.agents.task_executor import TaskInfraError
from app.models.task_card import Artifact, Block, TaskCardCreate
from app.models.task_run import IterationSummary, TaskRunBlockState, TaskRunCreate
from app.storage.task_cards import TaskCardStorage
from app.storage.task_runs import TaskRunStorage
from app.utils.resume_targets import (
    cancelled_sibling_summary, is_cancelled_sibling_artifact, parallel_replay_indices)

LOOP_ID = "b-fan"


def _loop(n: int) -> Block:
    return Block(
        id=LOOP_ID, block_type="repeat", name="fan", repeat_mode="count",
        repeat_count=n, repeat_parallel=True, repeat_propagate="none",
        body=[Block(id="t1", block_type="task", name="w", instructions="go")],
    )


def _root(n: int) -> Block:
    return Block(block_type="group", id="root", name="card", body=[
        Block(block_type="task", id="plan", name="plan", instructions="plan"),
        _loop(n),
    ])


def _marker(i: int) -> Artifact:
    """Exactly what the executor synthesized for a gate-cancelled peer."""
    return Artifact(summary=cancelled_sibling_summary(i),
                    decisions=["asyncio.exceptions.CancelledError"],
                    duration_ms=0, created_at=time.time(), failed=False)


# ── A: the executor persists "cancelled" ──────────────────────────────


class _FakeStorage:
    def __init__(self):
        self.summaries: List[IterationSummary] = []

    def write_iteration_artifact(self, *a, **kw):
        pass

    def append_iteration_summary(self, run_id, block_id, summary):
        self.summaries.append(summary)


@pytest.mark.asyncio
async def test_executor_persists_cancelled_peers_and_resume_rebanks_none(monkeypatch):
    """Real executor, real ``_record_iteration``, real banking function."""
    async def _run(block, **kwargs):
        from app.context import get_task_iteration_context
        idx = (get_task_iteration_context() or {}).get("index")
        if idx == 2:
            await asyncio.sleep(0.05)
            raise TaskInfraError("credentials expired",
                                 infra_kind="authentication_error", block_id="t1")
        await asyncio.sleep(5)
        return Artifact(summary=f"iteration {idx} ok", created_at=time.time())

    store = _FakeStorage()
    sink = be.ExecutionContext(run_id="r-sink", project_id="p", project_root="/tmp")
    sink.storage = store
    real_record = be._record_iteration

    async def _persist(block, ctx, index, artifact, item_key=None):
        await real_record(block, sink, index, artifact, item_key=item_key)

    monkeypatch.setattr(be, "execute_task_block", _run)
    monkeypatch.setattr(be, "_record_iteration", _persist)
    ctx = be.ExecutionContext(run_id="r-exec", project_id="p", project_root="/tmp")
    with pytest.raises(TaskInfraError):
        await be.execute_block(_loop(3), ctx)

    status = {s.index: s.status for s in store.summaries}
    assert status == {0: "cancelled", 1: "cancelled", 2: "failed"}
    sums = [s.model_dump() for s in store.summaries]
    assert parallel_replay_indices(_root(3).model_dump(), LOOP_ID, sums) == []


def test_genuine_pass_still_banks():
    """Positive control for A: only the cancelled status is excluded."""
    sums = [IterationSummary(index=0, status="passed", has_artifact=True).model_dump(),
            IterationSummary(index=1, status="cancelled", has_artifact=True).model_dump()]
    assert parallel_replay_indices(_root(2).model_dump(), LOOP_ID, sums) == [0]


def test_marker_detection_is_exact():
    assert is_cancelled_sibling_artifact(_marker(3))
    assert is_cancelled_sibling_artifact(_marker(3).model_dump())
    # the live legacy record, verbatim shape
    assert is_cancelled_sibling_artifact({
        "summary": "Iteration 0 cancelled — a sibling hit an infrastructure fault",
        "decisions": ["asyncio.exceptions.CancelledError"], "outputs": [],
        "tokens": 0, "tool_calls": 0, "failed": False})
    # an iteration that did work is never a marker, even with that wording
    assert not is_cancelled_sibling_artifact(Artifact(
        summary=cancelled_sibling_summary(1), created_at=time.time(), tokens=5))
    assert not is_cancelled_sibling_artifact(Artifact(summary="ok", created_at=time.time()))


# ── B, C: the resume endpoint over legacy records ─────────────────────


def _project(tmp_path):
    home = tmp_path / ".ziya"
    pid = "proj-cancelled"
    pdir = home / "projects" / pid
    (pdir / "chats").mkdir(parents=True)
    (pdir / "project.json").write_text(json.dumps({
        "id": pid, "name": "Cancelled", "path": str(tmp_path),
        "settings": {"defaultContextIds": [], "defaultSkillIds": []},
        "createdAt": int(time.time() * 1000), "lastAccessedAt": int(time.time() * 1000)}))
    return home, pid, pdir


def _source(pdir, root, **create_kw):
    card = TaskCardStorage(pdir).create(TaskCardCreate(name="card", root=root))
    runs = TaskRunStorage(pdir)
    src = runs.create(TaskRunCreate(card_id=card.id, source_conversation_id="c", **create_kw))
    runs.set_card_snapshot(src.id, {"name": "card", "description": "", "root": root.model_dump()})
    runs.set_block_state(src.id, TaskRunBlockState(
        block_id="plan", block_type="task", status="done",
        artifact=Artifact(summary="planned", created_at=time.time())))
    return runs, card, src


def _client(home, pdir, card_id, captured):
    async def _capture(**kwargs):
        captured.update(kwargs)
        return TaskRunStorage(pdir).create(TaskRunCreate(card_id=card_id))
    return (patch("app.api.task_runs.get_ziya_home", return_value=home),
            patch("app.api.task_runs.get_project_dir", return_value=pdir),
            patch("app.api.task_cards._launch_run_for_card", new=_capture))


def _resume_banked(home, pid, pdir, card_id, run_id) -> Set[int]:
    captured: Dict[str, Any] = {}
    p1, p2, p3 = _client(home, pdir, card_id, captured)
    with p1, p2, p3:
        from app.api.task_runs import router
        app = FastAPI(); app.include_router(router)
        res = TestClient(app).post(
            f"/api/v1/projects/{pid}/task-runs/{run_id}/resume-from/{LOOP_ID}?mode=retry")
    assert res.status_code == 200, res.text
    return {int(k) for k in (captured.get("resume_iteration_artifacts") or {})}


def test_first_resume_does_not_bank_legacy_cancelled_records(tmp_path):
    """Source written before A: cancelled peers are stored as PASSED.
    0 genuinely finished; 1, 2 were cancelled; 3 faulted (run daf6eedd)."""
    home, pid, pdir = _project(tmp_path)
    runs, card, src = _source(pdir, _root(4))
    runs.write_iteration_artifact(src.id, LOOP_ID, 0,
                                  Artifact(summary="run 0 done", created_at=time.time()))
    for i in (1, 2):
        runs.write_iteration_artifact(src.id, LOOP_ID, i, _marker(i))
    runs.write_iteration_artifact(src.id, LOOP_ID, 3,
                                  Artifact(summary="auth", created_at=time.time(), failed=True))
    runs.set_block_state(src.id, TaskRunBlockState(
        block_id=LOOP_ID, block_type="repeat", status="held", iteration_summaries=[
            IterationSummary(index=0, status="passed", has_artifact=True),
            IterationSummary(index=1, status="passed", has_artifact=True),
            IterationSummary(index=2, status="passed", has_artifact=True),
            IterationSummary(index=3, status="failed", has_artifact=True, signature="x")]))
    runs.update_status(src.id, "held")
    assert _resume_banked(home, pid, pdir, card.id, src.id) == {0}


def test_chained_resume_does_not_rebank_carried_markers(tmp_path):
    """Attempt 2 (run 8c5deca5's state): it CARRIED markers for 1 and 2
    from attempt 1 and a real artifact for 0; it executed only 3."""
    home, pid, pdir = _project(tmp_path)
    runs, card, src = _source(
        pdir, _root(4), attempt=2, resume_kind="retry_from", resumed_from_block_id=LOOP_ID,
        resume_iteration_artifacts={0: Artifact(summary="run 0 done", created_at=time.time()),
                                    1: _marker(1), 2: _marker(2)})
    runs.write_iteration_artifact(src.id, LOOP_ID, 0,
                                  Artifact(summary="run 0 done", created_at=time.time()))
    for i in (1, 2):
        runs.write_iteration_artifact(src.id, LOOP_ID, i, _marker(i))
    runs.write_iteration_artifact(src.id, LOOP_ID, 3,
                                  Artifact(summary="auth again", created_at=time.time(), failed=True))
    runs.set_block_state(src.id, TaskRunBlockState(
        block_id=LOOP_ID, block_type="repeat", status="held", iteration_summaries=[
            IterationSummary(index=0, status="passed", has_artifact=True, replayed=True),
            IterationSummary(index=1, status="passed", has_artifact=True, replayed=True),
            IterationSummary(index=2, status="passed", has_artifact=True, replayed=True),
            IterationSummary(index=3, status="failed", has_artifact=True, signature="x")]))
    runs.update_status(src.id, "held")
    assert _resume_banked(home, pid, pdir, card.id, src.id) == {0}
