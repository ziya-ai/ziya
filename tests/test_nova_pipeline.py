"""
End-to-end tests for the Nova model pipeline.
These tests verify that the Nova models work correctly through the entire pipeline.
"""
import os
import pytest
import asyncio
from unittest.mock import patch
import json
import logging

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from app.agents.models import ModelManager
from app.agents.agent import RetryingChatBedrock, parse_output
from app.agents.custom_message import ZiyaMessageChunk
from app.utils.logging_utils import logger

# Configure logging for tests
logging.basicConfig(level=logging.INFO)


@pytest.fixture
def setup_nova_environment():
    """Set up the environment variables for Nova model testing."""
    # Save original environment variables
    original_env = {}
    for key in ["ZIYA_ENDPOINT", "ZIYA_MODEL", "ZIYA_MODEL_ID_OVERRIDE"]:
        if key in os.environ:
            original_env[key] = os.environ[key]
    
    # Set environment variables for Nova
    os.environ["ZIYA_ENDPOINT"] = "bedrock"
    os.environ["ZIYA_MODEL"] = "nova-lite"  # Use nova-lite for testing
    
    yield
    
    # Restore original environment variables
    for key in ["ZIYA_ENDPOINT", "ZIYA_MODEL", "ZIYA_MODEL_ID_OVERRIDE"]:
        if key in original_env:
            os.environ[key] = original_env[key]
        elif key in os.environ:
            del os.environ[key]


@pytest.mark.transaction
def test_nova_model_initialization(setup_nova_environment):
    """Test that Nova models can be initialized correctly."""
    try:
        # Initialize model manager
        model_instance = ModelManager.initialize_model(force_reinit=True)
        
        # Check that the model is initialized
        assert model_instance is not None
        
        # Check that it's a Nova model
        model_id = ModelManager.get_model_id(model_instance)
        assert "nova" in model_id.lower()
        
        logger.info(f"Successfully initialized Nova model: {model_id}")
        
    except Exception as e:
        pytest.fail(f"Error initializing Nova model: {str(e)}")


@pytest.mark.transaction
def test_nova_direct_invoke(setup_nova_environment):
    """Test direct invocation of Nova model without streaming."""
    try:
        # Initialize model
        model_instance = ModelManager.initialize_model(force_reinit=True)
        
        # Create a simple message
        message = HumanMessage(content="Hello, please respond with a short greeting.")
        
        # Invoke the model directly
        response = model_instance.invoke([message])
        
        # Check the response
        assert response is not None
        assert hasattr(response, "content")
        assert isinstance(response.content, str)
        assert len(response.content) > 0
        
        logger.info(f"Nova direct invoke response: {response.content[:100]}...")
        
    except Exception as e:
        pytest.fail(f"Error in Nova direct invoke: {str(e)}")


@pytest.mark.transaction
def test_nova_streaming_response(setup_nova_environment):
    """Test streaming response from Nova model."""
    try:
        # Initialize model
        model_instance = ModelManager.initialize_model(force_reinit=True)
        
        # Create a simple message
        message = HumanMessage(content="Hello, please respond with a short greeting.")
        
        # Stream the response
        async def run_streaming_test():
            chunks = []
            async for chunk in model_instance.astream([message]):
                logger.info(f"Received chunk type: {type(chunk)}")
                logger.info(f"Chunk has id: {hasattr(chunk, 'id')}")
                logger.info(f"Chunk has content: {hasattr(chunk, 'content')}")
                logger.info(f"Chunk content: {chunk.content[:50] if hasattr(chunk, 'content') and chunk.content else 'No content'}")
                chunks.append(chunk)
            return chunks
        
        chunks = asyncio.run(run_streaming_test())
        
        # Check that we got chunks
        assert len(chunks) > 0
        
        # Check the content of the chunks
        for i, chunk in enumerate(chunks):
            assert chunk is not None
            assert hasattr(chunk, "content")
            logger.info(f"Chunk {i} type: {type(chunk)}")
            logger.info(f"Chunk {i} content type: {type(chunk.content)}")
            logger.info(f"Chunk {i} content preview: {chunk.content[:50] if chunk.content else 'Empty'}")
        
    except Exception as e:
        pytest.fail(f"Error in Nova streaming test: {str(e)}")


@pytest.mark.transaction
def test_nova_with_retrying_wrapper(setup_nova_environment):
    """Test Nova model with the RetryingChatBedrock wrapper."""
    try:
        # Initialize model
        model_instance = ModelManager.initialize_model(force_reinit=True)
        wrapped_model = RetryingChatBedrock(model_instance)
        
        # Create a simple message
        message = HumanMessage(content="Hello, please respond with a short greeting.")
        
        # Test the wrapped model
        async def run_wrapped_test():
            chunks = []
            async for chunk in wrapped_model.astream([message]):
                logger.info(f"Wrapped chunk type: {type(chunk)}")
                logger.info(f"Wrapped chunk has id: {hasattr(chunk, 'id')}")
                logger.info(f"Wrapped chunk has message: {hasattr(chunk, 'message')}")
                logger.info(f"Wrapped chunk has content: {hasattr(chunk, 'content')}")
                
                # Log all attributes
                for attr_name in dir(chunk):
                    if not attr_name.startswith('_') and not callable(getattr(chunk, attr_name)):
                        try:
                            attr_value = getattr(chunk, attr_name)
                            logger.info(f"Attribute {attr_name}: {type(attr_value)}")
                        except Exception as e:
                            logger.info(f"Error accessing attribute {attr_name}: {e}")
                
                chunks.append(chunk)
            return chunks
        
        chunks = asyncio.run(run_wrapped_test())
        
        # Check that we got chunks
        assert len(chunks) > 0
        
        # Check the content of the chunks
        for i, chunk in enumerate(chunks):
            assert chunk is not None
            assert hasattr(chunk, "content")
            
            # Test the parse_output function with this chunk
            try:
                result = parse_output(chunk)
                logger.info(f"parse_output result type: {type(result)}")
                logger.info(f"parse_output result: {result}")
            except Exception as e:
                logger.error(f"Error in parse_output for chunk {i}: {e}")
                raise
        
    except Exception as e:
        pytest.fail(f"Error in Nova with RetryingChatBedrock test: {str(e)}")


@pytest.mark.transaction
def test_nova_full_pipeline(setup_nova_environment):
    """Test the complete Nova pipeline from model initialization to response handling."""
    try:
        # Initialize model
        model_instance = ModelManager.initialize_model(force_reinit=True)
        wrapped_model = RetryingChatBedrock(model_instance)
        
        # Create a simple message
        message = HumanMessage(content="Hello, please respond with a short greeting.")
        
        # Test the full pipeline
        async def run_pipeline_test():
            # Track the object at each stage
            pipeline_stages = []
            
            # Stage 1: Initial astream call
            logger.info("=== STAGE 1: Initial astream call ===")
            chunks = []
            async for chunk in wrapped_model.astream([message]):
                logger.info(f"Stage 1 chunk type: {type(chunk)}")
                logger.info(f"Stage 1 chunk has id: {hasattr(chunk, 'id')}")
                logger.info(f"Stage 1 chunk has message: {hasattr(chunk, 'message')}")
                logger.info(f"Stage 1 chunk has content: {hasattr(chunk, 'content')}")
                
                # Store the chunk for inspection
                chunks.append(chunk)
                
                # Record the stage info
                stage_info = {
                    "stage": "astream",
                    "type": str(type(chunk)),
                    "has_id": hasattr(chunk, 'id'),
                    "has_message": hasattr(chunk, 'message'),
                    "has_content": hasattr(chunk, 'content'),
                    "content_type": str(type(chunk.content)) if hasattr(chunk, 'content') else "None"
                }
                pipeline_stages.append(stage_info)
            
            # Stage 2: Parse output
            logger.info("=== STAGE 2: Parse output ===")
            parsed_results = []
            for i, chunk in enumerate(chunks):
                try:
                    result = parse_output(chunk)
                    logger.info(f"Stage 2 result type: {type(result)}")
                    
                    # Record the stage info
                    stage_info = {
                        "stage": "parse_output",
                        "type": str(type(result)),
                        "output": str(result)[:100] + "..." if len(str(result)) > 100 else str(result)
                    }
                    pipeline_stages.append(stage_info)
                    
                    parsed_results.append(result)
                except Exception as e:
                    logger.error(f"Error in parse_output for chunk {i}: {e}")
                    
                    # Record the error
                    stage_info = {
                        "stage": "parse_output_error",
                        "error": str(e)
                    }
                    pipeline_stages.append(stage_info)
                    raise
            
            return pipeline_stages
        
        pipeline_results = asyncio.run(run_pipeline_test())
        
        # Check that we got results from each stage
        assert len(pipeline_results) > 0
        
        # Log the pipeline results
        logger.info("=== PIPELINE RESULTS ===")
        for i, stage in enumerate(pipeline_results):
            logger.info(f"Stage {i}: {json.dumps(stage)}")
        
    except Exception as e:
        pytest.fail(f"Error in Nova full pipeline test: {str(e)}")


@pytest.mark.transaction
def test_nova_custom_message_preservation(setup_nova_environment):
    """Test that custom message attributes are preserved throughout the pipeline."""
    try:
        # Initialize model
        model_instance = ModelManager.initialize_model(force_reinit=True)
        
        # Create a ZiyaMessageChunk directly
        custom_chunk = ZiyaMessageChunk(content="Test content", id="test-id-12345")
        
        # Verify the custom chunk has the expected attributes
        assert hasattr(custom_chunk, 'id')
        assert custom_chunk.id == "test-id-12345"
        assert hasattr(custom_chunk, 'message')
        assert custom_chunk.message == "Test content"
        assert hasattr(custom_chunk, 'content')
        assert custom_chunk.content == "Test content"
        
        # Test string conversion
        chunk_str = str(custom_chunk)
        logger.info(f"String representation: {chunk_str}")
        
        # Test parse_output with the custom chunk
        result = parse_output(custom_chunk)
        logger.info(f"parse_output result with custom chunk: {result}")
        
        # Test with RetryingChatBedrock's _format_message_content
        wrapped_model = RetryingChatBedrock(model_instance)
        formatted_content = wrapped_model._format_message_content(custom_chunk)
        logger.info(f"_format_message_content result: {formatted_content}")
        
        # Verify the formatted content is correct
        assert formatted_content == "Test content"
        
    except Exception as e:
        pytest.fail(f"Error in Nova custom message preservation test: {str(e)}")


if __name__ == "__main__":
    # This allows running the tests directly with python
    pytest.main(["-xvs", __file__])
