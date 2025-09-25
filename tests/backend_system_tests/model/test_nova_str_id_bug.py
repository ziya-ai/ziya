"""
Test for the 'str' object has no attribute 'id' bug with Nova Pro.
This test demonstrates the issue where a Generation object is converted to a string
and then the code tries to access the 'id' attribute on that string.
"""

import pytest
from unittest.mock import MagicMock, patch
from langchain_core.outputs import Generation
from langchain_core.messages import AIMessageChunk, HumanMessage
import types

class TestNovaStrIdBug:
    """Test suite for the 'str' object has no attribute 'id' bug."""

    def test_generation_string_conversion(self):
        """Test that demonstrates the bug where a Generation object is converted to a string."""
        # Create a test string
        response_text = "This is a test response from Nova Pro."
        
        # Create a Generation object
        generation = Generation(text=response_text)
        
        # The default string representation includes the class name and attributes
        generation_str = str(generation)
        
        # Add id attribute to the Generation object using object.__setattr__
        object.__setattr__(generation, 'id', "test-id")
        
        # Convert to string again
        generation_str = str(generation)
        
        # The string conversion should still lose attributes
        with pytest.raises(AttributeError, match="'str' object has no attribute 'id'"):
            _ = generation_str.id
    
    def test_ziya_string_preservation(self):
        """Test that ZiyaString preserves attributes after string conversion."""
        # Import ZiyaString
        from app.agents.custom_message import ZiyaString
        
        # Create a test string
        response_text = "This is a test response from Nova Pro."
        
        # Create a ZiyaString
        ziya_str = ZiyaString(response_text, id="test-id")
        
        # Verify it has the attributes
        assert hasattr(ziya_str, 'id')
        assert ziya_str.id == "test-id"
        assert hasattr(ziya_str, 'message')
        assert ziya_str.message == response_text
        
        # Convert to string using str()
        str_value = str(ziya_str)
        
        # This should be a regular string now, not a ZiyaString
        assert isinstance(str_value, str)
        assert not isinstance(str_value, ZiyaString)
        
        # The string conversion should lose attributes
        with pytest.raises(AttributeError, match="'str' object has no attribute 'id'"):
            _ = str_value.id
