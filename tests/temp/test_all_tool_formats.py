#!/usr/bin/env python3
"""
Test all tool call formats that LLMs might use
"""

import asyncio
import json
import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.middleware.streaming import StreamingMiddleware

async def test_tool_format(name, chunks):
    """Test a specific tool format"""
    print(f"\n=== Testing {name} ===")
    
    middleware = StreamingMiddleware(None)
    
    async def chunk_generator():
        for chunk in chunks:
            yield chunk
    
    results = []
    tool_detected = False
    
    async for chunk in middleware.safe_stream(chunk_generator()):
        results.append(chunk)
        if 'tool_call' in chunk:
            tool_detected = True
            print(f"✅ Tool detected: {chunk[:100]}...")
        else:
            print(f"   Chunk: {chunk.strip()[:50]}...")
    
    print(f"Result: {'✅ DETECTED' if tool_detected else '❌ MISSED'}")
    return tool_detected

async def test_all_formats():
    """Test all known tool call formats"""
    
    formats = [
        # Standard MCP format
        ("MCP Shell", ["<mcp_run_shell_command>", "<command>ls</command>", "</mcp_run_shell_command>"]),
        
        # TOOL_SENTINEL format
        ("TOOL_SENTINEL", ["<TOOL_SENTINEL>", "<n>mcp_run_shell_command</n>", "<arguments><command>ls</command></arguments>", "</TOOL_SENTINEL>"]),
        
        # Alternative TOOL_SENTINEL with <name>
        ("TOOL_SENTINEL name", ["<TOOL_SENTINEL>", "<name>get_current_time</name>", "</TOOL_SENTINEL>"]),
        
        # Invoke format
        ("Invoke format", ["<invoke name=\"run_shell_command\">", "<parameter name=\"command\">ls</parameter>", "</invoke>"]),
        
        # Direct tool names
        ("Direct get_current_time", ["<get_current_time>", "</get_current_time>"]),
        ("Direct run_shell_command", ["<run_shell_command>", "<command>pwd</command>", "</run_shell_command>"]),
        
        # Fragmented (like the original issue)
        ("Fragmented MCP", ["<m", "cp_run_shell_command>", "\n<arguments", ">", "\n<comman", "d>grep", " -n test</command>", "\n</arguments>\n</mcp", "_run_shell_command>"]),
        
        # Mixed with text
        ("Mixed content", ["Let me check ", "<mcp_get_current_time></mcp_get_current_time>", " for you."]),
    ]
    
    results = {}
    for name, chunks in formats:
        detected = await test_tool_format(name, chunks)
        results[name] = detected
    
    print(f"\n=== SUMMARY ===")
    for name, detected in results.items():
        status = "✅" if detected else "❌"
        print(f"{status} {name}")
    
    total = len(results)
    passed = sum(results.values())
    print(f"\nPassed: {passed}/{total}")

if __name__ == "__main__":
    asyncio.run(test_all_formats())
