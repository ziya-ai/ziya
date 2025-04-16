"""
Tests for the CustomBedrockClient class.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from app.utils.custom_bedrock import CustomBedrockClient


class TestCustomBedrockClient(unittest.TestCase):
    """Tests for the CustomBedrockClient class."""
    
    def setUp(self):
        """Set up the test case."""
        self.mock_client = MagicMock()
        self.mock_client.invoke_model_with_response_stream = MagicMock(return_value={"body": []})
        self.mock_client.invoke_model = MagicMock(return_value={"body": b"{}"})
        
        # Create the custom client
        self.custom_client = CustomBedrockClient(self.mock_client, max_tokens=4000)
    
    def test_init(self):
        """Test initialization."""
        self.assertEqual(self.custom_client.client, self.mock_client)
        self.assertEqual(self.custom_client.max_tokens, 4000)
    
    def test_invoke_model_with_response_stream_no_body(self):
        """Test invoke_model_with_response_stream with no body."""
        # Call the method
        result = self.custom_client.invoke_model_with_response_stream(modelId="test-model")
        
        # Check that the original method was called
        self.mock_client.invoke_model_with_response_stream.assert_called_once_with(modelId="test-model")
        
        # Check the result
        self.assertEqual(result, {"body": []})
    
    def test_invoke_model_with_response_stream_with_body_no_max_tokens(self):
        """Test invoke_model_with_response_stream with body but no max_tokens."""
        # Create a request body
        body = json.dumps({"messages": [{"role": "user", "content": "Hello"}]})
        
        # Call the method
        result = self.custom_client.invoke_model_with_response_stream(modelId="test-model", body=body)
        
        # Check that the original method was called with modified body
        expected_body = json.dumps({
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 4000
        })
        self.mock_client.invoke_model_with_response_stream.assert_called_once_with(
            modelId="test-model", body=expected_body
        )
        
        # Check the result
        self.assertEqual(result, {"body": []})
    
    def test_invoke_model_with_response_stream_with_body_with_max_tokens(self):
        """Test invoke_model_with_response_stream with body and max_tokens."""
        # Create a request body
        body = json.dumps({
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 8000
        })
        
        # Call the method
        result = self.custom_client.invoke_model_with_response_stream(modelId="test-model", body=body)
        
        # Check that the original method was called with modified body
        expected_body = json.dumps({
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 4000
        })
        self.mock_client.invoke_model_with_response_stream.assert_called_once_with(
            modelId="test-model", body=expected_body
        )
        
        # Check the result
        self.assertEqual(result, {"body": []})
    
    def test_invoke_model_with_invalid_body(self):
        """Test invoke_model_with_response_stream with invalid body."""
        # Create an invalid request body
        body = "not-json"
        
        # Call the method
        result = self.custom_client.invoke_model_with_response_stream(modelId="test-model", body=body)
        
        # Check that the original method was called with the original body
        self.mock_client.invoke_model_with_response_stream.assert_called_once_with(
            modelId="test-model", body=body
        )
        
        # Check the result
        self.assertEqual(result, {"body": []})
    
    def test_getattr(self):
        """Test __getattr__ method."""
        # Set up a mock attribute
        self.mock_client.some_attribute = "test-value"
        
        # Access the attribute through the custom client
        value = self.custom_client.some_attribute
        
        # Check the value
        self.assertEqual(value, "test-value")


if __name__ == "__main__":
    unittest.main()
