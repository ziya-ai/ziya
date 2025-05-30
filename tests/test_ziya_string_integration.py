"""
Integration tests for ZiyaString with model wrappers.

This test suite verifies that ZiyaString properly integrates with
model wrappers and preserves attributes throughout the processing pipeline.
"""

import pytest
from unittest.mock import MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.outputs import Generation
from app.agents.custom_message import ZiyaString

class MockResponse:
    """Mock responses for various model wrappers."""
    
    @staticmethod
    def create_nova_response(text):
        """Create a mock Nova response."""
        return {
            "ResponseMetadata": {
                "RequestId": "test-request-id",
                "HTTPStatusCode": 200,
                "HTTPHeaders": {
                    "date": "Wed, 26 Mar 2025 07:32:22 GMT",
                    "content-type": "application/json",
                    "content-length": "404",
                    "connection": "keep-alive",
                    "x-amzn-requestid": "test-request-id"
                },
                "RetryAttempts": 0
            },
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": text}]
                }
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 100, "outputTokens": 50},
            "metrics": {"latencyMs": 1000}
        }
    
    @staticmethod
    def create_claude_response(text):
        """Create a mock Claude response."""
        return {
            "completion": text,
            "stop_reason": "stop_sequence",
            "amazon-bedrock-invocationMetrics": {
                "inputTokenCount": 100,
                "outputTokenCount": 50,
                "invocationLatency": 1000,
                "firstByteLatency": 500
            }
        }


@patch('boto3.client')
def test_nova_wrapper_ziya_string_integration(mock_boto3_client):
    """Test ZiyaString integration with Nova wrapper."""
    # Import the NovaWrapper class
    from app.agents.nova_wrapper import NovaWrapper
    
    # Create a mock client
    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client
    
    # Set up the mock response
    response_text = "This is a test response from Nova."
    mock_client.converse.return_value = MockResponse.create_nova_response(response_text)
    
    # Initialize the wrapper
    wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
    
    # Create test messages
    messages = [
        HumanMessage(content="Hello, Nova!")
    ]
    
    # Generate a response
    result = wrapper._generate(messages)
    
    # Verify the result
    assert len(result.generations) == 1
    assert result.generations[0].message.content == response_text
    assert hasattr(result.generations[0], 'id')
    assert hasattr(result.generations[0], 'message')
    
    # Convert to string and verify attributes are lost
    generation_str = str(result.generations[0])
    assert isinstance(generation_str, str)
    with pytest.raises(AttributeError):
        _ = generation_str.id
    
    # Extract the message content and verify it's a string
    message_content = result.generations[0].message.content
    assert isinstance(message_content, str)
    
    # If it's a ZiyaString, verify attributes are preserved
    if isinstance(message_content, ZiyaString):
        assert hasattr(message_content, 'id')
        assert hasattr(message_content, 'message')
        assert message_content.message == response_text


def test_ziya_string_in_generation():
    """Test using ZiyaString in a Generation object."""
    # Create a ZiyaString
    text = "This is a test response."
    ziya_str = ZiyaString(text, id="test-id")
    
    # Create a Generation object with the ZiyaString
    generation = Generation(text=ziya_str)
    
    # Verify the text is the ZiyaString
    assert generation.text == text
    
    # But the text is now a regular string, not a ZiyaString
    assert not isinstance(generation.text, ZiyaString)
    
    # Add the ZiyaString as a separate attribute
    object.__setattr__(generation, 'ziya_text', ziya_str)
    
    # Verify the attribute is the ZiyaString
    assert generation.ziya_text == text
    assert isinstance(generation.ziya_text, ZiyaString)
    assert generation.ziya_text.id == "test-id"
    
    # Convert to string and verify attributes are lost
    generation_str = str(generation)
    assert isinstance(generation_str, str)
    with pytest.raises(AttributeError):
        _ = generation_str.id


def test_ziya_string_in_processing_pipeline():
    """Test ZiyaString in a processing pipeline."""
    # Create a ZiyaString
    text = "This is a test response."
    ziya_str = ZiyaString(text, id="test-id")
    
    # Step 1: Create a Generation object
    generation = Generation(text=ziya_str)
    object.__setattr__(generation, 'id', ziya_str.id)
    object.__setattr__(generation, 'message', ziya_str.message)
    
    # Verify attributes
    assert hasattr(generation, 'id')
    assert hasattr(generation, 'message')
    assert generation.id == "test-id"
    assert generation.message == text
    
    # Step 2: Convert to string (this would happen in the processing pipeline)
    generation_str = str(generation)
    
    # Verify attributes are lost
    assert isinstance(generation_str, str)
    with pytest.raises(AttributeError):
        _ = generation_str.id
    
    # Step 3: Wrap in a ZiyaString again
    wrapped_str = ZiyaString(generation_str, id=generation.id, message=generation.message)
    
    # Verify attributes are preserved
    assert hasattr(wrapped_str, 'id')
    assert hasattr(wrapped_str, 'message')
    assert wrapped_str.id == "test-id"
    assert wrapped_str.message == text


def test_ziya_string_in_agent_ensure_chunk_has_id():
    """Test ZiyaString in the agent's _ensure_chunk_has_id method."""
    # Create a mock agent
    class MockAgent:
        def _ensure_chunk_has_id(self, chunk):
            """Ensure the chunk has an ID."""
            if isinstance(chunk, str):
                from app.agents.custom_message import ZiyaString
                return ZiyaString(chunk, id=f"str-{hash(chunk) % 10000}", message=chunk)
            elif not hasattr(chunk, 'id'):
                object.__setattr__(chunk, 'id', f"gen-{hash(str(chunk)) % 10000}")
                object.__setattr__(chunk, 'message', str(chunk))
            return chunk
    
    agent = MockAgent()
    
    # Test with a string
    string_chunk = "This is a string chunk."
    result = agent._ensure_chunk_has_id(string_chunk)
    
    # Verify the result
    assert isinstance(result, ZiyaString)
    assert hasattr(result, 'id')
    assert hasattr(result, 'message')
    assert result.message == string_chunk
    
    # Test with a Generation object
    generation = Generation(text="This is a Generation chunk.")
    result = agent._ensure_chunk_has_id(generation)
    
    # Verify the result
    assert hasattr(result, 'id')
    assert hasattr(result, 'message')
    assert result.message == "This is a Generation chunk."
