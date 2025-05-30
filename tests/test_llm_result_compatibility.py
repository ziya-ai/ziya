"""
Test to reproduce the LLMResult validation error with ZiyaMessageChunk.
"""

import pytest
from unittest.mock import MagicMock, patch
from langchain_core.outputs import LLMResult, Generation
from app.agents.custom_message import ZiyaMessageChunk, ZiyaString

def test_llm_result_direct_validation_error():
    """Test that reproduces the LLMResult validation error with ZiyaMessageChunk."""
    # Create a ZiyaMessageChunk
    content = "Yes, I can hear you. How can I assist you with your codebase?"
    chunk = ZiyaMessageChunk(
        content=content,
        id="test-chunk-id",
        response_metadata={"model": "test-model"},
        generation_info={}
    )
    
    # Try to create an LLMResult with this chunk directly
    # This should fail with a Pydantic validation error
    with pytest.raises(Exception) as excinfo:
        result = LLMResult(
            generations=[[chunk]]  # LLMResult expects a list of lists of Generation objects
        )
    
    # Verify that the error is a Pydantic validation error
    error_str = str(excinfo.value)
    assert "Input should be a valid dictionary or instance of Generation" in error_str
    assert "ZiyaMessageChunk" in error_str

def test_llm_result_with_generation():
    """Test that LLMResult works with a Generation object created from ZiyaMessageChunk."""
    # Create a ZiyaMessageChunk
    content = "Yes, I can hear you. How can I assist you with your codebase?"
    chunk = ZiyaMessageChunk(
        content=content,
        id="test-chunk-id",
        response_metadata={"model": "test-model"},
        generation_info={}
    )
    
    # Convert to a Generation object
    generation = chunk.to_generation()
    
    # Create an LLMResult with the Generation object
    # This should work without errors
    result = LLMResult(
        generations=[[generation]]
    )
    
    # Verify the result has the expected structure
    assert len(result.generations) == 1
    assert len(result.generations[0]) == 1
    assert result.generations[0][0].text == content

def test_simulate_langchain_pipeline():
    """Test that simulates the LangChain pipeline with ZiyaMessageChunk."""
    # Create a ZiyaMessageChunk
    content = "Yes, I can hear you. How can I assist you with your codebase?"
    chunk = ZiyaMessageChunk(
        content=content,
        id="test-chunk-id",
        response_metadata={"model": "test-model"},
        generation_info={}
    )
    
    # Simulate the LangChain pipeline
    # 1. Collect chunks from the model
    chunks = [chunk]
    
    # 2. Process chunks and create LLMResult
    processed_chunks = []
    for c in chunks:
        # This is where our conversion should happen
        if hasattr(c, 'to_generation'):
            processed_chunks.append(c.to_generation())
        else:
            processed_chunks.append(c)
    
    # 3. Create LLMResult
    result = LLMResult(
        generations=[processed_chunks]
    )
    
    # Verify the result has the expected structure
    assert len(result.generations) == 1
    assert len(result.generations[0]) == 1
    assert result.generations[0][0].text == content
def test_simulate_real_error_case():
    """Test that simulates the real error case we're seeing in the logs."""
    from langchain_core.outputs import LLMResult
    from app.agents.custom_message import ZiyaMessageChunk
    
    # Create a ZiyaMessageChunk
    content = "Yes, I can hear you. How can I assist you with your codebase?"
    chunk = ZiyaMessageChunk(
        content=content,
        id="nova-2909",
        response_metadata={},
        generation_info={}
    )
    
    # Convert to a Generation object
    generation = chunk.to_generation()
    print(f"Generation type: {type(generation)}")
    print(f"Generation attributes: {dir(generation)}")
    
    # This is what's happening in the code:
    # 1. We yield the Generation object
    yielded_object = generation
    
    # 2. But somehow, the ZiyaMessageChunk is still being used
    # Let's simulate this by trying to create an LLMResult with the original chunk
    with pytest.raises(Exception) as excinfo:
        result = LLMResult(
            generations=[[chunk]]
        )
    
    # Verify that the error matches what we're seeing in the logs
    error_str = str(excinfo.value)
    assert "Input should be a valid dictionary or instance of Generation" in error_str
    assert "ZiyaMessageChunk" in error_str
    
    # Now let's try to understand where the conversion might be failing
    # Let's check if the yielded object is being properly converted
    assert isinstance(yielded_object, Generation)
    
    # Let's check if there's any reference to the original chunk in the yielded object
    assert not hasattr(yielded_object, '_ziya_message_chunk')
    
    # Let's check if the yielded object can be used in an LLMResult
    result = LLMResult(
        generations=[[yielded_object]]
    )
    
    # Verify the result has the expected structure
    assert len(result.generations) == 1
    assert len(result.generations[0]) == 1
    assert result.generations[0][0].text == content
def test_multiple_conversion_layers():
    """Test that simulates multiple layers of conversion that might be happening."""
    from langchain_core.outputs import LLMResult, Generation
    from app.agents.custom_message import ZiyaMessageChunk
    
    # Create a ZiyaMessageChunk
    content = "Yes, I can hear you. How can I assist you with your codebase?"
    chunk = ZiyaMessageChunk(
        content=content,
        id="nova-2909",
        response_metadata={},
        generation_info={}
    )
    
    # Simulate what might be happening in the code:
    # 1. We convert to a Generation object
    generation = chunk.to_generation()
    
    # 2. But somewhere else, the original chunk is being used
    # Let's simulate this by creating a list that contains both objects
    mixed_list = [generation, chunk]
    
    # 3. Now let's try to create an LLMResult with this mixed list
    # This should fail because of the ZiyaMessageChunk
    with pytest.raises(Exception) as excinfo:
        result = LLMResult(
            generations=[mixed_list]
        )
    
    # Verify that the error matches what we're seeing in the logs
    error_str = str(excinfo.value)
    assert "Input should be a valid dictionary or instance of Generation" in error_str
    assert "ZiyaMessageChunk" in error_str
    
    # 4. Let's try to fix this by ensuring all items in the list are Generation objects
    fixed_list = [item.to_generation() if hasattr(item, 'to_generation') else item for item in mixed_list]
    
    # 5. Now let's try to create an LLMResult with the fixed list
    result = LLMResult(
        generations=[fixed_list]
    )
    
    # Verify the result has the expected structure
    assert len(result.generations) == 1
    assert len(result.generations[0]) == 2
    assert result.generations[0][0].text == content
    assert result.generations[0][1].text == content
def test_generation_object_storage():
    """Test that a pre-created Generation object can be stored and retrieved."""
    from langchain_core.outputs import Generation, LLMResult
    from app.agents.custom_message import ZiyaMessageChunk
    
    # Create a ZiyaMessageChunk
    content = "Yes, I can hear you. How can I assist you with your codebase?"
    chunk = ZiyaMessageChunk(
        content=content,
        id="test-chunk-id",
        response_metadata={},
        generation_info={}
    )
    
    # Create a Generation object
    generation = Generation(text=content, generation_info={})
    
    # Store the Generation object on the chunk
    object.__setattr__(chunk, "_generation", generation)
    
    # Retrieve the Generation object
    retrieved_generation = chunk.to_generation()
    
    # Verify that the retrieved object is the same as the stored object
    assert retrieved_generation is generation
    
    # Create an LLMResult with the retrieved Generation object
    result = LLMResult(
        generations=[[retrieved_generation]]
    )
    
    # Verify the result has the expected structure
    assert len(result.generations) == 1
    assert len(result.generations[0]) == 1
    assert result.generations[0][0].text == content
