"""
ZiyaBedrock wrapper for AWS Bedrock models.
Ensures all parameters are correctly passed to the Bedrock API.
"""

from typing import Any, Dict, List, Optional
from langchain_aws import ChatBedrock
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatResult
from app.utils.logging_utils import logger
from app.utils.custom_bedrock import CustomBedrockClient

class ZiyaBedrock:
    """
    ZiyaBedrock wrapper for AWS Bedrock models.
    Ensures all parameters are correctly passed to the Bedrock API.
    
    This class wraps ChatBedrock to ensure that parameters like max_tokens, 
    temperature, top_k, thinking_mode, etc. are correctly passed to the 
    underlying Bedrock API calls.
    """
    
    def __init__(
        self,
        model_id: str,
        client: Any = None,
        region_name: Optional[str] = None,
        credentials_profile_name: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model_kwargs: Optional[Dict[str, Any]] = None,
        streaming: bool = False,
        callbacks: Optional[List[Any]] = None,
        verbose: bool = False,
        thinking_mode: bool = False,
        **kwargs: Any,
    ):
        """Initialize the ZiyaBedrock wrapper."""
        # Store our custom parameters
        self.model_id = model_id
        self.ziya_max_tokens = max_tokens
        self.ziya_temperature = temperature
        self.ziya_thinking_mode = thinking_mode
        self.ziya_top_k = model_kwargs.get("top_k") if model_kwargs else None
        self.ziya_top_p = model_kwargs.get("top_p") if model_kwargs else None
        self.streaming = streaming
        self.callbacks = callbacks
        self.verbose = verbose
        self.region_name = region_name
        self.credentials_profile_name = credentials_profile_name
        self.kwargs = kwargs
        
        # Create the underlying ChatBedrock instance
        self.bedrock_model = ChatBedrock(
            model_id=model_id,
            client=client,
            region_name=region_name,
            credentials_profile_name=credentials_profile_name,
            model_kwargs=model_kwargs or {},
            streaming=streaming,
            callbacks=callbacks,
            verbose=verbose,
            **kwargs,
        )
        
        # Log initialization parameters for debugging
        logger.info(f"Initializing ZiyaBedrock with model_id={model_id}")
        logger.info(f"ZiyaBedrock parameters: max_tokens={max_tokens}, temperature={temperature}, thinking_mode={thinking_mode}")
        if model_kwargs:
            logger.info(f"Additional model_kwargs: {model_kwargs}")
            
        # Wrap the client with our custom client to ensure max_tokens is correctly passed
        if hasattr(self.bedrock_model, 'client') and self.bedrock_model.client is not None:
            self.bedrock_model.client = CustomBedrockClient(self.bedrock_model.client, max_tokens=max_tokens)
            logger.info(f"Wrapped boto3 client with CustomBedrockClient, max_tokens={max_tokens}")
    
    def _generate(
        self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kwargs: Any
    ) -> ChatResult:
        """
        Generate a response from the model.
        """
        # Add our stored parameters to kwargs if not already present
        if self.ziya_max_tokens is not None and "max_tokens" not in kwargs:
            kwargs["max_tokens"] = self.ziya_max_tokens
            logger.debug(f"Added max_tokens={self.ziya_max_tokens} to _generate kwargs")
        
        if self.ziya_temperature is not None and "temperature" not in kwargs:
            kwargs["temperature"] = self.ziya_temperature
            logger.debug(f"Added temperature={self.ziya_temperature} to _generate kwargs")
        
        if self.ziya_top_k is not None and "top_k" not in kwargs:
            kwargs["top_k"] = self.ziya_top_k
            logger.debug(f"Added top_k={self.ziya_top_k} to _generate kwargs")
        
        if self.ziya_top_p is not None and "top_p" not in kwargs:
            kwargs["top_p"] = self.ziya_top_p
            logger.debug(f"Added top_p={self.ziya_top_p} to _generate kwargs")
        
        # Apply thinking mode if needed
        if self.ziya_thinking_mode or kwargs.get("thinking_mode"):
            messages = self._apply_thinking_mode(messages)
            # Remove thinking_mode from kwargs to avoid confusion
            if "thinking_mode" in kwargs:
                del kwargs["thinking_mode"]
        
        # Call the underlying model's _generate method
        return self.bedrock_model._generate(messages, stop=stop, **kwargs)
    
    def _apply_thinking_mode(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        """Apply thinking mode to the messages."""
        thinking_instruction = "\nThink through this step-by-step. First analyze the problem, then explore possible approaches, and finally provide your solution with clear reasoning."
        
        # Make a copy of the messages to avoid modifying the original
        messages_copy = list(messages)
        
        # Check if there's a system message
        system_message_index = None
        for i, message in enumerate(messages_copy):
            if message.type == "system":
                system_message_index = i
                break
        
        if system_message_index is not None:
            # Add thinking instruction to the system message
            system_message = messages_copy[system_message_index]
            content = system_message.content + thinking_instruction
            messages_copy[system_message_index] = SystemMessage(content=content)
        else:
            # Create a new system message with the thinking instruction
            messages_copy.insert(0, SystemMessage(content=thinking_instruction))
        
        return messages_copy
    
    def bind(self, **kwargs: Any) -> BaseChatModel:
        """
        Bind parameters to the model.
        """
        # Update our stored parameters with any new values
        if "max_tokens" in kwargs:
            self.ziya_max_tokens = kwargs["max_tokens"]
            logger.debug(f"Updated ziya_max_tokens to {self.ziya_max_tokens} in bind")
        
        if "temperature" in kwargs:
            self.ziya_temperature = kwargs["temperature"]
            logger.debug(f"Updated ziya_temperature to {self.ziya_temperature} in bind")
        
        if "top_k" in kwargs:
            self.ziya_top_k = kwargs["top_k"]
            logger.debug(f"Updated ziya_top_k to {self.ziya_top_k} in bind")
        
        if "top_p" in kwargs:
            self.ziya_top_p = kwargs["top_p"]
            logger.debug(f"Updated ziya_top_p to {self.ziya_top_p} in bind")
        
        if "thinking_mode" in kwargs:
            self.ziya_thinking_mode = kwargs["thinking_mode"]
            logger.debug(f"Updated ziya_thinking_mode to {self.ziya_thinking_mode} in bind")
        
        # Create a new instance with updated parameters
        model_kwargs = {}
        if self.ziya_top_k is not None:
            model_kwargs["top_k"] = self.ziya_top_k
        if self.ziya_top_p is not None:
            model_kwargs["top_p"] = self.ziya_top_p
            
        # Create a new bedrock model with updated parameters
        self.bedrock_model = ChatBedrock(
            model_id=self.model_id,
            region_name=self.region_name,
            credentials_profile_name=self.credentials_profile_name,
            temperature=self.ziya_temperature,
            max_tokens=self.ziya_max_tokens,
            model_kwargs=model_kwargs,
            streaming=self.streaming,
            callbacks=self.callbacks,
            verbose=self.verbose,
            **self.kwargs
        )
        
        # Wrap the client with our custom client
        if hasattr(self.bedrock_model, 'client') and self.bedrock_model.client is not None:
            self.bedrock_model.client = CustomBedrockClient(self.bedrock_model.client, max_tokens=self.ziya_max_tokens)
            logger.debug(f"Wrapped boto3 client with CustomBedrockClient, max_tokens={self.ziya_max_tokens}")
        
        return self
    
    def get_model_id(self) -> str:
        """
        Get the model ID.
        
        Returns:
            str: The model ID
        """
        return self.model_id
    
    def get_parameters(self) -> Dict[str, Any]:
        """
        Get the current parameters.
        
        Returns:
            Dict[str, Any]: The current parameters
        """
        return {
            "max_tokens": self.ziya_max_tokens,
            "temperature": self.ziya_temperature,
            "top_k": self.ziya_top_k,
            "top_p": self.ziya_top_p,
            "thinking_mode": self.ziya_thinking_mode
        }
    
    # Forward LangChain BaseChatModel required methods
    @property
    def _llm_type(self) -> str:
        """Return the type of LLM."""
        return "ziya-bedrock"
    
    @property
    def _identifying_params(self) -> Dict[str, Any]:
        """Return identifying parameters."""
        return {
            "model_id": self.model_id,
            "max_tokens": self.ziya_max_tokens,
            "temperature": self.ziya_temperature,
            "top_k": self.ziya_top_k,
            "top_p": self.ziya_top_p,
            "thinking_mode": self.ziya_thinking_mode
        }
