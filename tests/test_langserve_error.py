"""
Test that reproduces the exact langserve error scenario.
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

class TestLangserveError(unittest.TestCase):
    """Test that reproduces the exact langserve error scenario."""
    
    def test_exact_error_scenario(self):
        """Test that reproduces the exact error from the logs."""
        # Create a mock stream that yields the exact sequence from the logs
        async def mock_stream():
            # First AIMessageChunk
            chunk1 = AIMessageChunk(content="First part of response")
            chunk1.id = "run-0aa19ecd-9402-4a2e-a1b3-e251cac4d1a5"
            yield chunk1
            
            # RunLogPatch sequence
            patch1 = MagicMock()
            patch1.__class__.__name__ = 'RunLogPatch'
            patch1.id = "log-1383"
            yield patch1
            
            # Second AIMessageChunk
            chunk2 = AIMessageChunk(content="Second part of response")
            chunk2.id = "run-0aa19ecd-9402-4a2e-a1b3-e251cac4d1a5"
            yield chunk2
            
            # More RunLogPatch chunks
            for log_id in ["log-9985", "log-60", "log-3885", "log-9468"]:
                patch = MagicMock()
                patch.__class__.__name__ = 'RunLogPatch'
                patch.id = log_id
                yield patch
        
        # Create a mock langserve stream_log function that matches the real one
        async def mock_langserve_stream_log(stream):
            try:
                async for chunk in stream:
                    # This is where langserve checks for RunLogPatch
                    if not isinstance(chunk, RunLogPatch):
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
            
            # Pass it through langserve first (this is key - langserve runs first)
            langserve_stream = mock_langserve_stream_log(original_stream)
            
            # Then pass it through our middleware
            processed_stream = middleware.safe_stream(langserve_stream)
            
            # Collect the results
            results = []
            async for chunk in processed_stream:
                results.append(chunk)
            
            return results
        
        # Run the async function
        results = asyncio.run(run_test())
        
        # Verify we got the expected error
        self.assertTrue(any("Expected a RunLog instance got <class 'langchain_core.messages.ai.AIMessageChunk'>" in str(r) for r in results), 
                       "Did not get the expected AIMessageChunk error")
        
        # Print the results for debugging
        print("\nFinal results:")
        for i, result in enumerate(results):
            print(f"Result {i}: {result}")

if __name__ == "__main__":
    unittest.main()
