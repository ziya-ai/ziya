"""
Pinning tests for StreamingMiddleware._looks_like_tool_output.

History: the classifier used a generic substring indicator list that
included "```", "$ ", and "Tool:".  Any model prose containing a code
block, a shell example, or a dollar amount was misclassified as tool
output.  Consequence: on a mid-stream error, the partial-response
preservation path stuffed that prose into successful_tool_outputs and
surfaced it to the frontend as "successful tool executions" with
has_successful_tools=true when no tool ever ran.

The classifier now counts only strong signals: serialized tool-event
JSON (tool_start / tool_display / tool_execution / tool_result /
tool_result_for_model) or markers actually emitted by the tool pipeline
(Exit code:, SECURITY BLOCK, Tool execution, MCP Tool).
"""
import json

import pytest

from app.middleware.streaming import StreamingMiddleware


BT = '`' * 3


@pytest.fixture()
def middleware():
    return StreamingMiddleware(app=None)


class TestProseIsNotToolOutput:
    def test_plain_prose(self, middleware):
        assert middleware._looks_like_tool_output('Here is my analysis.') is False

    def test_prose_with_code_fence(self, middleware):
        # The old "```" indicator misclassified most Ziya responses.
        content = f'Use this snippet:\n{BT}python\nx = 1\n{BT}\n'
        assert middleware._looks_like_tool_output(content) is False

    def test_prose_with_shell_example(self, middleware):
        # The old "$ " indicator fired on illustrative shell commands.
        content = 'You could run:\n\n$ git status\n\nto check.'
        assert middleware._looks_like_tool_output(content) is False

    def test_prose_with_dollar_amount(self, middleware):
        assert middleware._looks_like_tool_output('It costs $ 5 per month.') is False

    def test_prose_mentioning_tools(self, middleware):
        # The old "Tool:" indicator fired on prose discussing tools.
        assert middleware._looks_like_tool_output('Tool: selection matters.') is False

    def test_non_tool_json(self, middleware):
        content = json.dumps({'type': 'text', 'content': 'hello'})
        assert middleware._looks_like_tool_output(content) is False

    def test_malformed_json_is_not_tool_output(self, middleware):
        assert middleware._looks_like_tool_output('{not valid json') is False


class TestToolSignalsAreToolOutput:
    @pytest.mark.parametrize('event_type', [
        'tool_start', 'tool_display', 'tool_execution',
        'tool_result', 'tool_result_for_model',
    ])
    def test_serialized_tool_event_json(self, middleware, event_type):
        content = json.dumps({'type': event_type, 'tool_name': 'run_shell_command'})
        assert middleware._looks_like_tool_output(content) is True

    def test_exit_code_marker(self, middleware):
        assert middleware._looks_like_tool_output('output\n[Exit code: 0]') is True

    def test_security_block_marker(self, middleware):
        assert middleware._looks_like_tool_output('SECURITY BLOCK: write denied') is True

    def test_tool_execution_marker(self, middleware):
        assert middleware._looks_like_tool_output('Tool execution complete') is True

    def test_mcp_tool_marker(self, middleware):
        assert middleware._looks_like_tool_output('MCP Tool result follows') is True

    def test_whitespace_padded_tool_json(self, middleware):
        content = '  ' + json.dumps({'type': 'tool_display', 'result': 'x'}) + '\n'
        assert middleware._looks_like_tool_output(content) is True
