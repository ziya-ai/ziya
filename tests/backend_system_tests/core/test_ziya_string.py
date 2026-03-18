"""
Test suite for ZiyaString class.

This test suite verifies that ZiyaString properly preserves attributes
when converted to string and back.
"""

import pytest
from app.agents.custom_message import ZiyaString

def test_ziya_string_creation():
    """Test creating a ZiyaString."""
    text = "This is a test string"
    ziya_str = ZiyaString(text, id="test-id")

    # ZiyaString is a str subclass — content check via equality
    assert ziya_str == text
    assert ziya_str.id == "test-id"
    # ZiyaString stores kwargs as attrs; 'message' is not auto-set
    assert not hasattr(ziya_str, 'message')

def test_ziya_string_conversion():
    """Test converting a ZiyaString to a regular string."""
    text = "This is a test string"
    ziya_str = ZiyaString(text, id="test-id")

    regular_str = str(ziya_str)

    assert isinstance(regular_str, str)
    assert not isinstance(regular_str, ZiyaString)
    assert regular_str == text

    # Attributes are lost on plain str conversion
    with pytest.raises(AttributeError):
        _ = regular_str.id

def test_ziya_string_operations():
    """Test string operations on ZiyaString."""
    text = "This is a test string"
    ziya_str = ZiyaString(text, id="test-id")

    assert ziya_str.upper() == "THIS IS A TEST STRING"
    assert ziya_str.lower() == "this is a test string"
    assert ziya_str.replace("test", "sample") == "This is a sample string"
    assert ziya_str.split() == ["This", "is", "a", "test", "string"]
    assert ziya_str.strip() == text
    assert ziya_str + " with more text" == "This is a test string with more text"
    assert "test" in ziya_str
    assert len(ziya_str) == len(text)

def test_ziya_string_with_custom_attributes():
    """Test ZiyaString with custom attributes via kwargs."""
    text = "This is a test string"
    ziya_str = ZiyaString(text, id="test-id", custom_attr="custom-value",
                          message=text)

    assert ziya_str == text
    assert ziya_str.id == "test-id"
    # 'message' is available because we passed it as a kwarg
    assert ziya_str.message == text
    assert ziya_str.custom_attr == "custom-value"

def test_ziya_string_in_message_chunk():
    """Test using ZiyaString in a message chunk."""
    from langchain_core.messages import AIMessageChunk

    text = "This is a test string"
    ziya_str = ZiyaString(text, id="test-id")

    message_chunk = AIMessageChunk(content=ziya_str)

    assert message_chunk.content == text

    # Content is now a regular string inside the chunk
    assert not isinstance(message_chunk.content, ZiyaString)

    # Attach ZiyaString as a separate attribute
    object.__setattr__(message_chunk, 'ziya_content', ziya_str)

    assert message_chunk.ziya_content == text
    assert isinstance(message_chunk.ziya_content, ZiyaString)
    assert message_chunk.ziya_content.id == "test-id"
