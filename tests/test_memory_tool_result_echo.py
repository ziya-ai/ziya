"""
memory_propose / memory_save must echo content, layer and tags in their
result dict.

The chat renderer (ToolBlock → formatMCPOutput → MemoryCard) only ever sees
the tool RESULT, never the tool input, so without this echo the memory card
has nothing to display and falls back to the bare "Memory proposed for
review (N pending)." line.  These tests pin the result contract the frontend
formatter (frontend/src/utils/__tests__/mcpFormatterMemory.test.ts) consumes.

Runs under the autouse conftest fixture (isolated tmp store, Noop embeddings).
"""
import pytest

from app.mcp.tools.memory_tools import MemoryProposeTool, MemorySaveTool

_WORKSPACE = "/tmp/ziya-echo-test-project"


@pytest.mark.asyncio
async def test_propose_echoes_content_layer_tags_and_pending_count():
    tool = MemoryProposeTool()
    result = await tool.execute(
        content="Echo test: it's a proposal with an apostrophe.",
        layer="decision",
        tags=["memory", "lifecycle"],
        conversation_id="conv-echo",
        _workspace_path=_WORKSPACE,
    )
    assert result.get("success"), result
    assert result["content"] == "Echo test: it's a proposal with an apostrophe."
    assert result["layer"] == "decision"
    assert result["tags"] == ["memory", "lifecycle"]
    assert isinstance(result["pending_count"], int) and result["pending_count"] >= 1
    assert result["proposal_id"].startswith("prop_")
    # Message still carries the count for legacy/plain-text consumers.
    assert f"({result['pending_count']} pending)" in result["message"]


@pytest.mark.asyncio
async def test_propose_accepts_comma_separated_tags_and_echoes_them_as_list():
    tool = MemoryProposeTool()
    result = await tool.execute(
        content="Tags as a string.",
        tags="alpha, beta",
        conversation_id="conv-echo",
        _workspace_path=_WORKSPACE,
    )
    assert result.get("success"), result
    assert result["tags"] == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_save_echoes_content_layer_tags_without_pending_count():
    tool = MemorySaveTool()
    result = await tool.execute(
        content="Echo test: saved directly to durable memory.",
        layer="architecture",
        tags=["frontend"],
        conversation_id="conv-echo",
        _workspace_path=_WORKSPACE,
    )
    assert result.get("success"), result
    assert result["content"] == "Echo test: saved directly to durable memory."
    assert result["layer"] == "architecture"
    assert result["tags"] == ["frontend"]
    assert result["memory_id"]
    assert "pending_count" not in result
