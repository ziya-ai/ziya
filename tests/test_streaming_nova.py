#!/usr/bin/env python3
"""
Test for streaming with Nova models.

This test verifies that the streaming functionality works correctly with Nova models.
It mocks the Nova response and tests the stream_chunks function.
"""

import os
import sys
import json
import asyncio
import unittest
from unittest.mock import MagicMock, patch
from typing import Dict, Any, List, AsyncIterator

# Add the project root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import the necessary modules
from app.agents.custom_message import ZiyaString
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration


class MockNovaResponse:
    """Mock response for Nova models."""
    
    @staticmethod
    def create_response(text):
        """Create a mock Nova response."""
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": text}]
                }
            }
        }


class MockAnthropicResponse:
    """Mock response for Anthropic models."""
    
    @staticmethod
    def create_response(text):
        """Create a mock Anthropic response."""
        return [{'type': 'text', 'text': text}]


class TestStreamingFunctionality(unittest.TestCase):
    """Test the streaming functionality."""
    
    def setUp(self):
        """Set up the test environment."""
        # Import the stream_chunks function
        from app.server import stream_chunks
        self.stream_chunks = stream_chunks
        
        # Create mock responses
        self.nova_response = MockNovaResponse.create_response("This is a test response from Nova.")
        self.anthropic_response = MockAnthropicResponse.create_response("This is a test response from Anthropic.")
        
        # Create a ZiyaString for testing
        self.ziya_string = ZiyaString("This is a ZiyaString test.", id="test-id", message="This is a ZiyaString test.")
        
        # Create a ChatGeneration for testing
        self.chat_generation = ChatGeneration(
            message=AIMessage(content="This is a ChatGeneration test."),
            generation_info={"model_id": "test-model"}
        )
        # Add id attribute to the Generation object
        object.__setattr__(self.chat_generation, 'id', "test-id")
    
    @patch('app.agents.agent.agent_executor.astream')
    async def test_stream_chunks_nova(self, mock_astream):
        """Test streaming with Nova response."""
        # Set up the mock to return a Nova response
        mock_astream.return_value = self._mock_nova_stream()
        
        # Call the stream_chunks function
        chunks = []
        async for chunk in self.stream_chunks({"question": "test"}):
            chunks.append(chunk)
        
        # Verify the chunks
        self.assertTrue(len(chunks) >= 2)  # At least processing message and done message
        
        # Check that the first chunk is the processing message
        first_chunk = chunks[0]
        self.assertTrue(first_chunk.startswith("data: "))
        first_data = json.loads(first_chunk.replace("data: ", "").strip())
        self.assertEqual(first_data["text"], "Processing response...")
        
        # Check that the last chunk is the done message
        last_chunk = chunks[-1]
        self.assertTrue(last_chunk.startswith("data: "))
        last_data = json.loads(last_chunk.replace("data: ", "").strip())
        self.assertTrue(last_data.get("done", False))
        
        # Check that the content chunks contain the expected text
        content_chunks = chunks[1:-1]  # Skip processing and done messages
        if content_chunks:
            content = ""
            for chunk in content_chunks:
                self.assertTrue(chunk.startswith("data: "))
                data = json.loads(chunk.replace("data: ", "").strip())
                if "text" in data:
                    content += data["text"]
            
            self.assertIn("This is a test response from Nova", content)
    
    @patch('app.agents.agent.agent_executor.astream')
    async def test_stream_chunks_anthropic(self, mock_astream):
        """Test streaming with Anthropic response."""
        # Set up the mock to return an Anthropic response
        mock_astream.return_value = self._mock_anthropic_stream()
        
        # Call the stream_chunks function
        chunks = []
        async for chunk in self.stream_chunks({"question": "test"}):
            chunks.append(chunk)
        
        # Verify the chunks
        self.assertTrue(len(chunks) >= 2)  # At least processing message and done message
        
        # Check that the first chunk is the processing message
        first_chunk = chunks[0]
        self.assertTrue(first_chunk.startswith("data: "))
        first_data = json.loads(first_chunk.replace("data: ", "").strip())
        self.assertEqual(first_data["text"], "Processing response...")
        
        # Check that the last chunk is the done message
        last_chunk = chunks[-1]
        self.assertTrue(last_chunk.startswith("data: "))
        last_data = json.loads(last_chunk.replace("data: ", "").strip())
        self.assertTrue(last_data.get("done", False))
        
        # Check that the content chunks contain the expected text
        content_chunks = chunks[1:-1]  # Skip processing and done messages
        if content_chunks:
            content = ""
            for chunk in content_chunks:
                self.assertTrue(chunk.startswith("data: "))
                data = json.loads(chunk.replace("data: ", "").strip())
                if "text" in data:
                    content += data["text"]
            
            self.assertIn("This is a test response from Anthropic", content)
    
    @patch('app.agents.agent.agent_executor.astream')
    async def test_stream_chunks_ziya_string(self, mock_astream):
        """Test streaming with ZiyaString."""
        # Set up the mock to return a ZiyaString
        mock_astream.return_value = self._mock_ziya_string_stream()
        
        # Call the stream_chunks function
        chunks = []
        async for chunk in self.stream_chunks({"question": "test"}):
            chunks.append(chunk)
        
        # Verify the chunks
        self.assertTrue(len(chunks) >= 2)  # At least processing message and done message
        
        # Check that the content chunks contain the expected text
        content_chunks = chunks[1:-1]  # Skip processing and done messages
        if content_chunks:
            content = ""
            for chunk in content_chunks:
                self.assertTrue(chunk.startswith("data: "))
                data = json.loads(chunk.replace("data: ", "").strip())
                if "text" in data:
                    content += data["text"]
            
            self.assertIn("This is a ZiyaString test", content)
    
    @patch('app.agents.agent.agent_executor.astream')
    async def test_stream_chunks_chat_generation(self, mock_astream):
        """Test streaming with ChatGeneration."""
        # Set up the mock to return a ChatGeneration
        mock_astream.return_value = self._mock_chat_generation_stream()
        
        # Call the stream_chunks function
        chunks = []
        async for chunk in self.stream_chunks({"question": "test"}):
            chunks.append(chunk)
        
        # Verify the chunks
        self.assertTrue(len(chunks) >= 2)  # At least processing message and done message
        
        # Check that the content chunks contain the expected text
        content_chunks = chunks[1:-1]  # Skip processing and done messages
        if content_chunks:
            content = ""
            for chunk in content_chunks:
                self.assertTrue(chunk.startswith("data: "))
                data = json.loads(chunk.replace("data: ", "").strip())
                if "text" in data:
                    content += data["text"]
            
            self.assertIn("This is a ChatGeneration test", content)
    
    async def _mock_nova_stream(self):
        """Mock a stream of Nova responses."""
        yield self.nova_response
    
    async def _mock_anthropic_stream(self):
        """Mock a stream of Anthropic responses."""
        yield str(self.anthropic_response)
    
    async def _mock_ziya_string_stream(self):
        """Mock a stream of ZiyaString responses."""
        yield self.ziya_string
    
    async def _mock_chat_generation_stream(self):
        """Mock a stream of ChatGeneration responses."""
        yield self.chat_generation


if __name__ == "__main__":
    # Run the tests
    unittest.main()
