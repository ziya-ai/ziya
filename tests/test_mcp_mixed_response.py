#!/usr/bin/env python3
"""
Test for mixed tag formats in the same response.
"""

import sys
import os
import re

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import the updated version
from app.mcp_fixes_updated import improved_parse_tool_call, find_and_execute_all_tools

def test_mixed_response():
    """Test a response with both <n> and <name> tag formats."""
    print("\nTesting mixed tag formats in the same response:")
    
    # Create a test input with both tag formats
    test_input = """
Here's a response with multiple tool calls:

<TOOL_SENTINEL><n>test_tool_1</n><arguments>{"arg1": "value1"}</arguments></TOOL_SENTINEL>

And another one:

<TOOL_SENTINEL><name>test_tool_2</name><arguments>{"arg2": "value2"}</arguments></TOOL_SENTINEL>
"""
    
    # Find all tool calls
    tool_calls = []
    start_idx = 0
    while True:
        start_idx = test_input.find("<TOOL_SENTINEL>", start_idx)
        if start_idx == -1:
            break
        
        end_idx = test_input.find("</TOOL_SENTINEL>", start_idx)
        if end_idx == -1:
            break
        
        # Extract the complete tool call
        end_idx += len("</TOOL_SENTINEL>")
        tool_call = test_input[start_idx:end_idx]
        tool_calls.append(tool_call)
        
        # Move past this tool call
        start_idx = end_idx
    
    # Parse each tool call
    results = []
    for tool_call in tool_calls:
        result = improved_parse_tool_call(tool_call)
        results.append(result)
    
    # Check results
    assert len(results) == 2, f"Expected 2 results, got {len(results)}"
    assert results[0]['name'] == "test_tool_1", f"Expected name: test_tool_1, Got: {results[0]['name']}"
    assert results[1]['name'] == "test_tool_2", f"Expected name: test_tool_2, Got: {results[1]['name']}"
    
    print("✅ Mixed tag formats in the same response test PASSED!")

if __name__ == "__main__":
    test_mixed_response()
    print("\nAll tests passed!")
