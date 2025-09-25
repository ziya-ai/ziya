"""
Test to reproduce the Pydantic validation error with Nova Pro model responses.
"""

import pytest
from unittest.mock import MagicMock, patch
from langchain_core.outputs import LLMResult
from app.agents.custom_message import ZiyaMessageChunk, ZiyaString

def test_llm_result_validation_error():
    """Test that reproduces the Pydantic validation error with Nova Pro."""
    # Create a ZiyaMessageChunk similar to what Nova Pro would return
    content = "Yes, I can hear you. How can I assist you with your codebase?"
    chunk = ZiyaMessageChunk(
        content=content,
        id="nova-test-id",
        response_metadata={"model": "nova-pro"},
        generation_info={}
    )
    
    # Verify the chunk has the expected attributes
    assert hasattr(chunk, 'id')
    assert hasattr(chunk, 'content')
    assert hasattr(chunk, 'text')
    assert hasattr(chunk, 'type')
    assert hasattr(chunk, 'response_metadata')
    assert hasattr(chunk, 'generation_info')
    
    # Try to create an LLMResult with this chunk
    # This should fail with a Pydantic validation error
    with pytest.raises(Exception) as excinfo:
        # This is similar to what happens in the LangChain pipeline
        result = LLMResult(
            generations=[[chunk]]  # LLMResult expects a list of lists of Generation objects
        )
    
    # Verify that the error is a Pydantic validation error
    error_str = str(excinfo.value)
    assert "Input should be a valid dictionary or instance of Generation" in error_str
    assert "ZiyaMessageChunk" in error_str

def test_llm_result_with_dict():
    """Test that LLMResult works with a dictionary representation."""
    # Create a dictionary representation of a Generation
    generation_dict = {
        "text": "Yes, I can hear you. How can I assist you with your codebase?",
        "generation_info": {}
    }
    
    # This should work without errors
    result = LLMResult(
        generations=[[generation_dict]]
    )
    
    # Verify the result has the expected structure
    assert len(result.generations) == 1
    assert len(result.generations[0]) == 1
    assert result.generations[0][0].text == "Yes, I can hear you. How can I assist you with your codebase?"

def test_generation_conversion():
    """Test converting ZiyaMessageChunk to a Generation object."""
    # Create a ZiyaMessageChunk
    content = "Test content"
    chunk = ZiyaMessageChunk(
        content=content,
        id="test-id",
        response_metadata={},
        generation_info={}
    )
    
    # Convert to a dictionary that can be used to create a Generation
    generation_dict = {
        "text": str(chunk.content),
        "generation_info": chunk.generation_info
    }
    
    # Create a Generation from the dictionary
    from langchain_core.outputs import Generation
    generation = Generation(**generation_dict)
    
    # Verify that the Generation was created with the correct attributes
    assert generation.text == "Test content"

def test_to_generation_method():
    """Test the to_generation method of ZiyaMessageChunk."""
    from langchain_core.outputs import Generation, LLMResult
    
    # Create a ZiyaMessageChunk
    content = "Test content"
    chunk = ZiyaMessageChunk(
        content=content,
        id="test-id",
        response_metadata={},
        generation_info={}
    )
    
    # Convert to a Generation object
    generation = chunk.to_generation()
    
    # Verify that the Generation was created with the correct attributes
    assert isinstance(generation, Generation)
    assert generation.text == "Test content"
    
    # Try to create an LLMResult with the Generation
    result = LLMResult(
        generations=[[generation]]
    )
    
    # Verify the result has the expected structure
    assert len(result.generations) == 1
    assert len(result.generations[0]) == 1
    assert result.generations[0][0].text == "Test content"
