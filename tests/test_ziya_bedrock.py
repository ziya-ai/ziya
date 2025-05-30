"""
Tests for the ZiyaBedrock wrapper class.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from app.agents.wrappers.ziya_bedrock import ZiyaBedrock
from langchain_core.messages import SystemMessage, HumanMessage


class TestZiyaBedrock(unittest.TestCase):
    """Tests for the ZiyaBedrock wrapper class."""
    
    def setUp(self):
        """Set up the test case."""
        # Mock the ChatBedrock class
        self.patcher = patch('app.agents.wrappers.ziya_bedrock.ChatBedrock')
        self.mock_chatbedrock = self.patcher.start()
        self.mock_bedrock_instance = MagicMock()
        self.mock_chatbedrock.return_value = self.mock_bedrock_instance
        
        # Mock the CustomBedrockClient
        self.client_patcher = patch('app.agents.wrappers.ziya_bedrock.CustomBedrockClient')
        self.mock_custom_client = self.client_patcher.start()
        
        # Create the ZiyaBedrock instance
        self.model = ZiyaBedrock(
            model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            region_name="us-west-2",
            temperature=0.7,
            max_tokens=4000,
            model_kwargs={"top_k": 40, "top_p": 0.9},
            thinking_mode=True
        )
    
    def tearDown(self):
        """Clean up after the test."""
        self.patcher.stop()
        self.client_patcher.stop()
    
    def test_init(self):
        """Test initialization."""
        # Check that ChatBedrock was initialized correctly
        self.mock_chatbedrock.assert_called_once()
        
        # Check that our parameters were stored
        self.assertEqual(self.model.ziya_temperature, 0.7)
        self.assertEqual(self.model.ziya_max_tokens, 4000)
        self.assertEqual(self.model.ziya_top_k, 40)
        self.assertEqual(self.model.ziya_top_p, 0.9)
        self.assertTrue(self.model.ziya_thinking_mode)
        
        # Check that CustomBedrockClient was used
        self.mock_custom_client.assert_called_once()
    
    def test_generate(self):
        """Test _generate method."""
        # Set up the mock
        self.mock_bedrock_instance._generate.return_value = "test_result"
        
        # Create test messages
        messages = [HumanMessage(content="Hello")]
        
        # Call the method
        result = self.model._generate(messages)
        
        # Check that the underlying model's _generate was called
        self.mock_bedrock_instance._generate.assert_called_once()
        
        # Check the result
        self.assertEqual(result, "test_result")
    
    def test_apply_thinking_mode_with_system_message(self):
        """Test _apply_thinking_mode with an existing system message."""
        # Create test messages with a system message
        messages = [
            SystemMessage(content="Original system message"),
            HumanMessage(content="Hello")
        ]
        
        # Apply thinking mode
        result = self.model._apply_thinking_mode(messages)
        
        # Check that the system message was modified
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].type, "system")
        self.assertTrue("Original system message" in result[0].content)
        self.assertTrue("Think through this step-by-step" in result[0].content)
    
    def test_apply_thinking_mode_without_system_message(self):
        """Test _apply_thinking_mode without an existing system message."""
        # Create test messages without a system message
        messages = [HumanMessage(content="Hello")]
        
        # Apply thinking mode
        result = self.model._apply_thinking_mode(messages)
        
        # Check that a system message was added
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].type, "system")
        self.assertTrue("Think through this step-by-step" in result[0].content)
        self.assertEqual(result[1].content, "Hello")
    
    def test_bind(self):
        """Test bind method."""
        # Call the bind method
        self.model.bind(max_tokens=2000, temperature=0.5)
        
        # Check that our parameters were updated
        self.assertEqual(self.model.ziya_max_tokens, 2000)
        self.assertEqual(self.model.ziya_temperature, 0.5)
        
        # Check that a new ChatBedrock instance was created
        self.assertEqual(self.mock_chatbedrock.call_count, 2)
        
        # Check that CustomBedrockClient was used again
        self.assertEqual(self.mock_custom_client.call_count, 2)
    
    def test_get_parameters(self):
        """Test get_parameters method."""
        # Call the method
        params = self.model.get_parameters()
        
        # Check the result
        self.assertEqual(params["max_tokens"], 4000)
        self.assertEqual(params["temperature"], 0.7)
        self.assertEqual(params["top_k"], 40)
        self.assertEqual(params["top_p"], 0.9)
        self.assertTrue(params["thinking_mode"])


if __name__ == "__main__":
    unittest.main()
