"""
Async LLM Interaction Regression Test Suite

This test suite covers various edge cases and potential issues in async LLM interactions,
particularly focusing on streaming responses, attribute preservation, and error handling.
"""

import pytest
import json
import types
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from langchain_core.outputs import Generation
from langchain_core.messages import AIMessageChunk, HumanMessage, AIMessage

class MockAsyncLLMResponse:
    """Mock async response from various LLM models."""
    
    @staticmethod
    async def create_bedrock_stream(texts, model="claude"):
        """Create a mock Bedrock streaming response."""
        for text in texts:
            if model == "claude":
                yield {
                    "completion": text,
                    "stop_reason": None,
                    "amazon-bedrock-invocationMetrics": {
                        "inputTokenCount": 100,
                        "outputTokenCount": len(text) // 4,
                        "invocationLatency": 100,
                        "firstByteLatency": 50
                    }
                }
            elif model == "nova":
                yield {
                    "ResponseMetadata": {
                        "RequestId": f"test-request-id-{hash(text) % 10000}",
                        "HTTPStatusCode": 200,
                        "HTTPHeaders": {
                            "date": "Wed, 26 Mar 2025 07:32:22 GMT",
                            "content-type": "application/json",
                            "content-length": str(len(text) * 2),
                            "connection": "keep-alive",
                            "x-amzn-requestid": f"test-request-id-{hash(text) % 10000}"
                        },
                        "RetryAttempts": 0
                    },
                    "output": {
                        "message": {
                            "role": "assistant",
                            "content": [{"text": text}]
                        }
                    },
                    "stopReason": None,
                    "usage": {"inputTokens": 100, "outputTokens": len(text) // 4},
                    "metrics": {"latencyMs": 100}
                }
            else:
                raise ValueError(f"Unknown model: {model}")


class TestAsyncStringHandling:
    """Test suite for async string handling in LLM interactions."""
    
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


class TestAsyncNovaWrapper:
    """Test suite for async Nova wrapper."""
    
    @pytest.mark.asyncio
    @patch('app.agents.nova_wrapper.BedrockRuntime')
    async def test_nova_wrapper_astream(self, mock_bedrock_runtime):
        """Test that Nova wrapper's astream method properly handles responses."""
        from app.agents.nova_wrapper import NovaWrapper
        
        # Create a mock Bedrock client
        mock_client = MagicMock()
        mock_bedrock_runtime.return_value = mock_client
        
        # Set up the mock response
        response_texts = ["This is chunk 1", "This is chunk 2", "This is chunk 3"]
        mock_client.converse = AsyncMock()
        mock_client.converse.return_value = {
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
                    "content": [{"text": "".join(response_texts)}]
                }
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 100, "outputTokens": 50},
            "metrics": {"latencyMs": 1000}
        }
        
        # Create a NovaWrapper instance
        nova_wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
        
        # Mock the _astream method to yield chunks
        async def mock_astream(*args, **kwargs):
            for text in response_texts:
                # Create a Generation object
                generation = Generation(text=text)
                
                # Add necessary attributes
                object.__setattr__(generation, 'id', f"nova-{hash(text) % 10000}")
                object.__setattr__(generation, 'message', text)
                object.__setattr__(generation, 'content', text)
                
                # Add to_generation method
                def to_generation():
                    return generation
                object.__setattr__(generation, 'to_generation', to_generation)
                
                yield generation
        
        # Replace the _astream method
        nova_wrapper._astream = mock_astream
        
        # Call the astream method
        messages = [HumanMessage(content="Test question")]
        result = []
        async for chunk in nova_wrapper.astream(messages, {}):
            # Verify the chunk has the necessary attributes
            assert hasattr(chunk, 'id')
            assert hasattr(chunk, 'message')
            assert hasattr(chunk, 'text')
            
            # Test string conversion
            chunk_str = str(chunk)
            assert isinstance(chunk_str, str)
            
            # The string conversion should lose attributes
            with pytest.raises(AttributeError, match="'str' object has no attribute 'id'"):
                _ = chunk_str.id
            
            result.append(chunk)
        
        # Verify the results
        assert len(result) == 3
        assert result[0].text == "This is chunk 1"
        assert result[1].text == "This is chunk 2"
        assert result[2].text == "This is chunk 3"


class TestAsyncErrorHandling:
    """Test suite for async error handling in LLM interactions."""
    
    @pytest.mark.asyncio
    async def test_astream_with_error(self):
        """Test astream with an error response."""
        # Create a mock astream function that raises an exception
        async def mock_astream_with_error(*args, **kwargs):
            yield "Chunk 1"  # First chunk is fine
            raise ValueError("Test error")  # Then an error
        
        # Create a wrapper function to handle errors
        async def handle_chunks_with_error(astream_func, *args, **kwargs):
            result = []
            try:
                async for chunk in astream_func(*args, **kwargs):
                    # Handle string chunks
                    if isinstance(chunk, str):
                        # Wrap in AIMessageChunk
                        new_chunk = AIMessageChunk(content=chunk)
                        object.__setattr__(new_chunk, 'id', f"str-{hash(chunk) % 10000}")
                        object.__setattr__(new_chunk, 'message', chunk)
                        chunk = new_chunk
                    
                    result.append(chunk)
            except Exception as e:
                # Create an error Generation
                error_text = f"Error: {str(e)}"
                error_generation = Generation(text=error_text, generation_info={"error": str(e)})
                
                # Add necessary attributes
                object.__setattr__(error_generation, 'id', f"error-{hash(error_text) % 10000}")
                object.__setattr__(error_generation, 'message', error_text)
                object.__setattr__(error_generation, 'content', error_text)
                
                result.append(error_generation)
            
            return result
        
        # Call the wrapper function
        chunks = await handle_chunks_with_error(mock_astream_with_error)
        
        # Verify the results
        assert len(chunks) == 2
        assert chunks[0].content == "Chunk 1"
        assert "Error: Test error" in chunks[1].text
        assert all(hasattr(chunk, 'id') for chunk in chunks)
        assert all(hasattr(chunk, 'message') for chunk in chunks)
    
    @pytest.mark.asyncio
    async def test_retry_on_error(self):
        """Test retrying on error."""
        # Create a counter for the number of attempts
        attempts = 0
        
        # Create a mock astream function that fails on the first attempt
        async def mock_astream_with_retry(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            
            if attempts == 1:
                raise ValueError("Temporary error")
            else:
                yield "Success after retry"
        
        # Create a wrapper function to handle retries
        async def handle_chunks_with_retry(astream_func, max_retries=3, *args, **kwargs):
            for attempt in range(max_retries):
                try:
                    result = []
                    async for chunk in astream_func(*args, **kwargs):
                        # Handle string chunks
                        if isinstance(chunk, str):
                            # Wrap in AIMessageChunk
                            new_chunk = AIMessageChunk(content=chunk)
                            object.__setattr__(new_chunk, 'id', f"str-{hash(chunk) % 10000}")
                            object.__setattr__(new_chunk, 'message', chunk)
                            chunk = new_chunk
                        
                        result.append(chunk)
                    return result
                except Exception as e:
                    if attempt == max_retries - 1:
                        # Create an error Generation on the last attempt
                        error_text = f"Error after {max_retries} retries: {str(e)}"
                        error_generation = Generation(text=error_text, generation_info={"error": str(e)})
                        
                        # Add necessary attributes
                        object.__setattr__(error_generation, 'id', f"error-{hash(error_text) % 10000}")
                        object.__setattr__(error_generation, 'message', error_text)
                        object.__setattr__(error_generation, 'content', error_text)
                        
                        return [error_generation]
                    
                    # Wait before retrying
                    await asyncio.sleep(0.1)
        
        # Call the wrapper function
        chunks = await handle_chunks_with_retry(mock_astream_with_retry)
        
        # Verify the results
        assert len(chunks) == 1
        assert chunks[0].content == "Success after retry"
        assert attempts == 2  # Should have retried once


class TestAsyncStreamCombining:
    """Test suite for combining async streams."""
    
    @pytest.mark.asyncio
    async def test_combine_streams(self):
        """Test combining multiple async streams."""
        # Create mock astream functions
        async def mock_astream1(*args, **kwargs):
            yield "Stream 1 Chunk 1"
            yield "Stream 1 Chunk 2"
        
        async def mock_astream2(*args, **kwargs):
            yield "Stream 2 Chunk 1"
            yield "Stream 2 Chunk 2"
        
        # Create a function to combine streams
        async def combine_streams(stream_funcs, *args, **kwargs):
            result = []
            for stream_func in stream_funcs:
                async for chunk in stream_func(*args, **kwargs):
                    # Handle string chunks
                    if isinstance(chunk, str):
                        # Wrap in AIMessageChunk
                        new_chunk = AIMessageChunk(content=chunk)
                        object.__setattr__(new_chunk, 'id', f"str-{hash(chunk) % 10000}")
                        object.__setattr__(new_chunk, 'message', chunk)
                        chunk = new_chunk
                    
                    result.append(chunk)
            return result
        
        # Call the combine function
        chunks = await combine_streams([mock_astream1, mock_astream2])
        
        # Verify the results
        assert len(chunks) == 4
        assert chunks[0].content == "Stream 1 Chunk 1"
        assert chunks[1].content == "Stream 1 Chunk 2"
        assert chunks[2].content == "Stream 2 Chunk 1"
        assert chunks[3].content == "Stream 2 Chunk 2"
        assert all(hasattr(chunk, 'id') for chunk in chunks)
        assert all(hasattr(chunk, 'message') for chunk in chunks)
    
    @pytest.mark.asyncio
    async def test_interleave_streams(self):
        """Test interleaving multiple async streams."""
        # Create mock astream functions with delays
        async def mock_astream1(*args, **kwargs):
            yield "Stream 1 Chunk 1"
            await asyncio.sleep(0.1)
            yield "Stream 1 Chunk 2"
        
        async def mock_astream2(*args, **kwargs):
            await asyncio.sleep(0.05)
            yield "Stream 2 Chunk 1"
            await asyncio.sleep(0.1)
            yield "Stream 2 Chunk 2"
        
        # Create a function to interleave streams
        async def interleave_streams(stream_funcs, *args, **kwargs):
            # Start all streams
            streams = [stream_func(*args, **kwargs) for stream_func in stream_funcs]
            
            # Create tasks for the first chunk from each stream
            tasks = [asyncio.create_task(anext(stream)) for stream in streams]
            
            # Process chunks as they become available
            result = []
            while tasks:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                
                for task in done:
                    try:
                        chunk = task.result()
                        
                        # Handle string chunks
                        if isinstance(chunk, str):
                            # Wrap in AIMessageChunk
                            new_chunk = AIMessageChunk(content=chunk)
                            object.__setattr__(new_chunk, 'id', f"str-{hash(chunk) % 10000}")
                            object.__setattr__(new_chunk, 'message', chunk)
                            chunk = new_chunk
                        
                        result.append(chunk)
                        
                        # Find the stream that produced this chunk
                        stream_index = next(i for i, t in enumerate(tasks) if t == task)
                        
                        # Create a new task for the next chunk from this stream
                        try:
                            tasks[stream_index] = asyncio.create_task(anext(streams[stream_index]))
                        except StopAsyncIteration:
                            # This stream is exhausted
                            tasks.pop(stream_index)
                            streams.pop(stream_index)
                    except StopAsyncIteration:
                        # This should not happen as we handle it above
                        pass
            
            return result
        
        # Call the interleave function
        chunks = await interleave_streams([mock_astream1, mock_astream2])
        
        # Verify the results
        assert len(chunks) == 4
        # The exact order depends on timing, but we should have all chunks
        contents = [chunk.content for chunk in chunks]
        assert "Stream 1 Chunk 1" in contents
        assert "Stream 1 Chunk 2" in contents
        assert "Stream 2 Chunk 1" in contents
        assert "Stream 2 Chunk 2" in contents
        assert all(hasattr(chunk, 'id') for chunk in chunks)
        assert all(hasattr(chunk, 'message') for chunk in chunks)
