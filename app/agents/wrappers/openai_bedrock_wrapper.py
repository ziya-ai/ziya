"""
Wrapper for OpenAI models on AWS Bedrock.
"""

import json
import os
from typing import Any, Dict, Iterator, List, Optional
import boto3

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.outputs import ChatGenerationChunk

from app.utils.logging_utils import logger


class OpenAIBedrock(BaseChatModel):
    """Wrapper for OpenAI models on AWS Bedrock."""
    
    client: Any = None
    model_id: str
    model_kwargs: Dict[str, Any] = {}
    streaming: bool = False
    
    def __init__(self, **kwargs):
        """Initialize the OpenAI Bedrock wrapper."""
        super().__init__(**kwargs)
        
        # OpenAI models are only available in us-west-2
        # Override any other region setting for OpenAI models
        region = "us-west-2"
        
        # Log if we're overriding the region
        requested_region = kwargs.get("region_name") or os.environ.get("AWS_REGION")
        if requested_region and requested_region != "us-west-2":
            logger.warning(f"OpenAI models are only available in us-west-2. Overriding region from {requested_region} to us-west-2")
        
        # Get AWS profile if set
        profile = os.environ.get("ZIYA_AWS_PROFILE")
        if profile:
            session = boto3.Session(profile_name=profile)
            self.client = session.client("bedrock-runtime", region_name=region)
        else:
            self.client = boto3.client("bedrock-runtime", region_name=region)
        
        logger.info(f"Initialized OpenAIBedrock with model_id: {self.model_id} in region: {region}")
        
        # Update environment variable to ensure consistency
        os.environ["AWS_REGION"] = region

        # Validate model availability
        self._validate_model_availability()

    def _validate_model_availability(self):
        """Check if the model is available in the current region."""
        try:
            # Try to list available models to check if this model exists
            bedrock_client = boto3.client("bedrock", region_name=self.client.meta.region_name)
            response = bedrock_client.list_foundation_models()

            available_models = [m['modelId'] for m in response.get('modelSummaries', [])]

            if self.model_id not in available_models:
                logger.warning(f"Model {self.model_id} not found in available models for region {self.client.meta.region_name}")
                logger.info(f"Available OpenAI models: {[m for m in available_models if 'openai' in m.lower()]}")

                # Check if we need a different region
                if not any('openai' in m.lower() for m in available_models):
                    logger.warning("No OpenAI models found in this region. OpenAI models may only be available in specific regions like us-east-1")
        except Exception as e:
            logger.warning(f"Could not validate model availability: {e}")
            # Continue anyway - the actual API call will fail if the model is not available
    
    def _convert_messages_to_openai_format(self, messages: List[BaseMessage]) -> List[Dict[str, str]]:
        """Convert LangChain messages to OpenAI format."""
        openai_messages = []
        
        for message in messages:
            if isinstance(message, SystemMessage):
                openai_messages.append({
                    "role": "system",
                    "content": message.content
                })
            elif isinstance(message, HumanMessage):
                openai_messages.append({
                    "role": "user",
                    "content": message.content
                })
            elif isinstance(message, AIMessage):
                openai_messages.append({
                    "role": "assistant",
                    "content": message.content
                })
            else:
                # Default to user role for unknown message types
                openai_messages.append({
                    "role": "user",
                    "content": str(message.content)
                })
        
        return openai_messages
    
    def _prepare_request_body(self, messages: List[BaseMessage]) -> str:
        """Prepare the request body for OpenAI models on Bedrock."""
        logger.debug(f"🔍 OPENAI_WRAPPER: Received {len(messages)} messages")
        for i, msg in enumerate(messages):
            logger.debug(f"🔍 OPENAI_WRAPPER: Message {i}: {type(msg).__name__} - {str(msg.content)[:100]}...")
        
        openai_messages = self._convert_messages_to_openai_format(messages)
        logger.debug(f"🔍 OPENAI_WRAPPER: Converted to {len(openai_messages)} OpenAI messages")
        
        # Build the request body
        # Get default max_tokens from environment
        env_max_tokens = os.environ.get("ZIYA_MAX_OUTPUT_TOKENS")
        default_max_tokens = 4096
        if env_max_tokens:
            try:
                default_max_tokens = int(env_max_tokens)
            except ValueError:
                pass
        
        body = {
            "messages": openai_messages,
            "max_completion_tokens": self.model_kwargs.get("max_tokens", default_max_tokens),
            "temperature": self.model_kwargs.get("temperature", 0.7),
        }
        
        # Add optional parameters if present
        if "top_p" in self.model_kwargs:
            body["top_p"] = self.model_kwargs["top_p"]
        
        if "top_k" in self.model_kwargs:
            body["top_k"] = self.model_kwargs["top_k"]
        
        logger.debug(f"OpenAI Bedrock request body: {json.dumps(body, indent=2)}")
        return json.dumps(body)
    
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Generate a response from OpenAI model on Bedrock."""
        try:
            # Log the actual model ID being used
            logger.info(f"Invoking OpenAI model: {self.model_id} in region: {self.client.meta.region_name}")

            # Check if this is a test/debug scenario
            if "ValidationException" in str(self.model_id):
                logger.error(f"Model ID appears to be invalid: {self.model_id}")
                raise ValueError(f"Invalid model ID: {self.model_id}. OpenAI models on Bedrock typically use format like 'openai.gpt-4' or similar.")

            logger.debug(f"Request will be sent to region: {self.client.meta.region_name}")

            body = self._prepare_request_body(messages)
            
            response = self.client.invoke_model(
                modelId=self.model_id,
                body=body,
                contentType="application/json",
                accept="application/json"
            )
            
            response_body = json.loads(response["body"].read())
            
            # Extract the assistant's response
            content = response_body.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # Create the AIMessage
            message = AIMessage(content=content)
            
            return ChatResult(generations=[ChatGeneration(message=message)])
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error in OpenAIBedrock._generate: {error_msg}")

            # Provide more helpful error messages
            if "ValidationException" in error_msg and "model identifier is invalid" in error_msg:
                logger.error(f"The model ID '{self.model_id}' is not valid for AWS Bedrock in region {self.client.meta.region_name}")
                logger.error("OpenAI models on Bedrock may:")
                logger.error("  1. Only be available in specific regions (try us-east-1)")
                logger.error("  2. Require different model IDs than documented")
                logger.error("  3. Need special access or permissions")
                logger.error("Please check AWS Bedrock console for available OpenAI models in your region")
            raise
    
    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """Stream responses from OpenAI model on Bedrock."""
        try:
            
            body = self._prepare_request_body(messages)
            
            response = self.client.invoke_model_with_response_stream(
                modelId=self.model_id,
                body=body,
                contentType="application/json",
                accept="application/json"
            )
            
            for event in response["body"]:
                chunk = json.loads(event["chunk"]["bytes"])
                
                # Extract content from the chunk
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    reasoning = delta.get("reasoning", "")
                    
                    if reasoning:
                        # Create a special chunk for reasoning content
                        reasoning_chunk = ChatGenerationChunk(
                            message=AIMessageChunk(content="", additional_kwargs={"reasoning": reasoning})
                        )
                        yield reasoning_chunk
                    
                    if content:
                        yield ChatGenerationChunk(
                            message=AIMessageChunk(content=content)
                        )
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error in OpenAIBedrock._stream: {error_msg}")

            # Provide more helpful error messages
            if "ValidationException" in error_msg and "model identifier is invalid" in error_msg:
                logger.error(f"The model ID '{self.model_id}' is not valid for AWS Bedrock in region {self.client.meta.region_name}")
                logger.error("OpenAI models on Bedrock may:")
                logger.error("  1. Only be available in specific regions (try us-east-1)")
                logger.error("  2. Require different model IDs than documented")
                logger.error("  3. Need special access or permissions")
                logger.error("Please check AWS Bedrock console for available OpenAI models in your region")
            raise
    
    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """Async stream - delegates to sync stream for now."""
        for chunk in self._stream(messages, stop, run_manager, **kwargs):
            yield chunk
    
    @property
    def _llm_type(self) -> str:
        """Return the type of language model."""
        return "openai-bedrock"
    
    @property
    def _identifying_params(self) -> Dict[str, Any]:
        """Return the identifying parameters."""
        return {
            "model_id": self.model_id,
            "model_kwargs": self.model_kwargs,
        }
