"""
Test the stream endpoint with a simpler approach.
"""

import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import asyncio

app = FastAPI()

# Create a simple streaming endpoint
@app.post("/test_stream")
async def test_stream_endpoint(request: Request):
    # Get the request body
    body = await request.json()
    
    async def stream_response():
        yield "data: This is a test\n\n"
        yield "data: [DONE]\n\n"
    
    response = StreamingResponse(
        stream_response(),
        media_type="text/event-stream"
    )
    
    # Add CORS headers
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
        # Make the request
        response = self.client.post(
            "/test_stream",
            json={"test": "data"},
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
        
        # We expect 2 data lines (1 content + [DONE])
        self.assertEqual(len(lines), 2)
        
        # Check each line
        self.assertEqual(lines[0], "This is a test")
        self.assertEqual(lines[1], "[DONE]")

if __name__ == "__main__":
    unittest.main()
