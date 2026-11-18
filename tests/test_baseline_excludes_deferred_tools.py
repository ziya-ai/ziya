"""The overhead baseline (the token panel's "tools" figure) is measured on
the tools that occupy the model's context, not on the whole ``tools`` array.

Reported 2026-11-14: with 7 servers within reach on a native tool-search
model, the panel showed tools = 343,161 while the MCP heading for the same
project reported 61,072 sent and 114,802 deferred.  The baseline had been
taken over all 368 entries of the array, deferred ones included, and it was
keyed on that same length, so moving a server between on hand and within
reach never invalidated it.

Unit half: ``mcp_ondemand.in_context_tools``.
Seam half: the executor's baseline block uses it for the invalidation
count, the provider probe and the size-based estimate (source assertions;
the block runs inside a live stream).
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

from app.utils import mcp_ondemand as od

ROOT = Path(__file__).resolve().parents[1]


def _pairs():
    def tool(name, server):
        return (SimpleNamespace(name=name, metadata={"server_name": server}),
                {"name": name, "description": "d" * 400, "input_schema": {"type": "object"}})
    return [tool("mcp_h1", "hand"), tool("mcp_r1", "reachy"), tool("mcp_r2", "reachy"),
            (SimpleNamespace(name="file_read", metadata={}),
             {"name": "file_read", "description": "b", "input_schema": {"type": "object"}})]


def _server_of(t):
    return (getattr(t, "metadata", None) or {}).get("server_name")


def test_deferred_tools_and_the_search_entry_are_not_in_context():
    payload = od.native_tool_payload(_pairs(), _server_of, {"reachy"})
    # Positive control: the request really carries the deferred tools.
    assert [t.get("name") for t in payload if t.get("defer_loading")] == ["mcp_r1", "mcp_r2"]
    assert od.is_native_search_request(payload)

    kept = od.in_context_tools(payload)
    assert [t["name"] for t in kept] == ["mcp_h1", "file_read"]
    assert not od.is_native_search_request(kept)
    # The size the fallback estimate prices drops by the deferred schemas.
    assert len(json.dumps(kept)) < len(json.dumps(payload)) - 800


def test_a_request_with_nothing_deferred_is_unchanged():
    tools = [schema for _t, schema in _pairs()]
    assert od.in_context_tools(tools) == tools
    assert od.in_context_tools(None) == [] and od.in_context_tools([]) == []


def test_moving_a_server_within_reach_changes_the_count_the_baseline_is_keyed_on():
    """Before: len(tools) was 5 either way minus loader bookkeeping, so the
    stale baseline was kept."""
    on_hand = od.in_context_tools(od.native_tool_payload(_pairs(), _server_of, set()))
    reach = od.in_context_tools(od.native_tool_payload(_pairs(), _server_of, {"reachy"}))
    assert len(on_hand) == 4 and len(reach) == 2


def _executor_source():
    src = (ROOT / "app" / "streaming_tool_executor.py").read_text()
    return "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))


def test_executor_baseline_is_keyed_and_measured_on_in_context_tools():
    src = _executor_source()
    # Invalidation check.
    check = src[src.index("should_establish_baseline = False"):src.index("hallucination_retries = 0")]
    assert "current_tool_count = len(in_context_tools(bedrock_tools))" in check
    assert "len(bedrock_tools)" not in check

    # Measurement block.
    start = src.index("if should_establish_baseline:")
    block = src[start:src.index("calibrator.request_save()", start)]
    assert "_baseline_tools = in_context_tools(bedrock_tools)" in block
    assert "mcp_tool_count = len(_baseline_tools)" in block
    assert "_measure(baseline_system_text, _baseline_tools)" in block
    assert "len(json.dumps(_baseline_tools))" in block
    assert "calibrator.baseline_tool_counts[model_family] = mcp_tool_count" in block
    # Nothing in the block still reads the full array except the one filter call.
    assert re.findall(r"bedrock_tools", block) == ["bedrock_tools"]
