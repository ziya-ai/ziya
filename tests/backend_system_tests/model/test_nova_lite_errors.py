"""
Test cases for Nova-Lite model errors.

This test suite demonstrates the errors encountered when using the Nova-Lite model.
"""

import unittest
import os
import sys
from unittest.mock import patch, MagicMock
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

# Add the app directory to the path so we can import modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import the relevant modules
from app.agents.nova_wrapper import NovaWrapper
from app.agents.custom_message import ZiyaString, ZiyaMessageChunk


class TestNovaLiteErrors(unittest.TestCase):
    """Test cases for Nova-Lite model errors."""

    def setUp(self):
        """Set up test environment."""
        # Mock AWS credentials
        os.environ["AWS_ACCESS_KEY_ID"] = "test_key"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "test_secret"
        
        # Create a mock bedrock client
        self.mock_bedrock_client = MagicMock()
        
        # Capture the actual error responses
        self.error_responses = []
    
    @patch('boto3.client')
    def test_nova_lite_message_format(self, mock_boto3_client):
        """Test that Nova-Lite model requires list/tuple for message content."""
        # Setup mock
        mock_boto3_client.return_value = self.mock_bedrock_client
        
        # Configure mock to raise the same error we're seeing
        self.mock_bedrock_client.converse.side_effect = TypeError(
            "Invalid type for parameter messages[1].content, value: test, type: <class 'str'>, "
            "valid types: <class 'list'>, <class 'tuple'>"
        )
        
        # Create NovaWrapper instance with Nova-Lite
        nova_wrapper = NovaWrapper(
            model_id="us.amazon.nova-lite-v1:0",
            temperature=0.7,
            top_p=0.9,
            top_k=40  # This will cause the "Unknown parameter" error
        )
        
        # Create messages with string content (which will cause the error)
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="test"),  # String content instead of list/tuple
            AIMessage(content="")  # String content instead of list/tuple
        ]
        
        # Try to generate content and expect the error
        with self.assertRaises(TypeError) as context:
            nova_wrapper._generate(messages)
        
        # Verify the error message
        self.assertIn("Invalid type for parameter messages", str(context.exception))
    
    @patch('boto3.client')
    def test_nova_lite_inference_config(self, mock_boto3_client):
        """Test that Nova-Lite model doesn't accept topK parameter."""
        # Setup mock
        mock_boto3_client.return_value = self.mock_bedrock_client
        
        # Configure mock to raise the same error we're seeing
        self.mock_bedrock_client.converse.side_effect = ValueError(
            "Unknown parameter in inferenceConfig: \"topK\", must be one of: "
            "maxTokens, temperature, topP, stopSequences"
        )
        
        # Create NovaWrapper instance with Nova-Lite and the problematic topK parameter
        nova_wrapper = NovaWrapper(
            model_id="us.amazon.nova-lite-v1:0",
            temperature=0.7,
            top_p=0.9,
            top_k=40  # This will cause the error
        )
        
        # Create properly formatted messages
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content=[{"type": "text", "text": "test"}]),
            AIMessage(content=[{"type": "text", "text": ""}])
        ]
        
        # Try to generate content and expect the error
        with self.assertRaises(ValueError) as context:
            nova_wrapper._generate(messages)
        
        # Verify the error message
        self.assertIn("Unknown parameter in inferenceConfig", str(context.exception))
    
    @patch('boto3.client')
    def test_runlog_vs_aimessagechunk(self, mock_boto3_client):
        """Test the type mismatch between RunLog and AIMessageChunk."""
        # This test simulates the streaming response scenario
        from langchain_core.messages import AIMessageChunk
        
        # Setup mock
        mock_boto3_client.return_value = self.mock_bedrock_client
        
        # Mock the streaming response
        mock_response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": "This is a test response"}]
                }
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 100, "outputTokens": 50},
            "metrics": {"latencyMs": 1000}
        }
        
        # Configure the mock to return the response
        self.mock_bedrock_client.converse.return_value = mock_response
        
        # Create NovaWrapper instance with Nova-Lite
        nova_wrapper = NovaWrapper(
            model_id="us.amazon.nova-lite-v1:0",
            temperature=0.7,
            top_p=0.9
        )
        
        # Create properly formatted messages
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content=[{"type": "text", "text": "test"}])
        ]
        
        # Try to generate streaming content
        # We expect a validation error because of the ChatGeneration initialization
        with self.assertRaises(Exception) as context:
            result = nova_wrapper._generate(messages)
        
        # Verify the error message
        self.assertIn("Error while initializing ChatGeneration", str(context.exception))


if __name__ == "__main__":
    unittest.main()
