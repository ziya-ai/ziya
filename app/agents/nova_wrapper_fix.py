"""
Nova wrapper for AWS Bedrock with fixes for Nova-Lite model.

This module provides a wrapper for the Nova model in AWS Bedrock,
with specific fixes for the Nova-Lite model.
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
        
        # Set default model parameters
        self.model_kwargs = {
            "temperature": kwargs.get("temperature", 0.7),
            "top_p": kwargs.get("top_p", 0.9),
            "max_tokens": kwargs.get("max_tokens", 4096),
        }
        
        # Only add top_k for non-lite models
        if "nova-lite" not in model_id.lower():
            self.model_kwargs["top_k"] = kwargs.get("top_k", 50)
    
    def _format_messages(self, messages: List[BaseMessage]) -> Dict[str, Any]:
        """
        Format messages for the Nova model.
        
        Args:
            messages: The messages to format
        
        Returns:
            Dict[str, Any]: The formatted messages
        """
        logger.info(f"Formatting {len(messages)} messages for Nova")
        
        # Check if we're using Nova-Lite
        is_nova_lite = "nova-lite" in self.model_id.lower()
        
        # Convert messages to the format expected by Nova
        formatted_messages = []
        for message in messages:
            if isinstance(message, HumanMessage):
                # For Nova-Lite, content must be a list/tuple
                if is_nova_lite and isinstance(message.content, str):
                    formatted_messages.append({
                        "role": "user",
                        "content": [{"type": "text", "text": message.content}]
                    })
                else:
                    formatted_messages.append({
                        "role": "user",
                        "content": message.content
                    })
            elif isinstance(message, AIMessage):
                # For Nova-Lite, content must be a list/tuple
                if is_nova_lite and isinstance(message.content, str):
                    formatted_messages.append({
                        "role": "assistant",
                        "content": [{"type": "text", "text": message.content}]
                    })
                else:
                    formatted_messages.append({
                        "role": "assistant",
                        "content": message.content
                    })
            elif isinstance(message, SystemMessage):
                # For Nova-Lite, content must be a list/tuple
                if is_nova_lite and isinstance(message.content, str):
                    formatted_messages.append({
                        "role": "system",
                        "content": [{"type": "text", "text": message.content}]
                    })
                else:
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
                
                # For Nova-Lite, content must be a list/tuple
                if is_nova_lite and isinstance(message.content, str):
                    formatted_messages.append({
                        "role": role,
                        "content": [{"type": "text", "text": message.content}]
                    })
                else:
                    formatted_messages.append({
                        "role": role,
                        "content": message.content
                    })
            else:
                # For Nova-Lite, content must be a list/tuple
                if is_nova_lite:
                    formatted_messages.append({
                        "role": "user",
                        "content": [{"type": "text", "text": str(message.content)}]
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
            "max_tokens": self.model_kwargs.get("max_tokens", 4096),
        }
        
        # Only add top_k for non-lite models
        if "nova-lite" not in self.model_id.lower():
            result["top_k"] = self.model_kwargs.get("top_k", 50)
        
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
        
        # Check if we're using Nova-Lite
        is_nova_lite = "nova-lite" in self.model_id.lower()
        
        try:
            # Call the model
            logger.info(f"Calling Bedrock converse with model_id: {self.model_id}")
            
            # Prepare inference config based on model type
            inference_config = {
                "temperature": request_body.get("temperature", 0.7),
                "topP": request_body.get("top_p", 0.9),
                "maxTokens": request_body.get("max_tokens", 4096),
            }
            
            # Only add topK for non-lite models
            if not is_nova_lite:
                inference_config["topK"] = request_body.get("top_k", 50)
            
            # Add stop sequences if provided
            if stop:
                inference_config["stopSequences"] = stop
            
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
            ai_message = AIMessage(content=text)
            
            # Create a Generation object
            logger.info("Creating Generation object directly")
            
            # Fix for pydantic validation error
            # We need to create a valid ChatGeneration object
            generation_info = {"model_id": self.model_id}
            
            # Store id and message in generation_info instead of as attributes
            if isinstance(text, ZiyaString):
                generation_info["id"] = text.id
                generation_info["message"] = text.message
            else:
                generation_info["id"] = f"nova-{hash(text) % 10000}"
                generation_info["message"] = str(text)
            
            generation = ChatGeneration(
                message=ai_message,
                generation_info=generation_info
            )
            
            logger.info(f"Created Generation with text length: {len(text)}")
            logger.info(f"Generation type: {type(generation)}")
            logger.info(f"Generation has id in generation_info: {generation.generation_info.get('id') is not None}")
            logger.info(f"Generation has message in generation_info: {generation.generation_info.get('message') is not None}")
            
            # Create the result with a list of generations
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
            logger.info(f"  Block 0 text: {str(message['content'])[:50]}...")
        
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
        
        # Check if we're using Nova-Lite
        is_nova_lite = "nova-lite" in self.model_id.lower()
        
        try:
            # Call the model
            logger.info(f"Calling Bedrock converse with model_id: {self.model_id}")
            
            # Prepare inference config based on model type
            inference_config = {
                "temperature": request_body.get("temperature", 0.7),
                "topP": request_body.get("top_p", 0.9),
                "maxTokens": request_body.get("max_tokens", 4096),
            }
            
            # Only add topK for non-lite models
            if not is_nova_lite:
                inference_config["topK"] = request_body.get("top_k", 50)
            
            # Add stop sequences if provided
            if stop:
                inference_config["stopSequences"] = stop
            
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
            ai_message = AIMessage(content=text)
            
            # Create a Generation object
            logger.info("Creating Generation object directly")
            
            # Fix for pydantic validation error
            # We need to create a valid ChatGeneration object
            generation_info = {"model_id": self.model_id}
            
            # Store id and message in generation_info instead of as attributes
            if isinstance(text, ZiyaString):
                generation_info["id"] = text.id
                generation_info["message"] = text.message
            else:
                generation_info["id"] = f"nova-{hash(text) % 10000}"
                generation_info["message"] = str(text)
            
            generation = ChatGeneration(
                message=ai_message,
                generation_info=generation_info
            )
            
            logger.info(f"Created Generation with text length: {len(text)}")
            logger.info(f"Generation type: {type(generation)}")
            logger.info(f"Generation has id in generation_info: {generation.generation_info.get('id') is not None}")
            logger.info(f"Generation has message in generation_info: {generation.generation_info.get('message') is not None}")
            
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
