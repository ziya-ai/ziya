"""
Test the stream_agent_response function.
"""

import unittest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import json
from typing import Dict, Any, List, AsyncIterator

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration

# Mock the agent_executor import to avoid AWS credential issues
import sys
sys.modules['app.agents.agent'] = MagicMock()
sys.modules['app.agents.models'] = MagicMock()

# Mock parse_output to return None
mock_parse_output = MagicMock()
mock_parse_output.return_value = None
sys.modules['app.agents.agent'].parse_output = mock_parse_output

# Mock add_routes to avoid Runnable type check
from unittest.mock import patch
import builtins
original_import = __import__

def patched_import(name, *args, **kwargs):
    if name == 'langserve':
        mock_langserve = MagicMock()
        mock_add_routes = MagicMock()
        mock_langserve.add_routes = mock_add_routes
        return mock_langserve
    return original_import(name, *args, **kwargs)

with patch('builtins.__import__', patched_import):
    from app.server import stream_agent_response

class TestStreamAgentResponse(unittest.TestCase):
    """Test the stream_agent_response function."""
    
    def test_stream_agent_response(self):
        """Test that stream_agent_response correctly processes chunks."""
        # Create a mock agent_executor
        mock_agent_executor = MagicMock()
        
        # Create a mock astream method that yields different types of chunks
        async def mock_astream(body):
            # Yield an AIMessageChunk
            yield AIMessageChunk(content="This is an AIMessageChunk")
            
            # Yield a ChatGeneration
            message = AIMessage(content="This is a ChatGeneration")
            yield ChatGeneration(message=message)
            
            # Yield a string
            yield "This is a string"
            
            # Yield an object with content attribute
            obj = MagicMock()
            obj.content = "This is an object with content"
            yield obj
            
            # Yield None (should be skipped)
            yield None
        
        # Set the mock astream method
        mock_agent_executor.astream = mock_astream
        
        # Run the function
        async def run_test():
            # Call the function with the mock
            with patch('app.server.agent_executor', mock_agent_executor), \
                 patch('app.server.parse_output', return_value=None):
                results = []
                async for chunk in stream_agent_response({}, None):
                    results.append(chunk)
                return results
        
        # Run the async function
        results = asyncio.run(run_test())
        
        # Check the results
        self.assertEqual(len(results), 5)  # 4 chunks + [DONE]
        self.assertEqual(results[0], "data: This is an AIMessageChunk\n\n")
        self.assertEqual(results[1], "data: This is a ChatGeneration\n\n")
        self.assertEqual(results[2], "data: This is a string\n\n")
        self.assertEqual(results[3], "data: This is an object with content\n\n")
        self.assertEqual(results[4], "data: [DONE]\n\n")

if __name__ == "__main__":
    unittest.main()
