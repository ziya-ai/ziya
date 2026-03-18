"""
Tests for message handling and attribute preservation.

Updated: ZiyaMessage class removed. Only ZiyaMessageChunk remains.
Tests updated to cover ZiyaMessageChunk attribute behavior.
"""

import pytest
from app.agents.custom_message import ZiyaMessageChunk, ZiyaString


def test_ziya_message_chunk_attributes():
    """Test that ZiyaMessageChunk correctly sets and preserves attributes."""
    content = "Test content"
    chunk = ZiyaMessageChunk(content=content, id="test-id-12345")

    assert hasattr(chunk, 'id')
    assert chunk.id == "test-id-12345"
    assert hasattr(chunk, 'message')
    assert chunk.message == content
    assert hasattr(chunk, 'content')
    assert chunk.content == content

    # Test attribute access after dict storage
    chunk_dict = {"chunk": chunk}
    assert chunk_dict["chunk"].id == "test-id-12345"
    assert chunk_dict["chunk"].message == content

    # Test with getattr
    assert getattr(chunk, 'id') == "test-id-12345"
    assert getattr(chunk, 'message') == content
    assert getattr(chunk, 'content') == content


def test_ziya_message_chunk_string_operations():
    """ZiyaMessageChunk.content should contain the original text."""
    chunk = ZiyaMessageChunk(content="Hello World", id="id-1")
    assert chunk.content == "Hello World"
    assert "Hello" in chunk.content


def test_ziya_message_chunk_empty_content():
    """Test ZiyaMessageChunk with empty content."""
    chunk = ZiyaMessageChunk(content="", id="empty-1")
    assert chunk.content == ""
    assert chunk.message == ""


def test_ziya_message_chunk_multiline():
    """Test ZiyaMessageChunk with multiline content."""
    content = "Line 1\nLine 2\nLine 3"
    chunk = ZiyaMessageChunk(content=content, id="multi-1")
    assert chunk.content == content
    assert "\n" in chunk.content


def test_ziya_string_attributes():
    """Test ZiyaString preserves string behavior."""
    s = ZiyaString("Test string content")
    assert str(s) == "Test string content"
    assert "string" in s
    assert s.startswith("Test")
    assert s.endswith("content")


def test_ziya_string_with_special_chars():
    """Test ZiyaString with special characters."""
    s = ZiyaString("Content with ```code``` and **bold**")
    assert "```" in s
    assert len(s) > 0
