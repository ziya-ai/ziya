"""
Tests for Nova Pro Generation object compatibility.
These tests verify that our fix for the Nova Pro validation error works correctly
and doesn't cause regressions with other models.
"""

import pytest
from unittest.mock import MagicMock, patch
from langchain_core.outputs import Generation, LLMResult
from langchain_core.messages import AIMessageChunk
from app.agents.custom_message import ZiyaMessageChunk, ZiyaString

class TestNovaGenerationCompatibility:
    """Test suite for Nova Pro Generation object compatibility."""

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

    def test_llm_result_with_converted_ziya_message_chunk(self):
        """Test that LLMResult works with a converted ZiyaMessageChunk."""
        # Create a ZiyaMessageChunk
        chunk = ZiyaMessageChunk(content="Test response", id="test-id")
        
        # Convert to a Generation object
        generation = chunk.to_generation()
        
        # Create an LLMResult with the Generation object
        result = LLMResult(generations=[[generation]])
        
        # Verify the result has the expected structure
        assert len(result.generations) == 1
        assert len(result.generations[0]) == 1
        assert result.generations[0][0].text == "Test response"

    def test_ai_message_chunk_compatibility(self):
        """Test that AIMessageChunk is still compatible with our changes."""
        # Create an AIMessageChunk
        chunk = AIMessageChunk(content="Test response")
        
        # Add id and message attributes
        object.__setattr__(chunk, 'id', "test-id")
        object.__setattr__(chunk, 'message', "Test response")
        
        # Verify that the chunk has the expected attributes
        assert chunk.id == "test-id"
        assert chunk.message == "Test response"
        assert chunk.content == "Test response"

    def test_generation_creation(self):
        """Test that Generation objects can be created directly."""
        # Create a Generation object
        generation = Generation(text="Test response", generation_info={})
        
        # Verify that the Generation object has the expected attributes
        assert generation.text == "Test response"
        assert generation.generation_info == {}
        
        # Verify that the Generation object can be used in an LLMResult
        result = LLMResult(generations=[[generation]])
        assert len(result.generations) == 1
        assert len(result.generations[0]) == 1
        assert result.generations[0][0].text == "Test response"

    def test_ziya_message_chunk_to_generation(self):
        """Test that ZiyaMessageChunk.to_generation works correctly."""
        # Create a ZiyaMessageChunk
        chunk = ZiyaMessageChunk(content="Test response", id="test-id")
        
        # Convert to a Generation object
        generation = chunk.to_generation()
        
        # Verify that the Generation object has the expected attributes
        assert isinstance(generation, Generation)
        assert generation.text == "Test response"
        
        # Verify that the Generation object can be used in an LLMResult
        result = LLMResult(generations=[[generation]])
        assert len(result.generations) == 1
        assert len(result.generations[0]) == 1
        assert result.generations[0][0].text == "Test response"

    def test_ziya_message_chunk_with_pre_created_generation(self):
        """Test that ZiyaMessageChunk with a pre-created Generation works correctly."""
        # Create a ZiyaMessageChunk
        chunk = ZiyaMessageChunk(content="Test response", id="test-id")
        
        # Create a Generation object
        generation = Generation(text="Pre-created response", generation_info={})
        
        # Store the Generation object on the chunk
        object.__setattr__(chunk, '_generation', generation)
        
        # Convert to a Generation object
        retrieved_generation = chunk.to_generation()
        
        # Verify that the retrieved Generation object is the pre-created one
        assert retrieved_generation is generation
        assert retrieved_generation.text == "Pre-created response"
        
        # Verify that the Generation object can be used in an LLMResult
        result = LLMResult(generations=[[retrieved_generation]])
        assert len(result.generations) == 1
        assert len(result.generations[0]) == 1
        assert result.generations[0][0].text == "Pre-created response"

    def test_error_generation_creation(self):
        """Test that error Generation objects can be created."""
        # Create an error message
        error_message = {
            "error": "test_error",
            "detail": "Test error message",
            "status_code": 500
        }
        
        # Create a Generation object with the error message
        error_text = f"Error: {error_message['detail']}"
        error_generation = Generation(text=error_text, generation_info={"error": error_message})
        
        # Verify that the Generation object has the expected attributes
        assert error_generation.text == "Error: Test error message"
        assert error_generation.generation_info["error"] == error_message
        
        # Verify that the Generation object can be used in an LLMResult
        result = LLMResult(generations=[[error_generation]])
        assert len(result.generations) == 1
        assert len(result.generations[0]) == 1
        assert "Error: Test error message" in result.generations[0][0].text
