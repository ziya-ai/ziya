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
        self.assertEqual(self.custom_client.user_max_tokens, 4000)
    
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
        self.mock_client.invoke_model_with_response_stream.assert_called_once()
        
        # Extract the actual body from the call
        actual_body = json.loads(self.mock_client.invoke_model_with_response_stream.call_args[1]['body'])
        expected_body_dict = json.loads(expected_body)
        
        # Check that the body was modified correctly
        self.assertEqual(actual_body.get("max_tokens"), expected_body_dict.get("max_tokens"))
        self.assertEqual(actual_body.get("messages"), expected_body_dict.get("messages"))
        
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
        
        # Check that the original method was called with the original body
        self.mock_client.invoke_model_with_response_stream.assert_called_once()
        
        # Extract the actual body from the call
        actual_body = json.loads(self.mock_client.invoke_model_with_response_stream.call_args[1]['body'])
        
        # Check that the body was not modified (preserves user's max_tokens)
        self.assertEqual(actual_body.get("max_tokens"), 8000)
        
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
    
    def test_extract_context_limit_info(self):
        """Test _extract_context_limit_info method."""
        # Create an error message
        error_message = "input length and `max_tokens` exceed context limit: 179563 + 64000 > 204698"
        
        # Extract the context limit info
        limit_info = self.custom_client._extract_context_limit_info(error_message)
        
        # Check the result
        self.assertEqual(limit_info["input_tokens"], 179563)
        self.assertEqual(limit_info["max_tokens"], 64000)
        self.assertEqual(limit_info["context_limit"], 204698)
    
    def test_calculate_safe_max_tokens(self):
        """Test _calculate_safe_max_tokens method."""
        # Calculate a safe max_tokens value
        safe_max_tokens = self.custom_client._calculate_safe_max_tokens(179563, 204698)
        
        # Check the result (should be context_limit - input_tokens - safety_margin)
        expected = 204698 - 179563 - self.custom_client.CLAUDE_SAFETY_MARGIN
        self.assertEqual(safe_max_tokens, expected)
    
    def test_retry_on_context_limit_error(self):
        """Test retry on context limit error."""
        # Create a request body
        body = json.dumps({
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 64000
        })
        
        # Set up the mock to raise an exception on first call and succeed on second call
        error_message = "input length and `max_tokens` exceed context limit: 179563 + 64000 > 204698"
        self.mock_client.invoke_model_with_response_stream.side_effect = [
            Exception(error_message),
            {"body": []}
        ]
        
        # Call the method
        result = self.custom_client.invoke_model_with_response_stream(modelId="test-model", body=body)
        
        # Check that the original method was called twice
        self.assertEqual(self.mock_client.invoke_model_with_response_stream.call_count, 2)
        
        # Extract the actual body from the second call
        actual_body = json.loads(self.mock_client.invoke_model_with_response_stream.call_args[1]['body'])
        
        # Check that the max_tokens was adjusted in the second call
        safe_max_tokens = 204698 - 179563 - self.custom_client.CLAUDE_SAFETY_MARGIN
        self.assertEqual(actual_body.get("max_tokens"), safe_max_tokens)
        
        # Check the result
        self.assertEqual(result, {"body": []})


if __name__ == "__main__":
    unittest.main()
