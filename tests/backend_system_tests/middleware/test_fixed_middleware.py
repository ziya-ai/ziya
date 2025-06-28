"""
Test for the fixed streaming middleware.
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

class TestFixedMiddleware(unittest.TestCase):
    """Test the fixed streaming middleware."""
    
    def test_middleware_fixes_aimessagechunk_issue(self):
        """Test that the middleware correctly fixes the AIMessageChunk issue."""
        # Create a mock stream that yields various chunk types
        async def mock_stream():
            # Create RunLogPatch chunks
            patch1 = MagicMock()
            patch1.__class__.__name__ = 'RunLogPatch'
            patch1.id = "log-8021"
            yield patch1
            
            # Create an AIMessageChunk
            chunk = AIMessageChunk(content="This is a test response")
            chunk.id = "run-22ac29d0-6754-4cc2-a654-8d4b425b95d3"
            yield chunk
        
        # Create a mock langserve stream_log function
        async def mock_langserve_stream_log(stream):
            try:
                async for chunk in stream:
                    # This is where langserve would check for RunLogPatch
                    if not isinstance(chunk, RunLogPatch) and not isinstance(chunk, str):
                        raise AssertionError(f"Expected a RunLog instance got {type(chunk)}")
                    yield chunk
            except Exception as e:
                # Send error as SSE data
                error_msg = {"error": "server_error", "detail": str(e), "status_code": 500}
                yield f"data: {json.dumps(error_msg)}\n\n"
        
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
        
        # Verify we got the expected results (no error)
        self.assertTrue(any("This is a test response" in str(r) for r in results), 
                       "Did not get the expected response")
        self.assertFalse(any("Expected a RunLog instance got" in str(r) for r in results), 
                        "Got an unexpected error")
        
        # Print the results for debugging
        print("\nFinal results:")
        for i, result in enumerate(results):
            print(f"Result {i}: {result}")

if __name__ == "__main__":
    unittest.main()
