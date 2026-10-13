"""Routing keys injected for dispatch never reach the model's own tool_use.

``execute_single_tool`` adds ``_workspace_path`` / ``conversation_id`` /
``_task_scope`` to the argument dict before dispatch.  Until this fix it did
so IN PLACE on the very dict the streaming executor had stored in
``all_tool_calls[i]['args']`` and later serialised as the assistant
message's ``tool_use.input`` -- so from the second turn on, the model saw
parameters it never wrote, and imitated them.  In the release sweep's
triage tasks (haiku) this degraded into ``conversion_id``, ``project_root``,
``workspace_path_alt2``, ``task_scope_3`` on every shell call, each an
"Unknown parameter" warning in app/mcp/client.py and, for schema-validated
tools, a rejected call.

The test holds the model's dict by reference -- the way the executor does --
and asserts on it after the call, not on ``ctx.args``.  Both dispatch
branches (builtin, MCP-routed) are covered; the MCP branch asserts the
routing keys DID reach the manager, so the test also fails if a future
change drops the injection altogether rather than detaching it.
"""

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ZIYA_TESTING", "1")

from app.mcp.enhanced_tools import DirectMCPTool  # noqa: E402
from app.mcp.tools.base import BaseMCPTool  # noqa: E402
from app.tool_execution import ToolExecContext, execute_single_tool  # noqa: E402
from app.utils import consent_gate as cg  # noqa: E402

CONV = "conv-leak-1"
MODEL_ARGS = {"command": "echo hi"}


class _PlainTool(BaseMCPTool):
    name = "leak_seam_tool"
    description = "fake builtin for the args-leak seam"
    consent_default = "off"

    async def execute(self, **kw):
        return {"ok": True}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    cg.reset_for_tests(tmp_path / "consents")
    monkeypatch.setattr(cg, "_consent_config", lambda: {})
    yield
    cg.reset_for_tests()


def _patches():
    return [
        patch("app.mcp.signing.verify_tool_result", return_value=(True, None)),
        patch("app.mcp.signing.strip_signature_metadata", side_effect=lambda r: r),
        patch("app.mcp.signing.sign_tool_result", side_effect=lambda *a, **k: a[2]),
        patch("app.server.record_verification_result"),
        patch("app.utils.tool_audit_log.log_tool_execution"),
        patch("app.utils.tool_result_sanitizer.sanitize_for_context", side_effect=lambda t, **k: t),
    ]


def _ctx(model_args, *, tools, actual_name, manager):
    ex = MagicMock()
    ex._get_tool_header.return_value = "x"
    ex._infer_syntax_hint.return_value = ""
    ex._format_tool_result.side_effect = lambda *a, **k: str(a)
    return ToolExecContext(
        tool_id="tc-leak", tool_name=actual_name, actual_tool_name=actual_name,
        # The SAME object the executor keeps in all_tool_calls -- no copy.
        args=model_args, all_tools=tools, internal_tool_names=set(),
        mcp_manager=manager, project_root="/tmp/proj", conversation_id=CONV,
        conversation=[], recent_commands=[],
        inter_tool_delay={"current": 0.0, "min": 0.0, "decay_factor": 0.9},
        iteration_start_time=0.0, track_yield_fn=lambda x: x,
        drain_feedback_fn=lambda: [], executor=ex,
    )


async def _run(ctx):
    ps = _patches()
    for p in ps:
        p.start()
    try:
        return [ev async for ev in execute_single_tool(ctx)]
    finally:
        for p in reversed(ps):
            p.stop()


def _manager():
    m = MagicMock()
    m._exceeds_turn_ceiling = lambda *_: False
    m._is_repetitive_call = lambda *_: False
    return m


@pytest.mark.asyncio
async def test_builtin_dispatch_leaves_the_models_dict_untouched():
    model_args = dict(MODEL_ARGS)
    tool = _PlainTool()
    ctx = _ctx(model_args, tools=[DirectMCPTool(tool)], actual_name=tool.name,
               manager=_manager())
    events = await _run(ctx)
    assert any(e.get("type") == "_tool_result" for e in events), events
    assert model_args == MODEL_ARGS, (
        f"routing keys leaked into the model's tool_use input: {model_args}")


@pytest.mark.asyncio
async def test_mcp_dispatch_injects_routing_keys_but_not_into_the_models_dict():
    model_args = dict(MODEL_ARGS)
    manager = _manager()
    received = {}

    async def _call_tool(name, arguments, server_name=None):
        received.update(arguments)
        return {"content": [{"type": "text", "text": "ran"}]}
    manager.call_tool = _call_tool

    ctx = _ctx(model_args, tools=[], actual_name="run_shell_command", manager=manager)
    with patch("app.mcp.manager.build_task_scope_envelope", return_value={"g": 1}, create=True):
        events = await _run(ctx)
    assert any(e.get("type") == "_tool_result" for e in events), events
    # Dispatch still carries the routing keys ...
    assert received.get("_workspace_path") == "/tmp/proj"
    assert received.get("conversation_id") == CONV
    # ... and the model's record of its own call does not.
    assert model_args == MODEL_ARGS, (
        f"routing keys leaked into the model's tool_use input: {model_args}")
