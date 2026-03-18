"""
Tests for real-world MCP tool call examples from production logs.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.mcp.utils import improved_parse_tool_call, clean_sentinels


def test_real_world_example_1():
    """Test with a basic tool call."""
    test_input = """<TOOL_SENTINEL><name>mcp_run_shell_command</name>
<arguments>{
  "command": "pwd",
  "timeout": "5"
}</arguments></TOOL_SENTINEL>"""

    result = improved_parse_tool_call(test_input)
    assert result is not None
    assert result['name'] == "mcp_run_shell_command"
    assert result['arguments'] == {"command": "pwd", "timeout": "5"}


def test_real_world_example_2():
    """Test with ls -la command."""
    test_input = """<TOOL_SENTINEL><name>mcp_run_shell_command</name>
<arguments>{
  "command": "ls -la",
  "timeout": "5"
}</arguments></TOOL_SENTINEL>"""

    result = improved_parse_tool_call(test_input)
    assert result is not None
    assert result['name'] == "mcp_run_shell_command"
    assert result['arguments'] == {"command": "ls -la", "timeout": "5"}


def test_real_world_example_3():
    """Test with <name> tag format (extra whitespace)."""
    test_input = """<TOOL_SENTINEL>
<name>mcp_run_shell_command</name>
<arguments>{
  "command": "cat /etc/hosts",
  "timeout": "5"
}</arguments>
</TOOL_SENTINEL>"""

    result = improved_parse_tool_call(test_input)
    assert result is not None
    assert result['name'] == "mcp_run_shell_command"
    assert result['arguments']['command'] == "cat /etc/hosts"


def test_real_world_example_4():
    """Test with pipe characters in command."""
    test_input = """<TOOL_SENTINEL><name>mcp_run_shell_command</name>
<arguments>{
  "command": "du -h --max-depth=1 | sort -hr",
  "timeout": "5"
}</arguments>
</TOOL_SENTINEL>"""

    result = improved_parse_tool_call(test_input)
    assert result is not None
    assert result['name'] == "mcp_run_shell_command"
    assert result['arguments'] == {"command": "du -h --max-depth=1 | sort -hr", "timeout": "5"}


def test_real_world_example_5():
    """Test with incomplete/minimal tag format."""
    test_input = """<TOOL_SENTINEL>
<name>mcp_run_shell_command</name>
<arguments>{
"command": "echo hello"
}</arguments>
</TOOL_SENTINEL>"""

    result = improved_parse_tool_call(test_input)
    assert result is not None


def test_clean_sentinels_preserves_content():
    """Clean sentinels should preserve non-sentinel content."""
    text = "Before <TOOL_SENTINEL><name>tool</name></TOOL_SENTINEL> After"
    cleaned = clean_sentinels(text)
    assert "Before" in cleaned
    assert "After" in cleaned
    assert "<TOOL_SENTINEL>" not in cleaned
