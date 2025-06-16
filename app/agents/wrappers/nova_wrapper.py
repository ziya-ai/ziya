"""
Nova wrapper for AWS Bedrock.

This module provides a wrapper for the Nova model in AWS Bedrock.
"""

import json
import os
from typing import Any, Dict, List, Optional, Iterator, AsyncIterator

import boto3
from botocore.client import BaseClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from app.utils.aws_utils import ThrottleSafeBedrock

from app.utils.logging_utils import logger
from app.agents.custom_message import ZiyaString, ZiyaMessageChunk
from app.utils.custom_bedrock import CustomBedrockClient
from app.agents.wrappers.nova_formatter import NovaFormatter
from pydantic import Field, validator


class NovaWrapper(BaseChatModel):
    """
    Wrapper for the Nova model in AWS Bedrock.
    """
    
    model_id: str
    client: Any = None  # Changed from Optional[BaseClient] to Any to accept any client type
    model_kwargs: Dict[str, Any] = {}
    
    @validator("client", pre=True)
    def validate_client(cls, v):
        """Validate the client - accept any client that has the necessary methods."""
        # Just check if the client has the necessary methods
        if v is not None and not hasattr(v, "converse"):
            raise ValueError("Client must have a 'converse' method")
        return v
    
    def __init__(self, model_id: str, **kwargs):
        """
        Initialize the NovaWrapper.
        
        Args:
            model_id: The model ID to use
            **kwargs: Additional arguments to pass to the model
        """
        super().__init__(model_id=model_id, **kwargs)
        
        # Initialize the client
        self.client = boto3.client("bedrock-runtime")
        
        # Get max_tokens from kwargs or model_kwargs
        max_tokens = kwargs.get("max_tokens")
        if max_tokens is None and "max_tokens" in self.model_kwargs:
            max_tokens = self.model_kwargs.get("max_tokens")
        
        # Wrap the client with our custom client to ensure max_tokens is correctly passed
        if max_tokens is not None:
            self.client = CustomBedrockClient(self.client, max_tokens=max_tokens)
            logger.info(f"Wrapped boto3 client with CustomBedrockClient in NovaWrapper, max_tokens={max_tokens}")
        
        # Set default model parameters - only include parameters that are provided
        self.model_kwargs = {}
        
        # Always include max_tokens and top_p which are supported by all Nova models
        self.model_kwargs["max_tokens"] = kwargs.get("max_tokens", 4096)
        self.model_kwargs["top_p"] = kwargs.get("top_p", 0.9)
        
        # Only include temperature if it's provided (will be filtered by supported_parameters)
        if "temperature" in kwargs:
            self.model_kwargs["temperature"] = kwargs.get("temperature")
            
        # Only include top_k if it's provided (will be filtered by supported_parameters)
        if "top_k" in kwargs:
            self.model_kwargs["top_k"] = kwargs.get("top_k")
            
        logger.info(f"Initialized NovaWrapper with model_kwargs: {self.model_kwargs}")
    
    def _format_messages(self, messages: List[BaseMessage]) -> Dict[str, Any]:
        """
        Format messages for the Nova model.
        
        Args:
            messages: The messages to format
        
        Returns:
            Dict[str, Any]: The formatted messages
        """
        logger.info(f"Formatting {len(messages)} messages for Nova")
        
        # Convert messages to the format expected by Nova
        formatted_messages = []
        
        # Extract system message content to prepend to the first user message
        system_content = ""
        for message in messages:
            if isinstance(message, SystemMessage):
                system_content += message.content + "\n\n"
        
        # Convert messages to dict format first
        message_dicts = []
        for message in messages:
            if isinstance(message, HumanMessage):
                content = message.content
                # If this is the first user message and we have system content, prepend it
                if system_content and not any(m.get("role") == "user" for m in message_dicts):
                    content = f"{system_content}\n\n{content}"
                
                message_dicts.append({
                    "role": "user",
                    "content": content
                })
            elif isinstance(message, AIMessage):
                message_dicts.append({
                    "role": "assistant",
                    "content": message.content
                })
            elif isinstance(message, SystemMessage):
                # Skip system messages - they're handled separately
                continue
            elif isinstance(message, ChatMessage):
                role = message.role
                if role == "human" or role == "user":
                    role = "user"
                elif role == "ai" or role == "assistant":
                    role = "assistant"
                else:
                    # Skip unsupported roles
                    logger.warning(f"Skipping unsupported role: {role}")
                    continue
                
                message_dicts.append({
                    "role": role,
                    "content": message.content
                })
            else:
                message_dicts.append({
                    "role": "user",
                    "content": str(message.content)
                })
        
        # Use NovaFormatter to format the messages
        formatted_messages = NovaFormatter.format_messages(message_dicts)
        
        # Add model parameters
        result = {
            "messages": formatted_messages,
            "temperature": self.model_kwargs.get("temperature", 0.7),
            "top_p": self.model_kwargs.get("top_p", 0.9),
            "max_tokens": self.model_kwargs.get("max_tokens", 4096),
        }
        
        return result
    
    def _parse_response(self, response: Dict[str, Any]) -> ZiyaString:
        """
        Parse the response from the Nova model.
        
        Args:
            response: The response from the Nova model
        
        Returns:
            ZiyaString: The parsed response
        """
        logger.info("=== NOVA WRAPPER parse_response START ===")
        
        # Use NovaFormatter to parse the response
        text = NovaFormatter.parse_response(response)
        
        # Create a ZiyaString from the parsed text
        if text:
            result = ZiyaString(text, id=f"nova-{hash(text) % 10000}", message=text)
            logger.info(f"Parsed response text length: {len(text)}")
            logger.info("=== NOVA WRAPPER parse_response END ===")
            return result
        
        # If we couldn't extract the text, return an error message
        error_message = f"Failed to parse Nova response: {str(response)[:100]}..."
        logger.error(error_message)
        
        logger.info("=== NOVA WRAPPER parse_response END ===")
        return ZiyaString(error_message, id=f"nova-error-{hash(error_message) % 10000}", message=error_message)
    
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """
        Generate a response from the Nova model.
        
        Args:
            messages: The messages to generate a response for
            stop: Optional stop sequences
            run_manager: Optional run manager
            **kwargs: Additional arguments
        
        Returns:
            ChatResult: The generated response
        """
        logger.info("=== NOVA WRAPPER _generate START ===")
        
        # Format the messages
        request_body = self._format_messages(messages)
        logger.info(f"Formatted {len(messages)} messages")
        
        # Use model_kwargs from the instance which have been filtered for supported parameters
        inference_params = {
            "max_tokens": self.model_kwargs.get("max_tokens", 4096),
            "top_p": self.model_kwargs.get("top_p", 0.9),
            "stop_sequences": stop
        }
        
        # Only include temperature if it's in model_kwargs
        if "temperature" in self.model_kwargs:
            inference_params["temperature"] = self.model_kwargs.get("temperature")
            
        logger.info(f"Using inference parameters: {inference_params}")
        
        # Format inference parameters using NovaFormatter
        inference_config, additional_fields = NovaFormatter.format_inference_params(inference_params)
        
        try:
            # Call the model
            logger.info(f"Calling Bedrock converse with model_id: {self.model_id}")
            response = self.client.converse(
                modelId=self.model_id,
                messages=request_body["messages"],
                inferenceConfig=inference_config
            )
            logger.info("Bedrock converse API call completed successfully")
            
            # Parse the response
            text = self._parse_response(response)
            
            # Clean any empty brackets from the response
            if isinstance(text, str) and ('[]' in text):
                cleaned_text = text.strip('[]')
                if cleaned_text != text:
                    logger.info(f"Cleaned empty brackets from Nova streaming response")
                    text = cleaned_text

            logger.info("About to parse response")
            text = self._parse_response(response)
            logger.info(f"Parsed response text length: {len(text)}")
            
            # Create an AIMessage with the text
            ai_message = AIMessage(content=text)
            
            # Create a Generation object
            logger.info("Creating Generation object directly")
            generation = ChatGeneration(
                message=ai_message,
                generation_info={"model_id": self.model_id}
            )
            logger.info(f"Created Generation with text length: {len(text)}")
            
            # Add id attribute to the Generation object
            if isinstance(text, ZiyaString):
                object.__setattr__(generation, 'id', text.id)
            else:
                object.__setattr__(generation, 'id', f"nova-{hash(text) % 10000}")
            
            logger.info(f"Generation type: {type(generation)}")
            logger.info(f"Generation has message attribute: {hasattr(generation, 'message')}")
            logger.info(f"Generation has id attribute: {hasattr(generation, 'id')}")
            
            # Create the result
            result = ChatResult(generations=[generation])
            
            logger.info("=== NOVA WRAPPER _generate END ===")
            return result
        except Exception as e:
            logger.error(f"Error calling Nova model: {str(e)}")
            logger.info("=== NOVA WRAPPER _generate END ===")
            raise
    
    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGeneration]:
        """
        Stream a response from the Nova model asynchronously.
        
        Args:
            messages: The messages to generate a response for
            stop: Optional stop sequences
            run_manager: Optional run manager
            **kwargs: Additional arguments
        
        Yields:
            ChatGeneration: The generated response chunks
        """
        logger.info("=== NOVA WRAPPER _astream START ===")
        
        # Format the messages
        request_body = self._format_messages(messages)
        logger.info(f"Converted messages to dict format, system prompt length: {len(json.dumps(request_body))}")
        
        # Use model_kwargs from the instance which have been filtered for supported parameters
        inference_params = {
            "max_tokens": self.model_kwargs.get("max_tokens", 4096),
            "top_p": self.model_kwargs.get("top_p", 0.9),
            "stop_sequences": stop
        }
        
        # Only include temperature if it's in model_kwargs
        if "temperature" in self.model_kwargs:
            inference_params["temperature"] = self.model_kwargs.get("temperature")
            
        logger.info(f"Using inference parameters for streaming: {inference_params}")
        
        # Format inference parameters using NovaFormatter
        inference_config, additional_fields = NovaFormatter.format_inference_params(inference_params)
        
        # Add system prompt if provided
        system_prompt = kwargs.get("system_prompt", "")
        if system_prompt:
            logger.info(f"Using system prompt, formatted length: {len(system_prompt)}")
        
        try:
            # Call the model
            logger.info(f"Calling Bedrock converse with model_id: {self.model_id}")
            response = self.client.converse(
                modelId=self.model_id,
                messages=request_body["messages"],
                inferenceConfig=inference_config
            )
            logger.info("Bedrock converse API call completed successfully")
            
            # Parse the response
            logger.info("About to parse response")
            text = self._parse_response(response)
            logger.info(f"Parsed response text length: {len(text)}")
            
            # Create an AIMessage with the text

            # Clean any empty brackets from the response
            if isinstance(text, str) and ('[]' in text):
                cleaned_text = text.strip('[]')
                if cleaned_text != text:
                    logger.info(f"Cleaned empty brackets from Nova response")
                    text = cleaned_text

            ai_message = AIMessage(content=text)
            
            # Create a Generation object
            logger.info("Creating Generation object directly")
            generation = ChatGeneration(
                message=ai_message,
                generation_info={"model_id": self.model_id}
            )
            logger.info(f"Created Generation with text length: {len(text)}")
            
            # Add id attribute to the Generation object
            if isinstance(text, ZiyaString):
                object.__setattr__(generation, 'id', text.id)
            else:
                object.__setattr__(generation, 'id', f"nova-{hash(text) % 10000}")
            
            logger.info(f"Generation type: {type(generation)}")
            logger.info(f"Generation has message attribute: {hasattr(generation, 'message')}")
            logger.info(f"Generation has id attribute: {hasattr(generation, 'id')}")
            
            # Yield the generation
            logger.info("=== NOVA WRAPPER _astream END ===")

            # Apply NovaFormatter cleaning to the chunk before yielding
            if hasattr(generation, 'message') and hasattr(generation.message, 'content'):
                content = generation.message.content
                if isinstance(content, str) and ('[]' in content):
                    cleaned_content = content.strip('[]')
                    if cleaned_content != content:
                        logger.info(f"Cleaned empty brackets from Nova streaming chunk")
                        # Create new message with cleaned content
                        new_message = AIMessage(content=cleaned_content)
                        generation = ChatGeneration(message=new_message, generation_info=generation.generation_info)

            yield generation
        except Exception as e:
            logger.error(f"Error calling Nova model: {str(e)}")
            logger.info("=== NOVA WRAPPER _astream END ===")
            raise
    
    @property
    def _llm_type(self) -> str:
        """
        Get the LLM type.
        
        Returns:
            str: The LLM type
        """
        return "nova"


class NovaBedrock(NovaWrapper):
    """
    Alias for NovaWrapper for backward compatibility.
    """
    pass
