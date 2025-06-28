"""
Runtime tests for Nova wrapper.

This test suite verifies that the Nova wrapper can be instantiated and used at runtime.
"""

import pytest
from unittest.mock import MagicMock, patch


@patch('boto3.client')
def test_nova_wrapper_instantiation(mock_boto3_client):
    """Test that the NovaWrapper class can be instantiated without errors."""
    # Create a mock client
    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client
    
    # Import and instantiate the NovaWrapper
    from app.agents.nova_wrapper import NovaWrapper
    wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
    
    # Verify the wrapper was instantiated correctly
    assert wrapper.model_id == "us.amazon.nova-pro-v1:0"
    assert wrapper.client is not None


@patch('boto3.client')
def test_nova_wrapper_astream_method(mock_boto3_client):
    """Test that the _astream method can be called without errors."""
    # Create a mock client
    mock_client = MagicMock()
    mock_boto3_client.return_value = mock_client
    
    # Set up the mock response
    mock_response = {
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
                "content": [{"text": "Test response"}]
            }
        },
        "stopReason": "end_turn",
        "usage": {"inputTokens": 100, "outputTokens": 50},
        "metrics": {"latencyMs": 1000}
    }
    mock_client.converse.return_value = mock_response
    
    # Import and instantiate the NovaWrapper
    from app.agents.nova_wrapper import NovaWrapper
    from langchain_core.messages import HumanMessage
    
    wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
    
    # Create test messages
    messages = [HumanMessage(content="Hello, Nova!")]
    
    # Call the _astream method
    import asyncio
    
    async def test_astream():
        chunks = []
        async for chunk in wrapper._astream(messages):
            chunks.append(chunk)
        return chunks
    
    # Run the async function
    chunks = asyncio.run(test_astream())
    
    # Verify the chunks
    assert len(chunks) == 1
    assert chunks[0].text == "Test response"
