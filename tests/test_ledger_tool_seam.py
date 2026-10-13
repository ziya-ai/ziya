"""Ledger tools through the real tool-execution path.

execute_single_tool -> DirectMCPTool -> Ledger*Tool -> service -> the store.
Asserts on what the model receives and on what landed in .ziya/ledger/.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.mcp.enhanced_tools import DirectMCPTool
from app.tool_execution import ToolExecContext, execute_single_tool
from app.utils.evidence import store as st


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    st._key_cache.clear()
    st._ledgers.clear()
    root = tmp_path / "proj"
    (root / "evidence").mkdir(parents=True)
    (root / "evidence" / "s1.csv").write_text("errors,requests\n22464,120000\n")
    return root


def _ctx(root, tool_cls, args):
    manager = MagicMock()
    manager._exceeds_turn_ceiling = lambda *_: False
    manager._is_repetitive_call = lambda *_: False
    executor = SimpleNamespace(
        _format_tool_result=lambda name, text, a: text,
        _infer_syntax_hint=lambda name, a: "json",
        _get_tool_header=lambda name, a: name,
    )
    tool = tool_cls()
    return ToolExecContext(
        tool_id="tc-ledger", tool_name=tool.name, actual_tool_name=tool.name,
        args=dict(args), all_tools=[DirectMCPTool(tool)],
        internal_tool_names=set(), mcp_manager=manager, project_root=str(root),
        conversation_id="conv-ledger", conversation=[], recent_commands=[],
        inter_tool_delay={'current': 0.0, 'min': 0.0, 'max': 1, 'decay_factor': 0.6,
                          'growth_factor': 2.5, 'last_was_throttled': False},
        iteration_start_time=0, track_yield_fn=lambda x: x,
        drain_feedback_fn=lambda: [], executor=executor,
    )


async def _model_text(root, tool_cls, args) -> str:
    events = []

    async def pull():
        async for ev in execute_single_tool(_ctx(root, tool_cls, args)):
            events.append(ev)
    await asyncio.wait_for(pull(), 60)
    model = [e for e in events if e.get("type") == "tool_result_for_model"]
    assert model, f"no tool_result_for_model event: {[e.get('type') for e in events]}"
    return json.dumps(model[-1])


@pytest.mark.asyncio
async def test_record_query_verify_through_the_tools(proj):
    from app.mcp.tools.ledger_tools import LedgerQueryTool, LedgerRecordTool, LedgerVerifyTool
    text = await _model_text(proj, LedgerRecordTool, {
        "observation": {"source": "sql", "query_text": "SELECT 1", "result_path": "evidence/s1.csv"},
        "claim": {"key": "audit.s1.errors", "value": 22464, "unit": "requests"},
    })
    assert "audit.s1.errors" in text and "recorded" in text
    led = st.ledger_for(str(proj))
    c = led.claim("audit.s1.errors")
    assert c["value"] == 22464
    # Provenance comes from the server context, not from the model's arguments.
    assert c["provenance"].get("via") == "tool"

    text = await _model_text(proj, LedgerVerifyTool, {"prefix": "audit", "method": "rehash"})
    assert "agrees" in text
    text = await _model_text(proj, LedgerQueryTool, {"ref": "audit.s1.errors"})
    assert "verified" in text


@pytest.mark.asyncio
async def test_path_outside_project_is_refused_through_the_tool(proj):
    from app.mcp.tools.ledger_tools import LedgerRecordTool
    text = await _model_text(proj, LedgerRecordTool, {
        "observation": {"source": "file", "result_path": "/etc/passwd"}})
    assert "outside the allowed directories" in text
    assert st.ledger_for(str(proj)).read("observation") == []


def test_tools_registered_in_evidence_category():
    from app.mcp.builtin_tools import BUILTIN_TOOL_CATEGORIES, get_builtin_tools_for_category
    assert BUILTIN_TOOL_CATEGORIES["evidence"]["enabled_by_default"] is True
    assert sorted(t().name for t in get_builtin_tools_for_category("evidence")) == [
        "ledger_query", "ledger_record", "ledger_verify"]
