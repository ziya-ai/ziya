"""
Tests for app.tool_execution — the extracted _execute_single_tool logic.

These tests exercise the ToolExecContext dataclass and the
execute_single_tool() async generator in isolation, without needing
a full StreamingToolExecutor or live MCP server.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.tool_execution import ToolExecContext, execute_single_tool, _process_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(**overrides) -> ToolExecContext:
    """Build a minimal ToolExecContext with sane defaults."""
    defaults = dict(
        tool_id="toolu_001",
        tool_name="mcp_run_shell_command",
        actual_tool_name="run_shell_command",
        args={"command": "echo hello"},
        all_tools=[],
        internal_tool_names=set(),
        mcp_manager=AsyncMock(),
        project_root=None,
        conversation_id="conv_1",
        conversation=[],
        recent_commands=[],
        inter_tool_delay={"current": 0.0, "min": 0.0, "decay_factor": 0.9},
        iteration_start_time=0.0,
        track_yield_fn=lambda x: x,
        drain_feedback_fn=lambda: [],
        executor=MagicMock(),
    )
    defaults.update(overrides)
    return ToolExecContext(**defaults)


async def _collect_events(ctx: ToolExecContext) -> list:
    """Run execute_single_tool and collect all yielded events."""
    events = []
    async for evt in execute_single_tool(ctx):
        events.append(evt)
    return events


# Patch targets — these are imported lazily inside execute_single_tool,
# so we must patch at the *source* module, not on app.tool_execution.
_PATCH_VERIFY  = "app.mcp.signing.verify_tool_result"
_PATCH_STRIP   = "app.mcp.signing.strip_signature_metadata"
_PATCH_SIGN    = "app.mcp.signing.sign_tool_result"
_PATCH_RECORD  = "app.server.record_verification_result"
_PATCH_LOG     = "app.utils.tool_audit_log.log_tool_execution"
_PATCH_SANITIZE = "app.utils.tool_result_sanitizer.sanitize_for_context"
_PATCH_FEEDBACK = "app.server.active_feedback_connections"


# ---------------------------------------------------------------------------
# _process_result unit tests
# ---------------------------------------------------------------------------

class TestProcessResult:
    """Tests for the _process_result helper."""

    def test_error_with_policy_block(self):
        result = {"error": True, "message": "🚫 BLOCKED: write not allowed", "policy_block": True}
        out = _process_result(result, "run_shell_command", "run_shell_command")
        assert "POLICY BLOCK" in out
        assert "do NOT retry" in out

    def test_error_with_nonzero_exit(self):
        result = {"error": True, "message": "non-zero exit status 1"}
        out = _process_result(result, "run_shell_command", "run_shell_command")
        assert "COMMAND FAILED" in out

    def test_error_with_truncation(self):
        result = {"error": True, "message": "Content truncated at 5000 chars"}
        out = _process_result(result, "mcp_fetch", "fetch")
        assert "PARTIAL RESULT" in out
        assert "start_index" in out

    def test_error_with_validation(self):
        result = {"error": True, "message": "Validation error: missing field 'url'"}
        out = _process_result(result, "mcp_fetch", "fetch")
        assert "PARAMETER ERROR" in out

    def test_error_generic(self):
        result = {"error": True, "message": "something went wrong"}
        out = _process_result(result, "tool", "tool")
        assert "ERROR:" in out

    def test_error_security_verification(self):
        result = {"error": True, "message": "SECURITY VERIFICATION FAILED: bad sig"}
        out = _process_result(result, "tool", "tool")
        assert out == "SECURITY VERIFICATION FAILED: bad sig"

    def test_content_text_block(self):
        result = {"content": [{"type": "text", "text": "hello world"}]}
        out = _process_result(result, "tool", "tool")
        assert out == "hello world"

    def test_content_image_block_preserved(self):
        result = {"content": [
            {"type": "image", "source": {"type": "base64", "data": "AAAA"}},
            {"type": "text", "text": "Rendered diagram."},
        ]}
        out = _process_result(result, "render_diagram", "render_diagram")
        assert isinstance(out, list)  # structured content preserved
        assert out[0]["type"] == "image"

    def test_content_empty_list(self):
        result = {"content": []}
        out = _process_result(result, "tool", "tool")
        assert isinstance(out, str)

    def test_plain_string_result(self):
        result = "just a string"
        out = _process_result(result, "tool", "tool")
        assert out == "just a string"

    def test_error_false_not_treated_as_error(self):
        """result.get('error') == False should not enter the error branch."""
        result = {"error": False, "content": [{"type": "text", "text": "ok"}]}
        out = _process_result(result, "tool", "tool")
        assert out == "ok"

    def test_repetitive_execution_blocked(self):
        result = {"error": True, "message": "repetitive execution detected"}
        out = _process_result(result, "tool", "tool")
        assert "BLOCKED" in out

    def test_content_string_with_path_returns_content_only(self):
        """file_read-style dicts return just the content string, not the wrapper."""
        result = {"content": "hello world", "metadata": "1 total lines", "path": "a.txt"}
        out = _process_result(result, "file_read", "file_read")
        assert out == "hello world"

    def test_content_string_without_path_serialises_as_json(self):
        """Dicts with string content but no path key still JSON-serialize."""
        result = {"content": "just text", "extra": "data"}
        out = _process_result(result, "tool", "tool")
        import json
        parsed = json.loads(out)
        assert parsed["content"] == "just text"


# ---------------------------------------------------------------------------
# execute_single_tool integration tests
# ---------------------------------------------------------------------------

class TestExecuteSingleTool:
    """Tests for the full execute_single_tool async generator."""

    @pytest.mark.asyncio
    async def test_yields_processing_state_and_tool_start(self):
        """First two events should be processing_state and tool_start."""
        mock_executor = MagicMock()
        mock_executor._get_tool_header.return_value = "Echo"
        mock_executor._infer_syntax_hint.return_value = "bash"
        mock_executor._format_tool_result.return_value = "hello\n"

        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"content": [{"type": "text", "text": "hello"}]}

        ctx = _make_ctx(
            mcp_manager=mock_mcp,
            executor=mock_executor,
        )

        with patch(_PATCH_VERIFY, return_value=(True, None)), \
             patch(_PATCH_STRIP, side_effect=lambda r: r), \
             patch(_PATCH_RECORD), \
             patch(_PATCH_LOG), \
             patch(_PATCH_SANITIZE, side_effect=lambda t, **kw: t):
            events = await _collect_events(ctx)

        types = [e['type'] for e in events]
        assert types[0] == 'processing_state'
        assert types[1] == 'tool_start'
        assert 'tool_display' in types
        assert 'tool_result_for_model' in types

    @pytest.mark.asyncio
    async def test_tool_result_event_carries_result(self):
        """The _tool_result sentinel should carry the processed result."""
        mock_executor = MagicMock()
        mock_executor._get_tool_header.return_value = "Test"
        mock_executor._infer_syntax_hint.return_value = ""
        mock_executor._format_tool_result.return_value = "output"

        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"content": [{"type": "text", "text": "output"}]}

        ctx = _make_ctx(mcp_manager=mock_mcp, executor=mock_executor)

        with patch(_PATCH_VERIFY, return_value=(True, None)), \
             patch(_PATCH_STRIP, side_effect=lambda r: r), \
             patch(_PATCH_RECORD), \
             patch(_PATCH_LOG), \
             patch(_PATCH_SANITIZE, side_effect=lambda t, **kw: t):
            events = await _collect_events(ctx)

        tool_result_events = [e for e in events if e.get('type') == '_tool_result']
        assert len(tool_result_events) == 1
        assert tool_result_events[0]['result'] == 'output'

    @pytest.mark.asyncio
    async def test_timeout_yields_error_events(self):
        """A tool timeout should yield error display + model result."""
        mock_executor = MagicMock()
        mock_executor._get_tool_header.return_value = "Slow"
        mock_executor._infer_syntax_hint.return_value = ""

        mock_mcp = AsyncMock()
        mock_mcp.call_tool.side_effect = asyncio.TimeoutError()

        ctx = _make_ctx(mcp_manager=mock_mcp, executor=mock_executor)

        with patch.dict("os.environ", {"TOOL_EXEC_TIMEOUT": "5"}):
            events = await _collect_events(ctx)

        types = [e['type'] for e in events]
        assert 'tool_display' in types
        assert 'tool_result_for_model' in types
        error_display = [e for e in events if e['type'] == 'tool_display'][0]
        assert 'timed out' in error_display['result']

    @pytest.mark.asyncio
    async def test_feedback_stop_sets_should_stop_stream(self):
        """Pre-execution stop feedback should set ctx.should_stop_stream."""
        mock_executor = MagicMock()
        mock_executor._get_tool_header.return_value = "X"
        mock_executor._infer_syntax_hint.return_value = ""

        # Simulate feedback queue with stop message
        mock_queue = MagicMock()
        mock_queue.get_nowait.return_value = {
            'type': 'tool_feedback',
            'message': 'stop please',
        }

        ctx = _make_ctx(executor=mock_executor, conversation_id="conv_1")

        with patch(_PATCH_FEEDBACK, {
            "conv_1": [{"feedback_queue": mock_queue}]
        }):
            events = await _collect_events(ctx)

        assert ctx.should_stop_stream is True
        types = [e['type'] for e in events]
        assert 'stream_end' in types

    @pytest.mark.asyncio
    async def test_directive_feedback_sets_feedback_received(self):
        """Non-stop feedback should set ctx.feedback_received and skip execution."""
        mock_executor = MagicMock()
        mock_executor._get_tool_header.return_value = "X"
        mock_executor._infer_syntax_hint.return_value = ""

        mock_queue = MagicMock()
        mock_queue.get_nowait.return_value = {
            'type': 'tool_feedback',
            'message': 'try a different approach',
        }

        ctx = _make_ctx(executor=mock_executor, conversation_id="conv_1")

        with patch(_PATCH_FEEDBACK, {
            "conv_1": [{"feedback_queue": mock_queue}]
        }):
            events = await _collect_events(ctx)

        assert ctx.feedback_received is True
        assert ctx.should_stop_stream is False
        # No tool_display or tool_result_for_model (execution was skipped)
        types = [e['type'] for e in events]
        assert 'tool_display' not in types

    @pytest.mark.asyncio
    async def test_recent_commands_updated(self):
        """run_shell_command should append to recent_commands."""
        mock_executor = MagicMock()
        mock_executor._get_tool_header.return_value = "Shell"
        mock_executor._infer_syntax_hint.return_value = "bash"
        mock_executor._format_tool_result.return_value = "done"

        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"content": [{"type": "text", "text": "done"}]}

        recent = []
        ctx = _make_ctx(
            mcp_manager=mock_mcp,
            executor=mock_executor,
            recent_commands=recent,
        )

        with patch(_PATCH_VERIFY, return_value=(True, None)), \
             patch(_PATCH_STRIP, side_effect=lambda r: r), \
             patch(_PATCH_RECORD), \
             patch(_PATCH_LOG), \
             patch(_PATCH_SANITIZE, side_effect=lambda t, **kw: t):
            await _collect_events(ctx)

        assert "echo hello" in recent

    @pytest.mark.asyncio
    async def test_post_execution_feedback_drain_stop(self):
        """Post-execution interrupt feedback should set should_stop_stream."""
        mock_executor = MagicMock()
        mock_executor._get_tool_header.return_value = "X"
        mock_executor._infer_syntax_hint.return_value = ""
        mock_executor._format_tool_result.return_value = "ok"

        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"content": [{"type": "text", "text": "ok"}]}

        ctx = _make_ctx(
            mcp_manager=mock_mcp,
            executor=mock_executor,
            drain_feedback_fn=lambda: [{'type': 'interrupt'}],
        )

        with patch(_PATCH_VERIFY, return_value=(True, None)), \
             patch(_PATCH_STRIP, side_effect=lambda r: r), \
             patch(_PATCH_RECORD), \
             patch(_PATCH_LOG), \
             patch(_PATCH_SANITIZE, side_effect=lambda t, **kw: t):
            events = await _collect_events(ctx)

        assert ctx.should_stop_stream is True

    @pytest.mark.asyncio
    async def test_verification_failure_suppresses_display(self):
        """Failed signature verification should suppress tool_display."""
        mock_executor = MagicMock()
        mock_executor._get_tool_header.return_value = "X"
        mock_executor._infer_syntax_hint.return_value = ""

        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"content": [{"type": "text", "text": "bad"}]}

        ctx = _make_ctx(mcp_manager=mock_mcp, executor=mock_executor)

        with patch(_PATCH_VERIFY, return_value=(False, "tampered")), \
             patch(_PATCH_RECORD), \
             patch(_PATCH_LOG), \
             patch(_PATCH_SANITIZE, side_effect=lambda t, **kw: t):
            events = await _collect_events(ctx)

        # Should still have tool_result_for_model (corrective error sent to model)
        model_results = [e for e in events if e.get('type') == 'tool_result_for_model']
        assert len(model_results) == 1
        assert 'SECURITY VERIFICATION FAILED' in str(model_results[0]['content'])
