#!/usr/bin/env python3
"""
Test specifically for <name> tag format recognition.
"""

import sys
import os
import re

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import the updated version
from app.mcp_fixes_updated import improved_parse_tool_call

def test_name_tag_recognition():
    """Test that <name> tag format is correctly recognized."""
    print("\nTesting <name> tag format recognition...")
    
    # Create a test input with <name> tags - explicitly written out
    test_input = "<TOOL_SENTINEL><name>test_tool</name><arguments>{\"arg1\": \"value1\"}</arguments></TOOL_SENTINEL>"
    
    # Print the test input for debugging
    print(f"Test input: {test_input}")
    
    # Parse the tool call
    result = improved_parse_tool_call(test_input)
    
    # Print the result for debugging
    print(f"Result: {result}")
    
    # Check if the tool was correctly parsed
    assert result is not None, "Expected a valid result, got None"
    assert result['name'] == "test_tool", f"Expected name: test_tool, Got: {result['name']}"
    assert result['arguments'] == {"arg1": "value1"}, f"Expected arguments don't match"
    
    print("✅ <name> tag format recognition test PASSED!")

if __name__ == "__main__":
    test_name_tag_recognition()
    print("\nAll tests passed!")
