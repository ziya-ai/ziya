"""
Test suite for ZiyaString class.

This test suite verifies that ZiyaString properly preserves attributes
when converted to string and back.
"""

import pytest
from app.agents.custom_message import ZiyaString

def test_ziya_string_creation():
    """Test creating a ZiyaString."""
    # Create a ZiyaString
    text = "This is a test string"
    ziya_str = ZiyaString(text, id="test-id")
    
    # Verify attributes
    assert ziya_str == text
    assert ziya_str.id == "test-id"
    assert ziya_str.message == text

def test_ziya_string_conversion():
    """Test converting a ZiyaString to a regular string."""
    # Create a ZiyaString
    text = "This is a test string"
    ziya_str = ZiyaString(text, id="test-id")
    
    # Convert to string
    regular_str = str(ziya_str)
    
    # Verify it's a regular string now
    assert isinstance(regular_str, str)
    assert not isinstance(regular_str, ZiyaString)
    assert regular_str == text
    
    # Verify attributes are lost
    with pytest.raises(AttributeError):
        _ = regular_str.id
    with pytest.raises(AttributeError):
        _ = regular_str.message

def test_ziya_string_operations():
    """Test string operations on ZiyaString."""
    # Create a ZiyaString
    text = "This is a test string"
    ziya_str = ZiyaString(text, id="test-id")
    
    # Test string operations
    assert ziya_str.upper() == "THIS IS A TEST STRING"
    assert ziya_str.lower() == "this is a test string"
    assert ziya_str.replace("test", "sample") == "This is a sample string"
    assert ziya_str.split() == ["This", "is", "a", "test", "string"]
    assert ziya_str.strip() == text
    assert ziya_str + " with more text" == "This is a test string with more text"
    assert "test" in ziya_str
    assert len(ziya_str) == len(text)

def test_ziya_string_with_custom_attributes():
    """Test ZiyaString with custom attributes."""
    # Create a ZiyaString with custom attributes
    text = "This is a test string"
    ziya_str = ZiyaString(text, id="test-id", custom_attr="custom-value")
    
    # Verify attributes
    assert ziya_str == text
    assert ziya_str.id == "test-id"
    assert ziya_str.message == text
    assert ziya_str.custom_attr == "custom-value"

def test_ziya_string_in_message_chunk():
    """Test using ZiyaString in a message chunk."""
    from langchain_core.messages import AIMessageChunk
    
    # Create a ZiyaString
    text = "This is a test string"
    ziya_str = ZiyaString(text, id="test-id")
    
    # Create a message chunk with the ZiyaString
    message_chunk = AIMessageChunk(content=ziya_str)
    
    # Verify the content is the ZiyaString
    assert message_chunk.content == text
    
    # But the content is now a regular string, not a ZiyaString
    assert not isinstance(message_chunk.content, ZiyaString)
    
    # Add the ZiyaString as a separate attribute
    object.__setattr__(message_chunk, 'ziya_content', ziya_str)
    
    # Verify the attribute is the ZiyaString
    assert message_chunk.ziya_content == text
    assert isinstance(message_chunk.ziya_content, ZiyaString)
    assert message_chunk.ziya_content.id == "test-id"
