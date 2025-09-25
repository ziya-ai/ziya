#!/usr/bin/env python3
"""
Realistic tests for MCP fixes that reproduce production behavior.
"""

# Set AWS profile to ziya - MUST be before any imports
import os
os.environ["AWS_PROFILE"] = "ziya"
os.environ["AWS_DEFAULT_PROFILE"] = "ziya"
print(f"Using AWS profile: {os.environ.get('AWS_PROFILE')}")

import pytest
import asyncio
import sys
import re
import json

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.mcp_fixes import clean_sentinels, improved_parse_tool_call, improved_extract_tool_output
from app.mcp_consolidated import execute_mcp_tools_with_status

# Real-world examples of problematic responses
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

REAL_RESPONSE_WITH_PARTIAL_SENTINEL = """
To check the current time, I'll use a tool:

<TOOL_SENTINEL><name>mcp_get_current_time</name>
<arguments>{}

Let me know if you need any other information!
"""

REAL_RESPONSE_WITH_EMPTY_TOOL_RESULT = """
Let me check the current directory:

<TOOL_SENTINEL><name>mcp_run_shell_command</name>
<arguments>{"command": "ls -la"}</arguments></TOOL_SENTINEL>

Now I'll analyze the output.
"""

REAL_STREAMING_RESPONSE_WITH_SENTINEL_FRAGMENTS = [
    "Let me check the current ",
    "time for you:\n\n<TOOL_",
    "SENTINEL><name>mcp_get_current_",
    "time</name><arguments>{}</arguments></TOOL_SENTINEL>\n\n",
    "Now I'll analyze the result."
]

def test_clean_sentinels_with_real_examples():
    """Test clean_sentinels with real-world examples."""
    print("Testing clean_sentinels with real-world examples...")
    
    # Test case 1: Complete tool call in real response
    cleaned = clean_sentinels(REAL_RESPONSE_WITH_LEAKING_SENTINELS)
    assert "<TOOL_SENTINEL>" not in cleaned, "Sentinel tags still present in cleaned output"
    assert "</TOOL_SENTINEL>" not in cleaned, "Sentinel tags still present in cleaned output"
    assert "<n>" not in cleaned, "Name tags still present in cleaned output"
    assert "<arguments>" not in cleaned, "Argument tags still present in cleaned output"
    
    # Test case 2: Partial sentinel in real response
    cleaned = clean_sentinels(REAL_RESPONSE_WITH_PARTIAL_SENTINEL)
    assert "<TOOL_SENTINEL>" not in cleaned, "Sentinel tags still present in cleaned output"
    assert "<n>" not in cleaned, "Name tags still present in cleaned output"
    assert "<arguments>" not in cleaned, "Argument tags still present in cleaned output"
    
    # Test case 3: Streaming response with sentinel fragments
    full_streaming = "".join(REAL_STREAMING_RESPONSE_WITH_SENTINEL_FRAGMENTS)
    cleaned = clean_sentinels(full_streaming)
    assert "<TOOL_" not in cleaned, "Sentinel fragments still present in cleaned output"
    assert "SENTINEL>" not in cleaned, "Sentinel fragments still present in cleaned output"
    assert "<n>" not in cleaned, "Name tags still present in cleaned output"
    assert "</n>" not in cleaned, "Name tags still present in cleaned output"
    assert "<arguments>" not in cleaned, "Argument tags still present in cleaned output"
    assert "</arguments>" not in cleaned, "Argument tags still present in cleaned output"
    
    print("clean_sentinels tests with real examples passed!")

def test_improved_parse_tool_call_with_real_examples():
    """Test improved_parse_tool_call with real-world examples."""
    print("Testing improved_parse_tool_call with real-world examples...")
    
    # Test case 1: Complete tool call in real response
    result = improved_parse_tool_call(REAL_RESPONSE_WITH_LEAKING_SENTINELS)
    assert result is not None, "Failed to parse tool call in real response"
    assert result['name'] == "mcp_run_shell_command", f"Expected name: mcp_run_shell_command, Got: {result.get('name')}"
    assert result['arguments'] == {"command": "echo 'Hello from shell command'"}, f"Arguments don't match expected"
    
    # Test case 2: Streaming response with complete tool call
    full_streaming = "".join(REAL_STREAMING_RESPONSE_WITH_SENTINEL_FRAGMENTS)
    result = improved_parse_tool_call(full_streaming)
    assert result is not None, "Failed to parse tool call in streaming response"
    assert result['name'] == "mcp_get_current_time", f"Expected name: mcp_get_current_time, Got: {result.get('name')}"
    assert result['arguments'] == {}, f"Expected empty arguments, Got: {result.get('arguments')}"
    
    print("improved_parse_tool_call tests with real examples passed!")

def test_streaming_chunk_processing():
    """Test processing of streaming chunks with sentinel fragments."""
    print("Testing streaming chunk processing...")
    
    from app.mcp_fixes import StreamingToolProcessor
    
    processor = StreamingToolProcessor()
    buffer_content = ""
    
    # Process each chunk
    for chunk in REAL_STREAMING_RESPONSE_WITH_SENTINEL_FRAGMENTS:
        processed_chunk, tool_info = processor.process_chunk(chunk)
        buffer_content += processed_chunk
        
        # If we have a complete tool call, verify it
        if tool_info:
            assert tool_info['name'] == "mcp_get_current_time", f"Expected name: mcp_get_current_time, Got: {tool_info.get('name')}"
            assert tool_info['arguments'] == {}, f"Expected empty arguments, Got: {tool_info.get('arguments')}"
    
    # Verify the final buffer doesn't contain sentinel fragments
    assert "<TOOL_" not in buffer_content, "Sentinel fragments still present in buffer"
    assert "SENTINEL>" not in buffer_content, "Sentinel fragments still present in buffer"
    assert "<n>" not in buffer_content, "Name tags still present in buffer"
    assert "</n>" not in buffer_content, "Name tags still present in buffer"
    assert "<arguments>" not in buffer_content, "Argument tags still present in buffer"
    assert "</arguments>" not in buffer_content, "Argument tags still present in buffer"
    
    print("Streaming chunk processing tests passed!")

async def test_execute_mcp_tools_with_status_mock():
    """Test execute_mcp_tools_with_status with mocked execution."""
    print("Testing execute_mcp_tools_with_status with mocked execution...")
    
    # Create a mock version of the function that doesn't actually execute tools
    async def mock_execute_mcp_tools_with_status(response):
        from app.mcp_fixes import clean_sentinels
        
        # Just clean the sentinels and return a mock result
        cleaned = clean_sentinels(response)
        
        # If it contained a tool call, add a mock result
        if "<TOOL_SENTINEL>" in response:
            if "mcp_get_current_time" in response:
                return cleaned + "\n\n```tool:mcp_get_current_time\n🔐 SECURE\n2023-07-05 12:34:56\n```"
            elif "mcp_run_shell_command" in response:
                return cleaned + "\n\n```tool:mcp_run_shell_command\n🔐 SECURE\nCommand output\n```"
            else:
                return cleaned + "\n\n```tool:unknown\n🔐 SECURE\nTool executed\n```"
        
        return cleaned
    
    # Test with real examples
    result1 = await mock_execute_mcp_tools_with_status(REAL_RESPONSE_WITH_LEAKING_SENTINELS)
    assert "<TOOL_SENTINEL>" not in result1, "Sentinel tags still present in result"
    assert "```tool:mcp_run_shell_command" in result1, "Tool result not added to response"
    
    result2 = await mock_execute_mcp_tools_with_status(REAL_RESPONSE_WITH_PARTIAL_SENTINEL)
    assert "<TOOL_SENTINEL>" not in result2, "Sentinel tags still present in result"
    
    print("execute_mcp_tools_with_status mock tests passed!")

if __name__ == "__main__":
    test_clean_sentinels_with_real_examples()
    test_improved_parse_tool_call_with_real_examples()
    test_streaming_chunk_processing()
    asyncio.run(test_execute_mcp_tools_with_status_mock())
    print("All realistic tests passed!")
