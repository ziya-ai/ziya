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
    # ZiyaString only carries the attributes it was given; the pipeline step
    # below reads ``message`` back, so it has to be set here.
    ziya_str = ZiyaString(text, id="test-id", message=text)
    
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
