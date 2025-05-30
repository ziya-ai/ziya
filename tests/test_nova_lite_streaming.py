"""
Test case for Nova-Lite streaming issues.

This test reproduces the issue where Nova-Lite sends a stream of chunks
that appear as <object><object> in the frontend, followed by an error.
"""

import unittest
import asyncio
from unittest.mock import MagicMock, patch
import json
from typing import Dict, Any, List, AsyncIterator
from pydantic import ValidationError

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.outputs import ChatGeneration
from langchain_core.tracers.log_stream import RunLogPatch
from langchain_core.agents import AgentFinish

class TestNovaLiteStreaming(unittest.TestCase):
    """Test Nova-Lite streaming behavior."""
    
    def setUp(self):
        """Set up test environment."""
        # Create a mock response that simulates Nova-Lite's behavior
        self.mock_response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "This is a test response"}]
                }
            }
        }
        
        # Create a list to capture chunks for inspection
        self.captured_chunks = []
        
        # Create a mock handler that will process chunks
        def mock_handler(chunk):
            self.captured_chunks.append(chunk)
            # Log the chunk for debugging
            print(f"Received chunk: {chunk}")
            print(f"Chunk type: {type(chunk)}")
            if hasattr(chunk, 'message'):
                print(f"Message type: {type(chunk.message)}")
                print(f"Message content: {chunk.message.content}")
            print("---")
        
        self.mock_handler = mock_handler
    
    @patch('boto3.client')
    def test_nova_lite_exact_error(self, mock_boto3_client):
        """Test that reproduces the exact error seen in the logs."""
        from app.agents.nova_wrapper import NovaWrapper
        
        # Setup mock bedrock client
        mock_client = MagicMock()
        mock_client.converse.return_value = self.mock_response
        mock_boto3_client.return_value = mock_client
        
        # Create NovaWrapper instance
        wrapper = NovaWrapper(
            model_id="us.amazon.nova-lite-v1:0",
            temperature=0.7,
            top_p=0.9
        )
        
        # Create test messages
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="Test message")
        ]
        
        # Create a mock middleware function that simulates the error handling middleware
        async def mock_safe_astream_log(original_iterator):
            try:
                async for chunk in original_iterator:
                    # Log chunk info for debugging
                    print("=== AGENT astream received chunk ===")
                    print(f"Chunk type: {type(chunk)}")
                    print(f"Chunk has id attribute: {hasattr(chunk, 'id')}")
                    
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
                        
                        # Try to create an AgentFinish with the chunk
                        # This will trigger the validation error we're seeing in the logs
                        try:
                            # Extract content from the chunk
                            if hasattr(chunk, 'message') and hasattr(chunk.message, 'content'):
                                content = chunk.message.content
                            else:
                                content = str(chunk)
                            
                            # Try to create an AgentFinish with the content as log
                            # This will fail with a validation error if content is not a string
                            agent_finish = AgentFinish(
                                return_values={"output": content},
                                log=[{"type": "text", "text": content, "index": 0}]  # This will cause the validation error
                            )
                            yield agent_finish
                        except Exception as validation_error:
                            error_msg = str(validation_error)
                            print(f"Error in safe_astream_log: {error_msg}")
                            yield f"data: {error_msg}\n\n"
                            
                            # After the validation error, we'll get the RunLog error
                            if not isinstance(chunk, RunLogPatch):
                                error_msg = f"Expected a RunLog instance got {type(chunk)}"
                                print(f"Error processing chunk: {error_msg}")
                                yield f"data: Error processing response: {error_msg}\n\n"
                            
                            # Send error as SSE data
                            error_msg = {
                                "error": "server_error",
                                "detail": f"Expected a RunLog instance got {type(chunk)}",
                                "status_code": 500
                            }
                            print(f"Sent error as SSE data: {error_msg}")
                            yield f"data: {json.dumps(error_msg)}\n\n"
                            continue
                        
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
            # Get the streaming response from the wrapper
            stream = wrapper._astream(messages)
            
            # Pass it through the mock middleware
            processed_stream = mock_safe_astream_log(stream)
            
            # Collect the results
            results = []
            async for chunk in processed_stream:
                results.append(chunk)
                self.mock_handler(chunk)
            
            return results
        
        # Run the async function and expect the validation error
        results = asyncio.run(run_stream())
        
        # Print the results for debugging
        print("\nFinal results:")
        for i, result in enumerate(results):
            print(f"Result {i}: {result}")
        
        # Verify we got the expected error messages
        validation_error_found = False
        runlog_error_found = False
        sse_error_found = False
        
        for result in results:
            result_str = str(result)
            if "validation error for AgentFinish" in result_str and "Input should be a valid string" in result_str:
                validation_error_found = True
            if "Expected a RunLog instance" in result_str and "ChatGeneration" in result_str:
                runlog_error_found = True
            if '"error": "server_error"' in result_str and "Expected a RunLog instance" in result_str:
                sse_error_found = True
        
        self.assertTrue(validation_error_found, "Did not get the expected validation error")
        self.assertTrue(runlog_error_found, "Did not get the expected RunLog error")
        self.assertTrue(sse_error_found, "Did not get the expected SSE error")

if __name__ == '__main__':
    unittest.main()
