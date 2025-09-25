"""
Integration tests for the streaming middleware with Nova-Lite.
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

class TestIntegration(unittest.TestCase):
    """Integration tests for the streaming middleware with Nova-Lite."""
    
    @patch('boto3.client')
    def test_nova_lite_with_middleware(self, mock_boto3_client):
        """Test that Nova-Lite works with the streaming middleware."""
        from app.agents.nova_wrapper import NovaWrapper
        
        # Setup mock bedrock client
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "This is a test response"}]
                }
            }
        }
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
        
        # Create the middleware
        middleware = StreamingMiddleware(None)
        
        # Run the streaming response through the middleware
        async def run_test():
            # Get the streaming response from the wrapper
            stream = wrapper._astream(messages)
            
            # Pass it through the middleware
            processed_stream = middleware.safe_stream(stream)
            
            # Collect the results
            results = []
            async for chunk in processed_stream:
                results.append(chunk)
            
            return results
        
        # Run the async function
        results = asyncio.run(run_test())
        
        # Verify we got the expected output
        self.assertEqual(len(results), 1)
        # The format changed to include a JSON wrapper, so update the expected output
        self.assertEqual(results[0], 'data: {"text": "This is a test response"}\n\n')

if __name__ == "__main__":
    unittest.main()
