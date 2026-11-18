"""Tool-search passback through the REAL executor loop.

Reported 2026-11-16 (conversation 27b0c90a, fable5.2 on the Bedrock Invoke
API): the fourth request of a turn was rejected with

    messages.25: `tool_search_tool_regex` tool use with id `srvtoolu_bdrk_…`
    was found without a corresponding `tool_search_tool_result` block

The existing coverage (tests/test_mcp_native_tool_search.py) checks the
provider's stream parser and the assembler in isolation, and checks the
executor only by reading its source.  Nothing drove the executor with a
search inside a response and looked at the prompts it then sent.  This does:
a scripted provider replays responses holding ``server_tool_use`` /
``tool_search_tool_result`` in the block orders the API produces, the real
loop runs several iterations, and every prompt the provider receives is
checked for the pairing the API enforces.

The invariant, per assistant message: each ``server_tool_use`` is followed
immediately by a ``tool_search_tool_result`` with its id, and no result
appears without its use.
"""
import asyncio
import copy
import json
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from app.providers.base import (
    LLMProvider, PassbackBlock, StreamEnd, TextDelta, ThinkingBlock,
    ToolUseEnd, ToolUseInput, ToolUseStart,
)


def _tool(tool_id, name, args, index):
    return [ToolUseStart(id=tool_id, name=name, index=index),
            ToolUseInput(partial_json=json.dumps(args), index=index),
            ToolUseEnd(id=tool_id, name=name, input=args, index=index)]


def _search(sid, index, found="mcp_ReadInternalWebsites"):
    """The two blocks one search adds to a response, at index and index+1."""
    return [
        PassbackBlock(block={"type": "server_tool_use", "id": sid,
                             "name": "tool_search_tool_regex",
                             "input": {"pattern": "internal"}}, index=index),
        PassbackBlock(block={"type": "tool_search_tool_result", "tool_use_id": sid,
                             "content": {"type": "tool_search_tool_search_result",
                                         "tool_references": [{"type": "tool_reference",
                                                              "tool_name": found}]}},
                      index=index + 1),
    ]


class _Provider:
    """Scripted responses; the real assembler; a deep copy of every prompt."""

    provider_name = "test_scripted"

    def __init__(self, turns, passback=False):
        self._turns = list(turns)
        self._passback = passback
        self.prompts: List[List[Dict[str, Any]]] = []

    async def stream_response(self, messages, system_content, tools, config):
        self.prompts.append(copy.deepcopy(messages))
        events = self._turns.pop(0) if self._turns else [
            TextDelta(content="done", index=0), StreamEnd(stop_reason="end_turn")]
        for ev in events:
            yield ev
            await asyncio.sleep(0)

    def supports_feature(self, name):
        return name == "thinking_passback" and self._passback

    def build_assistant_message(self, text, tool_uses, thinking_blocks=None,
                                text_index=None, text_segments=None, **kw):
        return {"role": "assistant", "content": LLMProvider._ordered_assistant_content(
            text, tool_uses, thinking_blocks, text_index, text_segments=text_segments)}

    def build_tool_result_message(self, results):
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["tool_use_id"], "content": r["content"]}
            for r in results]}


def _make_executor(provider):
    from app.streaming_tool_executor import StreamingToolExecutor
    with patch.object(StreamingToolExecutor, "__init__", lambda self, **kw: None):
        exe = StreamingToolExecutor.__new__(StreamingToolExecutor)
        exe.provider = provider
        exe.model_id = "anthropic.claude-sonnet-4-20250514-v1:0"
        exe.model_config = {"family": "claude", "max_output_tokens": 8192}
        exe.bedrock = None
        exe.endpoint = "bedrock"
        exe.region = "us-east-1"
        return exe


async def _fake_execute(ctx):
    yield {'type': 'tool_start', 'tool_id': ctx.tool_id, 'tool_name': ctx.tool_name}
    await asyncio.sleep(0)
    yield {'type': 'tool_display', 'tool_id': ctx.tool_id, 'tool_name': ctx.tool_name, 'result': 'ok'}
    yield {'type': '_tool_result', 'tool_id': ctx.tool_id, 'tool_name': ctx.tool_name,
           'result': f'result-{ctx.tool_id}'}


async def _run(provider, conv_id):
    exe = _make_executor(provider)
    with patch("app.tool_execution.execute_single_tool", _fake_execute), \
            patch("app.tool_batch.execute_single_tool", _fake_execute), \
            patch.object(exe, "_load_and_prepare_tools",
                         return_value=([], [], set(), set(), set())):
        return [ev async for ev in exe.stream_with_tools(
            [{"role": "user", "content": "go"}], conversation_id=conv_id)]


def pairing_faults(prompt) -> List[str]:
    """Every violation of the API's server_tool_use / result pairing."""
    faults = []
    for mi, msg in enumerate(prompt):
        content = msg.get("content")
        if msg.get("role") != "assistant" or not isinstance(content, list):
            continue
        types = [b.get("type") for b in content]
        for bi, b in enumerate(content):
            if b.get("type") == "server_tool_use":
                nxt = content[bi + 1] if bi + 1 < len(content) else {}
                if nxt.get("type") != "tool_search_tool_result" or nxt.get("tool_use_id") != b.get("id"):
                    faults.append(f"messages.{mi}: {b.get('id')} has no result after it {types}")
            if b.get("type") == "tool_search_tool_result":
                prev = content[bi - 1] if bi else {}
                if prev.get("type") != "server_tool_use" or prev.get("id") != b.get("tool_use_id"):
                    faults.append(f"messages.{mi}: result for {b.get('tool_use_id')} has no use before it {types}")
    return faults


def _searches_in(prompt) -> List[str]:
    return [b.get("id") for m in prompt if isinstance(m.get("content"), list)
            for b in m["content"] if b.get("type") == "server_tool_use"]


_SIGNED = ThinkingBlock(content="plan", signature="sig", index=0)
_UNSIGNED = ThinkingBlock(content="plan", signature=None, index=0)

# One entry per first response; three plain tool iterations follow each, so
# the search turn is replayed in four later prompts (the report failed on
# the third replay).
FIRST_RESPONSES = {
    "search_then_tool": [
        TextDelta(content="Looking.", index=0),
        *_search("srv_a", 1),
        *_tool("t1", "file_read", {"path": "a.py"}, 3),
    ],
    "search_between_tools": [
        TextDelta(content="Looking.", index=0),
        *_tool("t1", "file_read", {"path": "a.py"}, 1),
        *_tool("t2", "file_read", {"path": "b.py"}, 2),
        *_search("srv_a", 3),
        *_tool("t3", "file_read", {"path": "c.py"}, 5),
    ],
    "search_after_all_tools": [
        TextDelta(content="Looking.", index=0),
        *_tool("t1", "file_read", {"path": "a.py"}, 1),
        *_search("srv_a", 2),
    ],
    "two_searches_thinking_between": [
        _SIGNED,
        *_search("srv_a", 1),
        ThinkingBlock(content="more", signature="sig2", index=3),
        *_search("srv_b", 4),
        *_tool("t1", "file_read", {"path": "a.py"}, 6),
    ],
    "unsigned_thinking_then_search": [
        _UNSIGNED,
        TextDelta(content="Looking.", index=1),
        *_search("srv_a", 2),
        *_tool("t1", "file_read", {"path": "a.py"}, 4),
    ],
    "text_on_both_sides_of_search": [
        _SIGNED,
        TextDelta(content="First part.", index=1),
        *_search("srv_a", 2),
        TextDelta(content="Second part.", index=4),
        *_tool("t1", "file_read", {"path": "a.py"}, 5),
    ],
}


def _later_turn(n):
    return [TextDelta(content=f"Step {n}.", index=0),
            *_tool(f"u{n}", "file_read", {"path": f"f{n}.py"}, 1),
            StreamEnd(stop_reason="tool_use")]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(FIRST_RESPONSES))
@pytest.mark.parametrize("passback", [True, False])
async def test_search_turn_stays_paired_in_every_later_prompt(name, passback):
    first = FIRST_RESPONSES[name] + [StreamEnd(stop_reason="tool_use")]
    provider = _Provider([first, _later_turn(1), _later_turn(2), _later_turn(3)],
                         passback=passback)
    await _run(provider, f"passback-seam-{name}-{passback}")

    # The path ran: the search turn was sent back in at least three prompts.
    assert len(provider.prompts) >= 4, len(provider.prompts)
    expected = [e.block["id"] for e in first
                if isinstance(e, PassbackBlock) and e.block["type"] == "server_tool_use"]
    for n, prompt in enumerate(provider.prompts[1:], start=1):
        assert _searches_in(prompt) == expected, f"prompt {n}: search blocks missing"
        assert pairing_faults(prompt) == [], f"prompt {n}"


def test_pairing_check_catches_the_reported_shape():
    """The checker itself: an unpaired use, as in the report, is a fault."""
    bad = [{"role": "assistant", "content": [
        {"type": "text", "text": "x"},
        {"type": "server_tool_use", "id": "srv_a", "name": "tool_search_tool_regex", "input": {}},
        {"type": "tool_use", "id": "t1", "name": "n", "input": {}}]}]
    assert pairing_faults(bad) and "srv_a" in pairing_faults(bad)[0]
    moved = [{"role": "assistant", "content": [
        {"type": "server_tool_use", "id": "srv_a", "name": "tool_search_tool_regex", "input": {}},
        {"type": "text", "text": "x"},
        {"type": "tool_search_tool_result", "tool_use_id": "srv_a", "content": {}}]}]
    assert len(pairing_faults(moved)) == 2
