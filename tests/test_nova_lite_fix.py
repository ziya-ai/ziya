"""
Test cases for the fixed Nova-Lite wrapper.

This test suite verifies that the fixes for Nova-Lite model work correctly.
"""

import unittest
import os
import sys
from unittest.mock import patch, MagicMock
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

# Add the app directory to the path so we can import modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import the relevant modules
from app.agents.nova_wrapper_fix import NovaWrapper
from app.agents.custom_message import ZiyaString


class TestNovaLiteFix(unittest.TestCase):
    """Test cases for the fixed Nova-Lite wrapper."""

    def setUp(self):
        """Set up test environment."""
        # Mock AWS credentials
        os.environ["AWS_ACCESS_KEY_ID"] = "test_key"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "test_secret"
        
        # Create a mock bedrock client
        self.mock_bedrock_client = MagicMock()
        
        # Mock response for successful calls
        self.mock_response = {
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
    
    @patch('boto3.client')
    def test_nova_lite_message_format_fix(self, mock_boto3_client):
        """Test that the fixed wrapper correctly formats messages for Nova-Lite."""
        # Setup mock
        mock_boto3_client.return_value = self.mock_bedrock_client
        self.mock_bedrock_client.converse.return_value = self.mock_response
        
        # Create NovaWrapper instance with Nova-Lite
        nova_wrapper = NovaWrapper(
            model_id="us.amazon.nova-lite-v1:0",
            temperature=0.7,
            top_p=0.9
        )
        
        # Create messages with string content
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="test"),
            AIMessage(content="")
        ]
        
        # Format the messages
        formatted = nova_wrapper._format_messages(messages)
        
        # Verify the formatting
        self.assertEqual(len(formatted["messages"]), 3)
        
        # Check that content is properly formatted as lists
        self.assertIsInstance(formatted["messages"][0]["content"], list)
        self.assertEqual(formatted["messages"][0]["content"][0]["type"], "text")
        self.assertEqual(formatted["messages"][0]["content"][0]["text"], "You are a helpful assistant.")
        
        self.assertIsInstance(formatted["messages"][1]["content"], list)
        self.assertEqual(formatted["messages"][1]["content"][0]["type"], "text")
        self.assertEqual(formatted["messages"][1]["content"][0]["text"], "test")
        
        self.assertIsInstance(formatted["messages"][2]["content"], list)
        self.assertEqual(formatted["messages"][2]["content"][0]["type"], "text")
        self.assertEqual(formatted["messages"][2]["content"][0]["text"], "")
        
        # Verify that top_k is not included for Nova-Lite
        self.assertNotIn("top_k", formatted)
    
    @patch('boto3.client')
    def test_nova_pro_message_format(self, mock_boto3_client):
        """Test that the fixed wrapper correctly formats messages for Nova Pro."""
        # Setup mock
        mock_boto3_client.return_value = self.mock_bedrock_client
        self.mock_bedrock_client.converse.return_value = self.mock_response
        
        # Create NovaWrapper instance with Nova Pro
        nova_wrapper = NovaWrapper(
            model_id="us.amazon.nova-pro-v1:0",
            temperature=0.7,
            top_p=0.9,
            top_k=40
        )
        
        # Create messages with string content
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="test"),
            AIMessage(content="")
        ]
        
        # Format the messages
        formatted = nova_wrapper._format_messages(messages)
        
        # Verify the formatting
        self.assertEqual(len(formatted["messages"]), 3)
        
        # Check that content is kept as strings for Nova Pro
        self.assertEqual(formatted["messages"][0]["content"], "You are a helpful assistant.")
        self.assertEqual(formatted["messages"][1]["content"], "test")
        self.assertEqual(formatted["messages"][2]["content"], "")
        
        # Verify that top_k is included for Nova Pro
        self.assertIn("top_k", formatted)
        self.assertEqual(formatted["top_k"], 40)
    
    @patch('boto3.client')
    def test_nova_lite_inference_config_fix(self, mock_boto3_client):
        """Test that the fixed wrapper correctly configures inference parameters for Nova-Lite."""
        # Setup mock
        mock_boto3_client.return_value = self.mock_bedrock_client
        self.mock_bedrock_client.converse.return_value = self.mock_response
        
        # Create NovaWrapper instance with Nova-Lite
        nova_wrapper = NovaWrapper(
            model_id="us.amazon.nova-lite-v1:0",
            temperature=0.7,
            top_p=0.9,
            top_k=40  # This should be ignored for Nova-Lite
        )
        
        # Create messages
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="test")
        ]
        
        # Generate content
        result = nova_wrapper._generate(messages)
        
        # Verify that the converse method was called with the correct parameters
        call_args = self.mock_bedrock_client.converse.call_args
        
        # Check that topK is not in the inferenceConfig
        self.assertNotIn("topK", call_args[1]["inferenceConfig"])
        
        # Check that the other parameters are present
        self.assertIn("temperature", call_args[1]["inferenceConfig"])
        self.assertIn("topP", call_args[1]["inferenceConfig"])
        self.assertIn("maxTokens", call_args[1]["inferenceConfig"])
    
    @patch('boto3.client')
    def test_nova_pro_inference_config(self, mock_boto3_client):
        """Test that the fixed wrapper correctly configures inference parameters for Nova Pro."""
        # Setup mock
        mock_boto3_client.return_value = self.mock_bedrock_client
        self.mock_bedrock_client.converse.return_value = self.mock_response
        
        # Create NovaWrapper instance with Nova Pro
        nova_wrapper = NovaWrapper(
            model_id="us.amazon.nova-pro-v1:0",
            temperature=0.7,
            top_p=0.9,
            top_k=40
        )
        
        # Create messages
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="test")
        ]
        
        # Generate content
        result = nova_wrapper._generate(messages)
        
        # Verify that the converse method was called with the correct parameters
        call_args = self.mock_bedrock_client.converse.call_args
        
        # Check that topK is in the inferenceConfig for Nova Pro
        self.assertIn("topK", call_args[1]["inferenceConfig"])
        self.assertEqual(call_args[1]["inferenceConfig"]["topK"], 40)
        
        # Check that the other parameters are present
        self.assertIn("temperature", call_args[1]["inferenceConfig"])
        self.assertIn("topP", call_args[1]["inferenceConfig"])
        self.assertIn("maxTokens", call_args[1]["inferenceConfig"])
    
    @patch('boto3.client')
    def test_response_parsing(self, mock_boto3_client):
        """Test that the fixed wrapper correctly parses responses."""
        # Setup mock
        mock_boto3_client.return_value = self.mock_bedrock_client
        self.mock_bedrock_client.converse.return_value = self.mock_response
        
        # Create NovaWrapper instance
        nova_wrapper = NovaWrapper(
            model_id="us.amazon.nova-lite-v1:0",
            temperature=0.7,
            top_p=0.9
        )
        
        # Create messages
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="test")
        ]
        
        # Generate content
        result = nova_wrapper._generate(messages)
        
        # Verify the result
        self.assertEqual(len(result.generations), 1)
        self.assertEqual(result.generations[0].message.content, "This is a test response")


if __name__ == "__main__":
    unittest.main()
