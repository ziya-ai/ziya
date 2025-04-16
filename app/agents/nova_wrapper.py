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

from app.utils.logging_utils import logger
from app.agents.custom_message import ZiyaString, ZiyaMessageChunk
from app.utils.custom_bedrock import CustomBedrockClient


class NovaWrapper(BaseChatModel):
    """
    Wrapper for the Nova model in AWS Bedrock.
    """
    
    model_id: str
    client: Optional[BaseClient] = None
    model_kwargs: Dict[str, Any] = {}
    
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
        
        # Set default model parameters
        self.model_kwargs = {
            "temperature": kwargs.get("temperature", 0.7),
            "top_p": kwargs.get("top_p", 0.9),
            "top_k": kwargs.get("top_k", 50),
            "max_tokens": kwargs.get("max_tokens", 4096),
        }
    
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
        for message in messages:
            if isinstance(message, HumanMessage):
                formatted_messages.append({
                    "role": "user",
                    "content": message.content
                })
            elif isinstance(message, AIMessage):
                formatted_messages.append({
                    "role": "assistant",
                    "content": message.content
                })
            elif isinstance(message, SystemMessage):
                formatted_messages.append({
                    "role": "system",
                    "content": message.content
                })
            elif isinstance(message, ChatMessage):
                role = message.role
                if role == "human":
                    role = "user"
                elif role == "ai":
                    role = "assistant"
                formatted_messages.append({
                    "role": role,
                    "content": message.content
                })
            else:
                formatted_messages.append({
                    "role": "user",
                    "content": str(message.content)
                })
        
        # Add model parameters
        result = {
            "messages": formatted_messages,
            "temperature": self.model_kwargs.get("temperature", 0.7),
            "top_p": self.model_kwargs.get("top_p", 0.9),
            "top_k": self.model_kwargs.get("top_k", 50),
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
        logger.info("=== NOVA FORMATTER parse_response START ===")
        
        # Log the response
        logger.info(f"Response type: {type(response)}")
        logger.info(f"Response keys: {list(response.keys())}")
        logger.info(f"Response preview: {str(response)[:500]}...")
        
        # Extract the message
        if "output" in response and "message" in response["output"]:
            message = response["output"]["message"]
            logger.info(f"Found output.message structure")
            logger.info(f"Message keys: {list(message.keys())}")
            
            # Extract the content
            if "content" in message and isinstance(message["content"], list):
                content_blocks = message["content"]
                logger.info(f"Found content array with {len(content_blocks)} items")
                
                # Extract the text from each content block
                text_parts = []
                for i, block in enumerate(content_blocks):
                    logger.info(f"Content block keys: {list(block.keys())}")
                    if "text" in block:
                        text_parts.append(block["text"])
                
                # Join the text parts
                text = "".join(text_parts)
                logger.info(f"Extracted text of length: {len(text)}")
                
                # Create a ZiyaString
                result = ZiyaString(text, id=f"nova-{hash(text) % 10000}", message=text)
                
                logger.info("=== NOVA FORMATTER parse_response END ===")
                return result
        
        # If we couldn't extract the text, return an error message
        error_message = f"Failed to parse Nova response: {str(response)[:100]}..."
        logger.error(error_message)
        
        logger.info("=== NOVA FORMATTER parse_response END ===")
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
        
        # Add stop sequences if provided
        if stop:
            request_body["stopSequences"] = stop
        
        # Convert the request body to JSON
        request_json = json.dumps(request_body)
        
        try:
            # Call the model
            logger.info(f"Calling Bedrock converse with model_id: {self.model_id}")
            response = self.client.converse(
                modelId=self.model_id,
                messages=request_body["messages"],
                inferenceConfig={
                    "temperature": request_body.get("temperature", 0.7),
                    "topP": request_body.get("top_p", 0.9),
                    "topK": request_body.get("top_k", 50),
                    "maxTokens": request_body.get("max_tokens", 4096),
                    "stopSequences": request_body.get("stopSequences", []),
                }
            )
            logger.info("Bedrock converse API call completed successfully")
            
            # Parse the response
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
        
        # Log the formatted messages
        for i, message in enumerate(request_body["messages"]):
            logger.info(f"Formatted message {i}: role={message['role']}, content blocks=1")
            logger.info(f"  Block 0 text: {message['content'][:50]}...")
        
        logger.info(f"Formatted {len(request_body['messages'])} messages")
        
        # Add system prompt if provided
        system_prompt = kwargs.get("system_prompt", "")
        if system_prompt:
            logger.info(f"Using system prompt, formatted length: {len(system_prompt)}")
        
        # Add stop sequences if provided
        inference_params = {}
        if stop:
            inference_params["stopSequences"] = stop
            logger.info(f"Using stop sequences: {stop}")
        
        try:
            # Call the model
            logger.info(f"Calling Bedrock converse with model_id: {self.model_id}")
            response = self.client.converse(
                modelId=self.model_id,
                messages=request_body["messages"],
                inferenceConfig={
                    "temperature": request_body.get("temperature", 0.7),
                    "topP": request_body.get("top_p", 0.9),
                    "topK": request_body.get("top_k", 50),
                    "maxTokens": request_body.get("max_tokens", 4096),
                    "stopSequences": stop or [],
                }
            )
            logger.info("Bedrock converse API call completed successfully")
            
            # Parse the response
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
            
            # Yield the generation
            logger.info("=== NOVA WRAPPER _astream END ===")
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
