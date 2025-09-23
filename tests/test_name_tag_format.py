#!/usr/bin/env python3
"""
Test the MCP fixes with <name> tag format.
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
from app.mcp_fixes_updated import improved_parse_tool_call

def test_name_tag_format():
    """Test the improved_parse_tool_call function with <name> tag format."""
    print("Testing <name> tag format...")
    
    # Test case: Valid tool call with <name> tags
    test_input = "<TOOL_SENTINEL><name>mcp_run_shell_command</name><arguments>{\"command\": \"pwd\"}</arguments></TOOL_SENTINEL>"
    result = improved_parse_tool_call(test_input)
    
    print(f"Result: {result}")
    
    assert result is not None, "Expected a valid result, got None"
    assert result['name'] == "mcp_run_shell_command", f"Expected name: mcp_run_shell_command, Got: {result['name']}"
    assert result['arguments'] == {"command": "pwd"}, f"Expected arguments: {{\"command\": \"pwd\"}}, Got: {result['arguments']}"
    
    print("<name> tag format test passed!")

if __name__ == "__main__":
    test_name_tag_format()
    print("All tests passed!")
