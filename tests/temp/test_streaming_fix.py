#!/usr/bin/env python3
"""
Test the streaming tool call detection fix
"""

import asyncio
import json
import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.middleware.streaming import StreamingMiddleware

async def test_streaming_fix():
    """Test that the fix preserves streaming while catching tool calls"""
    
    middleware = StreamingMiddleware(None)
    
    # Test 1: Normal text should stream immediately
    print("Test 1: Normal text streaming")
    async def normal_text_chunks():
        yield "Hello"
        yield " world"
        yield "!"
    
    results = []
    async for chunk in middleware.safe_stream(normal_text_chunks()):
        results.append(chunk)
        print(f"Streamed: {chunk.strip()}")
    
    # Should get immediate streaming
    assert len(results) == 4  # 3 text chunks + [DONE]
    
    # Test 2: Tool call should be detected and structured
    print("\nTest 2: Tool call detection")
    async def tool_call_chunks():
        yield "<mcp_run_shell_command>"
        yield "\n<command>ls</command>"
        yield "\n</mcp_run_shell_command>"
    
    results = []
    async for chunk in middleware.safe_stream(tool_call_chunks()):
        results.append(chunk)
        print(f"Streamed: {chunk.strip()}")
    
    # Should detect tool call
    tool_call_found = any('tool_call' in chunk for chunk in results)
    print(f"Tool call detected: {tool_call_found}")
    
    # Test 3: Mixed content
    print("\nTest 3: Mixed content")
    async def mixed_chunks():
        yield "Here's some text"
        yield " and then "
        yield "<mcp_get_current_time></mcp_get_current_time>"
        yield " and more text"
    
    results = []
    async for chunk in middleware.safe_stream(mixed_chunks()):
        results.append(chunk)
        print(f"Streamed: {chunk.strip()}")
    
    print(f"\nTotal chunks in mixed test: {len(results)}")

if __name__ == "__main__":
    asyncio.run(test_streaming_fix())
