"""A satisfied output contract outranks a ``partial`` self-assessment.

The self-assessment exists to catch a task that abandoned its goal mid-run.
A required output contract tests that directly: a task that abandoned
mid-run does not have every required output present and validating.  When
it does, the self-report is the weaker evidence, so the task is recorded ok
with the verdict kept as a warning.  Everything else is unchanged:

  * no contract            -> ``partial`` still fails (the verdict is the
                              only evidence);
  * optional-only contract -> ``partial`` still fails (nothing was tested);
  * ``false``              -> still fails even with outputs present;
  * contract NOT satisfied -> fails on the contract, as before;
  * truncated/empty stream -> still fails even with outputs present.

Drives execute_block with a scripted stream and the REAL emit_artifact tool
(pattern from test_output_contract_task_seam.py), asserting on the artifact
and the task_finished event — the two surfaces an operator reads.
"""

import asyncio
from unittest.mock import patch

import pytest

from app.agents.block_executor import ExecutionContext, execute_block
from app.models.task_card import Block

FINDING = {"id": "T-X", "title": "t", "verdict": "candidate"}
FIND = [{"name": "finding", "schema": "ziya:finding/v1", "required": True}]
OPTIONAL = [{"name": "finding", "schema": "ziya:finding/v1", "required": False}]
PARTIAL = ('did most of it <self_assessment objective_met="partial" '
           'rationale="lane 1B skipped"/>')
FALSE = 'gave up <self_assessment objective_met="false" rationale="no"/>'


async def _emit(**kw):
    from app.mcp.tools.emit_artifact import EmitArtifactTool
    out = await EmitArtifactTool().execute(**kw)
    return out["content"][0]["text"]


class _Scripted:
    script = []
    closing = PARTIAL
    calls = []

    def __init__(self, *a, **k):
        pass

    async def stream_with_tools(self, messages, tools=None, project_root=None, **_):
        n = len(type(self).calls)
        type(self).calls.append(list(messages))
        turn = type(self).script[n] if n < len(type(self).script) else None
        for kw in (turn or []):
            await _emit(**kw)
        if type(self).closing is not None:
            yield {"type": "text", "content": type(self).closing}
        yield {"type": "stream_end"}


def _run(root, proj, script, closing=PARTIAL):
    _Scripted.script, _Scripted.calls, _Scripted.closing = script, [], closing
    events = []

    async def _push(run_id, event):
        events.append(event)

    with patch("app.streaming_tool_executor.StreamingToolExecutor", _Scripted), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x", "current_model": "f"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]), \
         patch("app.agents.task_run_stream_relay.safe_push", _push):
        ctx = ExecutionContext(run_id="run-p", project_root=str(proj), storage=None)
        art = asyncio.run(execute_block(root, ctx))
    fin = [e for e in events if e.get("type") == "task_finished"]
    assert len(fin) == 1, fin
    return art, fin[0]


def _task(contract=None):
    kw = {"output_contract": contract} if contract is not None else {}
    return Block(block_type="task", id="t1", name="Lane", instructions="find it", **kw)


EMITTED = [[dict(name="finding", part_type="data", data=FINDING)]]


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def test_partial_with_required_outputs_present_is_ok_with_warning(proj):
    art, fin = _run(_task(FIND), proj, EMITTED)
    assert not art.failed, art.decisions
    assert art.signature is None
    assert fin["ok"] is True and fin["failure_reason"] is None
    # The verdict is not discarded: it stays on the artifact and the event
    # and is named in decisions as the warning it has become.
    assert art.self_assessment["objective_met"] == "partial"
    assert fin["self_assessment"]["objective_met"] == "partial"
    [d] = [d for d in art.decisions if "objective_met=partial" in d]
    assert "every required contracted output is present" in d
    assert "lane 1B skipped" in d


def test_partial_without_a_contract_still_fails(proj):
    """Negative control for the override — the pre-(d) rule, unchanged."""
    art, fin = _run(_task(), proj, [[]])
    assert art.failed and art.signature == "self_assessment_partial"
    assert fin["ok"] is False
    assert fin["failure_reason"].startswith("self_assessment objective_met=partial")


def test_partial_with_only_optional_contract_entries_still_fails(proj):
    """An all-optional contract tests nothing, so it cannot vouch for the task."""
    art, _ = _run(_task(OPTIONAL), proj, EMITTED)
    assert art.failed and art.signature == "self_assessment_partial"


def test_false_is_not_overridden_by_a_satisfied_contract(proj):
    art, fin = _run(_task(FIND), proj, EMITTED, closing=FALSE)
    assert art.failed and art.signature == "self_assessment_false"
    assert fin["ok"] is False


def test_partial_with_contract_unmet_fails_on_the_contract(proj):
    art, fin = _run(_task(FIND), proj, [[], []])
    assert art.failed and art.signature == "output_contract:finding"
    assert fin["failure_signature"] == "output_contract:finding"


def test_truncated_stream_fails_even_with_outputs_present(proj):
    """Outputs emitted, then the stream ended at the tool boundary with no
    closing prose and no verdict: the executor-imposed failure stands."""
    class _Truncating(_Scripted):
        async def stream_with_tools(self, messages, tools=None, project_root=None, **_):
            type(self).calls.append(list(messages))
            if len(type(self).calls) == 1:
                await _emit(name="finding", part_type="data", data=FINDING)
                yield {"type": "text", "content": "emitting now"}
                yield {"type": "tool_display", "tool_name": "emit_artifact",
                       "tool_id": "x", "result": "ok"}
            yield {"type": "stream_end"}

    _Truncating.calls = []
    with patch("app.streaming_tool_executor.StreamingToolExecutor", _Truncating), \
         patch("app.agents.models.ModelManager.get_state",
               return_value={"aws_region": "us-east-1", "aws_profile": "x", "current_model": "f"}), \
         patch("app.mcp.enhanced_tools.create_secure_mcp_tools", return_value=[]):
        ctx = ExecutionContext(run_id="run-t", project_root=str(proj), storage=None)
        art = asyncio.run(execute_block(_task(FIND), ctx))
    assert art.failed and art.signature == "truncated_summary"
