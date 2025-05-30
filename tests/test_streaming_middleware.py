"""
Tests for the streaming middleware.
"""

import unittest
import asyncio
from unittest.mock import MagicMock, patch
import json
from typing import Dict, Any, List, AsyncIterator

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration
from langchain_core.tracers.log_stream import RunLogPatch

from app.utils.middleware import StreamingMiddleware

class TestStreamingMiddleware(unittest.TestCase):
    """Test the streaming middleware."""
    
    def test_safe_stream_chat_generation(self):
        """Test that safe_stream handles ChatGeneration objects correctly."""
        # Create a mock stream that yields a ChatGeneration
        async def mock_stream():
            message = AIMessage(content="Test message")
            chunk = ChatGeneration(message=message)
            yield chunk
        
        # Create the middleware
        middleware = StreamingMiddleware(None)
        
        # Process the stream through the middleware's safe_stream
        results = []
        
        async def run_test():
            async for chunk in middleware.safe_stream(mock_stream()):
                results.append(chunk)
        
        # Run the async function
        asyncio.run(run_test())
        
        # Verify we got the expected output
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], 'data: {"text": "Test message"}\n\n')
    
    def test_safe_stream_ai_message_chunk(self):
        """Test that safe_stream handles AIMessageChunk objects correctly."""
        # Create a mock stream that yields an AIMessageChunk
        async def mock_stream():
            chunk = AIMessageChunk(content="Test message")
            yield chunk
        
        # Create the middleware
        middleware = StreamingMiddleware(None)
        
        # Process the stream through the middleware's safe_stream
        results = []
        
        async def run_test():
            async for chunk in middleware.safe_stream(mock_stream()):
                results.append(chunk)
        
        # Run the async function
        asyncio.run(run_test())
        
        # Verify we got the expected output
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], 'data: {"text": "Test message"}\n\n')
    
    def test_safe_stream_run_log_patch(self):
        """Test that safe_stream handles RunLogPatch objects correctly."""
        # Create a mock stream that yields a RunLogPatch
        async def mock_stream():
            # Create a RunLogPatch with the correct parameters
            # RunLogPatch doesn't accept 'id' directly
            patch = MagicMock()
            patch.__class__.__name__ = 'RunLogPatch'
            patch.id = "test-id"
            yield patch
        
        # Create the middleware
        middleware = StreamingMiddleware(None)
        
        # Process the stream through the middleware's safe_stream
        results = []
        
        async def run_test():
            async for chunk in middleware.safe_stream(mock_stream()):
                results.append(chunk)
        
        # Run the async function
        asyncio.run(run_test())
        
        # Verify we got no output (RunLogPatch should be skipped)
        self.assertEqual(len(results), 0)
    
    def test_safe_stream_string(self):
        """Test that safe_stream handles string chunks correctly."""
        # Create a mock stream that yields a string
        async def mock_stream():
            yield "Test message"
        
        # Create the middleware
        middleware = StreamingMiddleware(None)
        
        # Process the stream through the middleware's safe_stream
        results = []
        
        async def run_test():
            async for chunk in middleware.safe_stream(mock_stream()):
                results.append(chunk)
        
        # Run the async function
        asyncio.run(run_test())
        
        # Verify we got the expected output
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], 'data: {"text": "Test message"}\n\n')
    
    def test_safe_stream_unknown_type(self):
        """Test that safe_stream handles unknown chunk types correctly."""
        # Create a mock stream that yields an unknown type
        async def mock_stream():
            yield {"unknown": "type"}
        
        # Create the middleware
        middleware = StreamingMiddleware(None)
        
        # Process the stream through the middleware's safe_stream
        results = []
        
        async def run_test():
            async for chunk in middleware.safe_stream(mock_stream()):
                results.append(chunk)
        
        # Run the async function
        asyncio.run(run_test())
        
        # Verify we got an error message
        self.assertEqual(len(results), 1)
        self.assertIn("Unknown chunk type", results[0])

if __name__ == "__main__":
    unittest.main()
