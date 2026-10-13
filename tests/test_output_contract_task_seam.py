"""Output contracts inside a running task (design/typed-findings.md, step 4).

The seam: execute_block -> pinned_for_run (run root) -> execute_task_block ->
(scripted model stream) -> the REAL emit_artifact tool -> the collector's
contract check -> the end-of-task check and its one correction turn -> the
task's artifact.

The scripted stream stands in for the model: each call to
``stream_with_tools`` runs the next scripted turn, which may emit through the
real tool exactly as a model would mid-task.
"""
import asyncio
import json
from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage

from app.agents.block_executor import ExecutionContext, execute_block
from app.models.task_card import Block
from app.utils import output_contracts as oc

DONE = 'done <self_assessment objective_met="true" rationale="ok"/>'
FINDING = {"id": "T-X", "title": "t", "verdict": "candidate"}


async def _emit(**kw):
    from app.mcp.tools.emit_artifact import EmitArtifactTool
    out = await EmitArtifactTool().execute(**kw)
    return out["content"][0]["text"]


class _Scripted:
    """Each stream_with_tools call runs the next turn of ``script``."""
    script = []
    calls = []          # messages passed on each call
    replies = []        # tool results the turns received
    on_call = None      # optional hook(n) run before turn n

    def __init__(self, *a, **k):
        pass

    async def stream_with_tools(self, messages, tools=None, project_root=None, **_):
        n = len(type(self).calls)
        type(self).calls.append(list(messages))
        if type(self).on_call:
            type(self).on_call(n)
        turn = type(self).script[n] if n < len(type(self).script) else None
        if turn is not None:
            for kw in turn:
                type(self).replies.append(await _emit(**kw))
        yield {"type": "text", "content": DONE}
        yield {"type": "stream_end"}


def _run(root, proj, script, storage=None, on_call=None):
    _Scripted.script, _Scripted.calls, _Scripted.replies = script, [], []
    _Scripted.on_call = on_call
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _Scripted), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x", "current_model": "f"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]):
        ctx = ExecutionContext(run_id="run-c", project_root=str(proj), storage=storage)
        return asyncio.run(execute_block(root, ctx))


def _task(contract=None, id_="t1"):
    kw = {"output_contract": contract} if contract is not None else {}
    return Block(block_type="task", id=id_, name="Lane", instructions="find it", **kw)


FIND = [{"name": "finding", "schema": "ziya:finding/v1", "required": True}]


def _outputs(art, name):
    return [o for o in art.outputs if getattr(o, "name", None) == name]


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


# ── the correction turn ───────────────────────────────────────────────────

def test_missing_output_gets_one_correction_turn_then_passes(proj):
    art = _run(_task(FIND), proj, [[], [dict(name="finding", part_type="data", data=FINDING)]])
    assert len(_Scripted.calls) == 2
    last = _Scripted.calls[1][-1]
    assert isinstance(last, HumanMessage) and "`finding`" in last.content
    assert "- verdict: one of" in last.content          # schema summary, generated
    assert not art.failed, art.decisions
    [f] = _outputs(art, "finding")
    assert f.schema_id == "ziya:finding/v1" and f.schema_sha256
    assert any("correction turn requested for finding" in d for d in art.decisions)


def test_still_missing_after_the_correction_fails_bounded(proj):
    art = _run(_task(FIND), proj, [[], [], []])
    assert len(_Scripted.calls) == 2, "exactly one correction turn"
    assert art.failed and art.signature == "output_contract:finding"
    assert any("not produced after one correction turn: finding" in d for d in art.decisions)


def test_an_untyped_task_is_unchanged(proj):
    art = _run(_task(), proj, [[]])
    assert len(_Scripted.calls) == 1 and not art.failed
    assert "REQUIRED OUTPUTS" not in _Scripted.calls[0][0].content


def test_prompt_lists_required_outputs_from_the_schema(proj):
    _run(_task(FIND), proj, [[dict(name="finding", part_type="data", data=FINDING)]])
    system = _Scripted.calls[0][0].content
    assert "REQUIRED OUTPUTS" in system and "`finding` (required; schema ziya:finding/v1)" in system
    assert len(_Scripted.calls) == 1


def test_rejected_emit_is_fixed_in_the_same_turn_without_a_correction(proj):
    bad = {**FINDING, "verdict": "maybe"}
    art = _run(_task(FIND), proj, [[dict(name="finding", part_type="data", data=bad),
                                    dict(name="finding", part_type="data", data=FINDING)]])
    assert "rejected by its output contract" in _Scripted.replies[0]
    assert "/verdict" in _Scripted.replies[0]
    assert len(_Scripted.calls) == 1 and not art.failed
    [f] = _outputs(art, "finding")
    assert f.data["verdict"] == "candidate"


def test_optional_outputs_need_no_correction(proj):
    art = _run(_task([{"name": "notes", "schema": "ziya:text", "required": False}]), proj, [[]])
    assert len(_Scripted.calls) == 1 and not art.failed


# ── pinning ───────────────────────────────────────────────────────────────

def _project_schema(proj, name, schema):
    d = proj / ".ziya" / "schemas"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(schema))


def test_schema_edited_mid_run_does_not_change_the_check(proj):
    _project_schema(proj, "p", {"type": "object", "required": ["a"],
                                "properties": {"a": {"description": "the a"}}})
    def weaken(n):
        if n == 0:                       # after launch, before the first emit
            _project_schema(proj, "p", {"type": "object"})

    art = _run(_task([{"name": "out", "schema": "project:p"}]), proj,
               [[dict(name="out", part_type="data", data={})],
                [dict(name="out", part_type="data", data={"a": 1})]], on_call=weaken)
    assert "/a: required (the a)" in _Scripted.replies[0], "the launch version still applied"
    assert not art.failed and len(_Scripted.calls) == 2


def test_the_launch_snapshot_on_the_run_record_is_what_applies(proj):
    # On disk the schema is permissive; the run record pinned a strict one.
    _project_schema(proj, "p", {"type": "object"})
    strict = {"$id": "project:p", "type": "object", "required": ["a"]}
    snap = oc.SchemaSet.resolve([strict]).to_snapshot()
    snap["aliases"]["project:p"] = next(iter(snap["schemas"]))

    class _Run:
        permissions_snapshot = {"output_schemas": snap}

    class _Storage:
        def get(self, run_id):
            return _Run()

        def __getattr__(self, name):          # every other storage call is a no-op
            return lambda *a, **k: None

    art = _run(_task([{"name": "out", "schema": "project:p"}]), proj,
               [[dict(name="out", part_type="data", data={})], []], storage=_Storage())
    assert "rejected" in _Scripted.replies[0]
    assert art.failed and art.signature == "output_contract:out"


def test_permissions_snapshot_records_the_pinned_schemas(proj):
    from app.utils.permissions_snapshot import build_permissions_snapshot
    root = Block(block_type="group", id="g", body=[_task(FIND)])
    snap = build_permissions_snapshot(root_block=root, project_root=str(proj))
    pinned = snap["output_schemas"]["schemas"]
    assert {"ziya:finding/v1", "ziya:number/v1", "ziya:distribution/v1"} <= set(pinned)
    assert all(len(v["sha256"]) == 64 for v in pinned.values())
    assert "output_schemas" not in build_permissions_snapshot(
        root_block=_task(), project_root=str(proj))


def test_an_unresolvable_schema_stops_the_run_at_its_root(proj):
    with pytest.raises(oc.ContractError, match="project:missing"):
        _run(_task([{"name": "out", "schema": "project:missing"}]), proj, [[]])
    assert _Scripted.calls == [], "no model call was made"


# ── State-block defaults (step 5) ─────────────────────────────────────────

def _state_defaults(id_="s", defaults=FIND):
    return Block(block_type="state", id=id_, name="Givens", output_contract_defaults=defaults)


def _has_section(call):
    return "REQUIRED OUTPUTS" in call[0].content


EMIT = [dict(name="finding", part_type="data", data=FINDING)]


def test_state_defaults_reach_later_tasks_in_scope_only(proj):
    # before: a task ahead of the State; inside: after it; outside: after the
    # Group that holds the State.  Only "inside" is in the State's scope.
    root = Block(block_type="group", id="g", body=[
        _task(id_="before"),
        Block(block_type="group", id="g2", body=[_state_defaults(), _task(id_="inside")]),
        _task(id_="outside"),
    ])
    art = _run(root, proj, [[], EMIT, []])
    assert len(_Scripted.calls) == 3, "no correction turn: inside emitted, the others owe nothing"
    assert [_has_section(c) for c in _Scripted.calls] == [False, True, False]
    assert not art.failed, art.decisions


def test_a_task_in_scope_that_omits_the_default_gets_the_correction_turn(proj):
    root = Block(block_type="group", id="g", body=[_state_defaults(), _task()])
    art = _run(root, proj, [[], []])
    assert len(_Scripted.calls) == 2
    assert art.failed and art.signature == "output_contract:finding"


def test_an_explicit_empty_contract_opts_out(proj):
    root = Block(block_type="group", id="g", body=[_state_defaults(), _task(contract=[])])
    art = _run(root, proj, [[]])
    assert len(_Scripted.calls) == 1 and not _has_section(_Scripted.calls[0])
    assert not art.failed


def test_defaults_merge_with_the_tasks_own_contract_by_name(proj):
    notes = [{"name": "notes", "schema": "ziya:text", "required": False}]
    root = Block(block_type="group", id="g", body=[_state_defaults(), _task(contract=notes)])
    _run(root, proj, [EMIT])
    system = _Scripted.calls[0][0].content
    assert "`finding` (required" in system and "`notes` (optional" in system


def test_a_later_state_overrides_a_default_of_the_same_name(proj):
    opt = [{"name": "finding", "schema": "ziya:finding/v1", "required": False}]
    root = Block(block_type="group", id="g", body=[
        _state_defaults("s1"), _state_defaults("s2", opt), _task()])
    art = _run(root, proj, [[]])
    assert len(_Scripted.calls) == 1 and not art.failed
    assert "`finding` (optional" in _Scripted.calls[0][0].content


def test_defaults_inside_a_parallel_loop_body_apply_per_iteration(proj):
    rep = Block(block_type="repeat", id="r", repeat_mode="count", repeat_count=2,
                repeat_parallel=True, body=[_state_defaults(), _task()])
    art = _run(Block(block_type="group", id="g", body=[rep, _task(id_="after")]),
               proj, [EMIT, EMIT, []])
    assert len(_Scripted.calls) == 3
    assert sorted(_has_section(c) for c in _Scripted.calls) == [False, True, True]
    assert not _has_section(_Scripted.calls[-1]), "the loop body's State does not leak past it"
    assert not art.failed, art.decisions


# ── evidence namespace -> require_claims ──────────────────────────────────

def test_an_evidence_namespace_makes_numbers_claims(proj):
    lit = {**FINDING, "impact": {"fleet_total": {"mid": {"value": 280}}}}
    root = Block(block_type="group", id="g", body=[
        Block(block_type="state", id="s", state_variables={"evidence_namespace": "ns"}),
        _task(FIND)])
    _run(root, proj, [[dict(name="finding", part_type="data", data=lit)], []])
    assert "/impact/fleet_total/mid: a literal number is not allowed" in _Scripted.replies[0]
    assert "ledger_record" in _Scripted.replies[0]
