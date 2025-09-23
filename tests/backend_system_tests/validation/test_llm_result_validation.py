"""
Tests for LLMResult validation with different object types.
"""

import pytest
from unittest.mock import MagicMock
from langchain_core.outputs import LLMResult, Generation
from langchain_core.messages import AIMessageChunk, AIMessage
from app.agents.custom_message import ZiyaMessageChunk, ZiyaString, ZiyaMessage

class TestLLMResultValidation:
    """Test suite for LLMResult validation with different object types."""

    def test_llm_result_with_generation(self):
        """Test that LLMResult works with a Generation object."""
        # Create a Generation object
        generation = Generation(text="Test response", generation_info={})
        
        # Create an LLMResult with the Generation object
        result = LLMResult(generations=[[generation]])
        
        # Verify the result has the expected structure
        assert len(result.generations) == 1
        assert len(result.generations[0]) == 1
        assert result.generations[0][0] is generation
        assert result.generations[0][0].text == "Test response"

    def test_llm_result_with_ai_message_chunk_fails(self):
        """Test that LLMResult fails with an AIMessageChunk."""
        # Create an AIMessageChunk
        chunk = AIMessageChunk(content="Test response")
        
        # Try to create an LLMResult with the AIMessageChunk
        with pytest.raises(Exception) as excinfo:
            LLMResult(generations=[[chunk]])
        
        # Verify that the error is a validation error
        error_str = str(excinfo.value)
        assert "Input should be a valid dictionary or instance of Generation" in error_str

    def test_llm_result_with_ai_message_fails(self):
        """Test that LLMResult fails with an AIMessage."""
        # Create an AIMessage
        message = AIMessage(content="Test response")
        
        # Try to create an LLMResult with the AIMessage
        with pytest.raises(Exception) as excinfo:
            LLMResult(generations=[[message]])
        
        # Verify that the error is a validation error
        error_str = str(excinfo.value)
        assert "Input should be a valid dictionary or instance of Generation" in error_str

    def test_llm_result_with_ziya_message_chunk_fails(self):
        """Test that LLMResult fails with a ZiyaMessageChunk."""
        # Create a ZiyaMessageChunk
        chunk = ZiyaMessageChunk(content="Test response", id="test-id")
        
        # Try to create an LLMResult with the ZiyaMessageChunk
        with pytest.raises(Exception) as excinfo:
            LLMResult(generations=[[chunk]])
        
        # Verify that the error is a validation error
        error_str = str(excinfo.value)
        assert "Input should be a valid dictionary or instance of Generation" in error_str

    def test_llm_result_with_ziya_message_fails(self):
        """Test that LLMResult fails with a ZiyaMessage."""
        # Create a ZiyaMessage
        message = ZiyaMessage(content="Test response", id="test-id")
        
        # Try to create an LLMResult with the ZiyaMessage
        with pytest.raises(Exception) as excinfo:
            LLMResult(generations=[[message]])
        
        # Verify that the error is a validation error
        error_str = str(excinfo.value)
        assert "Input should be a valid dictionary or instance of Generation" in error_str

    def test_llm_result_with_ziya_string_fails(self):
        """Test that LLMResult fails with a ZiyaString."""
        # Create a ZiyaString
        ziya_string = ZiyaString("Test response", id="test-id")
        
        # Try to create an LLMResult with the ZiyaString
        with pytest.raises(Exception) as excinfo:
            LLMResult(generations=[[ziya_string]])
        
        # Verify that the error is a validation error
        error_str = str(excinfo.value)
        assert "Input should be a valid dictionary or instance of Generation" in error_str

    def test_llm_result_with_string_fails(self):
        """Test that LLMResult fails with a string."""
        # Create a string
        string = "Test response"
        
        # Try to create an LLMResult with the string
        with pytest.raises(Exception) as excinfo:
            LLMResult(generations=[[string]])
        
        # Verify that the error is a validation error
        error_str = str(excinfo.value)
        assert "Input should be a valid dictionary or instance of Generation" in error_str

    def test_llm_result_with_dict(self):
        """Test that LLMResult works with a dict."""
        # Create a dict
        dict_obj = {"text": "Test response", "generation_info": {}}
        
        # Create an LLMResult with the dict
        result = LLMResult(generations=[[dict_obj]])
        
        # Verify the result has the expected structure
        assert len(result.generations) == 1
        assert len(result.generations[0]) == 1
        assert result.generations[0][0].text == "Test response"

    def test_llm_result_with_converted_objects(self):
        """Test that LLMResult works with converted objects."""
        # Create different types of objects
        ai_chunk = AIMessageChunk(content="AIMessageChunk response")
        ziya_chunk = ZiyaMessageChunk(content="ZiyaMessageChunk response", id="ziya-chunk-id")
        ai_message = AIMessage(content="AIMessage response")
        ziya_message = ZiyaMessage(content="ZiyaMessage response", id="ziya-message-id")
        ziya_string = ZiyaString("ZiyaString response", id="ziya-string-id")
        string = "String response"
        
        # Convert all objects to Generation objects
        objects = [ai_chunk, ziya_chunk, ai_message, ziya_message, ziya_string, string]
        generations = []
        
        for obj in objects:
            if hasattr(obj, 'to_generation'):
                generations.append(obj.to_generation())
            elif isinstance(obj, Generation):
                generations.append(obj)
            else:
                content = obj.content if hasattr(obj, 'content') else str(obj)
                generations.append(Generation(text=str(content), generation_info={}))
        
        # Create an LLMResult with the Generation objects
        result = LLMResult(generations=[generations])
        
        # Verify the result has the expected structure
        assert len(result.generations) == 1
        assert len(result.generations[0]) == 6
        assert result.generations[0][0].text == "AIMessageChunk response"
        assert result.generations[0][1].text == "ZiyaMessageChunk response"
        assert result.generations[0][2].text == "AIMessage response"
        assert result.generations[0][3].text == "ZiyaMessage response"
        assert result.generations[0][4].text == "ZiyaString response"
        assert result.generations[0][5].text == "String response"
