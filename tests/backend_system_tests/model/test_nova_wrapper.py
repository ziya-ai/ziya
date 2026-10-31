"""
Test suite for Nova wrapper.

This test suite verifies that the Nova wrapper properly handles responses
and preserves attributes in the generated output.
"""

import pytest
import json
from unittest.mock import MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage
from app.agents.custom_message import ZiyaString

class MockResponse:
    """Mock response for Bedrock client."""
    
    @staticmethod
    def create_nova_response(text):
        """Create a mock Nova response."""
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


@patch('boto3.client')
def test_nova_wrapper_initialization(mock_boto3_client):
    """Only parameters that were passed are sent; unsupported ones are omitted."""
    from app.agents.wrappers.nova_wrapper import NovaWrapper

    mock_boto3_client.return_value = MagicMock()
    wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0", temperature=0.7)

    assert wrapper.model_id == "us.amazon.nova-pro-v1:0"
    assert wrapper.client is not None
    assert wrapper.model_kwargs["temperature"] == 0.7
    assert wrapper.model_kwargs["top_p"] == 0.9
    assert wrapper.model_kwargs["max_tokens"] == wrapper._get_current_max_tokens()
    # Not passed, so not sent (Nova Pro rejects top_k).
    assert "top_k" not in wrapper.model_kwargs
    bare = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
    assert "temperature" not in bare.model_kwargs


def _text(content):
    """Nova content is a list of {"text": ...} blocks; accept a bare string too."""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content)


@patch('boto3.client')
def test_nova_wrapper_format_messages(mock_boto3_client):
    """Messages are converted to Nova's role/content-block shape, in order."""
    from app.agents.wrappers.nova_wrapper import NovaWrapper

    mock_boto3_client.return_value = MagicMock()
    wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
    messages = [
        HumanMessage(content="Hello, Nova!"),
        AIMessage(content="Hello, human!"),
        HumanMessage(content="How are you?"),
    ]
    formatted = wrapper._format_messages(messages)

    assert [m["role"] for m in formatted["messages"]] == ["user", "assistant", "user"]
    assert [_text(m["content"]) for m in formatted["messages"]] == [
        "Hello, Nova!", "Hello, human!", "How are you?"]
    assert formatted["messages"][0]["content"] == [{"text": "Hello, Nova!"}]
    assert formatted["top_p"] == 0.9
    assert formatted["max_tokens"] == wrapper._get_current_max_tokens()


@patch('boto3.client')
def test_nova_wrapper_parse_response(mock_boto3_client):
    """Test parsing responses from the Nova wrapper."""
    # Import the NovaWrapper class
    from app.agents.wrappers.nova_wrapper import NovaWrapper
    
    # Create a mock client
    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client
    
    # Initialize the wrapper
    wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
    
    # Create a test response
    response_text = "This is a test response from Nova."
    response = MockResponse.create_nova_response(response_text)
    
    # Parse the response
    result = wrapper._parse_response(response)
    
    # Verify the result
    assert result == response_text
    assert isinstance(result, ZiyaString)
    assert hasattr(result, 'id')
    assert hasattr(result, 'message')
    assert result.message == response_text


@patch('boto3.client')
def test_nova_wrapper_generate(mock_boto3_client):
    """Test generating responses with the Nova wrapper."""
    # Import the NovaWrapper class
    from app.agents.wrappers.nova_wrapper import NovaWrapper
    
    # Create a mock client
    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client
    
    # Set up the mock response
    response_text = "This is a test response from Nova."
    mock_client.converse.return_value = MockResponse.create_nova_response(response_text)
    
    # Initialize the wrapper
    wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
    
    # Create test messages
    messages = [
        HumanMessage(content="Hello, Nova!"),
        AIMessage(content="Hello, human!"),
        HumanMessage(content="How are you?")
    ]
    
    # Generate a response
    result = wrapper._generate(messages)
    
    # Verify the result
    assert len(result.generations) == 1
    assert result.generations[0].message.content == response_text
    assert hasattr(result.generations[0], 'id')
    assert hasattr(result.generations[0], 'message')
    assert result.generations[0].message.content == response_text


@pytest.mark.asyncio
@patch('boto3.client')
async def test_nova_wrapper_astream(mock_boto3_client):
    """_astream reads converse_stream deltas and yields one ChatGeneration."""
    from app.agents.wrappers.nova_wrapper import NovaWrapper

    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client
    # The old test stubbed ``converse``; _astream calls ``converse_stream``,
    # so the stub never ran and the stream was empty.
    mock_client.converse_stream.return_value = {"stream": [
        {"contentBlockDelta": {"delta": {"text": "This is a test "}}},
        {"contentBlockDelta": {"delta": {"text": "response from Nova."}}},
        {"messageStop": {}},
        {"contentBlockDelta": {"delta": {"text": " after stop"}}},
    ]}
    wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")

    chunks = [c async for c in wrapper._astream([HumanMessage(content="How are you?")])]

    assert mock_client.converse_stream.call_count == 1
    assert len(chunks) == 1
    assert chunks[0].message.content == "This is a test response from Nova."
    assert chunks[0].generation_info["model_id"] == "us.amazon.nova-pro-v1:0"


@patch('boto3.client')
def test_nova_wrapper_error_handling(mock_boto3_client):
    """Test error handling in the Nova wrapper."""
    # Import the NovaWrapper class
    from app.agents.wrappers.nova_wrapper import NovaWrapper
    
    # Create a mock client
    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client
    
    # Set up the mock to raise an exception
    mock_client.converse.side_effect = Exception("Test error")
    
    # Initialize the wrapper
    wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
    
    # Create test messages
    messages = [
        HumanMessage(content="Hello, Nova!")
    ]
    
    # Verify that an exception is raised
    with pytest.raises(Exception) as excinfo:
        wrapper._generate(messages)
    
    # Verify the exception message
    assert "Test error" in str(excinfo.value)


@patch('boto3.client')
def test_nova_wrapper_with_stop_sequences(mock_boto3_client):
    """Test the Nova wrapper with stop sequences."""
    # Import the NovaWrapper class
    from app.agents.wrappers.nova_wrapper import NovaWrapper
    
    # Create a mock client
    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client
    
    # Set up the mock response
    response_text = "This is a test response from Nova."
    mock_client.converse.return_value = MockResponse.create_nova_response(response_text)
    
    # Initialize the wrapper
    wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
    
    # Create test messages
    messages = [
        HumanMessage(content="Hello, Nova!")
    ]
    
    # Generate a response with stop sequences
    stop = ["</tool_input>"]
    result = wrapper._generate(messages, stop=stop)
    
    # Verify the result
    assert len(result.generations) == 1
    assert result.generations[0].message.content == response_text
    
    # Verify that the stop sequences were passed to the client
    call_kwargs = mock_client.converse.call_args[1]
    assert "inferenceConfig" in call_kwargs
    assert "stopSequences" in call_kwargs["inferenceConfig"]
    assert call_kwargs["inferenceConfig"]["stopSequences"] == stop
