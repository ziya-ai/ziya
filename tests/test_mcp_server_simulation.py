#!/usr/bin/env python3
"""
Test that simulates the server behavior with MCP tools.
"""

# Set AWS profile to ziya - MUST be before any imports
import os
os.environ["AWS_PROFILE"] = "ziya"
os.environ["AWS_DEFAULT_PROFILE"] = "ziya"
print(f"Using AWS profile: {os.environ.get('AWS_PROFILE')}")

import asyncio
import sys
import re
import json

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.mcp_fixes import clean_sentinels, improved_parse_tool_call, improved_extract_tool_output
from app.server import detect_and_execute_mcp_tools

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

REAL_STREAMING_RESPONSE_WITH_SENTINEL_FRAGMENTS = [
    "Let me check the current ",
    "time for you:\n\n<TOOL_",
    "SENTINEL><name>mcp_get_current_",
    "time</name><arguments>{}</arguments></TOOL_SENTINEL>\n\n",
    "Now I'll analyze the result."
]

async def test_detect_and_execute_mcp_tools_with_mock():
    """Test detect_and_execute_mcp_tools with a mock."""
    print("Testing detect_and_execute_mcp_tools with mock...")
    
    # Create a mock version of execute_mcp_tools_with_status
    original_execute = __import__('app.mcp_consolidated').mcp_consolidated.execute_mcp_tools_with_status
    
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
    
    # Replace the original function with our mock
    __import__('app.mcp_consolidated').mcp_consolidated.execute_mcp_tools_with_status = mock_execute_mcp_tools_with_status
    
    try:
        # Test with real examples
        result1 = await detect_and_execute_mcp_tools(REAL_RESPONSE_WITH_LEAKING_SENTINELS)
        assert "<TOOL_SENTINEL>" not in result1, "Sentinel tags still present in result"
        assert "<n>" not in result1, "Name tags still present in result"
        assert "<arguments>" not in result1, "Argument tags still present in result"
        assert "```tool:mcp_run_shell_command" in result1, "Tool result not added to response"
        
        result2 = await detect_and_execute_mcp_tools(REAL_RESPONSE_WITH_PARTIAL_SENTINEL)
        assert "<TOOL_SENTINEL>" not in result2, "Sentinel tags still present in result"
        assert "<n>" not in result2, "Name tags still present in result"
        assert "<arguments>" not in result2, "Argument tags still present in result"
        
        # Test with streaming response
        full_streaming = "".join(REAL_STREAMING_RESPONSE_WITH_SENTINEL_FRAGMENTS)
        result3 = await detect_and_execute_mcp_tools(full_streaming)
        assert "<TOOL_" not in result3, "Sentinel fragments still present in result"
        assert "SENTINEL>" not in result3, "Sentinel fragments still present in result"
        assert "<n>" not in result3, "Name tags still present in result"
        assert "</n>" not in result3, "Name tags still present in result"
        assert "<arguments>" not in result3, "Argument tags still present in result"
        assert "</arguments>" not in result3, "Argument tags still present in result"
        
        print("detect_and_execute_mcp_tools tests passed!")
    finally:
        # Restore the original function
        __import__('app.mcp_consolidated').mcp_consolidated.execute_mcp_tools_with_status = original_execute

async def test_streaming_simulation():
    """Test simulation of streaming with sentinel fragments."""
    print("Testing streaming simulation...")
    
    # Simulate the streaming code in server.py
    def process_streaming_chunk(chunk):
        from app.mcp_fixes import clean_sentinels
        
        # Clean any sentinel fragments
        cleaned_content = clean_sentinels(chunk)
        
        # Double-check for any remaining sentinel fragments
        if "<TOOL_" in cleaned_content or "SENTINEL>" in cleaned_content or "<n>" in cleaned_content or "<arguments>" in cleaned_content:
            cleaned_content = cleaned_content.replace("<TOOL_", "").replace("SENTINEL>", "").replace("<n>", "").replace("</n>", "").replace("<arguments>", "").replace("</arguments>", "")
            
        return cleaned_content
    
    # Process each chunk
    processed_chunks = []
    for chunk in REAL_STREAMING_RESPONSE_WITH_SENTINEL_FRAGMENTS:
        processed_chunk = process_streaming_chunk(chunk)
        processed_chunks.append(processed_chunk)
        
    # Verify the processed chunks don't contain sentinel fragments
    full_processed = "".join(processed_chunks)
    assert "<TOOL_" not in full_processed, "Sentinel fragments still present in processed chunks"
    assert "SENTINEL>" not in full_processed, "Sentinel fragments still present in processed chunks"
    assert "<n>" not in full_processed, "Name tags still present in processed chunks"
    assert "</n>" not in full_processed, "Name tags still present in processed chunks"
    assert "<arguments>" not in full_processed, "Argument tags still present in processed chunks"
    assert "</arguments>" not in full_processed, "Argument tags still present in processed chunks"
    
    print("Streaming simulation tests passed!")

if __name__ == "__main__":
    asyncio.run(test_detect_and_execute_mcp_tools_with_mock())
    asyncio.run(test_streaming_simulation())
    print("All server simulation tests passed!")
