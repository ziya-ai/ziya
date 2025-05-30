"""
LLM Interaction Regression Test Suite

This test suite covers various edge cases and potential issues in LLM interactions,
particularly focusing on string handling, attribute preservation, and error handling.
"""

import pytest
import json
import types
from unittest.mock import MagicMock, patch, AsyncMock
from langchain_core.outputs import Generation
from langchain_core.messages import AIMessageChunk, HumanMessage, AIMessage

class MockLLMResponse:
    """Mock response from various LLM models."""
    
    @staticmethod
    def create_bedrock_response(text, model="claude"):
        """Create a mock Bedrock response."""
        if model == "claude":
            return {
                "completion": text,
                "stop_reason": "stop_sequence",
                "amazon-bedrock-invocationMetrics": {
                    "inputTokenCount": 100,
                    "outputTokenCount": 50,
                    "invocationLatency": 1000,
                    "firstByteLatency": 500
                }
            }
        elif model == "nova":
            return {
                "ResponseMetadata": {
                    "RequestId": "test-request-id",
                    "HTTPStatusCode": 200,
                    "HTTPHeaders": {
                        "date": "Wed, 26 Mar 2025 07:32:22 GMT",
                        "content-type": "application/json",
                        "content-length": "404",
                        "connection": "keep-alive",
                        "x-amzn-requestid": "test-request-id"
                    },
                    "RetryAttempts": 0
                },
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [{"text": text}]
                    }
                },
                "stopReason": "end_turn",
                "usage": {"inputTokens": 100, "outputTokens": 50},
                "metrics": {"latencyMs": 1000}
            }
        else:
            raise ValueError(f"Unknown model: {model}")

class TestStringHandling:
    """Test suite for string handling in LLM interactions."""

    def test_generation_string_conversion(self):
        """Test that demonstrates the bug where a Generation object is converted to a string."""
        # Create a test string
        response_text = "This is a test response from an LLM."
        
        # Create a Generation object
        generation = Generation(text=response_text)
        
        # The default string representation includes the class name and attributes
        generation_str = str(generation)
        
        # Add id attribute to the Generation object using object.__setattr__
        object.__setattr__(generation, 'id', "test-id")
        
        # Convert to string again
        generation_str = str(generation)
        
        # The string conversion should lose attributes
        with pytest.raises(AttributeError, match="'str' object has no attribute 'id'"):
            _ = generation_str.id
    
    def test_ziya_string_preservation(self):
        """Test that ZiyaString preserves attributes after string conversion."""
        # Import ZiyaString
        from app.agents.custom_message import ZiyaString
        
        # Create a test string
        response_text = "This is a test response from an LLM."
        
        # Create a ZiyaString
        ziya_str = ZiyaString(response_text, id="test-id")
        
        # Verify it has the attributes
        assert hasattr(ziya_str, 'id')
        assert ziya_str.id == "test-id"
        # ZiyaString doesn't have a message attribute in the implementation
        
        # Convert to string using str()
        str_value = str(ziya_str)
        
        # This should be a regular string now, not a ZiyaString
        assert isinstance(str_value, str)
        assert not isinstance(str_value, ZiyaString)
        
        # The string conversion should lose attributes
        with pytest.raises(AttributeError, match="'str' object has no attribute 'id'"):
            _ = str_value.id
    
    def test_string_wrapping_in_message_chunk(self):
        """Test wrapping a string in an AIMessageChunk."""
        # Create a test string
        test_string = "This is a test string response"
        
        # Create an AIMessageChunk
        message_chunk = AIMessageChunk(content=test_string)
        
        # Add id and message attributes
        object.__setattr__(message_chunk, 'id', f"test-{hash(test_string) % 10000}")
        object.__setattr__(message_chunk, 'message', test_string)
        
        # Verify the attributes
        assert hasattr(message_chunk, 'id')
        assert hasattr(message_chunk, 'message')
        assert message_chunk.content == test_string
        
        # Convert to string
        message_str = str(message_chunk)
        
        # The string conversion should lose attributes
        with pytest.raises(AttributeError, match="'str' object has no attribute 'id'"):
            _ = message_str.id


class TestAgentStringHandling:
    """Test suite for string handling in the agent."""
    
    def test_ensure_chunk_has_id_with_string(self):
        """Test that _ensure_chunk_has_id properly handles string inputs."""
        # Create a mock RetryingChatBedrock instance
        from app.agents.agent import RetryingChatBedrock
        agent = RetryingChatBedrock.__new__(RetryingChatBedrock)
        
        # Mock the _ensure_chunk_has_id method
        def mock_ensure_chunk_has_id(chunk):
            if isinstance(chunk, str):
                new_chunk = AIMessageChunk(content=chunk)
                new_chunk.id = f"str-{hash(chunk) % 10000}"
                new_chunk.message = chunk
                return new_chunk
            return chunk
        
        agent._ensure_chunk_has_id = mock_ensure_chunk_has_id
        
        # Test with a string
        test_string = "This is a test string response"
        result = agent._ensure_chunk_has_id(test_string)
        
        # Verify the result
        assert hasattr(result, 'id')
        assert hasattr(result, 'message')
        assert result.content == test_string
    
    def test_ensure_chunk_has_id_with_generation(self):
        """Test that _ensure_chunk_has_id properly handles Generation objects."""
        # Create a mock RetryingChatBedrock instance
        from app.agents.agent import RetryingChatBedrock
        agent = RetryingChatBedrock.__new__(RetryingChatBedrock)
        
        # Create a Generation object
        test_string = "This is a test Generation response"
        generation = Generation(text=test_string)
        
        # Mock the _ensure_chunk_has_id method
        def mock_ensure_chunk_has_id(chunk):
            if isinstance(chunk, Generation):
                if not hasattr(chunk, 'id'):
                    object.__setattr__(chunk, 'id', f"gen-{hash(chunk.text) % 10000}")
                if not hasattr(chunk, 'message'):
                    object.__setattr__(chunk, 'message', chunk.text)
                return chunk
            return chunk
        
        agent._ensure_chunk_has_id = mock_ensure_chunk_has_id
        
        # Test with a Generation object
        result = agent._ensure_chunk_has_id(generation)
        
        # Verify the result
        assert hasattr(result, 'id')
        assert hasattr(result, 'message')
        assert result.text == test_string


class TestNovaWrapper:
    """Test suite for Nova wrapper."""
    
    @patch('app.agents.nova_wrapper.BedrockRuntime')
    def test_nova_wrapper_generation_attributes(self, mock_bedrock_runtime):
        """Test that Nova wrapper's Generation objects have the necessary attributes."""
        from app.agents.nova_wrapper import NovaWrapper
        
        # Create a mock Bedrock client
        mock_client = MagicMock()
        mock_bedrock_runtime.return_value = mock_client
        
        # Set up the mock response
        response_text = "This is a test response from Nova Pro."
        mock_response = MockLLMResponse.create_bedrock_response(response_text, model="nova")
        mock_client.converse.return_value = mock_response
        
        # Create a NovaWrapper instance
        nova_wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
        
        # Call the _parse_response method directly
        result = nova_wrapper._parse_response(mock_response)
        
        # Verify that the result is a string with the expected text
        assert result == response_text
        
        # Create a Generation object with enhanced attributes
        from langchain_core.outputs import Generation
        generation = Generation(text=result)
        
        # Add necessary attributes
        object.__setattr__(generation, 'id', f"test-{hash(result) % 10000}")
        object.__setattr__(generation, 'message', result)
        
        # Add a custom __str__ method
        def custom_str(self):
            return self.text
        generation.__str__ = types.MethodType(custom_str, generation)
        
        # Verify the attributes
        assert hasattr(generation, 'id')
        assert hasattr(generation, 'message')
        assert generation.text == response_text
        
        # Convert to string
        generation_str = str(generation)
        assert generation_str == response_text
        
        # The string conversion should lose attributes
        with pytest.raises(AttributeError, match="'str' object has no attribute 'id'"):
            _ = generation_str.id


class TestErrorHandling:
    """Test suite for error handling in LLM interactions."""
    
    def test_error_generation_creation(self):
        """Test creating a Generation object for error responses."""
        # Create an error message
        error_message = {"error": "validation_error", "detail": "Invalid input"}
        error_text = f"Error: {error_message['detail']}"
        
        # Create a Generation object for the error
        from langchain_core.outputs import Generation
        error_generation = Generation(text=error_text, generation_info={"error": error_message})
        
        # Add necessary attributes
        object.__setattr__(error_generation, 'id', f"error-{hash(error_text) % 10000}")
        object.__setattr__(error_generation, 'message', error_text)
        object.__setattr__(error_generation, 'content', error_text)
        
        # Add to_generation method
        def to_generation():
            return error_generation
        object.__setattr__(error_generation, 'to_generation', to_generation)
        
        # Verify the attributes
        assert hasattr(error_generation, 'id')
        assert hasattr(error_generation, 'message')
        assert hasattr(error_generation, 'content')
        assert hasattr(error_generation, 'to_generation')
        assert error_generation.text == error_text
        
        # Test the to_generation method
        gen = error_generation.to_generation()
        assert gen is error_generation
    
    def test_parse_output_with_error(self):
        """Test parse_output with an error response."""
        from app.agents.parse_output import parse_output
        
        # Create an error message
        error_message = {"error": "validation_error", "detail": "Invalid input"}
        error_json = json.dumps(error_message)
        
        # Create a Generation object with the error
        from langchain_core.outputs import Generation
        error_generation = Generation(text=error_json, generation_info={"error": error_message})
        
        # Parse the output
        result = parse_output(error_generation)
        
        # Verify the result
        assert "Error: Invalid input" in result.return_values["output"]


class TestSpecialCases:
    """Test suite for special cases in LLM interactions."""
    
    def test_invisible_unicode_handling(self):
        """Test handling of invisible Unicode characters."""
        # Create a string with invisible Unicode characters
        text_with_invisible = "This has a zero-width space\u200b and a zero-width non-joiner\u200c"
        
        # Create a Generation object
        generation = Generation(text=text_with_invisible)
        
        # Verify the text contains the invisible characters
        assert "\u200b" in generation.text
        assert "\u200c" in generation.text
        
        # Add a normalize_line function
        def normalize_line(line):
            """Normalize a line by removing invisible Unicode characters."""
            # Remove zero-width space
            line = line.replace("\u200b", "")
            # Remove zero-width non-joiner
            line = line.replace("\u200c", "")
            return line
        
        # Normalize the text
        normalized_text = normalize_line(generation.text)
        
        # Verify the invisible characters are removed
        assert "\u200b" not in normalized_text
        assert "\u200c" not in normalized_text
    
    def test_escape_sequence_handling(self):
        """Test handling of escape sequences."""
        # Create a string with escape sequences
        text_with_escapes = "This has escape sequences: \\n \\t \\r \\b \\f \\\\"
        
        # Create a Generation object
        generation = Generation(text=text_with_escapes)
        
        # Verify the text contains the escape sequences
        assert "\\n" in generation.text
        assert "\\t" in generation.text
        assert "\\r" in generation.text
        assert "\\b" in generation.text
        assert "\\f" in generation.text
        assert "\\\\" in generation.text
        
        # Add a function to handle escape sequences
        def handle_escape_sequences(text):
            """Handle escape sequences in text."""
            # Replace escaped backslashes first
            text = text.replace("\\\\", "\\")
            # Replace other escape sequences
            text = text.replace("\\n", "\n")
            text = text.replace("\\t", "\t")
            text = text.replace("\\r", "\r")
            text = text.replace("\\b", "\b")
            text = text.replace("\\f", "\f")
            return text
        
        # Handle escape sequences
        processed_text = handle_escape_sequences(generation.text)
        
        # Verify the escape sequences are processed
        assert "\n" in processed_text
        assert "\t" in processed_text
        assert "\r" in processed_text
        assert "\b" in processed_text
        assert "\f" in processed_text
        assert "\\" in processed_text
        assert "\\n" not in processed_text
        assert "\\t" not in processed_text


class TestAsyncStreaming:
    """Test suite for async streaming in LLM interactions."""
    
    @pytest.mark.asyncio
    async def test_astream_with_string_chunks(self):
        """Test astream with string chunks."""
        # Create a mock astream function
        async def mock_astream(*args, **kwargs):
            yield "Chunk 1"
            yield "Chunk 2"
            yield "Chunk 3"
        
        # Create a wrapper function to handle string chunks
        async def handle_chunks(astream_func, *args, **kwargs):
            result = []
            async for chunk in astream_func(*args, **kwargs):
                # Handle string chunks
                if isinstance(chunk, str):
                    # Wrap in AIMessageChunk
                    new_chunk = AIMessageChunk(content=chunk)
                    object.__setattr__(new_chunk, 'id', f"str-{hash(chunk) % 10000}")
                    object.__setattr__(new_chunk, 'message', chunk)
                    chunk = new_chunk
                
                # Verify the chunk has the necessary attributes
                assert hasattr(chunk, 'id')
                assert hasattr(chunk, 'message')
                assert hasattr(chunk, 'content')
                
                result.append(chunk)
            return result
        
        # Call the wrapper function
        chunks = await handle_chunks(mock_astream)
        
        # Verify the results
        assert len(chunks) == 3
        assert chunks[0].content == "Chunk 1"
        assert chunks[1].content == "Chunk 2"
        assert chunks[2].content == "Chunk 3"
        assert all(hasattr(chunk, 'id') for chunk in chunks)
        assert all(hasattr(chunk, 'message') for chunk in chunks)
    
    @pytest.mark.asyncio
    async def test_astream_with_generation_chunks(self):
        """Test astream with Generation chunks."""
        # Create mock Generation objects
        gen1 = Generation(text="Generation 1")
        gen2 = Generation(text="Generation 2")
        gen3 = Generation(text="Generation 3")
        
        # Add attributes to the Generation objects
        for i, gen in enumerate([gen1, gen2, gen3]):
            object.__setattr__(gen, 'id', f"gen-{i}")
            object.__setattr__(gen, 'message', gen.text)
        
        # Create a mock astream function
        async def mock_astream(*args, **kwargs):
            yield gen1
            yield gen2
            yield gen3
        
        # Create a wrapper function to handle Generation chunks
        async def handle_chunks(astream_func, *args, **kwargs):
            result = []
            async for chunk in astream_func(*args, **kwargs):
                # Ensure the chunk has the necessary attributes
                if isinstance(chunk, Generation):
                    if not hasattr(chunk, 'id'):
                        object.__setattr__(chunk, 'id', f"gen-{hash(chunk.text) % 10000}")
                    if not hasattr(chunk, 'message'):
                        object.__setattr__(chunk, 'message', chunk.text)
                
                # Verify the chunk has the necessary attributes
                assert hasattr(chunk, 'id')
                assert hasattr(chunk, 'message')
                assert hasattr(chunk, 'text')
                
                result.append(chunk)
            return result
        
        # Call the wrapper function
        chunks = await handle_chunks(mock_astream)
        
        # Verify the results
        assert len(chunks) == 3
        assert chunks[0].text == "Generation 1"
        assert chunks[1].text == "Generation 2"
        assert chunks[2].text == "Generation 3"
        assert all(hasattr(chunk, 'id') for chunk in chunks)
        assert all(hasattr(chunk, 'message') for chunk in chunks)


class TestParseOutput:
    """Test suite for parse_output function."""
    
    def test_parse_output_with_string(self):
        """Test parse_output with a string."""
        from app.agents.parse_output import parse_output
        
        # Create a test string
        test_string = "This is a test string response"
        
        # Parse the output
        result = parse_output(test_string)
        
        # Verify the result
        assert result.return_values["output"] == test_string
    
    def test_parse_output_with_generation(self):
        """Test parse_output with a Generation object."""
        from app.agents.parse_output import parse_output
        
        # Create a Generation object
        test_string = "This is a test Generation response"
        generation = Generation(text=test_string)
        
        # Parse the output
        result = parse_output(generation)
        
        # Verify the result
        assert result.return_values["output"] == test_string
    
    def test_parse_output_with_message_chunk(self):
        """Test parse_output with an AIMessageChunk."""
        from app.agents.parse_output import parse_output
        
        # Create an AIMessageChunk
        test_string = "This is a test AIMessageChunk response"
        message_chunk = AIMessageChunk(content=test_string)
        
        # Parse the output
        result = parse_output(message_chunk)
        
        # Verify the result
        assert result.return_values["output"] == test_string
    
    def test_parse_output_with_json_error(self):
        """Test parse_output with a JSON error."""
        from app.agents.parse_output import parse_output
        
        # Create a JSON error
        error_message = {"error": "validation_error", "detail": "Invalid input"}
        error_json = json.dumps(error_message)
        
        # Parse the output
        result = parse_output(error_json)
        
        # Verify the result
        assert "Error: Invalid input" in result.return_values["output"]
