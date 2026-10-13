"""Container (gather) contracts in a running card (design/typed-findings.md,
steps 6b-6d).

The seam: execute_block -> the container's push-down -> execute_task_block
(scripted model) -> the REAL emit_artifact tool -> the container-end check.

The scripted model is keyed by what the task was asked and where it is
(its iteration path), not by call order, because Parallel children run
concurrently and correction turns add calls.
"""
import asyncio
from unittest.mock import patch

import pytest

from app.agents.block_executor import ExecutionContext, execute_block
from app.models.task_card import Block

DONE = 'done <self_assessment objective_met="true" rationale="ok"/>'
FINDING = {"id": "T-X", "title": "t", "verdict": "candidate"}
GATHER = {"name": "finding", "schema": "ziya:finding/v1", "per": "child"}


async def _emit(**kw):
    from app.mcp.tools.emit_artifact import EmitArtifactTool
    out = await EmitArtifactTool().execute(**kw)
    return out["content"][0]["text"]


class _Fake:
    behave = staticmethod(lambda instr, path, nth: [])
    calls = []      # (instructions, iteration path, messages)
    replies = []
    events = []

    def __init__(self, *a, **k):
        pass

    async def stream_with_tools(self, messages, **_):
        from app.context import get_task_iteration_context
        instr = messages[1].content
        nth = sum(1 for c in type(self).calls if c[0] == instr)
        path = (get_task_iteration_context() or {}).get("path")
        type(self).calls.append((instr, path, list(messages)))
        for kw in type(self).behave(instr, path, nth):
            type(self).replies.append(await _emit(**kw))
        yield {"type": "text", "content": DONE}
        yield {"type": "stream_end"}


def _run(root, proj, behave):
    _Fake.behave, _Fake.calls, _Fake.replies, _Fake.events = behave, [], [], []

    async def _capture(ctx, evt):
        _Fake.events.append(evt)

    with patch("app.streaming_tool_executor.StreamingToolExecutor", _Fake), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x", "current_model": "f"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]), \
         patch("app.agents.block_executor._emit", _capture):
        return asyncio.run(execute_block(root, ExecutionContext(run_id="run-g", project_root=str(proj))))


def _t(instr, id_):
    return Block(block_type="task", id=id_, name=id_, instructions=instr)


EMIT = [dict(name="finding", part_type="data", data=FINDING)]


def _calls_for(word):
    return [c for c in _Fake.calls if word in c[0]]


def _shortfalls():
    return [e for e in _Fake.events if e.get("type") == "contract_shortfall"]


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def _lanes(**g):
    return Block(block_type="parallel", id="lanes", name="Lanes",
                 gather_contract=[{**GATHER, **g}],
                 body=[_t("lane one", "c1"), _t("lane two", "c2"), _t("lane three", "c3")])


# ── Parallel, per child ───────────────────────────────────────────────────

def test_a_child_that_never_emits_gets_a_correction_turn_then_fails_the_parallel(proj):
    art = _run(_lanes(), proj, lambda instr, path, nth: [] if "two" in instr else EMIT)
    assert len(_calls_for("two")) == 2, "the pushed-down required contract gave a correction turn"
    assert len(_calls_for("one")) == 1 and len(_calls_for("three")) == 1
    assert art.failed and art.signature == "gather_contract:finding"
    assert any("c2 (0)" in d for d in art.decisions) and not any("c1 (" in d for d in art.decisions)
    assert len([o for o in art.outputs if getattr(o, "name", None) == "finding"]) == 2
    [ev] = _shortfalls()
    assert ev["units"] == [{"unit": "c2", "count": 0}] and ev["rerun_attempted"] is False


def test_all_children_emit_and_the_parallel_passes(proj):
    art = _run(_lanes(), proj, lambda instr, path, nth: EMIT)
    assert not art.failed, art.decisions
    assert len(_Fake.calls) == 3 and not _shortfalls()
    assert all("REQUIRED OUTPUTS" in c[2][0].content for c in _Fake.calls)


def test_max_per_unit_is_enforced(proj):
    two = EMIT * 2
    art = _run(_lanes(max=1), proj, lambda instr, path, nth: two if "one" in instr else EMIT)
    assert any("allows 1" in r for r in _Fake.replies), "the second emit was refused at emit"
    assert not art.failed, art.decisions


# ── producers ─────────────────────────────────────────────────────────────

def test_an_explicit_producer_is_required_and_other_tasks_are_optional(proj):
    child = Block(block_type="group", id="lane", name="Lane",
                  body=[_t("pull data", "a"), _t("write finding", "b")])
    par = Block(block_type="parallel", id="lanes", name="Lanes",
                gather_contract=[{**GATHER, "producer": ["b"]}], body=[child])
    bad = dict(name="finding", part_type="data", data={**FINDING, "verdict": "maybe"})

    def behave(instr, path, nth):
        return [bad] if "pull" in instr else (EMIT if nth == 1 else [])
    art = _run(par, proj, behave)
    assert any("rejected by its output contract" in r for r in _Fake.replies), \
        "a non-producer's part is still validated (optional contract)"
    assert len(_calls_for("pull")) == 1, "a non-producer owes nothing"
    assert len(_calls_for("write")) == 2, "the producer got the correction turn"
    assert not art.failed, art.decisions


# ── Repeat, per iteration ─────────────────────────────────────────────────

def test_nested_loops_credit_parts_to_the_outer_iteration(proj):
    inner = Block(block_type="repeat", id="inner", name="Inner", repeat_mode="count",
                  repeat_count=2, body=[_t("work", "w")])
    outer = Block(block_type="repeat", id="outer", name="Outer", repeat_mode="count",
                  repeat_count=2, body=[inner],
                  gather_contract=[{**GATHER, "per": "iteration", "producer": ["w"]}])
    # Emit only in outer iteration 0 (any inner iteration, first attempt).
    art = _run(outer, proj,
               lambda instr, path, nth: EMIT if path and path[0] == ["outer", 0] and nth == 0 else [])
    assert all(c[1][0][0] == "outer" for c in _Fake.calls), "the outer pair heads every path"
    assert art.failed and art.signature == "gather_contract:finding"
    [ev] = _shortfalls()
    assert [u["count"] for u in ev["units"]] == [0] and ev["total_units"] == 2


def test_for_each_shortfall_names_the_roster_member(proj):
    loop = Block(block_type="repeat", id="each", name="Each", repeat_mode="for_each",
                 repeat_for_each_source='["a", "b", "c"]',
                 body=[_t("audit {{item}}", "w")],
                 gather_contract=[{**GATHER, "per": "iteration"}])
    art = _run(loop, proj, lambda instr, path, nth: [] if "audit b" in instr else EMIT)
    assert art.failed
    [ev] = _shortfalls()
    assert [u["unit"] for u in ev["units"]] == ["b"]


def test_per_total_counts_the_whole_container(proj):
    par = Block(block_type="parallel", id="lanes", name="Lanes",
                gather_contract=[{**GATHER, "per": "total", "min": 1, "producer": []}],
                body=[_t("lane one", "c1"), _t("lane two", "c2")])
    art = _run(par, proj, lambda instr, path, nth: EMIT if "one" in instr else [])
    assert not art.failed, art.decisions
    art = _run(par, proj, lambda instr, path, nth: [])
    assert art.failed and _shortfalls()[0]["units"] == [{"unit": "total", "count": 0}]


# ── State gather defaults ─────────────────────────────────────────────────

def test_state_gather_defaults_apply_to_matching_containers(proj):
    state = Block(block_type="state", id="s", name="Givens",
                  gather_contract_defaults=[{**GATHER, "container": "parallel"}])
    lanes = Block(block_type="parallel", id="lanes", name="Lanes",
                  body=[_t("lane one", "c1"), _t("lane two", "c2")])
    art = _run(Block(block_type="group", id="g", name="G", body=[state, lanes]), proj,
               lambda instr, path, nth: [] if "two" in instr else EMIT)
    assert art.failed and any("gather_contract" in d for d in art.decisions)


def test_without_a_gather_contract_nothing_changes(proj):
    par = Block(block_type="parallel", id="lanes", name="Lanes",
                body=[_t("lane one", "c1"), _t("lane two", "c2")])
    art = _run(par, proj, lambda instr, path, nth: [])
    assert not art.failed and len(_Fake.calls) == 2 and not _shortfalls()
