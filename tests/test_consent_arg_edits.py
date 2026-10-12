"""The approval panel may rewrite an argument before approving (git commit
message), and the tool runs with the human's text.

Hops this change crosses, each asserted:

  tool declares consent_editable_args
    -> gate advertises payload["editable"] on consent_opened
    -> POST /api/consent carries edits (validated against "editable")
    -> ledger stores them on the approval
    -> gate's decision carries arg_edits (re-filtered)
    -> execute_single_tool applies them to the args the tool runs with
    -> the result tells the model the human edited them

Driven through the REAL execute_single_tool and the REAL route handler.
"""
import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

os.environ.setdefault("ZIYA_TESTING", "1")

from app.api import consent as consent_api  # noqa: E402
from app.mcp.enhanced_tools import DirectMCPTool  # noqa: E402
from app.mcp.tools.base import BaseMCPTool  # noqa: E402
from app.tool_execution import ToolExecContext, execute_single_tool  # noqa: E402
from app.utils import consent_gate as cg  # noqa: E402

CONV = "conv-edit-1"


class _CommitTool(BaseMCPTool):
    name = "edit_seam_git"
    description = "fake git for the edit seam"
    consent_default = "ask"
    consent_op_key = "op"
    consent_editable_args = {"commit": ("message",)}

    def __init__(self):
        self.ran_with = None

    async def preflight(self, **kw):
        return {"op": kw.get("op"), "message": (kw.get("message") or "") + "\n", "files": ["a.py"]}

    async def execute(self, **kw):
        self.ran_with = dict(kw)
        return {"op": "commit", "message": kw.get("message"), "commit": "abc123"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    cg.reset_for_tests(tmp_path / "consents")
    monkeypatch.setattr(cg, "_consent_config", lambda: {})
    yield
    cg.reset_for_tests()


def _ctx(tool, args):
    manager = MagicMock()
    manager._exceeds_turn_ceiling = lambda *_: False
    manager._is_repetitive_call = lambda *_: False
    ex = MagicMock()
    ex._get_tool_header.return_value = "git"
    ex._infer_syntax_hint.return_value = ""
    ex._format_tool_result.side_effect = lambda *a, **k: str(a)
    return ToolExecContext(
        tool_id="tc-edit", tool_name=tool.name, actual_tool_name=tool.name,
        args=dict(args), all_tools=[DirectMCPTool(tool)], internal_tool_names=set(),
        mcp_manager=manager, project_root="/tmp", conversation_id=CONV,
        conversation=[], recent_commands=[],
        inter_tool_delay={"current": 0.0, "min": 0.0, "decay_factor": 0.9},
        iteration_start_time=0.0, track_yield_fn=lambda x: x,
        drain_feedback_fn=lambda: [], executor=ex,
    )


def _patches():
    return [
        patch("app.mcp.signing.verify_tool_result", return_value=(True, None)),
        patch("app.mcp.signing.strip_signature_metadata", side_effect=lambda r: r),
        patch("app.mcp.signing.sign_tool_result", side_effect=lambda *a, **k: a[2]),
        patch("app.server.record_verification_result"),
        patch("app.utils.tool_audit_log.log_tool_execution"),
        patch("app.utils.tool_result_sanitizer.sanitize_for_context", side_effect=lambda t, **k: t),
    ]


async def _drive(tool, args, answer_fn):
    """Run the call until it parks, answer it, then run it to the end."""
    ps = _patches()
    for p in ps:
        p.start()
    try:
        gen = execute_single_tool(_ctx(tool, args))
        events = []

        async def until_parked():
            async for ev in gen:
                events.append(ev)
                if ev.get("type") == "consent_opened":
                    return
        await asyncio.wait_for(until_parked(), 3)
        opened = [e for e in events if e.get("type") == "consent_opened"]
        assert opened, f"never parked: {[e.get('type') for e in events]}"
        answer_fn(opened[-1])

        async def rest():
            async for ev in gen:
                events.append(ev)
        await asyncio.wait_for(rest(), 3)
        return events
    finally:
        for p in reversed(ps):
            p.stop()


@pytest.mark.asyncio
async def test_approved_edit_replaces_the_argument_the_tool_runs_with():
    tool = _CommitTool()
    seen = {}

    def answer(opened):
        seen["editable"] = opened["payload"].get("editable")
        consent_api.answer_consent(opened["request_id"], consent_api.ConsentAnswer(
            decision="approve", edits={"message": "fix: what the human wrote"}))

    events = await _drive(tool, {"op": "commit", "message": "model wrote this"}, answer)
    assert seen["editable"] == ["message"]
    assert tool.ran_with is not None, "approved call never ran"
    assert tool.ran_with["message"] == "fix: what the human wrote"
    answered = [e for e in events if e.get("type") == "consent_answered"][-1]
    assert answered["edited_args"] == ["message"]
    result = str([e for e in events if e.get("type") == "_tool_result"][-1]["result"])
    assert "edited_by_user" in result


@pytest.mark.asyncio
async def test_approve_without_edits_runs_the_model_text():
    tool = _CommitTool()
    await _drive(tool, {"op": "commit", "message": "model wrote this"},
                 lambda o: consent_api.answer_consent(
                     o["request_id"], consent_api.ConsentAnswer(decision="approve")))
    assert tool.ran_with["message"] == "model wrote this"


@pytest.mark.asyncio
async def test_api_refuses_an_edit_to_an_argument_the_tool_did_not_declare():
    tool = _CommitTool()
    refused = {}

    def answer(opened):
        rid = opened["request_id"]
        with pytest.raises(HTTPException) as e:
            consent_api.answer_consent(rid, consent_api.ConsentAnswer(
                decision="approve", edits={"paths": "everything"}))
        refused["status"] = e.value.status_code
        with pytest.raises(HTTPException) as e2:
            consent_api.answer_consent(rid, consent_api.ConsentAnswer(
                decision="approve", edits={"message": "x" * (cg.EDIT_MAX_CHARS + 1)}))
        refused["oversize"] = e2.value.status_code
        # Still open after the refusals; a valid answer lands.
        consent_api.answer_consent(rid, consent_api.ConsentAnswer(decision="approve"))

    await _drive(tool, {"op": "commit", "message": "m"}, answer)
    assert refused == {"status": 422, "oversize": 422}
    assert tool.ran_with["message"] == "m"


@pytest.mark.asyncio
async def test_gate_drops_undeclared_edits_that_reach_the_ledger_directly():
    """Second line: an answer written to the ledger without the API check."""
    tool = _CommitTool()

    def answer(opened):
        cg.ledger().record_answer(opened["request_id"], "approve",
                                  edits={"op": "push", "message": "ok"})

    await _drive(tool, {"op": "commit", "message": "m"}, answer)
    assert tool.ran_with["op"] == "commit"
    assert tool.ran_with["message"] == "ok"


@pytest.mark.asyncio
async def test_rejected_call_with_edits_does_not_run_and_stores_no_edits():
    tool = _CommitTool()
    rid = {}

    def answer(opened):
        rid["id"] = opened["request_id"]
        consent_api.answer_consent(opened["request_id"], consent_api.ConsentAnswer(
            decision="reject", edits={"message": "never"}))

    events = await _drive(tool, {"op": "commit", "message": "m"}, answer)
    assert tool.ran_with is None
    assert any(e.get("type") == "_tool_result" for e in events)
    assert "edits" not in (cg.ledger().get(rid["id"]).answer or {})


def test_git_tool_declares_the_message_editable_only_where_it_is_a_message():
    from app.mcp.tools.git_tool import GitTool
    inst = GitTool()
    for op in ("commit", "stash", "stash_push"):
        assert cg.editable_args(inst, op) == ["message"], op
    for op in ("push", "add", "checkout", "clone", "*"):
        assert cg.editable_args(inst, op) == [], op
