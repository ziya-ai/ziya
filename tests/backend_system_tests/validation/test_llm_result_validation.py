"""
Tests for LLMResult validation with different object types.

Updated: ZiyaMessage removed. Tests for ZiyaMessage replaced with
ZiyaMessageChunk equivalents.
"""

import pytest
from langchain_core.outputs import LLMResult, Generation
from langchain_core.messages import AIMessageChunk, AIMessage
from app.agents.custom_message import ZiyaMessageChunk, ZiyaString


class TestLLMResultValidation:
    """Test suite for LLMResult validation with different object types."""

    def test_llm_result_with_generation(self):
        """LLMResult works with a Generation object."""
        generation = Generation(text="Test response", generation_info={})
        result = LLMResult(generations=[[generation]])

        assert len(result.generations) == 1
        assert len(result.generations[0]) == 1
        assert result.generations[0][0] is generation
        assert result.generations[0][0].text == "Test response"

    def test_llm_result_with_ai_message_chunk_fails(self):
        """LLMResult should fail with an AIMessageChunk (not a Generation)."""
        chunk = AIMessageChunk(content="Test response")
        with pytest.raises(Exception) as excinfo:
            LLMResult(generations=[[chunk]])
        error_str = str(excinfo.value)
        assert "Generation" in error_str or "valid" in error_str.lower()

    def test_llm_result_with_ai_message_fails(self):
        """LLMResult should fail with an AIMessage (not a Generation)."""
        message = AIMessage(content="Test response")
        with pytest.raises(Exception) as excinfo:
            LLMResult(generations=[[message]])
        error_str = str(excinfo.value)
        assert "Generation" in error_str or "valid" in error_str.lower()

    def test_llm_result_with_ziya_message_chunk_fails(self):
        """LLMResult should fail with a ZiyaMessageChunk (not a Generation)."""
        chunk = ZiyaMessageChunk(content="Test response", id="test-id")
        with pytest.raises(Exception) as excinfo:
            LLMResult(generations=[[chunk]])
        error_str = str(excinfo.value)
        assert "Generation" in error_str or "valid" in error_str.lower()

    def test_llm_result_with_multiple_generations(self):
        """LLMResult should work with multiple Generation objects."""
        gen1 = Generation(text="Response 1")
        gen2 = Generation(text="Response 2")
        result = LLMResult(generations=[[gen1, gen2]])
        assert len(result.generations[0]) == 2

    def test_generation_text_access(self):
        """Generation text should be accessible."""
        gen = Generation(text="Hello world")
        assert gen.text == "Hello world"

    def test_ziya_string_not_generation(self):
        """ZiyaString should not be usable as a Generation."""
        s = ZiyaString("test")
        with pytest.raises(Exception):
            LLMResult(generations=[[s]])
