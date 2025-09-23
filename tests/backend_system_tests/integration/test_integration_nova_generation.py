"""
Integration tests for Nova Pro Generation object compatibility.
These tests verify that our fix for the Nova Pro validation error works correctly
in a more integrated environment.
"""

import pytest
from unittest.mock import MagicMock, patch
from langchain_core.outputs import Generation, LLMResult
from langchain_core.messages import AIMessageChunk, HumanMessage
from app.agents.custom_message import ZiyaMessageChunk, ZiyaString

class MockBedrockResponse:
    """Mock response from Bedrock."""
    
    def __init__(self, text):
        self.response = {
            "ResponseMetadata": {
                "RequestId": "test-request-id",
                "HTTPStatusCode": 200,
                "HTTPHeaders": {
                    "date": "Wed, 26 Mar 2025 07:09:24 GMT",
                    "content-type": "application/json",
                    "content-length": "371",
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

@pytest.mark.skip(reason="These tests require pytest-asyncio to run properly")
class TestIntegrationNovaGeneration:
    """Integration test suite for Nova Pro Generation object compatibility."""

    @patch('app.agents.nova_wrapper.BedrockRuntime')
    async def test_nova_wrapper_full_flow(self, mock_bedrock_runtime):
        """Test the full flow from Nova wrapper to agent."""
        from app.agents.nova_wrapper import NovaWrapper
        
        # Create a mock Bedrock client
        mock_client = MagicMock()
        mock_bedrock_runtime.return_value = mock_client
        
        # Set up the mock response
        response_text = "This is a test response from Nova Pro."
        mock_response = MockBedrockResponse(response_text)
        mock_client.converse.return_value = mock_response.response
        
        # Create a NovaWrapper instance
        nova_wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
        
        # Call the astream method and verify the result
        messages = [HumanMessage(content="Test question")]
        result = []
        async for chunk in nova_wrapper.astream(messages, {}):
            result.append(chunk)
        
        # Verify that the result is a Generation object
        assert len(result) == 1
        assert isinstance(result[0], Generation)
        assert result[0].text == response_text

    @patch('app.agents.nova_wrapper.BedrockRuntime')
    @patch('app.agents.agent.RetryingChatBedrock.astream')
    async def test_agent_with_nova_wrapper(self, mock_agent_astream, mock_bedrock_runtime):
        """Test the agent with Nova wrapper."""
        from app.agents.agent import RetryingChatBedrock
        from app.agents.nova_wrapper import NovaWrapper
        
        # Create a mock Bedrock client
        mock_client = MagicMock()
        mock_bedrock_runtime.return_value = mock_client
        
        # Set up the mock response
        response_text = "This is a test response from Nova Pro."
        mock_response = MockBedrockResponse(response_text)
        mock_client.converse.return_value = mock_response.response
        
        # Create a NovaWrapper instance
        nova_wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
        
        # Set up the mock agent to use the Nova wrapper
        generation = Generation(text=response_text, generation_info={})
        
        # Create a RetryingChatBedrock instance
        agent = RetryingChatBedrock(nova_wrapper)
        
        # Call the astream method and verify the result
        messages = [HumanMessage(content="Test question")]
        result = []
        async for chunk in agent.astream(messages, {}):
            result.append(chunk)
        
        # Verify that the result is a Generation object
        assert len(result) == 1
        assert isinstance(result[0], Generation)
        assert result[0].text == response_text

    @patch('app.agents.nova_wrapper.BedrockRuntime')
    async def test_nova_wrapper_error_handling(self, mock_bedrock_runtime):
        """Test error handling in the Nova wrapper."""
        from app.agents.nova_wrapper import NovaWrapper
        
        # Create a mock Bedrock client that raises an exception
        mock_client = MagicMock()
        mock_bedrock_runtime.return_value = mock_client
        mock_client.converse.side_effect = Exception("Test error")
        
        # Create a NovaWrapper instance
        nova_wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
        
        # Call the astream method and verify the result
        messages = [HumanMessage(content="Test question")]
        
        # The wrapper should handle the exception and yield an error message
        with pytest.raises(Exception):
            result = []
            async for chunk in nova_wrapper.astream(messages, {}):
                result.append(chunk)

    @patch('app.agents.nova_wrapper.BedrockRuntime')
    @patch('app.agents.agent.RetryingChatBedrock.model')
    async def test_agent_error_handling_with_generation(self, mock_model, mock_bedrock_runtime):
        """Test that agent error handling correctly creates a Generation object."""
        from app.agents.agent import RetryingChatBedrock
        
        # Create a mock model that raises a validation error
        mock_model.astream.side_effect = Exception(
            "Input should be a valid dictionary or instance of Generation [type=model_type, input_value=ZiyaMessageChunk]"
        )
        
        # Create a RetryingChatBedrock instance
        agent = RetryingChatBedrock(mock_model)
        
        # Call the astream method and verify the result
        messages = [HumanMessage(content="Test question")]
        result = []
        
        # The agent should handle the exception and yield an error message as a Generation object
        async for chunk in agent.astream(messages, {}):
            result.append(chunk)
        
        # Verify that the result contains an error message
        assert len(result) > 0
        # The result could be either a Generation object or an AIMessageChunk with an error message
        if isinstance(result[0], Generation):
            assert "Error" in result[0].text
        else:
            assert "error" in result[0].content.lower()

    @patch('app.agents.nova_wrapper.BedrockRuntime')
    async def test_llm_result_creation_with_generation(self, mock_bedrock_runtime):
        """Test that LLMResult creation works with Generation objects from Nova wrapper."""
        from app.agents.nova_wrapper import NovaWrapper
        
        # Create a mock Bedrock client
        mock_client = MagicMock()
        mock_bedrock_runtime.return_value = mock_client
        
        # Set up the mock response
        response_text = "This is a test response from Nova Pro."
        mock_response = MockBedrockResponse(response_text)
        mock_client.converse.return_value = mock_response.response
        
        # Create a NovaWrapper instance
        nova_wrapper = NovaWrapper(model_id="us.amazon.nova-pro-v1:0")
        
        # Call the astream method and get the result
        messages = [HumanMessage(content="Test question")]
        result = []
        async for chunk in nova_wrapper.astream(messages, {}):
            result.append(chunk)
        
        # Verify that the result is a Generation object
        assert len(result) == 1
        assert isinstance(result[0], Generation)
        
        # Create an LLMResult with the Generation object
        llm_result = LLMResult(generations=[result])
        
        # Verify the LLMResult has the expected structure
        assert len(llm_result.generations) == 1
        assert len(llm_result.generations[0]) == 1
        assert llm_result.generations[0][0].text == response_text
