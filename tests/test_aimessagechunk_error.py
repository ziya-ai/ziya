"""
Test case for AIMessageChunk streaming issues.

This test reproduces the issue where AIMessageChunk causes an error in the streaming middleware.
"""

import unittest
import asyncio
from unittest.mock import MagicMock, patch
import json
from typing import Dict, Any, List, AsyncIterator

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration
from langchain_core.tracers.log_stream import RunLogPatch
from langchain_core.agents import AgentFinish

class TestAIMessageChunkError(unittest.TestCase):
    """Test AIMessageChunk streaming error."""
    
    def setUp(self):
        """Set up test environment."""
        # Create a list to capture chunks for inspection
        self.captured_chunks = []
        
        # Create a mock handler that will process chunks
        def mock_handler(chunk):
            self.captured_chunks.append(chunk)
            # Log the chunk for debugging
            print(f"Received chunk: {chunk}")
            print(f"Chunk type: {type(chunk)}")
            if hasattr(chunk, 'content'):
                print(f"Chunk content: {chunk.content}")
            print("---")
        
        self.mock_handler = mock_handler
    
    def test_aimessagechunk_error(self):
        """Test that reproduces the exact error seen in the logs with AIMessageChunk."""
        # Create a mock stream that yields an AIMessageChunk
        async def mock_stream():
            # Create a RunLogPatch with an ID
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
        
        # Create a mock middleware function that simulates the error handling middleware
        async def mock_langserve_stream_log(stream):
            try:
                async for chunk in stream:
                    # Log chunk info for debugging
                    print("=== AGENT astream received chunk ===")
                    print(f"Chunk type: {type(chunk)}")
                    print(f"Chunk has id attribute: {hasattr(chunk, 'id')}")
                    
                    if hasattr(chunk, 'id'):
                        print(f"Chunk id: {chunk.id}")
                    
                    # Process the chunk
                    try:
                        # Special handling for RunLogPatch objects
                        if hasattr(chunk, '__class__') and chunk.__class__.__name__ == 'RunLogPatch':
                            print(f"Processing RunLogPatch: {type(chunk)}")
                            print(f"Added id to RunLogPatch: {chunk.id}")
                            continue
                        
                        # Special handling for None chunks
                        if chunk is None:
                            print("Skipping None chunk")
                            continue
                        
                        # Log final chunk info
                        print(f"Final chunk type: {type(chunk)}")
                        print(f"Final chunk has id: {hasattr(chunk, 'id')}")
                        print(f"Final chunk has message: {hasattr(chunk, 'message')}")
                        print(f"Final chunk has content: {hasattr(chunk, 'content')}")
                        
                        # This is where the error happens in langserve
                        if not isinstance(chunk, RunLogPatch):
                            raise AssertionError(f"Expected a RunLog instance got {type(chunk)}")
                        
                        # Yield the chunk
                        if hasattr(chunk, 'id'):
                            print(f"Yielding chunk with id: {chunk.id}")
                        yield chunk
                        
                    except Exception as chunk_error:
                        print(f"Error processing chunk: {str(chunk_error)}")
                        # Send error message as SSE
                        error_msg = f"Error processing response: {str(chunk_error)}"
                        yield f"data: {error_msg}\n\n"
                        continue
            except Exception as e:
                print(f"Error in safe_astream_log: {str(e)}")
                # Send error message as SSE
                error_msg = {"error": "server_error", "detail": str(e), "status_code": 500}
                print(f"Sent error as SSE data: {error_msg}")
                yield f"data: {json.dumps(error_msg)}\n\n"
        
        # Run the streaming response through the mock middleware
        async def run_stream():
            # Pass it through the mock middleware
            processed_stream = mock_langserve_stream_log(mock_stream())
            
            # Collect the results
            results = []
            async for chunk in processed_stream:
                results.append(chunk)
                self.mock_handler(chunk)
            
            return results
        
        # Run the async function and expect the error
        results = asyncio.run(run_stream())
        
        # Verify we got the expected error
        self.assertTrue(any("Expected a RunLog instance got <class 'langchain_core.messages.ai.AIMessageChunk'>" in str(r) for r in results), 
                       "Did not get the expected AIMessageChunk error")
        
        # Print the results for debugging
        print("\nFinal results:")
        for i, result in enumerate(results):
            print(f"Result {i}: {result}")

if __name__ == '__main__':
    unittest.main()
