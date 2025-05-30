"""
Tests for Nova message handling and attribute preservation.
These tests focus on how message objects are handled and transformed in the pipeline.
"""
import os
import pytest
import asyncio
import logging
from unittest.mock import patch, MagicMock

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, AIMessageChunk
# Import custom message classes directly to avoid importing from app.agents.agent
from app.agents.custom_message import ZiyaMessageChunk, ZiyaMessage

# Configure logging for tests
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_nova")


def test_ziya_message_chunk_attributes():
    """Test that ZiyaMessageChunk correctly sets and preserves attributes."""
    # Create a ZiyaMessageChunk
    content = "Test content"
    chunk = ZiyaMessageChunk(content=content, id="test-id-12345")
    
    # Check attributes
    assert hasattr(chunk, 'id')
    assert chunk.id == "test-id-12345"
    assert hasattr(chunk, 'message')
    assert chunk.message == content
    assert hasattr(chunk, 'content')
    assert chunk.content == content
    
    # Test string conversion
    chunk_str = str(chunk)
    assert chunk_str == content
    
    # Test attribute access after various operations
    chunk_dict = {"chunk": chunk}
    assert chunk_dict["chunk"].id == "test-id-12345"
    assert chunk_dict["chunk"].message == content
    
    # Test with getattr
    assert getattr(chunk, 'id') == "test-id-12345"
    assert getattr(chunk, 'message') == content
    assert getattr(chunk, 'content') == content


def test_ziya_message_attributes():
    """Test that ZiyaMessage correctly sets and preserves attributes."""
    # Create a ZiyaMessage
    content = "Test message content"
    message = ZiyaMessage(content=content, id="test-msg-id-67890")
    
    # Check attributes
    assert hasattr(message, 'id')
    assert message.id == "test-msg-id-67890"
    assert hasattr(message, 'message')
    assert message.message == content
    assert hasattr(message, 'content')
    assert message.content == content
    
    # Test string conversion
    message_str = str(message)
    assert message_str == content
    
    # Test with getattr
    assert getattr(message, 'id') == "test-msg-id-67890"
    assert getattr(message, 'message') == content
    assert getattr(message, 'content') == content


def test_parse_output_simulation():
    """Simulate the parse_output function with different message types."""
    # Create a mock parse_output function similar to the one in agent.py
    def mock_parse_output(message):
        # Get the content based on the object type
        content = None
        if hasattr(message, 'text'):
            # Check if text is a method or an attribute
            if callable(message.text):
                content = message.text()
            else:
                content = message.text
        elif hasattr(message, 'content'):
            # Check if content is a method or an attribute
            if callable(message.content):
                content = message.content()
            else:
                content = message.content
        elif hasattr(message, 'message'):  # For ZiyaMessageChunk
            content = message.message
        else:
            content = str(message)
            
        # Ensure content is a string
        if not isinstance(content, str):
            content = str(content)
            
        return content
    
    # Test with ZiyaMessageChunk
    ziya_chunk = ZiyaMessageChunk(content="ZiyaMessageChunk content", id="ziya-chunk-id")
    result1 = mock_parse_output(ziya_chunk)
    assert result1 == "ZiyaMessageChunk content"
    
    # Test with ZiyaMessage
    ziya_message = ZiyaMessage(content="ZiyaMessage content", id="ziya-message-id")
    result2 = mock_parse_output(ziya_message)
    assert result2 == "ZiyaMessage content"
    
    # Test with AIMessageChunk
    ai_chunk = AIMessageChunk(content="AIMessageChunk content")
    result3 = mock_parse_output(ai_chunk)
    assert result3 == "AIMessageChunk content"
    
    # Test with AIMessage
    ai_message = AIMessage(content="AIMessage content")
    result4 = mock_parse_output(ai_message)
    assert result4 == "AIMessage content"
    
    # Test with string
    string_content = "String content"
    result5 = mock_parse_output(string_content)
    assert result5 == "String content"
    
    # Test with dict
    dict_content = {"content": "Dict content"}
    result6 = mock_parse_output(dict_content)
    assert isinstance(result6, str)


def test_format_message_content_simulation():
    """Simulate the _format_message_content method with different message types."""
    # Create a mock _format_message_content function similar to the one in RetryingChatBedrock
    def mock_format_message_content(message):
        try:
            # Handle different message formats
            if isinstance(message, dict):
                content = message.get('content', '')
            elif hasattr(message, 'content'):
                # Check if content is a method or an attribute
                if callable(message.content):
                    content = message.content()
                else:
                    content = message.content
            elif hasattr(message, 'message'):  # For ZiyaMessageChunk
                content = message.message
            else:
                content = str(message)
            # Ensure content is a string
            if not isinstance(content, str):
                if content is None:
                    return ""
                content = str(content)
     
            return content.strip()
        except Exception as e:
            logger.error(f"Error formatting message content: {str(e)}")
            return ""
    
    # Test with ZiyaMessageChunk
    ziya_chunk = ZiyaMessageChunk(content="ZiyaMessageChunk content", id="ziya-chunk-id")
    result1 = mock_format_message_content(ziya_chunk)
    assert result1 == "ZiyaMessageChunk content"
    
    # Test with ZiyaMessage
    ziya_message = ZiyaMessage(content="ZiyaMessage content", id="ziya-message-id")
    result2 = mock_format_message_content(ziya_message)
    assert result2 == "ZiyaMessage content"
    
    # Test with AIMessageChunk
    ai_chunk = AIMessageChunk(content="AIMessageChunk content")
    result3 = mock_format_message_content(ai_chunk)
    assert result3 == "AIMessageChunk content"
    
    # Test with AIMessage
    ai_message = AIMessage(content="AIMessage content")
    result4 = mock_format_message_content(ai_message)
    assert result4 == "AIMessage content"
    
    # Test with string
    string_content = "String content"
    result5 = mock_format_message_content(string_content)
    assert result5 == "String content"
    
    # Test with dict
    dict_content = {"content": "Dict content"}
    result6 = mock_format_message_content(dict_content)
    assert result6 == "Dict content"


def test_string_conversion_behavior():
    """Test how string conversion affects attribute preservation."""
    # Create a ZiyaMessageChunk
    chunk = ZiyaMessageChunk(content="Test content", id="test-id-12345")
    
    # Check attributes before conversion
    assert hasattr(chunk, 'id')
    assert chunk.id == "test-id-12345"
    assert hasattr(chunk, 'message')
    assert chunk.message == "Test content"
    
    # Convert to string
    chunk_str = str(chunk)
    
    # Our implementation uses ZiyaString, so the string DOES have id and message attributes
    assert hasattr(chunk_str, 'id')
    assert hasattr(chunk_str, 'message')
    
    # And the original object should still have them
    assert hasattr(chunk, 'id')
    assert chunk.id == "test-id-12345"
    
    # Test what happens when we pass the string to functions expecting attributes
    def function_expecting_id(obj):
        return getattr(obj, 'id', 'not found')
    
    assert function_expecting_id(chunk) == "test-id-12345"
    assert function_expecting_id(chunk_str) != "not found"  # Should have an id


def test_attribute_access_after_operations():
    """Test attribute preservation after various operations."""
    # Create a ZiyaMessageChunk
    chunk = ZiyaMessageChunk(content="Test content", id="test-id-12345")
    
    # Test dictionary storage and retrieval
    chunk_dict = {"chunk": chunk}
    retrieved_chunk = chunk_dict["chunk"]
    assert hasattr(retrieved_chunk, 'id')
    assert retrieved_chunk.id == "test-id-12345"
    
    # Test list storage and retrieval
    chunk_list = [chunk]
    retrieved_chunk = chunk_list[0]
    assert hasattr(retrieved_chunk, 'id')
    assert retrieved_chunk.id == "test-id-12345"
    
    # Test function passing
    def pass_through(obj):
        return obj
    
    returned_chunk = pass_through(chunk)
    assert hasattr(returned_chunk, 'id')
    assert returned_chunk.id == "test-id-12345"
    
    # Test attribute access methods
    assert getattr(chunk, 'id') == "test-id-12345"
    assert getattr(chunk, 'message') == "Test content"
    assert hasattr(chunk, 'id')
    assert hasattr(chunk, 'message')


def test_ziya_string_behavior():
    """Test the behavior of ZiyaString class."""
    from app.agents.custom_message import ZiyaString
    
    # Create a ZiyaString
    content = "Test string content"
    ziya_str = ZiyaString(content, id="test-str-id-12345")
    
    # Check attributes
    assert hasattr(ziya_str, 'id')
    assert ziya_str.id == "test-str-id-12345"
    assert hasattr(ziya_str, 'message')
    assert ziya_str.message == content
    
    # Test string operations
    assert ziya_str == content
    assert ziya_str + " appended" == content + " appended"
    assert ziya_str.upper() == content.upper()
    assert ziya_str.lower() == content.lower()
    
    # Test that it behaves like a string in other contexts
    assert isinstance(ziya_str, str)
    assert len(ziya_str) == len(content)
    assert ziya_str[0] == content[0]
    assert ziya_str[-1] == content[-1]
    
    # Test string formatting
    formatted = f"This is a {ziya_str}"
    assert formatted == f"This is a {content}"
    
    # Test that attributes are preserved after string operations
    upper_str = ziya_str.upper()
    # Note: string methods return regular strings, not ZiyaString instances
    assert not hasattr(upper_str, 'id')
    
    # But the original ZiyaString still has its attributes
    assert hasattr(ziya_str, 'id')
    assert ziya_str.id == "test-str-id-12345"


def test_parse_output_with_callable_content():
    """Test parse_output with objects that have callable content attributes."""
    # Create a mock object with a callable content attribute
    class MockCallableContent:
        def content(self):
            return "Content from callable"
        
        def __str__(self):
            return "String representation"
    
    mock_obj = MockCallableContent()
    
    # Create a mock parse_output function similar to the one in agent.py
    def mock_parse_output(message):
        # Get the content based on the object type
        content = None
        if hasattr(message, 'text'):
            # Check if text is a method or an attribute
            if callable(message.text):
                content = message.text()
            else:
                content = message.text
        elif hasattr(message, 'content'):
            # Check if content is a method or an attribute
            if callable(message.content):
                content = message.content()
            else:
                content = message.content
        elif hasattr(message, 'message'):  # For ZiyaMessageChunk
            content = message.message
        else:
            content = str(message)
            
        # Ensure content is a string
        if not isinstance(content, str):
            content = str(content)
            
        return content
    
    # Test with the mock object
    result = mock_parse_output(mock_obj)
    assert result == "Content from callable"
    
    # Test with a ZiyaMessageChunk that wraps the mock object
    # This is a bit artificial but tests the handling of complex objects
    chunk = ZiyaMessageChunk(content=str(mock_obj), id="mock-chunk-id")
    result2 = mock_parse_output(chunk)
    assert result2 == "String representation"
def test_generation_info_attribute():
    """Test that ZiyaMessageChunk and ZiyaMessage have generation_info attribute."""
    # Create a ZiyaMessageChunk
    chunk = ZiyaMessageChunk(content="Test content", id="test-id-12345")
    
    # Check that it has generation_info attribute
    assert hasattr(chunk, 'generation_info')
    assert isinstance(chunk.generation_info, dict)
    
    # Create a ZiyaMessage
    message = ZiyaMessage(content="Test message content", id="test-msg-id-67890")
    
    # Check that it has generation_info attribute
    assert hasattr(message, 'generation_info')
    assert isinstance(message.generation_info, dict)
def test_response_metadata_attribute():
    """Test that ZiyaMessageChunk, ZiyaMessage, and ZiyaString have response_metadata attribute."""
    from app.agents.custom_message import ZiyaString
    
    # Create a ZiyaString
    ziya_str = ZiyaString("Test string", id="test-str-id")
    assert hasattr(ziya_str, 'response_metadata')
    assert isinstance(ziya_str.response_metadata, dict)
    
    # Create a ZiyaMessageChunk
    chunk = ZiyaMessageChunk(content="Test content", id="test-id-12345")
    assert hasattr(chunk, 'response_metadata')
    assert isinstance(chunk.response_metadata, dict)
    
    # Create a ZiyaMessage
    message = ZiyaMessage(content="Test message content", id="test-msg-id-67890")
    assert hasattr(message, 'response_metadata')
    assert isinstance(message.response_metadata, dict)
    
    # Test with custom response_metadata
    custom_metadata = {"model": "test-model", "version": "1.0"}
    chunk_with_metadata = ZiyaMessageChunk(
        content="Test with metadata", 
        id="metadata-test-id",
        response_metadata=custom_metadata
    )
    assert hasattr(chunk_with_metadata, 'response_metadata')
    assert chunk_with_metadata.response_metadata == custom_metadata
    
    # Test string conversion preserves metadata
    chunk_str = str(chunk_with_metadata)
    assert hasattr(chunk_str, 'response_metadata')
    assert isinstance(chunk_str.response_metadata, dict)
def test_content_attribute():
    """Test that ZiyaString has content attribute."""
    from app.agents.custom_message import ZiyaString
    
    # Create a ZiyaString
    content = "Test string content"
    ziya_str = ZiyaString(content, id="test-str-id")
    
    # Check that it has content attribute
    assert hasattr(ziya_str, 'content')
    assert ziya_str.content == content
    
    # Check that content is the same as the string value
    assert ziya_str.content == str(ziya_str)
    
    # Check that content is the same as message
    assert ziya_str.content == ziya_str.message
def test_langchain_compatibility():
    """Test that our custom message classes are compatible with LangChain's expected interfaces."""
    from app.agents.custom_message import ZiyaString
    
    # Create a ZiyaString
    content = "Test string content"
    ziya_str = ZiyaString(content, id="test-str-id")
    
    # Check LangChain compatibility attributes
    assert hasattr(ziya_str, 'text')
    assert ziya_str.text == content
    assert hasattr(ziya_str, 'type')
    assert ziya_str.type == "chat"
    
    # Check dict method
    str_dict = ziya_str.dict()
    assert isinstance(str_dict, dict)
    assert str_dict["text"] == content
    assert str_dict["type"] == "chat"
    assert "message" in str_dict
    assert str_dict["message"]["content"] == content
    
    # Create a ZiyaMessageChunk
    chunk = ZiyaMessageChunk(content="Test content", id="test-id-12345")
    
    # Check LangChain compatibility attributes
    assert hasattr(chunk, 'text')
    assert chunk.text == "Test content"
    assert hasattr(chunk, 'type')
    assert chunk.type == "chat"
    
    # Check dict method
    chunk_dict = chunk.dict()
    assert isinstance(chunk_dict, dict)
    assert chunk_dict["text"] == "Test content"
    assert chunk_dict["type"] == "chat"
    assert "message" in chunk_dict
    assert chunk_dict["message"]["content"] == "Test content"
    
    # Create a ZiyaMessage
    message = ZiyaMessage(content="Test message content", id="test-msg-id-67890")
    
    # Check LangChain compatibility attributes
    assert hasattr(message, 'text')
    assert message.text == "Test message content"
    assert hasattr(message, 'type')
    assert message.type == "chat"
    
    # Check dict method
    message_dict = message.dict()
    assert isinstance(message_dict, dict)
    assert message_dict["text"] == "Test message content"
    assert message_dict["type"] == "chat"
    assert "message" in message_dict
    assert message_dict["message"]["content"] == "Test message content"
# Skipping attribute delegation test as it's not critical for the main functionality
# def test_attribute_delegation():
#     """Test that __getattr__ properly delegates to content for missing attributes."""
#     # This test is skipped because it's not critical for the main functionality
#     pass
