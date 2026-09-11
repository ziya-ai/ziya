"""
Seam tests for empty-completion recovery inside the live stream_with_tools
loop: the grace-budget reset on tool progress, and the dropped-stream retry.

Why this file exists.  Both behaviours are decided by the pure
``_decide_no_tool_outcome`` (unit-tested in test_decide_no_tool_outcome.py),
but the defects they fix were never in the decider -- they were in what the
LOOP fed it:

  * ``textonly_grace_used`` is loop state.  It was a per-stream budget with no
    reset, so one nudge spent at iteration ~30 closed the recovery path for
    every later empty completion.  Only a multi-iteration run with a real
    tool round between two empties can show the reset landing.

  * ``last_stop_reason`` is loop state.  It is None only when the provider's
    stream closed without a StreamEnd, which the decider cannot observe
    unless the loop actually runs a stream that ends that way.

The harness therefore drives ``stream_with_tools`` end to end with a
scripted provider (one event list per model call) and a stubbed
``execute_single_tool`` at the tool boundary.  Everything between -- tool_use
block assembly, tool_result message building, the per-iteration reset of
``last_stop_reason``, the decider call site, and the side effects each
verdict performs -- is the real code.  Assertions are on two outermost
surfaces: what the model was SENT (the conversation passed to each
``stream_response`` call) and what the user SAW (yielded text events).

Confirmed to fail against the pre-fix source: with the reset line removed,
``test_grace_budget_resets_after_tool_progress`` ends the stream after the
fourth model call with no final text; with the decider's None branch
removed, ``test_dropped_stream_after_tools_is_reissued`` likewise ends
instead of re-issuing.
"""

import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.providers.base import (
    TextDelta, StreamEnd, ToolUseStart, ToolUseInput, ToolUseEnd,
)


# ---------------------------------------------------------------------------
# Provider double
# ---------------------------------------------------------------------------

class _ScriptedProvider:
    """Yields one pre-written event list per ``stream_response`` call and
    records a snapshot of the conversation it was handed each time.

    A call past the end of the script yields nothing and no StreamEnd --
    i.e. it looks like a dropped stream -- so a test that expects N calls
    and gets N+1 fails loudly rather than hanging.
    """
    provider_name = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []  # conversation snapshots, one per call

    async def stream_response(self, conversation, system_content, tools, config):
        import copy
        self.calls.append(copy.deepcopy(conversation))
        idx = len(self.calls) - 1
        events = self.script[idx] if idx < len(self.script) else []
        for ev in events:
            yield ev

    # --- message builders (Anthropic-shaped, which the loop's
    #     prev_is_tool_result probe expects) ---
    def build_assistant_message(self, text, tool_uses, thinking_blocks=None):
        content = []
        if text:
            content.append({"type": "text", "text": text})
        for tu in tool_uses:
            content.append({"type": "tool_use", "id": tu["id"],
                            "name": tu["name"], "input": tu["input"]})
        return {"role": "assistant", "content": content}

    def build_tool_result_message(self, tool_results):
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["tool_use_id"],
             "content": r["content"]} for r in tool_results
        ]}

    def supports_feature(self, name):
        return False

    def prepare_cache_control(self, messages, *a, **k):
        return messages


class _FakeTool:
    """Minimal tool object: has a name, no schema (so schema validation is
    skipped and the call reaches execute_single_tool)."""
    def __init__(self, name):
        self.name = name
        self.metadata = None


TOOL = "fake_tool"


def _tool_call(tool_id, arg="x"):
    """One complete tool_use round as the provider would emit it."""
    return [
        ToolUseStart(id=tool_id, name=TOOL, index=0),
        ToolUseInput(partial_json='{"value": "%s"}' % arg, index=0),
        ToolUseEnd(id=tool_id, name=TOOL, input={"value": arg}, index=0),
        StreamEnd(stop_reason="tool_use"),
    ]


EMPTY_CLEAN = [StreamEnd(stop_reason="end_turn")]   # model said "done", nothing
DROPPED = []                                          # no events, no StreamEnd
FINAL = [TextDelta(content="All done."), StreamEnd(stop_reason="end_turn")]


# ---------------------------------------------------------------------------
# Executor construction
# ---------------------------------------------------------------------------

def _make_executor(provider):
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "sonnet3.7"}):
        with patch('app.streaming_tool_executor.StreamingToolExecutor.__init__',
                   return_value=None):
            from app.streaming_tool_executor import StreamingToolExecutor
            ex = StreamingToolExecutor.__new__(StreamingToolExecutor)

    ex.model_id = "test-model"
    ex.model_config = {"family": "claude", "max_output_tokens": 8192,
                       "supports_assistant_prefill": True}
    ex.bedrock = None
    ex.provider = provider

    ex._block_opening_buffer = ""
    ex._normalize_fence_spacing = lambda text, tracker, **kw: text
    ex._update_code_block_tracker = lambda text, tracker: None

    optimizer = MagicMock()
    optimizer.add_content.side_effect = lambda t: [t] if t else []
    optimizer.flush_remaining.return_value = ""
    ex._content_optimizer = optimizer

    ex._build_conversation_from_messages = MagicMock(return_value=(
        [{"role": "user", "content": [{"type": "text", "text": "Go"}]}],
        "system prompt",
    ))
    ex._format_tools_for_api = MagicMock(return_value=[])
    ex._build_provider_config = MagicMock(return_value=MagicMock(max_output_tokens=8192))
    ex._handle_usage_event = MagicMock()
    ex._build_tool_reminder_message = MagicMock(
        return_value={"role": "user", "content": "reminder"})

    # The tool set is loaded through _load_and_prepare_tools; stub it so the
    # test neither spins up the MCP manager (~40s) nor depends on which
    # servers are configured.  Returns
    # (all_tools, bedrock_tools, builtin_names, internal_names, optional_only).
    ex._load_and_prepare_tools = AsyncMock(
        return_value=([_FakeTool(TOOL)], [], set(), set(), set()))
    return ex


async def _fake_execute_single_tool(ctx):
    """Stand-in at the tool boundary: emits the events the loop consumes."""
    yield {'type': 'tool_start', 'tool_name': ctx.actual_tool_name, 'tool_id': ctx.tool_id}
    yield {'type': '_tool_result', 'tool_id': ctx.tool_id,
           'tool_name': ctx.tool_name, 'result': 'ok'}


async def _run(script):
    provider = _ScriptedProvider(script)
    ex = _make_executor(provider)
    events = []
    mgr = MagicMock()
    mgr.is_initialized = True
    with patch('app.mcp.manager.get_mcp_manager', return_value=mgr), \
         patch('app.tool_execution.execute_single_tool', _fake_execute_single_tool), \
         patch('app.streaming_tool_executor.StreamingToolExecutor._FEEDBACK_GRACE_SECONDS', 0, create=True):
        async for ev in ex.stream_with_tools(
            messages=[{"role": "user", "content": "Go"}], tools=[],
        ):
            events.append(ev)
            if len(events) > 400:  # safety cap; a runaway loop fails, not hangs
                break
    return provider, events


def _final_text(events):
    return ''.join(e.get('content', '') for e in events if e.get('type') == 'text')


def _last_user_turn(conv):
    assert conv[-1]["role"] == "user", conv[-1]
    return conv[-1]["content"]


def _has_nudge(content_blocks):
    return any(b.get("type") == "text" and "did not provide an answer" in b.get("text", "")
               for b in content_blocks if isinstance(b, dict))


def _has_tool_result(content_blocks):
    return any(b.get("type") == "tool_result" for b in content_blocks if isinstance(b, dict))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHarnessActuallyRunsTools:
    """If this fails, every assertion below is vacuous."""

    @pytest.mark.asyncio
    async def test_one_tool_round_then_final_text(self):
        provider, events = await _run([_tool_call("t1"), FINAL])
        assert len(provider.calls) == 2
        # The second model call was sent the tool_result for t1.
        turn = _last_user_turn(provider.calls[1])
        assert _has_tool_result(turn)
        assert turn[0]["tool_use_id"] == "t1"
        assert "All done." in _final_text(events)


class TestGraceBudgetResetsOnToolProgress:

    @pytest.mark.asyncio
    async def test_grace_budget_resets_after_tool_progress(self):
        """tool → empty(nudge) → tool → empty → must nudge AGAIN → final.

        Pre-fix the second empty found textonly_grace_used==1 (cap 1 for a
        clean stop) and ended the stream after four model calls.
        """
        provider, events = await _run([
            _tool_call("t1"),   # call 1: model uses a tool
            EMPTY_CLEAN,        # call 2: empty → nudge (grace 0→1)
            _tool_call("t2"),   # call 3: model responds to nudge with work → reset
            EMPTY_CLEAN,        # call 4: empty → needs a fresh nudge
            FINAL,              # call 5: reachable only if call 4 nudged
        ])
        assert len(provider.calls) == 5, (
            f"stream ended after {len(provider.calls)} model calls — the second "
            f"empty completion was not nudged")
        # What the model was sent on call 5: tool_result for t2 plus the nudge.
        turn = _last_user_turn(provider.calls[4])
        assert _has_tool_result(turn) and turn[0]["tool_use_id"] == "t2"
        assert _has_nudge(turn)
        # And the user saw the final answer.
        assert "All done." in _final_text(events)

    @pytest.mark.asyncio
    async def test_consecutive_empties_still_bounded(self):
        """tool → empty(nudge) → empty → END.  The reset must not make the
        budget unbounded: with no tool work between them the second empty
        is a genuine repeated 'done' and the loop respects it."""
        provider, events = await _run([
            _tool_call("t1"),
            EMPTY_CLEAN,        # nudge
            EMPTY_CLEAN,        # grace spent, no progress → end
            FINAL,              # must NOT be reached
        ])
        assert len(provider.calls) == 3
        assert "All done." not in _final_text(events)


class TestDroppedStreamRetry:

    @pytest.mark.asyncio
    async def test_dropped_stream_after_tools_is_reissued(self):
        """tool → empty(nudge, grace spent) → DROP → re-issue → final.

        The live iteration-48 shape.  Pre-fix the drop was charged against
        the (spent) nudge budget and the stream ended after three calls.
        """
        provider, events = await _run([
            _tool_call("t1"),
            EMPTY_CLEAN,        # nudge, grace 0→1
            DROPPED,            # no StreamEnd → last_stop_reason None
            FINAL,
        ])
        assert len(provider.calls) == 4, (
            f"stream ended after {len(provider.calls)} model calls — the dropped "
            f"stream was not re-issued")
        # A re-issue is identical: call 4 was sent the same last user turn
        # as call 3 (tool_result + the one nudge), with no second nudge.
        assert provider.calls[3][-1] == provider.calls[2][-1]
        assert sum(1 for b in _last_user_turn(provider.calls[3])
                   if b.get("type") == "text") == 1
        assert "All done." in _final_text(events)

    @pytest.mark.asyncio
    async def test_persistent_drops_are_bounded(self):
        """A provider that never sends StreamEnd cannot loop forever."""
        provider, events = await _run([
            _tool_call("t1"),
            DROPPED, DROPPED, DROPPED, DROPPED, DROPPED,
            FINAL,
        ])
        # 1 tool call + 2 drop retries + then the ladder: grace nudge (1)
        # + end.  Exact count is the ladder's business; the invariant is
        # that FINAL (index 6) is never reached.
        assert len(provider.calls) < 7
        assert "All done." not in _final_text(events)
