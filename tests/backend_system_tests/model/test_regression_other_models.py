"""
Regression tests for other models to ensure our Nova Pro fix doesn't break them.
"""

import pytest
from unittest.mock import MagicMock, patch
from langchain_core.outputs import Generation, LLMResult
from langchain_core.messages import AIMessageChunk, AIMessage
from app.agents.custom_message import ZiyaMessageChunk, ZiyaString

class TestRegressionOtherModels:
    """Regression test suite for other models."""

    def test_ai_message_chunk_compatibility(self):
        """Test that AIMessageChunk is still compatible with our changes."""
        # Create an AIMessageChunk
        chunk = AIMessageChunk(content="AIMessageChunk response")
        
        # Add id and message attributes
        object.__setattr__(chunk, 'id', "ai-chunk-id")
        object.__setattr__(chunk, 'message', "AIMessageChunk response")
        
        # Verify that the chunk has the expected attributes
        assert chunk.id == "ai-chunk-id"
        assert chunk.message == "AIMessageChunk response"
        assert chunk.content == "AIMessageChunk response"

    def test_ai_message_compatibility(self):
        """Test that AIMessage is still compatible with our changes."""
        # Create an AIMessage
        message = AIMessage(content="AIMessage response")
        
        # Add id and message attributes
        object.__setattr__(message, 'id', "ai-message-id")
        object.__setattr__(message, 'message', "AIMessage response")
        
        # Verify that the message has the expected attributes
        assert message.id == "ai-message-id"
        assert message.message == "AIMessage response"
        assert message.content == "AIMessage response"
    def test_ziya_message_chunk_compatibility(self):
        """Test that ZiyaMessageChunk is still compatible with our changes."""
        # Create a ZiyaMessageChunk
        chunk = ZiyaMessageChunk(content="ZiyaMessageChunk response", id="ziya-chunk-id")
        
        # Verify that the chunk has the expected attributes
        assert chunk.id == "ziya-chunk-id"
        assert chunk.content == "ZiyaMessageChunk response"
        assert chunk.text == "ZiyaMessageChunk response"
        
        # Verify that to_generation works
        generation = chunk.to_generation()
        assert isinstance(generation, Generation)
        assert generation.text == "ZiyaMessageChunk response"

    def test_mixed_object_types(self):
        """Test that mixed object types can be handled correctly."""
        # Create different types of objects
        ai_chunk = AIMessageChunk(content="AIMessageChunk response")
        ziya_chunk = ZiyaMessageChunk(content="ZiyaMessageChunk response", id="ziya-chunk-id")
        generation = Generation(text="Generation response", generation_info={})
        
        # Convert all objects to Generation objects
        objects = [ai_chunk, ziya_chunk, generation]
        generations = []
        
        for obj in objects:
            if hasattr(obj, 'to_generation'):
                generations.append(obj.to_generation())
            elif isinstance(obj, Generation):
                generations.append(obj)
            else:
                generations.append(Generation(text=str(obj.content), generation_info={}))
        
        # Verify that all objects were converted to Generation objects
        assert len(generations) == 3
        assert all(isinstance(gen, Generation) for gen in generations)
        assert generations[0].text == "AIMessageChunk response"
        assert generations[1].text == "ZiyaMessageChunk response"
        assert generations[2].text == "Generation response"
        
        # Verify that the Generation objects can be used in an LLMResult
        result = LLMResult(generations=[generations])
        assert len(result.generations) == 1
        assert len(result.generations[0]) == 3
        assert result.generations[0][0].text == "AIMessageChunk response"
        assert result.generations[0][1].text == "ZiyaMessageChunk response"
        assert result.generations[0][2].text == "Generation response"

    def test_generation_with_metadata(self):
        """Test that Generation objects with metadata work correctly."""
        # Create a Generation object with metadata
        generation = Generation(
            text="Generation with metadata",
            generation_info={"model": "test-model", "temperature": 0.7}
        )
        
        # Verify that the Generation object has the expected attributes
        assert generation.text == "Generation with metadata"
        assert generation.generation_info["model"] == "test-model"
        assert generation.generation_info["temperature"] == 0.7
        
        # Verify that the Generation object can be used in an LLMResult
        result = LLMResult(generations=[[generation]])
        assert len(result.generations) == 1
        assert len(result.generations[0]) == 1
        assert result.generations[0][0].text == "Generation with metadata"
        assert result.generations[0][0].generation_info["model"] == "test-model"
        assert result.generations[0][0].generation_info["temperature"] == 0.7