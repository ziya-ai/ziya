"""
Integration test for the streaming middleware with langserve.
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

class TestLangserveIntegration(unittest.TestCase):
    """Integration test for the streaming middleware with langserve."""
    
    def test_langserve_error_with_aimessagechunk(self):
        """Test that reproduces the langserve error with AIMessageChunk."""
        # Create a mock stream that yields an AIMessageChunk
        async def mock_stream():
            # Create RunLogPatch chunks
            patch1 = MagicMock()
            patch1.__class__.__name__ = 'RunLogPatch'
            patch1.id = "log-8021"
            yield patch1
            
            patch2 = MagicMock()
            patch2.__class__.__name__ = 'RunLogPatch'
            patch2.id = "log-7928"
            yield patch2
            
            patch3 = MagicMock()
            patch3.__class__.__name__ = 'RunLogPatch'
            patch3.id = "log-826"
            yield patch3
            
            patch4 = MagicMock()
            patch4.__class__.__name__ = 'RunLogPatch'
            patch4.id = "log-8556"
            yield patch4
            
            # Create an AIMessageChunk that will cause the error
            chunk = AIMessageChunk(content="This is a test response")
            chunk.id = "run-22ac29d0-6754-4cc2-a654-8d4b425b95d3"
            yield chunk
        
        # Create a mock langserve stream_log function
        async def mock_langserve_stream_log(stream):
            try:
                async for chunk in stream:
                    # This is where langserve would check for RunLogPatch
                    if not isinstance(chunk, RunLogPatch):
                        # Use the actual class name in the error message
                        raise AssertionError(f"Expected a RunLog instance got {chunk.__class__}")
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
        
        # Verify we got the expected error
        self.assertEqual(len(results), 1)
        
        # The error message format might be different, so check for 'str' in the error
        # The middleware is converting AIMessageChunk to a string format
        error_str = str(results[0])
        self.assertTrue("str" in error_str, 
                       f"Did not get the expected error. Got: {error_str}")
        
        # Print the results for debugging
        print("\nFinal results:")
        for i, result in enumerate(results):
            print(f"Result {i}: {result}")

if __name__ == "__main__":
    unittest.main()
