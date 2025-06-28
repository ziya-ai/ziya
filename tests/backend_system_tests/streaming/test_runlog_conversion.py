"""
Test for converting AIMessageChunk to RunLogPatch.
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

class TestRunLogConversion(unittest.TestCase):
    """Test converting AIMessageChunk to RunLogPatch."""
    
    def test_aimessagechunk_to_runlog_conversion(self):
        """Test that AIMessageChunk is converted to RunLogPatch."""
        # Create a mock stream that yields an AIMessageChunk
        async def mock_stream():
            # Create an AIMessageChunk
            chunk = AIMessageChunk(content="This is a test response")
            chunk.id = "run-22ac29d0-6754-4cc2-a654-8d4b425b95d3"
            yield chunk
        
        # Create a mock langserve stream_log function
        async def mock_langserve_stream_log(stream):
            async for chunk in stream:
                # This is where langserve would check for RunLogPatch
                if not isinstance(chunk, RunLogPatch):
                    raise AssertionError(f"Expected a RunLog instance got {type(chunk)}")
                # If we get here, the chunk is a RunLogPatch
                print(f"Received RunLogPatch with id: {chunk.id}")
                print(f"RunLogPatch ops: {chunk.ops}")
                yield chunk
        
        # Create the middleware
        middleware = StreamingMiddleware(None)
        
        # Run the test
        async def run_test():
            # Get the original stream
            original_stream = mock_stream()
            
            # Pass it through the middleware
            processed_stream = middleware.safe_stream(original_stream)
            
            # Pass it through langserve
            results = []
            async for chunk in mock_langserve_stream_log(processed_stream):
                results.append(chunk)
            
            return results
        
        # Run the async function
        results = asyncio.run(run_test())
        
        # Verify we got the expected results
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], RunLogPatch)
        self.assertEqual(results[0].id, "run-22ac29d0-6754-4cc2-a654-8d4b425b95d3")
        self.assertEqual(len(results[0].ops), 1)
        self.assertEqual(results[0].ops[0]['op'], 'add')
        self.assertEqual(results[0].ops[0]['path'], '/logs/-')
        self.assertEqual(results[0].ops[0]['value']['content'], "This is a test response")

    def test_chatgeneration_to_runlog_conversion(self):
        """Test that ChatGeneration is converted to RunLogPatch."""
        # Create a mock stream that yields a ChatGeneration
        async def mock_stream():
            # Create a ChatGeneration
            message = AIMessage(content="This is a test response")
            gen = ChatGeneration(message=message)
            gen.id = "gen-12345"
            yield gen
        
        # Create a mock langserve stream_log function
        async def mock_langserve_stream_log(stream):
            async for chunk in stream:
                # This is where langserve would check for RunLogPatch
                if not isinstance(chunk, RunLogPatch):
                    raise AssertionError(f"Expected a RunLog instance got {type(chunk)}")
                # If we get here, the chunk is a RunLogPatch
                print(f"Received RunLogPatch with id: {chunk.id}")
                print(f"RunLogPatch ops: {chunk.ops}")
                yield chunk
        
        # Create the middleware
        middleware = StreamingMiddleware(None)
        
        # Run the test
        async def run_test():
            # Get the original stream
            original_stream = mock_stream()
            
            # Pass it through the middleware
            processed_stream = middleware.safe_stream(original_stream)
            
            # Pass it through langserve
            results = []
            async for chunk in mock_langserve_stream_log(processed_stream):
                results.append(chunk)
            
            return results
        
        # Run the async function
        results = asyncio.run(run_test())
        
        # Verify we got the expected results
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], RunLogPatch)
        self.assertEqual(results[0].id, "gen-12345")
        self.assertEqual(len(results[0].ops), 1)
        self.assertEqual(results[0].ops[0]['op'], 'add')
        self.assertEqual(results[0].ops[0]['path'], '/logs/-')
        self.assertEqual(results[0].ops[0]['value']['content'], "This is a test response")

if __name__ == "__main__":
    unittest.main()
