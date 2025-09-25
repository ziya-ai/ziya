"""
Test for converting chunks to SSE data.
"""

import unittest
import asyncio
from unittest.mock import MagicMock, patch
import json
from typing import Dict, Any, List, AsyncIterator

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration
from langchain_core.tracers.log_stream import RunLogPatch

from app.middleware import StreamingMiddleware

class TestSSEConversion(unittest.TestCase):
    """Test converting chunks to SSE data."""
    
    def test_aimessagechunk_to_sse(self):
        """Test that AIMessageChunk is converted to SSE data."""
        # Create a mock stream that yields an AIMessageChunk
        async def mock_stream():
            # Create an AIMessageChunk
            chunk = AIMessageChunk(content="This is a test response")
            chunk.id = "run-22ac29d0-6754-4cc2-a654-8d4b425b95d3"
            yield chunk
        
        # Create the middleware
        middleware = StreamingMiddleware(None)
        
        # Run the test
        async def run_test():
            # Get the original stream
            original_stream = mock_stream()
            
            # Pass it through the middleware
            processed_stream = middleware.safe_stream(original_stream)
            
            # Collect the results
            results = []
            async for chunk in processed_stream:
                results.append(chunk)
            
            return results
        
        # Run the async function
        results = asyncio.run(run_test())
        
        # Verify we got the expected results
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], "data: This is a test response\n\n")
    
    def test_chatgeneration_to_sse(self):
        """Test that ChatGeneration is converted to SSE data."""
        # Create a mock stream that yields a ChatGeneration
        async def mock_stream():
            # Create a ChatGeneration
            message = AIMessage(content="This is a test response")
            gen = ChatGeneration(message=message)
            yield gen
        
        # Create the middleware
        middleware = StreamingMiddleware(None)
        
        # Run the test
        async def run_test():
            # Get the original stream
            original_stream = mock_stream()
            
            # Pass it through the middleware
            processed_stream = middleware.safe_stream(original_stream)
            
            # Collect the results
            results = []
            async for chunk in processed_stream:
                results.append(chunk)
            
            return results
        
        # Run the async function
        results = asyncio.run(run_test())
        
        # Verify we got the expected results
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], "data: This is a test response\n\n")
    
    def test_runlogpatch_passthrough(self):
        """Test that RunLogPatch objects are passed through unchanged."""
        # Create a mock stream that yields a RunLogPatch
        async def mock_stream():
            # Create a RunLogPatch
            patch = MagicMock()
            patch.__class__.__name__ = 'RunLogPatch'
            patch.id = "log-1234"
            yield patch
        
        # Create the middleware
        middleware = StreamingMiddleware(None)
        
        # Run the test
        async def run_test():
            # Get the original stream
            original_stream = mock_stream()
            
            # Pass it through the middleware
            processed_stream = middleware.safe_stream(original_stream)
            
            # Collect the results
            results = []
            async for chunk in processed_stream:
                results.append(chunk)
            
            return results
        
        # Run the async function
        results = asyncio.run(run_test())
        
        # Verify we got the expected results
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, "log-1234")

if __name__ == "__main__":
    unittest.main()
