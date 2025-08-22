#!/usr/bin/env python3
"""
Test the MCP fixes with both <n> and <name> tag formats.
"""

# Set AWS profile to ziya - MUST be before any imports
import os
os.environ["AWS_PROFILE"] = "ziya"
os.environ["AWS_DEFAULT_PROFILE"] = "ziya"
print(f"Using AWS profile: {os.environ.get('AWS_PROFILE')}")

import sys
import re

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import the updated version
from app.mcp_fixes_updated import improved_parse_tool_call, clean_sentinels

def test_n_tag_format():
    """Test the improved_parse_tool_call function with <n> tag format."""
    print("Testing <n> tag format...")
    
    # Test case: Valid tool call with <n> tags
    test_input = "<TOOL_SENTINEL><n>test_tool</n><arguments>{\"arg1\": \"value1\"}</arguments></TOOL_SENTINEL>"
    result = improved_parse_tool_call(test_input)
    assert result is not None, "Expected a valid result, got None"
    assert result['name'] == "test_tool", f"Expected name: test_tool, Got: {result['name']}"
    assert result['arguments'] == {"arg1": "value1"}, f"Expected arguments: {{\"arg1\": \"value1\"}}, Got: {result['arguments']}"
    
    print("<n> tag format test passed!")

def test_name_tag_format():
    """Test the improved_parse_tool_call function with <name> tag format."""
    print("Testing <name> tag format...")
    
    # Test case: Valid tool call with <name> tags
    test_input = "<TOOL_SENTINEL><name>mcp_run_shell_command</name><arguments>{\"command\": \"pwd\"}</arguments></TOOL_SENTINEL>"
    result = improved_parse_tool_call(test_input)
    assert result is not None, "Expected a valid result, got None"
    assert result['name'] == "mcp_run_shell_command", f"Expected name: mcp_run_shell_command, Got: {result['name']}"
    assert result['arguments'] == {"command": "pwd"}, f"Expected arguments: {{\"command\": \"pwd\"}}, Got: {result['arguments']}"
    
    print("<name> tag format test passed!")

def test_clean_sentinels_with_n_tags():
    """Test the clean_sentinels function with <n> tags."""
    print("Testing clean_sentinels with <n> tags...")
    
    # Test case: Complete tool call with <n> tags
    test_input = "This is a test <TOOL_SENTINEL><n>tool_name</n><arguments>{}</arguments></TOOL_SENTINEL> with a tool call."
    expected_output = "This is a test  with a tool call."
    actual_output = clean_sentinels(test_input)
    assert actual_output == expected_output, f"Expected: {expected_output}, Got: {actual_output}"
    
    print("clean_sentinels with <n> tags test passed!")

def test_clean_sentinels_with_name_tags():
    """Test the clean_sentinels function with <name> tags."""
    print("Testing clean_sentinels with <name> tags...")
    
    # Test case: Complete tool call with <name> tags
    test_input = "This is a test <TOOL_SENTINEL><name>tool_name</name><arguments>{}</arguments></TOOL_SENTINEL> with a tool call."
    expected_output = "This is a test  with a tool call."
    actual_output = clean_sentinels(test_input)
    assert actual_output == expected_output, f"Expected: {expected_output}, Got: {actual_output}"
    
    print("clean_sentinels with <name> tags test passed!")

if __name__ == "__main__":
    test_n_tag_format()
    test_name_tag_format()
    test_clean_sentinels_with_n_tags()
    test_clean_sentinels_with_name_tags()
    print("All tests passed!")
