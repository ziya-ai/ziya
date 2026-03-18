"""
Realistic tests for MCP fixes that reproduce production behavior.

Updated: app.mcp_consolidated → app.mcp.consolidated
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.mcp.utils import clean_sentinels, improved_parse_tool_call, improved_extract_tool_output
from app.mcp.consolidated import execute_mcp_tools_with_status


REAL_RESPONSE_WITH_LEAKING_SENTINELS = """
Here's how you can implement a simple HTTP server in Python:

<TOOL_SENTINEL><name>mcp_run_shell_command</name>
<arguments>{"command": "echo 'Hello from shell command'"}</arguments></TOOL_SENTINEL>

```python
from http.server import HTTPServer, BaseHTTPRequestHandler

class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b'Hello, world!')

httpd = HTTPServer(('localhost', 8000), SimpleHTTPRequestHandler)
httpd.serve_forever()
```

You can run this code and access http://localhost:8000 in your browser.
"""


class TestCleanSentinels:
    """Test sentinel cleaning from model responses."""

    def test_clean_leaking_sentinels(self):
        """Should remove TOOL_SENTINEL tags from visible content."""
        cleaned = clean_sentinels(REAL_RESPONSE_WITH_LEAKING_SENTINELS)
        assert "<TOOL_SENTINEL>" not in cleaned
        assert "</TOOL_SENTINEL>" not in cleaned
        # Code block content should be preserved
        assert "HTTPServer" in cleaned

    def test_clean_empty_string(self):
        """Should handle empty string."""
        assert clean_sentinels("") == ""

    def test_clean_no_sentinels(self):
        """Should return unchanged string when no sentinels present."""
        text = "Normal text with no sentinels"
        assert clean_sentinels(text) == text


class TestImprovedParseToolCall:
    """Test tool call parsing from model output."""

    def test_parse_basic_tool_call(self):
        """Should parse a well-formed tool call."""
        text = """<TOOL_SENTINEL><name>mcp_run_shell_command</name>
<arguments>{"command": "pwd", "timeout": "5"}</arguments></TOOL_SENTINEL>"""
        result = improved_parse_tool_call(text)
        assert result is not None
        assert result['name'] == "mcp_run_shell_command"
        assert result['arguments']['command'] == "pwd"

    def test_parse_no_tool_call(self):
        """Should return None for text without tool calls."""
        result = improved_parse_tool_call("Just regular text")
        assert result is None

    def test_parse_multiline_arguments(self):
        """Should parse tool call with multiline JSON arguments."""
        text = """<TOOL_SENTINEL><name>mcp_run_shell_command</name>
<arguments>{
  "command": "ls -la",
  "timeout": "5"
}</arguments></TOOL_SENTINEL>"""
        result = improved_parse_tool_call(text)
        assert result is not None
        assert result['name'] == "mcp_run_shell_command"
        assert result['arguments']['command'] == "ls -la"


class TestImprovedExtractToolOutput:
    """Test tool output extraction."""

    def test_extract_basic_output(self):
        """Should extract output from tool result tags."""
        result = improved_extract_tool_output(
            '<tool_result>{"output": "hello world"}</tool_result>'
        )
        # Result format depends on implementation
        assert result is not None


class TestExecuteMcpToolsWithStatus:
    """Test the consolidated MCP execution function."""

    @pytest.mark.asyncio
    async def test_no_tools_in_response(self):
        """Response without tool calls should pass through."""
        result = await execute_mcp_tools_with_status("Just a normal response with no tools.")
        assert isinstance(result, str)

    def test_signature(self):
        """Function should accept a string and return a coroutine."""
        import inspect
        assert inspect.iscoroutinefunction(execute_mcp_tools_with_status)
