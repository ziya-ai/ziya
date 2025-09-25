#!/usr/bin/env python3
"""
Test the MCP fixes with mixed and malformed tag formats.
"""

import sys
import os
import re

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import the updated version
from app.mcp_fixes_updated import improved_parse_tool_call, clean_sentinels, StreamingToolProcessor

def test_mixed_format():
    """Test a response with both <n> and <name> tag formats."""
    print("\nTesting mixed tag formats...")
    
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
    
    print("✅ Mixed tag formats test PASSED!")

def test_malformed_tool_call():
    """Test malformed tool calls."""
    print("\nTesting malformed tool calls...")
    
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

def test_streaming_fragments():
    """Test tool calls split across streaming fragments."""
    print("\nTesting streaming fragments...")
    
    # Create a streaming processor
    processor = StreamingToolProcessor()
    
    # Test with fragments that form a complete tool call when combined
    fragments = [
        "Let me check the current ",
        "time for you:\n\n<TOOL_",
        "SENTINEL><name>mcp_get_current_",
        "time</name><arguments>{}</arguments></TOOL_SENTINEL>\n\n",
        "Now I'll analyze the result."
    ]
    
    # Process each fragment
    tool_info = None
    for fragment in fragments:
        cleaned_chunk, result = processor.process_chunk(fragment)
        if result:
            tool_info = result
    
    # Check if the tool was correctly parsed
    assert tool_info is not None, "Expected a tool info result"
    assert tool_info['name'] == "mcp_get_current_time", f"Expected name: mcp_get_current_time, Got: {tool_info['name']}"
    
    print("✅ Streaming fragments test PASSED!")

def test_sentinel_leakage():
    """Test cleaning of sentinel leakage."""
    print("\nTesting sentinel leakage cleanup...")
    
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

def test_real_world_example():
    """Test with a real-world example from the logs."""
    print("\nTesting real-world example from logs...")
    
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

if __name__ == "__main__":
    test_mixed_format()
    test_malformed_tool_call()
    test_streaming_fragments()
    test_sentinel_leakage()
    test_real_world_example()
    print("\nAll tests passed!")
