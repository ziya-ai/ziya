#!/usr/bin/env python3
"""
Comprehensive test for improved_parse_tool_call with both tag formats and real-world examples.
"""

import sys
import os
import re

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import the updated version
from app.mcp_fixes_updated import improved_parse_tool_call, clean_sentinels

def test_n_tag_format():
    """Test with <n> tag format."""
    print("\nTesting <n> tag format:")
    
    # Create a test input with <n> tags
    test_input = "<TOOL_SENTINEL><n>test_tool</n><arguments>{\"arg1\": \"value1\"}</arguments></TOOL_SENTINEL>"
    
    # Parse the tool call
    result = improved_parse_tool_call(test_input)
    
    # Check if the tool was correctly parsed
    assert result is not None, "Expected a valid result, got None"
    assert result['name'] == "test_tool", f"Expected name: test_tool, Got: {result['name']}"
    assert result['arguments'] == {"arg1": "value1"}, f"Expected arguments don't match"
    
    print("✅ <n> tag format test PASSED!")

def test_name_tag_format():
    """Test with <name> tag format."""
    print("\nTesting <name> tag format:")
    
    # Create a test input with <name> tags
    test_input = "<TOOL_SENTINEL><name>test_tool</name><arguments>{\"arg1\": \"value1\"}</arguments></TOOL_SENTINEL>"
    
    # Parse the tool call
    result = improved_parse_tool_call(test_input)
    
    # Check if the tool was correctly parsed
    assert result is not None, "Expected a valid result, got None"
    assert result['name'] == "test_tool", f"Expected name: test_tool, Got: {result['name']}"
    assert result['arguments'] == {"arg1": "value1"}, f"Expected arguments don't match"
    
    print("✅ <name> tag format test PASSED!")

def test_real_world_example():
    """Test with a real-world example."""
    print("\nTesting real-world example:")
    
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
    
    print("✅ Real-world example test PASSED!")

def test_malformed_tool_call():
    """Test malformed tool calls."""
    print("\nTesting malformed tool calls:")
    
    # Test case 1: Incomplete tool call (missing end tag)
    test_input_1 = "<TOOL_SENTINEL><name>test_tool</name><arguments>{\"arg1\": \"value1\"}"
    result_1 = improved_parse_tool_call(test_input_1)
    assert result_1 is None, "Expected None for incomplete tool call"
    
    # Test case 2: Missing arguments tag
    test_input_2 = "<TOOL_SENTINEL><name>test_tool</name></TOOL_SENTINEL>"
    result_2 = improved_parse_tool_call(test_input_2)
    assert result_2 is not None, "Expected a result even with missing arguments"
    assert result_2['name'] == "test_tool", f"Expected name: test_tool, Got: {result_2['name']}"
    assert result_2['arguments'] == {}, f"Expected empty arguments, Got: {result_2['arguments']}"
    
    # Test case 3: Malformed JSON in arguments
    test_input_3 = "<TOOL_SENTINEL><name>test_tool</name><arguments>arg1: value1}</arguments></TOOL_SENTINEL>"
    result_3 = improved_parse_tool_call(test_input_3)
    assert result_3 is not None, "Expected a result even with malformed JSON"
    assert result_3['name'] == "test_tool", f"Expected name: test_tool, Got: {result_3['name']}"
    assert isinstance(result_3['arguments'], dict), f"Expected arguments to be a dict, Got: {type(result_3['arguments'])}"
    
    print("✅ Malformed tool calls test PASSED!")

def test_sentinel_leakage():
    """Test cleaning of sentinel leakage."""
    print("\nTesting sentinel leakage cleanup:")
    
    # Test case: Response with leaking sentinels
    test_input = """
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
    
    # Clean the sentinels
    cleaned = clean_sentinels(test_input)
    
    # Check if all sentinel tags were removed
    assert "<TOOL_SENTINEL>" not in cleaned, "Sentinel tags still present in cleaned output"
    assert "</TOOL_SENTINEL>" not in cleaned, "Sentinel tags still present in cleaned output"
    assert "<name>" not in cleaned, "Name tags still present in cleaned output"
    assert "</name>" not in cleaned, "Name tags still present in cleaned output"
    assert "<arguments>" not in cleaned, "Argument tags still present in cleaned output"
    assert "</arguments>" not in cleaned, "Argument tags still present in cleaned output"
    
    # Check if the code block is still intact
    assert "```python" in cleaned, "Python code block should be preserved"
    assert "HTTPServer" in cleaned, "Code content should be preserved"
    
    print("✅ Sentinel leakage cleanup test PASSED!")

def test_direct_regex_patterns():
    """Test regex patterns directly."""
    print("\nTesting regex patterns directly:")
    
    # Define the regex patterns
    n_pattern = r'<n>\s*([^<]+?)\s*</n>'
    name_pattern = r'<name>\s*([^<]+?)\s*</name>'
    
    # Test with <n> tag format
    n_tag_input = "<n>test_tool</n>"
    n_match = re.search(n_pattern, n_tag_input)
    assert n_match is not None, "Expected <n> pattern to match <n> tag format"
    assert n_match.group(1) == "test_tool", f"Expected 'test_tool', Got: {n_match.group(1)}"
    
    # Test with <name> tag format
    name_tag_input = "<name>test_tool</name>"
    name_match = re.search(name_pattern, name_tag_input)
    assert name_match is not None, "Expected <name> pattern to match <name> tag format"
    assert name_match.group(1) == "test_tool", f"Expected 'test_tool', Got: {name_match.group(1)}"
    
    print("✅ Direct regex patterns test PASSED!")

if __name__ == "__main__":
    test_n_tag_format()
    test_name_tag_format()
    test_real_world_example()
    test_malformed_tool_call()
    test_sentinel_leakage()
    test_direct_regex_patterns()
    print("\nAll tests passed!")
