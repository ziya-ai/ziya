#!/usr/bin/env python3
"""
Test for real-world examples from the logs.
"""

import sys
import os
import re

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import the updated version
from app.mcp_fixes_updated import improved_parse_tool_call, clean_sentinels

def test_real_world_example_1():
    """Test with a real-world example from the logs."""
    print("\nTesting real-world example 1:")
    
    # Real-world example from the logs
    test_input = """<TOOL_SENTINEL><name>mcp_run_shell_command</name>
<arguments>{
  "command": "pwd",
  "timeout": "5"
}</arguments></TOOL_SENTINEL>"""
    
    # Parse the tool call
    result = improved_parse_tool_call(test_input)
    
    # Check if the tool was correctly parsed
    assert result is not None, "Expected a valid result, got None"
    assert result['name'] == "mcp_run_shell_command", f"Expected name: mcp_run_shell_command, Got: {result['name']}"
    assert result['arguments'] == {"command": "pwd", "timeout": "5"}, f"Expected arguments don't match"
    
    print("✅ Real-world example 1 test PASSED!")

def test_real_world_example_2():
    """Test with another real-world example from the logs."""
    print("\nTesting real-world example 2:")
    
    # Another real-world example from the logs
    test_input = """<TOOL_SENTINEL><name>mcp_run_shell_command</name>
<arguments>{
  "command": "ls -la",
  "timeout": "5"
}</arguments></TOOL_SENTINEL>"""
    
    # Parse the tool call
    result = improved_parse_tool_call(test_input)
    
    # Check if the tool was correctly parsed
    assert result is not None, "Expected a valid result, got None"
    assert result['name'] == "mcp_run_shell_command", f"Expected name: mcp_run_shell_command, Got: {result['name']}"
    assert result['arguments'] == {"command": "ls -la", "timeout": "5"}, f"Expected arguments don't match"
    
    print("✅ Real-world example 2 test PASSED!")

def test_real_world_example_3():
    """Test with a real-world example with <name> tag format."""
    print("\nTesting real-world example 3 with <name> tag format:")
    
    # Real-world example with <name> tag format
    test_input = """<TOOL_SENTINEL><name>mcp_run_shell_command</name>
<arguments>{
  "command": "find . -maxdepth 2 -type d | head -20",
  "timeout": "5"
}</arguments></TOOL_SENTINEL>"""
    
    # Parse the tool call
    result = improved_parse_tool_call(test_input)
    
    # Check if the tool was correctly parsed
    assert result is not None, "Expected a valid result, got None"
    assert result['name'] == "mcp_run_shell_command", f"Expected name: mcp_run_shell_command, Got: {result['name']}"
    assert result['arguments'] == {"command": "find . -maxdepth 2 -type d | head -20", "timeout": "5"}, f"Expected arguments don't match"
    
    print("✅ Real-world example 3 with <name> tag format test PASSED!")

def test_real_world_example_4():
    """Test with a real-world example with malformed tag format."""
    print("\nTesting real-world example 4 with malformed tag format:")
    
    # Real-world example with malformed tag format
    test_input = """<TOOL_SENTINEL>
<name>mcp_run_shell_command</name>
<arguments>{
  "command": "du -h --max-depth=1 | sort -hr",
  "timeout": "5"
}</arguments>
</TOOL_SENTINEL>"""
    
    # Parse the tool call
    result = improved_parse_tool_call(test_input)
    
    # Check if the tool was correctly parsed
    assert result is not None, "Expected a valid result, got None"
    assert result['name'] == "mcp_run_shell_command", f"Expected name: mcp_run_shell_command, Got: {result['name']}"
    assert result['arguments'] == {"command": "du -h --max-depth=1 | sort -hr", "timeout": "5"}, f"Expected arguments don't match"
    
    print("✅ Real-world example 4 with malformed tag format test PASSED!")

def test_real_world_example_5():
    """Test with a real-world example with incomplete tag format."""
    print("\nTesting real-world example 5 with incomplete tag format:")
    
    # Real-world example with incomplete tag format
    test_input = """<TOOL_SENTINEL>
<name>mcp_run_
