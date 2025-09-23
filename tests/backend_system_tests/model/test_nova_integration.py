"""
Integration tests for Nova models in the Ziya application.
These tests verify that Nova models work correctly with the full application stack.
"""
import os
import pytest
import asyncio
import logging
from unittest.mock import patch, MagicMock

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from app.agents.models import ModelManager
from app.agents.agent import RetryingChatBedrock, parse_output, model
from app.agents.custom_message import ZiyaMessageChunk
from app.utils.logging_utils import logger

# Configure logging for tests
logging.basicConfig(level=logging.INFO)


@pytest.fixture
def setup_nova_pro():
    """Set up the environment for Nova Pro testing."""
    # Save original environment variables
    original_env = {}
    for key in ["ZIYA_ENDPOINT", "ZIYA_MODEL", "ZIYA_MODEL_ID_OVERRIDE"]:
        if key in os.environ:
            original_env[key] = os.environ[key]
    
    # Set environment variables for Nova Pro
    os.environ["ZIYA_ENDPOINT"] = "bedrock"
    os.environ["ZIYA_MODEL"] = "nova-pro"
    
    yield
    
    # Restore original environment variables
    for key in ["ZIYA_ENDPOINT", "ZIYA_MODEL", "ZIYA_MODEL_ID_OVERRIDE"]:
        if key in original_env:
            os.environ[key] = original_env[key]
        elif key in os.environ:
            del os.environ[key]


@pytest.fixture
def setup_nova_lite():
    """Set up the environment for Nova Lite testing."""
    # Save original environment variables
    original_env = {}
    for key in ["ZIYA_ENDPOINT", "ZIYA_MODEL", "ZIYA_MODEL_ID_OVERRIDE"]:
        if key in os.environ:
            original_env[key] = os.environ[key]
    
    # Set environment variables for Nova Lite
    os.environ["ZIYA_ENDPOINT"] = "bedrock"
    os.environ["ZIYA_MODEL"] = "nova-lite"
    
    yield
    
    # Restore original environment variables
    for key in ["ZIYA_ENDPOINT", "ZIYA_MODEL", "ZIYA_MODEL_ID_OVERRIDE"]:
        if key in original_env:
            os.environ[key] = original_env[key]
        elif key in os.environ:
            del os.environ[key]


@pytest.mark.transaction
def test_lazy_loaded_model_with_nova_pro(setup_nova_pro):
    """Test the LazyLoadedModel with Nova Pro."""
    try:
        # Reset the model to force reinitialization
        model.reset()
        
        # Get the model instance
        model_instance = model.get_model()
        
        # Check that the model is initialized
        assert model_instance is not None
        
        # Create a simple message
        message = HumanMessage(content="Hello, please respond with a short greeting.")
        
        # Test the model
        async def run_model_test():
            chunks = []
            async for chunk in model_instance.astream([message]):
                logger.info(f"Chunk type: {type(chunk)}")
                logger.info(f"Chunk has id: {hasattr(chunk, 'id')}")
                logger.info(f"Chunk has message: {hasattr(chunk, 'message')}")
                logger.info(f"Chunk has content: {hasattr(chunk, 'content')}")
                chunks.append(chunk)
            return chunks
        
        chunks = asyncio.run(run_model_test())
        
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
        pytest.fail(f"Error in LazyLoadedModel with Nova Pro test: {str(e)}")


@pytest.mark.transaction
def test_lazy_loaded_model_with_nova_lite(setup_nova_lite):
    """Test the LazyLoadedModel with Nova Lite."""
    try:
        # Reset the model to force reinitialization
        model.reset()
        
        # Get the model instance
        model_instance = model.get_model()
        
        # Check that the model is initialized
        assert model_instance is not None
        
        # Create a simple message
        message = HumanMessage(content="Hello, please respond with a short greeting.")
        
        # Test the model
        async def run_model_test():
            chunks = []
            async for chunk in model_instance.astream([message]):
                logger.info(f"Chunk type: {type(chunk)}")
                logger.info(f"Chunk has id: {hasattr(chunk, 'id')}")
                logger.info(f"Chunk has message: {hasattr(chunk, 'message')}")
                logger.info(f"Chunk has content: {hasattr(chunk, 'content')}")
                chunks.append(chunk)
            return chunks
        
        chunks = asyncio.run(run_model_test())
        
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
        pytest.fail(f"Error in LazyLoadedModel with Nova Lite test: {str(e)}")


@pytest.mark.transaction
def test_model_with_stop_sequences(setup_nova_lite):
    """Test the model with stop sequences."""
    try:
        # Reset the model to force reinitialization
        model.reset()
        
        # Get the model with stop sequences
        llm_with_stop = model.bind(stop=["</tool_input>"])
        
        # Check that the model is initialized
        assert llm_with_stop is not None
        
        # Create a simple message
        message = HumanMessage(content="Hello, please respond with a short greeting.")
        
        # Test the model
        async def run_model_test():
            chunks = []
            async for chunk in llm_with_stop.astream([message]):
                logger.info(f"Chunk type: {type(chunk)}")
                logger.info(f"Chunk has id: {hasattr(chunk, 'id')}")
                logger.info(f"Chunk has message: {hasattr(chunk, 'message')}")
                logger.info(f"Chunk has content: {hasattr(chunk, 'content')}")
                chunks.append(chunk)
            return chunks
        
        chunks = asyncio.run(run_model_test())
        
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
        pytest.fail(f"Error in model with stop sequences test: {str(e)}")


@pytest.mark.transaction
def test_system_message_with_nova(setup_nova_lite):
    """Test Nova model with system messages."""
    try:
        # Reset the model to force reinitialization
        model.reset()
        
        # Get the model instance
        model_instance = model.get_model()
        
        # Create messages with a system message
        system_message = SystemMessage(content="You are a helpful assistant that provides short, concise responses.")
        human_message = HumanMessage(content="Hello, please introduce yourself.")
        
        # Test the model with system message
        async def run_system_message_test():
            chunks = []
            async for chunk in model_instance.astream([system_message, human_message]):
                logger.info(f"Chunk type: {type(chunk)}")
                logger.info(f"Chunk has id: {hasattr(chunk, 'id')}")
                logger.info(f"Chunk has message: {hasattr(chunk, 'message')}")
                logger.info(f"Chunk has content: {hasattr(chunk, 'content')}")
                chunks.append(chunk)
            return chunks
        
        chunks = asyncio.run(run_system_message_test())
        
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
        pytest.fail(f"Error in system message with Nova test: {str(e)}")


@pytest.mark.transaction
def test_chat_history_with_nova(setup_nova_lite):
    """Test Nova model with chat history."""
    try:
        # Reset the model to force reinitialization
        model.reset()
        
        # Get the model instance
        model_instance = model.get_model()
        
        # Create a chat history
        messages = [
            HumanMessage(content="Hello, I'm a user."),
            AIMessage(content="Hello! I'm an AI assistant. How can I help you today?"),
            HumanMessage(content="Please tell me a short joke.")
        ]
        
        # Test the model with chat history
        async def run_chat_history_test():
            chunks = []
            async for chunk in model_instance.astream(messages):
                logger.info(f"Chunk type: {type(chunk)}")
                logger.info(f"Chunk has id: {hasattr(chunk, 'id')}")
                logger.info(f"Chunk has message: {hasattr(chunk, 'message')}")
                logger.info(f"Chunk has content: {hasattr(chunk, 'content')}")
                chunks.append(chunk)
            return chunks
        
        chunks = asyncio.run(run_chat_history_test())
        
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
        pytest.fail(f"Error in chat history with Nova test: {str(e)}")


if __name__ == "__main__":
    # This allows running the tests directly with python
    pytest.main(["-xvs", __file__])
