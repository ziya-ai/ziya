"""``analyze`` through the real tool-execution path (design/local-analytics.md, Seam).

execute_single_tool -> DirectMCPTool -> AnalyzeTool -> run_analyze -> the
locked engine -> the dataset store.  Asserts on what the model receives,
not on an intermediate.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("sqlglot")

from app.mcp.enhanced_tools import DirectMCPTool  # noqa: E402
from app.tool_execution import ToolExecContext, execute_single_tool  # noqa: E402
from app.utils import dataset_store as ds  # noqa: E402


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "outages.csv").write_text("host_id,s,e\na,0,10\na,5,15\nb,0,4\n")
    return root


def _ctx(root, args):
    from app.mcp.tools.dataset_tools import AnalyzeTool
    manager = MagicMock()
    manager._exceeds_turn_ceiling = lambda *_: False
    manager._is_repetitive_call = lambda *_: False
    executor = SimpleNamespace(
        _format_tool_result=lambda name, text, a: text,
        _infer_syntax_hint=lambda name, a: "json",
        _get_tool_header=lambda name, a: "analyze",
    )
    return ToolExecContext(
        tool_id="tc-an", tool_name="analyze", actual_tool_name="analyze",
        args=dict(args), all_tools=[DirectMCPTool(AnalyzeTool())],
        internal_tool_names=set(), mcp_manager=manager, project_root=str(root),
        conversation_id="conv-analyze", conversation=[], recent_commands=[],
        inter_tool_delay={'current': 0.0, 'min': 0.0, 'max': 1, 'decay_factor': 0.6,
                          'growth_factor': 2.5, 'last_was_throttled': False},
        iteration_start_time=0, track_yield_fn=lambda x: x,
        drain_feedback_fn=lambda: [], executor=executor,
    )


async def _model_text(root, args) -> str:
    events = []

    async def pull():
        async for ev in execute_single_tool(_ctx(root, args)):
            events.append(ev)
    await asyncio.wait_for(pull(), 60)
    model = [e for e in events if e.get("type") == "tool_result_for_model"]
    assert model, f"no tool_result_for_model event: {[e.get('type') for e in events]}"
    return json.dumps(model[-1])


@pytest.mark.asyncio
async def test_model_receives_result_dataset_with_lineage(proj):
    text = await _model_text(proj, {
        "sql": "SELECT entity, total_s FROM interval_duration(o, 'host_id', 's', 'e') ORDER BY 1",
        "inputs": {"o": "outages.csv"},
    })
    # Overlap-merged duration (15, not 20) reached the model.
    assert "total_s" in text and "15.0" in text and "20.0" not in text
    stored = [m for m in ds.list_datasets(conversation_id="conv-analyze")
              if (m.get("source") or {}).get("kind") == "analyze"]
    assert len(stored) == 1
    meta = ds.load_meta(stored[0]["id"])
    (inp,) = meta["lineage"]["inputs"]
    assert inp["alias"] == "o" and inp["sha256"] == ds.load_meta(inp["id"])["sha256"]
    assert stored[0]["id"] in text


@pytest.mark.asyncio
async def test_path_outside_project_is_refused_through_the_tool(proj):
    text = await _model_text(proj, {"sql": "SELECT 1", "inputs": {"x": "/etc/passwd"}})
    assert "outside the allowed directories" in text
    assert ds.list_datasets(conversation_id="conv-analyze") == []


def test_tool_is_registered_in_datasets_category():
    from app.mcp.builtin_tools import get_builtin_tools_for_category
    assert "analyze" in {t().name for t in get_builtin_tools_for_category("datasets")}


def test_description_lists_every_macro():
    from app.mcp.tools.dataset_tools import AnalyzeTool
    from app.utils.analytics.primitives import TABLE_ARGS
    desc = AnalyzeTool().description
    assert all(f"{m}(" in desc for m in TABLE_ARGS)
