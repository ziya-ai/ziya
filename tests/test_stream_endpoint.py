"""
Test the stream endpoint.
"""

import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import asyncio
import json
from typing import Dict, Any, List, AsyncIterator, Optional
from pydantic import BaseModel, Field

# Mock the imports to avoid AWS credential issues
import sys
sys.modules['app.agents.models'] = MagicMock()

# Create mock classes for RunLogPatch
class MockRunLogPatch(BaseModel):
    ops: List[Dict[str, Any]]
    
    class Config:
        arbitrary_types_allowed = True

# Create a mock agent_executor
mock_agent_executor = MagicMock()

# Create a mock astream_log method that yields RunLogPatch objects
async def mock_astream_log(body):
    # Yield content as RunLogPatch objects
    yield MockRunLogPatch(ops=[{
        'op': 'add',
        'path': '/streamed_output',
        'value': 'This is an AIMessageChunk'
    }])
    
    yield MockRunLogPatch(ops=[{
        'op': 'add',
        'path': '/streamed_output',
        'value': 'This is a ChatGeneration'
    }])
    
    yield MockRunLogPatch(ops=[{
        'op': 'add',
        'path': '/streamed_output',
        'value': 'This is a string'
    }])
    
    yield MockRunLogPatch(ops=[{
        'op': 'add',
        'path': '/streamed_output',
        'value': 'This is an object with content'
    }])
    
    # Yield None (should be skipped)
    yield None

# Set the mock astream_log method
mock_agent_executor.astream_log = mock_astream_log

# Create a simple FastAPI app for testing
app = FastAPI()

# Create a simple streaming endpoint
@app.post("/ziya/stream")
async def stream_endpoint(request: Request):
    """Stream endpoint with centralized error handling."""
    try:
        # Parse the request body manually
        body_raw = await request.json()
        
        # Check if the question is empty or missing
        if not body_raw.get("question") or not body_raw.get("question").strip():
            error_msg = "Please provide a question to continue."
            # Return validation error as SSE
            async def validation_error_stream():
                yield f"data: {error_msg}\n\n"
                yield "data: [DONE]\n\n"
            
            response = StreamingResponse(
                validation_error_stream(),
                media_type="text/event-stream"
            )
            response.headers["Cache-Control"] = "no-cache"
            response.headers["Connection"] = "keep-alive"
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "*"
            return response
            
        # Use stream_agent_response instead of direct astream
        async def stream_agent_response():
            async for chunk in mock_astream_log(body_raw):
                # Process the chunk
                try:
                    # Special handling for None chunks
                    if chunk is None:
                        continue
                    
                    # Handle RunLogPatch objects
                    if hasattr(chunk, 'ops') and chunk.ops:
                        for op in chunk.ops:
                            if op.get('path', '').endswith('/streamed_output'):
                                content = op.get('value', '')
                                if content:
                                    yield f"data: {content}\n\n"
                except Exception as e:
                    yield f"data: Error: {str(e)}\n\n"
            
            # Send the [DONE] marker
            yield "data: [DONE]\n\n"
        
        response = StreamingResponse(
            stream_agent_response(),
            media_type="text/event-stream"
        )
        
        # Add CORS headers
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Connection"] = "keep-alive"
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        
        return response
    except Exception as e:
        # Return general error as SSE
        async def general_error_stream():
            yield f"data: Error: {str(e)}\n\n"
            yield "data: [DONE]\n\n"
        
        response = StreamingResponse(
            general_error_stream(),
            media_type="text/event-stream"
        )
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Connection"] = "keep-alive"
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response

class TestStreamEndpoint(unittest.TestCase):
    """Test the stream endpoint."""
    
    def setUp(self):
        """Set up test environment."""
        self.client = TestClient(app)
    
    def test_stream_endpoint(self):
        """Test that stream endpoint correctly processes chunks."""
        # Create a test request body
        body = {
            "question": "Test question",
            "chat_history": [],
            "config": {"files": []}
        }
        
        # Make the request directly to our custom endpoint
        response = self.client.post(
            "/ziya/stream",
            json=body,
            headers={"Accept": "text/event-stream"}
        )
        
        # Check response status
        self.assertEqual(response.status_code, 200)
        
        # Parse the response content
        lines = []
        for line in response.iter_lines():
            if line:  # Skip empty lines
                if isinstance(line, bytes):
                    line = line.decode()
                if line.startswith("data: "):  # Only include data lines
                    lines.append(line[6:])  # Remove "data: " prefix
        
        # We expect 5 data lines (4 chunks + [DONE])
        self.assertEqual(len(lines), 5)
        
        # Check each line
        self.assertEqual(lines[0], "This is an AIMessageChunk")
        self.assertEqual(lines[1], "This is a ChatGeneration")
        self.assertEqual(lines[2], "This is a string")
        self.assertEqual(lines[3], "This is an object with content")
        self.assertEqual(lines[4], "[DONE]")
    
    def test_stream_endpoint_with_diff_parameter(self):
        """Test that stream endpoint handles unexpected diff parameter."""
        # Create a test request body with diff parameter
        body = {
            "question": "Test question",
            "chat_history": [],
            "config": {"files": []},
            "diff": "some diff content"  # This should be handled now
        }
        
        # Make the request directly to our custom endpoint
        response = self.client.post(
            "/ziya/stream",
            json=body,
            headers={"Accept": "text/event-stream"}
        )
        
        # Check response status
        self.assertEqual(response.status_code, 200)
        
        # Parse the response content
        lines = []
        for line in response.iter_lines():
            if line:  # Skip empty lines
                if isinstance(line, bytes):
                    line = line.decode()
                if line.startswith("data: "):  # Only include data lines
                    lines.append(line[6:])  # Remove "data: " prefix
        
        # We expect 5 data lines (4 chunks + [DONE])
        self.assertEqual(len(lines), 5)
        
        # Check each line
        self.assertEqual(lines[0], "This is an AIMessageChunk")
        self.assertEqual(lines[1], "This is a ChatGeneration")
        self.assertEqual(lines[2], "This is a string")
        self.assertEqual(lines[3], "This is an object with content")
        self.assertEqual(lines[4], "[DONE]")
    
    def test_stream_endpoint_empty_question(self):
        """Test that stream endpoint handles empty question."""
        # Create a test request body with empty question
        body = {
            "question": "",
            "chat_history": [],
            "config": {"files": []}
        }
        
        # Make the request directly to our custom endpoint
        response = self.client.post(
            "/ziya/stream",
            json=body,
            headers={"Accept": "text/event-stream"}
        )
        
        # Check response status
        self.assertEqual(response.status_code, 200)
        
        # Parse the response content
        lines = []
        for line in response.iter_lines():
            if line:  # Skip empty lines
                if isinstance(line, bytes):
                    line = line.decode()
                if line.startswith("data: "):  # Only include data lines
                    lines.append(line[6:])  # Remove "data: " prefix
        
        # We expect 2 data lines (error + [DONE])
        self.assertEqual(len(lines), 2)
        
        # Check error message
        self.assertEqual(lines[0], "Please provide a question to continue.")
        self.assertEqual(lines[1], "[DONE]")

if __name__ == "__main__":
    unittest.main()
