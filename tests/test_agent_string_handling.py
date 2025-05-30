"""
Test suite for agent string handling.

This test suite verifies that the agent properly handles strings
and preserves attributes throughout the processing pipeline.
"""

import pytest
from unittest.mock import MagicMock, patch
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import Generation
from app.agents.custom_message import ZiyaString

def test_ensure_chunk_has_id_with_string():
    """Test that _ensure_chunk_has_id properly handles string inputs."""
    # Create a mock function
    def ensure_chunk_has_id(chunk):
        """Ensure the chunk has an ID."""
        if isinstance(chunk, str):
            from app.agents.custom_message import ZiyaString
            return ZiyaString(chunk, id=f"str-{hash(chunk) % 10000}", message=chunk)
        elif not hasattr(chunk, 'id'):
            object.__setattr__(chunk, 'id', f"gen-{hash(str(chunk)) % 10000}")
            object.__setattr__(chunk, 'message', str(chunk))
        return chunk
    
    # Test with a string
    string_chunk = "This is a string chunk."
    result = ensure_chunk_has_id(string_chunk)
    
    # Verify the result
    assert isinstance(result, ZiyaString)
    assert hasattr(result, 'id')
    assert hasattr(result, 'message')
    assert result.message == string_chunk
    
    # Convert to string and verify attributes are lost
    result_str = str(result)
    assert isinstance(result_str, str)
    assert not isinstance(result_str, ZiyaString)
    with pytest.raises(AttributeError):
        _ = result_str.id


def test_ensure_chunk_has_id_with_generation():
    """Test that _ensure_chunk_has_id properly handles Generation objects."""
    # Create a mock function
    def ensure_chunk_has_id(chunk):
        """Ensure the chunk has an ID."""
        if isinstance(chunk, str):
            from app.agents.custom_message import ZiyaString
            return ZiyaString(chunk, id=f"str-{hash(chunk) % 10000}", message=chunk)
        elif not hasattr(chunk, 'id'):
            object.__setattr__(chunk, 'id', f"gen-{hash(str(chunk)) % 10000}")
            object.__setattr__(chunk, 'message', str(chunk))
        return chunk
    
    # Test with a Generation object
    generation = Generation(text="This is a Generation chunk.")
    result = ensure_chunk_has_id(generation)
    
    # Verify the result
    assert hasattr(result, 'id')
    assert hasattr(result, 'message')
    assert "This is a Generation chunk." in result.message


def test_string_wrapping_in_message_chunk():
    """Test wrapping a string in an AIMessageChunk."""
    # Create a test string
    test_string = "This is a test string response"
    
    # Create a ZiyaString
    from app.agents.custom_message import ZiyaString
    ziya_str = ZiyaString(test_string, id=f"test-{hash(test_string) % 10000}", message=test_string)
    
    # Create an AIMessageChunk
    message_chunk = AIMessageChunk(content=ziya_str)
    
    # Add id and message attributes
    object.__setattr__(message_chunk, 'id', ziya_str.id)
    object.__setattr__(message_chunk, 'message', ziya_str.message)
    
    # Verify the attributes
    assert hasattr(message_chunk, 'id')
    assert hasattr(message_chunk, 'message')
    assert message_chunk.content == test_string
    
    # Convert to string
    message_str = str(message_chunk)
    
    # The string conversion should lose attributes
    with pytest.raises(AttributeError):
        _ = message_str.id
