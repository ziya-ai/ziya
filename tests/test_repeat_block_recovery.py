"""
End-to-end recovery test: repeated-block auto-recovery in stream_with_tools().

Context (session cli_20260716_000737_41665): a model wedged re-issuing an
identical `find ... CHANGELOG*` shell command. The per-call repetitive guard
(_is_repetitive_call) began BLOCKING the duplicate once we made it reachable on
the workspace-scoped path — but blocking a single call cannot END the loop: the
model just retries the block on the next iteration, and the orchestrator hands
it another round indefinitely (the log shows EXECUTE→BLOCK→BLOCK→EXECUTE for 13+
iterations until the user hit ^C).

The fix (streaming_tool_executor.py) adds a CROSS-ITERATION counter
(consecutive_blocked_calls) that, on the 3rd consecutive blocked iteration,
injects a firm stop-and-summarize `user` turn so the model breaks out on its
own (auto-recover, not a hard kill). Any non-blocked iteration clears the
counter, and the counter resets after injecting.

Unlike the isolated counter-logic simulation, these tests drive the REAL
stream_with_tools() async generator through a stateful fake provider, so they
exercise the actual detection site, the actual injection into `conversation`,
and prove the injected message is visible to the next provider call and
terminates the loop. They are written to FAIL against pre-fix code (no
injection → the fake provider loops until the max-iteration safety cap).
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock

from app.providers.base import TextDelta, ToolUseStart, ToolUseInput, ToolUseEnd, StreamEnd


_BLOCK_PHRASE = "has been called repeatedly with similar arguments"
_BLOCK_RESULT = (
    f"Tool call blocked: run_shell_command {_BLOCK_PHRASE}. Please try a "
    f"different approach or check if the previous results contain what you need."
)
_RECOVERY_MARKER = "blocked as a repeated-call loop"
_CMD = 'find /Users/dcohn/workplace/ziya -maxdepth 1 -iname "CHANGELOG*"'
_CMD_JSON = json.dumps({"command": _CMD})  # properly escaped wire JSON


def _make_executor():
    """Real StreamingToolExecutor with __init__ bypassed, wired for the tool
    loop. Uses the genuine AnthropicDirect build_* methods so assistant /
    tool_result message shapes entering `conversation` are authentic."""
    with patch.dict(os.environ, {"ZIYA_ENDPOINT": "bedrock", "ZIYA_MODEL": "sonnet3.7"}):
        with patch(
            "app.streaming_tool_executor.StreamingToolExecutor.__init__",
            return_value=None,
        ):
            from app.streaming_tool_executor import StreamingToolExecutor
            from app.providers.anthropic_direct import AnthropicDirectProvider

            ex = StreamingToolExecutor.__new__(StreamingToolExecutor)
            ex.model_id = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
            ex.model_config = {"family": "claude", "max_output_tokens": 8192}
            ex.bedrock = None

            provider = MagicMock()
            provider.provider_name = "mock"
            # Use the REAL message builders (static-shape pure functions).
            provider.build_assistant_message = (
                AnthropicDirectProvider.build_assistant_message.__get__(provider)
            )
            provider.build_tool_result_message = (
                AnthropicDirectProvider.build_tool_result_message.__get__(provider)
            )
            ex.provider = provider

            # State/collaborators the loop touches.
            ex._block_opening_buffer = ""
            ex._repetition_suppressed = False
            ex._normalize_fence_spacing = lambda text, tracker: text
            ex._update_code_block_tracker = lambda text, tracker: None
            ex._sanitize_assistant_text = lambda t: t
            ex._normalize_tool_name = lambda n: n
            opt = MagicMock()
            opt.add_content.side_effect = lambda t: [t] if t else []
            opt.flush_remaining.return_value = ""
            ex._content_optimizer = opt

            ex._build_conversation_from_messages = MagicMock(
                return_value=([{"role": "user", "content": [{"type": "text", "text": "find the changelog"}]}],
                              "system prompt")
            )
            ex._build_provider_config = MagicMock(return_value=MagicMock(max_output_tokens=8192))
            ex._handle_usage_event = MagicMock()
            ex._load_and_prepare_tools = _async_return(([], [], set(), set(), set()))
            return ex


def _async_return(value):
    async def _fn(*a, **k):
        return value
    return _fn


class _RepeatingProvider:
    """Fake provider that emits an identical shell tool call every iteration
    UNTIL it observes the recovery instruction already appended to the
    conversation, at which point it 'complies' (text + end_turn), ending the
    loop. This models the real model's behavior and lets us assert that the
    injection both fires AND actually breaks the loop."""

    provider_name = "mock"

    def __init__(self):
        self.iterations = 0
        self.saw_recovery = False
        self.build_assistant_message = None  # set from executor's provider
        self.build_tool_result_message = None

    def supports_feature(self, feature_name: str) -> bool:
        """Mirror BaseProvider.supports_feature, which defaults to False.

        Required because stream_with_tools queries
        ``supports_feature('image_tool_results')`` when assembling tool
        results - 12 lines BEFORE the repeated-block detection this test
        exercises.  A fake without it raised AttributeError inside the
        loop's broad ``except``, which logged and ended the iteration:
        detection never ran, no injection fired, and the test failed as
        though the recovery mechanism were broken.  The production guard
        was fine the whole time.
        """
        return False

    async def stream_response(self, conversation, system_content, tools, config):
        self.iterations += 1
        recovered = any(
            isinstance(m.get("content"), str) and _RECOVERY_MARKER in m["content"]
            for m in conversation
        )
        if recovered:
            self.saw_recovery = True
            yield TextDelta(content="Understood — I'll stop. No CHANGELOG file was found.")
            yield StreamEnd(stop_reason="end_turn")
            return
        # Otherwise: emit the same identical tool call again.
        yield ToolUseStart(id=f"tu_{self.iterations}", name="mcp_run_shell_command", index=0)
        yield ToolUseInput(partial_json=_CMD_JSON, index=0)
        yield ToolUseEnd(id=f"tu_{self.iterations}", name="mcp_run_shell_command",
                         input={"command": _CMD}, index=0)
        yield StreamEnd(stop_reason="tool_use")


async def _collect(agen, cap=400):
    out = []
    async for e in agen:
        out.append(e)
        if len(out) >= cap:
            break
    return out


def _install_blocking_exec(monkeypatch_target, block=True):
    """Patch execute_single_tool to yield a _tool_result — either the block
    error (simulating _is_repetitive_call firing) or a normal success."""
    result = _BLOCK_RESULT if block else "(tool completed successfully with no output)"

    async def _fake_exec(ctx):
        yield {
            "type": "_tool_result",
            "tool_id": ctx.tool_id,
            "tool_name": ctx.tool_name,
            "result": result,
        }
    return _fake_exec


class TestRepeatBlockRecoveryE2E:
    """Drive the real stream_with_tools generator through repeated blocks."""

    @pytest.mark.asyncio
    async def test_recovery_injected_and_loop_terminates(self):
        """Three consecutive blocked iterations must trigger the stop-and-
        summarize injection, which the provider then sees and complies with,
        ending the loop well under the max-iteration cap."""
        ex = _make_executor()
        prov = _RepeatingProvider()
        prov.build_assistant_message = ex.provider.build_assistant_message
        prov.build_tool_result_message = ex.provider.build_tool_result_message
        ex.provider = prov

        mgr = MagicMock()
        mgr.is_initialized = True
        mgr.reset_turn_tool_count = MagicMock()

        with patch.dict(os.environ, {"ZIYA_MAX_TOOL_ITERATIONS": "40"}), \
             patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
             patch("app.tool_execution.execute_single_tool", _install_blocking_exec(None)):
            events = await _collect(
                ex.stream_with_tools(
                    messages=[{"role": "user", "content": "find the changelog"}],
                    tools=[], conversation_id="conv-e2e-recover",
                )
            )

        # The provider complied only because it saw the recovery injection.
        assert prov.saw_recovery, "recovery instruction was never injected/observed"
        # Loop terminated far below the safety cap (proves recovery, not cap).
        assert prov.iterations <= 6, (
            f"loop should end shortly after the 3rd block, took {prov.iterations} iterations"
        )
        # A stream_end was emitted (clean termination).
        assert any(e.get("type") == "stream_end" for e in events)

    @pytest.mark.asyncio
    async def test_no_injection_before_threshold(self):
        """Fewer than 3 consecutive blocks must NOT inject — a provider that
        blocks twice then complies on its own never sees the recovery text."""
        ex = _make_executor()

        class _TwoBlockProvider(_RepeatingProvider):
            async def stream_response(self, conversation, system_content, tools, config):
                self.iterations += 1
                # Comply voluntarily on the 3rd call (after 2 blocks) — before
                # the cross-iteration counter reaches the threshold of 3.
                if self.iterations >= 3:
                    yield TextDelta(content="Done on my own.")
                    yield StreamEnd(stop_reason="end_turn")
                    return
                yield ToolUseStart(id=f"tu_{self.iterations}", name="mcp_run_shell_command", index=0)
                yield ToolUseInput(partial_json=_CMD_JSON, index=0)
                yield ToolUseEnd(id=f"tu_{self.iterations}", name="mcp_run_shell_command",
                                 input={"command": _CMD}, index=0)
                yield StreamEnd(stop_reason="tool_use")

        prov = _TwoBlockProvider()
        prov.build_assistant_message = ex.provider.build_assistant_message
        prov.build_tool_result_message = ex.provider.build_tool_result_message
        ex.provider = prov

        mgr = MagicMock()
        mgr.is_initialized = True
        mgr.reset_turn_tool_count = MagicMock()

        with patch.dict(os.environ, {"ZIYA_MAX_TOOL_ITERATIONS": "40"}), \
             patch("app.mcp.manager.get_mcp_manager", return_value=mgr), \
             patch("app.tool_execution.execute_single_tool", _install_blocking_exec(None)):
            await _collect(
                ex.stream_with_tools(
                    messages=[{"role": "user", "content": "x"}],
                    tools=[], conversation_id="conv-e2e-nothresh",
                )
            )

        assert prov.saw_recovery is False, "recovery must not fire before 3 consecutive blocks"


class TestRepeatBlockCounterLogic:
    """Fast unit-level checks of the counter semantics (threshold=3, reset on
    success, reset after inject) mirroring the exact code in the loop. These
    document the contract the E2E test exercises."""

    @staticmethod
    def _simulate(block_sequence, threshold=3):
        counter = 0
        injections = []
        for i, blocked in enumerate(block_sequence, start=1):
            if blocked:
                counter += 1
            else:
                counter = 0
            if counter >= threshold:
                injections.append(i)
                counter = 0
        return injections

    def test_three_consecutive_blocks_inject(self):
        assert self._simulate([True, True, True]) == [3]

    def test_success_resets_counter(self):
        # block, block, success, block, block -> never reaches 3 consecutive
        assert self._simulate([True, True, False, True, True]) == []

    def test_reset_after_inject_allows_next_cycle(self):
        # 6 straight blocks -> inject at 3, reset, inject again at 6
        assert self._simulate([True] * 6) == [3, 6]

    def test_two_blocks_never_inject(self):
        assert self._simulate([True, True]) == []
