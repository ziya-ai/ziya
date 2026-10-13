"""Gather-contract re-runs and iteration attempts (design/typed-findings.md,
steps 6f and 6g).

Seam: execute_block -> container -> scripted model -> REAL emit_artifact ->
the container's rerun pass -> the container-end check, with REAL
TaskRunStorage for the attempt records, so the highest-attempt rule is
checked where resume reads it.
"""
import asyncio
import json
import time
from unittest.mock import patch

import pytest

from app.agents.block_executor import ExecutionContext, execute_block
from app.models.task_card import Block

DONE = 'done <self_assessment objective_met="true" rationale="ok"/>'
FINDING = {"id": "T-X", "title": "t", "verdict": "candidate"}
EMIT = [dict(name="finding", part_type="data", data=FINDING)]
G = {"name": "finding", "schema": "ziya:finding/v1"}


async def _emit(**kw):
    from app.mcp.tools.emit_artifact import EmitArtifactTool
    return (await EmitArtifactTool().execute(**kw))["content"][0]["text"]


class _Fake:
    behave = staticmethod(lambda instr, nth: [])
    calls = []

    def __init__(self, *a, **k):
        pass

    async def stream_with_tools(self, messages, **_):
        instr = messages[1].content
        key = _key(instr)
        nth = sum(1 for c in type(self).calls if _key(c) == key)
        type(self).calls.append(instr)
        for kw in type(self).behave(instr, nth):
            await _emit(**kw)
        yield {"type": "text", "content": DONE}
        yield {"type": "stream_end"}


def _key(instr):
    # Identity of a task across attempts: its own instruction line, which
    # the re-run note is prepended to.
    return instr.strip().splitlines()[-1]


def _run(root, behave, storage=None, proj="/tmp"):
    _Fake.behave, _Fake.calls = behave, []
    events = []

    async def _capture(ctx, evt):
        events.append(evt)

    with patch("app.streaming_tool_executor.StreamingToolExecutor", _Fake), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x", "current_model": "f"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]), \
         patch("app.agents.block_executor._emit", _capture):
        art = asyncio.run(execute_block(root, ExecutionContext(
            run_id=getattr(storage, "_rid", "run-r"), project_root=str(proj), storage=storage)))
    return art, events


def _t(instr, id_):
    return Block(block_type="task", id=id_, name=id_, instructions=instr)


def _lanes(rerun=1, **g):
    return Block(block_type="parallel", id="lanes", name="Lanes",
                 gather_contract=[{**G, "per": "child", "rerun_missing": rerun, **g}],
                 body=[_t("lane one", "c1"), _t("lane two", "c2"), _t("lane three", "c3")])


def _of(events, t):
    return [e for e in events if e.get("type") == t]


def _attempts(word):
    return [c for c in _Fake.calls if c.strip().endswith(word)]


def _findings(art):
    return [o for o in art.outputs if getattr(o, "name", None) == "finding"]


# ── Parallel ──────────────────────────────────────────────────────────────

def test_a_short_child_is_rerun_once_with_a_note_and_the_parallel_passes():
    # Child 2 emits nothing on attempt 1 (call 0, plus its correction turn,
    # call 1) and a finding on attempt 2.
    def behave(instr, nth):
        if instr.endswith("two"):
            return EMIT if nth >= 2 else []
        return EMIT
    art, ev = _run(_lanes(), behave)
    [rr] = _of(ev, "unit_rerun")
    assert rr["units"] == ["c2"] and "produced no valid `finding` and failed (output_contract:finding)" in rr["reasons"][0]
    two = _attempts("two")
    assert len(two) == 3, "attempt 1 + its correction turn + the re-run"
    assert two[2].startswith("[Re-run of 'c2'"), "the re-run is told why"
    assert len(_attempts("one")) == 1 and len(_attempts("three")) == 1
    assert not art.failed, art.decisions
    assert len(_findings(art)) == 3
    assert [s.get("attempt") for s in art.stages] == [None, 2, None]
    assert not _of(ev, "contract_shortfall")


def test_without_rerun_missing_the_same_script_fails_naming_the_child():
    art, ev = _run(_lanes(rerun=0), lambda instr, nth: [] if instr.endswith("two") and nth < 2 else EMIT)
    assert art.failed and art.signature == "gather_contract:finding"
    assert not _of(ev, "unit_rerun") and len(_attempts("two")) == 2


def test_still_short_after_the_rerun_fails_and_says_a_rerun_was_attempted():
    art, ev = _run(_lanes(), lambda instr, nth: [] if instr.endswith("two") else EMIT)
    assert len(_attempts("two")) == 4, "two attempts, each with its correction turn: bounded"
    assert art.failed
    [sf] = _of(ev, "contract_shortfall")
    assert sf["rerun_attempted"] is True and sf["units"] == [{"unit": "c2", "count": 0}]


def test_units_over_max_are_not_rerun():
    # max 1: c1 tries to emit twice; the second is refused at emit, so it
    # holds exactly 1 and is neither short nor re-run (a unit cannot be
    # pushed over max through emit, which is why "over max" never re-runs).
    two = EMIT * 2
    art, ev = _run(_lanes(max=1), lambda instr, nth: two if instr.endswith("one") else EMIT)
    assert not _of(ev, "unit_rerun") and not art.failed


# ── Repeat: attempts on the record ────────────────────────────────────────

@pytest.fixture
def storage(tmp_path):
    from app.storage.task_runs import TaskRunStorage
    from app.models.task_run import TaskRunBlockState, TaskRunCreate
    st = TaskRunStorage(tmp_path / "proj")
    run = st.create(TaskRunCreate(card_id="c"))
    st.set_block_state(run.id, TaskRunBlockState(
        block_id="each", block_type="repeat", status="queued"))
    st._rid = run.id
    return st


def _each(parallel):
    return Block(block_type="repeat", id="each", name="Each", repeat_mode="for_each",
                 repeat_for_each_source='["a", "b", "c"]', repeat_parallel=parallel,
                 repeat_require_complete=True,
                 gather_contract=[{**G, "per": "iteration", "rerun_missing": 1}],
                 body=[_t("audit {{item}}", "w")])


@pytest.mark.parametrize("parallel", [False, True])
def test_a_short_member_is_rerun_with_its_item_and_both_attempts_are_recorded(storage, tmp_path, parallel):
    def behave(instr, nth):
        if instr.endswith("audit b"):
            return EMIT if nth >= 2 else []
        return EMIT
    art, ev = _run(_each(parallel), behave, storage=storage, proj=tmp_path)
    assert not art.failed, art.decisions
    assert _of(ev, "unit_rerun")[0]["units"] == ["b"]
    b = _attempts("audit b")
    assert len(b) == 3 and b[2].startswith("[Re-run of 'b'")
    assert len(_findings(art)) == 3, "the re-run's part replaced the (empty) first attempt"

    run = storage.get(storage._rid)
    sums = run.block_states["each"].iteration_summaries
    b_recs = [s for s in sums if s.item_key == "b"]
    assert [(s.attempt, s.status) for s in b_recs] == [(1, "failed"), (2, "passed")], "attempt 1 failed its pushed-down contract"
    # Per-attempt files: attempt 1 is kept, and the default read is attempt 2.
    a1 = storage.read_iteration_artifact(storage._rid, "each", 1, attempt=1)
    a2 = storage.read_iteration_artifact(storage._rid, "each", 1)
    assert a1 is not None and a2 is not None
    assert not [o for o in a1.outputs if getattr(o, "name", None) == "finding"]
    assert [o for o in a2.outputs if getattr(o, "name", None) == "finding"]


def test_highest_attempt_wins_for_resume_and_outcome(tmp_path):
    from app.models.task_run import IterationSummary
    from app.utils.iteration_attempts import latest_attempts
    from app.utils.resume_targets import parallel_replay_indices, serial_replay_prefix
    from app.utils.run_outcome import _iteration_statuses
    sums = [IterationSummary(index=0, status="passed"),
            IterationSummary(index=1, status="failed"),
            IterationSummary(index=1, status="passed", attempt=2),
            IterationSummary(index=2, status="passed")]
    assert [(s.index, s.attempt) for s in latest_attempts(sums)] == [(0, 1), (1, 2), (2, 1)]
    dumped = [s.model_dump() for s in sums]
    par = {"block_type": "repeat", "id": "r", "repeat_parallel": True, "body": []}
    ser = {"block_type": "repeat", "id": "r", "body": []}
    assert parallel_replay_indices(par, "r", dumped) == [0, 1, 2]
    assert serial_replay_prefix(ser, "r", dumped) == 3, "attempt 1's failure no longer stops the prefix"
    assert _iteration_statuses({"iteration_summaries": dumped}) == ["passed", "passed", "passed"]
    # Reversed order: the attempt number decides, not list position.
    rev = [dumped[0], dumped[2], dumped[1], dumped[3]]
    assert serial_replay_prefix(ser, "r", rev) == 3
