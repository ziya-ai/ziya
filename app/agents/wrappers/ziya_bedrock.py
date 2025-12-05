"""
ZiyaBedrock wrapper for AWS Bedrock models.
Ensures all parameters are correctly passed to the Bedrock API.
"""
# a comment added to see if you are notified of this difference
import os

from typing import Any, Dict, List, Optional, AsyncIterator, Iterator, Callable, Union
from langchain_aws import ChatBedrock
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from app.utils.custom_bedrock import CustomBedrockClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableConfig
from app.utils.logging_utils import logger
from app.utils.custom_bedrock import CustomBedrockClient
import os
from app.utils.context_cache import get_context_cache_manager
from app.utils.conversation_context import conversation_context


class ZiyaBedrock(Runnable):
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
        self._model_id = model_id  # For compatibility with LangChain
        self.ziya_max_tokens = max_tokens
        self.ziya_temperature = temperature 
        self.ziya_thinking_mode = thinking_mode
        self.ziya_top_k = model_kwargs.get("top_k") if model_kwargs else None
        logger.info(f"ZiyaBedrock initialized with thinking_mode={thinking_mode}")
        self.ziya_top_p = model_kwargs.get("top_p") if model_kwargs else None
        self.streaming = streaming
        self.callbacks = callbacks
        self.context_cache_manager = get_context_cache_manager()
        self.verbose = verbose
        self.region_name = os.environ.get("AWS_REGION", region_name)
        self.credentials_profile_name = credentials_profile_name
        self.kwargs = kwargs
        
        # Initialize repetition detection state
        self._recent_lines = []
        self._max_repetitions = 5  # Lower threshold for better detection
        
        # Check if the model supports top_k
        from app.agents.models import ModelManager
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        model_name = os.environ.get("ZIYA_MODEL")
        model_config = ModelManager.get_model_config(endpoint, model_name)
        
        # If model doesn't support top_k, set it to None
        if ('supported_parameters' not in model_config or 
            'top_k' not in model_config.get('supported_parameters', [])):
            logger.info(f"Model {model_name} doesn't support top_k, setting to None")
            self.ziya_top_k = None
            # Also remove from model_kwargs if present
            if model_kwargs and "top_k" in model_kwargs:
                del model_kwargs["top_k"]

        # Ensure model_kwargs is a dict and update max_tokens
        current_model_kwargs = model_kwargs or {} # Use a temporary var or modify model_kwargs directly
        if max_tokens is not None:
            current_model_kwargs["max_tokens"] = max_tokens
            
        # Filter model kwargs based on the model's supported parameters
        from app.agents.models import ModelManager
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        model_name = os.environ.get("ZIYA_MODEL")
        
        model_config = ModelManager.get_model_config(endpoint, model_name)
        filtered_kwargs = ModelManager.filter_model_kwargs(current_model_kwargs, model_config)
        logger.info(f"Filtered model_kwargs: {filtered_kwargs}")

        # Handle boto3 client compatibility issues
        bedrock_client = None
        if client:
            try:
                # Test if the client is working properly by accessing a simple property
                _ = client.meta.region_name
                bedrock_client = client
                logger.debug("Bedrock client validation successful")
            except (AttributeError, RecursionError) as e:
                logger.debug(f"Client validation failed, creating fallback: {e}")
                # Create a fresh client if the provided one has issues
                import boto3
                bedrock_client = boto3.client('bedrock-runtime', region_name=region_name)
        
        # Create the underlying ChatBedrock instance with the fresh client
        self.bedrock_model = ChatBedrock(
            model_id=model_id,
            client=bedrock_client,  # Use the validated/fresh client
            region_name=region_name,
            max_tokens=max_tokens,  # Explicitly pass max_tokens
            credentials_profile_name=credentials_profile_name,
            model_kwargs=filtered_kwargs,
            streaming=streaming,
            callbacks=callbacks,
            verbose=verbose,
            **kwargs,
        )
        
        # Client is already wrapped by ModelManager, no need to wrap again
        logger.info(f"Using persistent Bedrock client for model_id={model_id}, max_tokens={max_tokens}")
        
        # Log initialization parameters for debugging
        logger.info(f"Initializing ZiyaBedrock with model_id={model_id}")
        logger.info(f"ZiyaBedrock parameters: max_tokens={self.ziya_max_tokens}, temperature={temperature}, thinking_mode={thinking_mode}")
        logger.info(f"Effective model_kwargs passed to ChatBedrock: {filtered_kwargs}")    

    def _prepare_messages_with_smart_caching(self, messages: List[BaseMessage], conversation_id: str = None, config: dict = None) -> List[BaseMessage]:
        """
        Prepare messages with smart context caching that splits stable and dynamic content.
        
        Args:
            messages: List of messages to prepare
            conversation_id: Optional conversation ID for caching
            config: Optional config dict that may contain conversation_id
            
        Returns:
            List of prepared messages with caching metadata
        """
        
        # Extract conversation_id from config if not provided directly
        if not conversation_id and config and isinstance(config, dict):
            conversation_id = config.get("conversation_id")
        
        # Extract conversation_id from config if not provided directly
        
        if not conversation_id:
            logger.debug(f"No conversation_id, skipping caching")
            return messages
            
        # Initialize conversation state before caching analysis if needed
        from app.utils.file_state_manager import FileStateManager
        file_state_manager = FileStateManager()
        
        if conversation_id not in file_state_manager.conversation_states:
            logger.info(f"🔧 CACHE: Initializing conversation state early for caching analysis")
            # We need to get the file list from somewhere - check if it's in the system message
            system_message = None
            for msg in messages:
                if isinstance(msg, SystemMessage):
                    system_message = msg
                    break
            
            if system_message:
                # Extract file paths from the system message content
                file_paths = self._extract_file_paths_from_content(system_message.content)
                if file_paths:
                    # Load file contents and initialize conversation state
                    file_contents = self._load_file_contents(file_paths)
                    file_state_manager.initialize_conversation(conversation_id, file_contents)
                    file_state_manager.mark_context_submission(conversation_id)
                    logger.info(f"🔧 CACHE: Early initialization complete for {len(file_contents)} files")
        
        # Context submission will be marked in extract_codebase before we get here
            return messages
            
        # Get model configuration to check caching support
        from app.agents.models import ModelManager
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        model_name = os.environ.get("ZIYA_MODEL")
        model_config = ModelManager.get_model_config(endpoint, model_name)
        
        if not model_config.get("supports_context_caching", False):
            logger.debug(f"Model doesn't support context caching")
            return messages
            
        # Process system messages with large content (codebase context)
        prepared_messages = []
        for message in messages:
            if isinstance(message, SystemMessage) and len(message.content) > 10000:
                logger.debug(f"🔍 CACHE: Analyzing system message with {len(message.content):,} characters")
                
                # Split context into stable and dynamic parts
                context_split = self._split_system_message_context(
                    message, conversation_id, model_config
                )
                
                if context_split:
                    # Replace the single large message with split messages
                    logger.info(f"✅ CACHE: Successfully split context into {len(context_split)} messages")
                    # Log the content of each split message to verify completeness
                    for i, msg in enumerate(context_split):
                        logger.info(f"Split message {i} ends with: {msg.content[-200:] if hasattr(msg, 'content') else 'No content'}")
                    prepared_messages.extend(context_split)
                    continue
                elif self.context_cache_manager.should_cache_context(message.content, model_config):
                    # Fall back to simple caching if splitting fails
                    logger.info(f"Enabling simple context caching for system message with {len(message.content)} characters")
                    
                    cached_message = SystemMessage(
                        content=message.content,
                        additional_kwargs={"cache_control": {"type": "ephemeral"}}
                    )
                    prepared_messages.append(cached_message)
                    continue
                else:
                    logger.info(f"❌ CACHE: Content not suitable for caching")
            prepared_messages.append(message)
            
        return prepared_messages

    def _split_system_message_context(
        self, 
        message: SystemMessage, 
        conversation_id: str, 
        model_config: Dict[str, Any]
    ) -> Optional[List[SystemMessage]]:
        """
        Split a system message with codebase context into cacheable and dynamic parts.
        
        Returns:
            List of SystemMessage objects if splitting was successful, None otherwise
        """
        if not conversation_id:
            logger.debug("No conversation_id provided, skipping context splitting")
            return None
            
        # Extract file paths from the message content
        file_paths = self._extract_file_paths_from_content(message.content)
        if not file_paths:
            return None
            
        # Split the context
        context_split = self.context_cache_manager.split_context_by_file_changes(
            conversation_id, message.content, file_paths
        )
        
        # Only split if we have substantial stable content to cache
        if len(context_split.stable_content) < 5000:
            logger.info("Stable content too small for splitting, using simple caching")
            return None
            
        messages = []
        
        # Add stable content with caching
        if context_split.stable_content:
            logger.info(f"💾 CACHE: Stable content: {len(context_split.stable_files)} files, "
                       f"{len(context_split.stable_content):,} chars → CACHED")
            stable_message = SystemMessage(
                content=context_split.stable_content,
                additional_kwargs={"cache_control": {"type": "ephemeral"}}
            )
            messages.append(stable_message)
            logger.info(f"Created cached stable context: {len(context_split.stable_content)} chars, {len(context_split.stable_files)} files")
        
        # Add dynamic content without caching
        if context_split.dynamic_content:
            logger.info(f"⚡ DYNAMIC: {len(context_split.dynamic_files)} files, "
                       f"{len(context_split.dynamic_content):,} chars → NOT CACHED")
            dynamic_message = SystemMessage(content=context_split.dynamic_content)
            messages.append(dynamic_message)
        
        return messages
    
    def _extract_file_paths_from_content(self, content: str) -> List[str]:
        """Extract file paths from system message content."""
        import re
        file_paths = []
        
        # Look for "File: " markers in the content
        for line in content.split('\n'):
            if line.startswith('File: '):
                file_path = line[6:].strip()  # Remove "File: " prefix
                if file_path:
                    file_paths.append(file_path)
        
        logger.debug(f"Extracted {len(file_paths)} file paths from system message")
        return file_paths
    
    def _load_file_contents(self, file_paths: List[str]) -> Dict[str, str]:
        """Load file contents for the given file paths."""
        from app.utils.file_utils import is_binary_file
        
        file_contents = {}
        base_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", "")
        
        for file_path in file_paths:
            try:
                full_path = os.path.join(base_dir, file_path)
                if os.path.isdir(full_path) or is_binary_file(full_path):
                    continue
                    
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    file_contents[file_path] = content
            except Exception as e:
                logger.warning(f"Failed to load file {file_path}: {e}")
                continue
        
        return file_contents

    def _generate(
        self, messages: List[BaseMessage], stop: Optional[List[str]] = None, config: Optional[Dict] = None, **kwargs: Any
    ) -> ChatResult:
        """
        Generate a response from the model.
        """
        
        # Add our stored parameters to kwargs if not already present

        # Prepare messages with caching if supported
        conversation_id = config.get("conversation_id") if config else None
        messages = self._prepare_messages_with_smart_caching(messages, conversation_id, config)

        # Ensure system messages are properly ordered after caching
        messages = self._ensure_system_message_ordering(messages)

        # Use much higher default if not set
        kwargs["max_tokens"] = int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", self.ziya_max_tokens or 32768))
        if self.ziya_max_tokens is not None and "max_tokens" not in kwargs:
            kwargs["max_tokens"] = self.ziya_max_tokens
            logger.debug(f"Added max_tokens={self.ziya_max_tokens} to _generate kwargs")
        
        if self.ziya_temperature is not None and "temperature" not in kwargs:
            kwargs["temperature"] = self.ziya_temperature 
            logger.debug(f"Added temperature={self.ziya_temperature} to _generate kwargs")
        
        # Only add top_k if it's not None (model supports it)
        if self.ziya_top_k is not None and "top_k" not in kwargs:
            kwargs["top_k"] = self.ziya_top_k
            logger.debug(f"Added top_k={self.ziya_top_k} to _generate kwargs")
        
        if self.ziya_top_p is not None and "top_p" not in kwargs:
            kwargs["top_p"] = self.ziya_top_p
            logger.debug(f"Added top_p={self.ziya_top_p} to _generate kwargs")

        # Filter kwargs based on the model's supported parameters
        from app.agents.models import ModelManager
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        model_name = os.environ.get("ZIYA_MODEL")
        
        # Get the model config and filter the kwargs
        model_config = ModelManager.get_model_config(endpoint, model_name)
        filtered_kwargs = ModelManager.filter_model_kwargs(kwargs, model_config)
        logger.info(f"Filtered _generate kwargs: {filtered_kwargs}")
        
        # Apply thinking mode if needed
        if self.ziya_thinking_mode or kwargs.get("thinking_mode"):
            messages = self._apply_thinking_mode(messages)
            logger.info("Applied thinking mode to messages")
            # Remove thinking_mode from kwargs to avoid confusion
            if "thinking_mode" in kwargs:
                del kwargs["thinking_mode"]
        
        # Call the underlying model's _generate method
        return self.bedrock_model._generate(messages, stop=stop, **filtered_kwargs)

    def _ensure_system_message_ordering(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        """
        Ensure system messages are properly ordered for Claude/Bedrock.
        Merges multiple consecutive system messages into a single one.
        """
        if not messages:
            return messages
            
        # Find all consecutive system messages at the beginning
        system_messages = []
        other_messages = []
        
        i = 0
        while i < len(messages) and isinstance(messages[i], SystemMessage):
            system_messages.append(messages[i])
            i += 1
            
        # Add remaining messages
        other_messages = messages[i:]
        
        # If we have multiple system messages, merge them
        if len(system_messages) > 1:
            logger.info(f"Merging {len(system_messages)} system messages into one for Claude compatibility")
            
            # Combine all system message content
            combined_content = ""
            combined_kwargs = {}
            
            for sys_msg in system_messages:
                combined_content += sys_msg.content + "\n\n"
                # Preserve cache control from any message that has it
                if hasattr(sys_msg, 'additional_kwargs') and sys_msg.additional_kwargs.get('cache_control'):
                    combined_kwargs['cache_control'] = sys_msg.additional_kwargs['cache_control']
            
            # Create single merged system message
            merged_system = SystemMessage(
                content=combined_content.strip(),
                additional_kwargs=combined_kwargs
            )
            
            return [merged_system] + other_messages
        
        return messages
    
    def _apply_thinking_mode(self, messages: List[BaseMessage]) -> List[BaseMessage]:
        """Apply thinking mode to the messages."""
        thinking_instruction = "\nThink through this step-by-step. First analyze the problem, then explore possible approaches, and finally provide your solution with clear reasoning."
        
        # Make a copy of the messages to avoid modifying the original list
        messages_copy = list(messages)
        
        # Check if there's a system message
        system_message_index = None
        for i, message in enumerate(messages_copy):
            if message.type == "system":
                system_message_index = i
                break
        logger.info(f"Applying thinking mode, found system message: {system_message_index is not None}")
        
        if system_message_index is not None:
            # Add thinking instruction to the system message
            system_message = messages_copy[system_message_index]
            content = system_message.content + thinking_instruction
            messages_copy[system_message_index] = SystemMessage(content=content)
        else:
            # Create a new system message with the thinking instruction
            messages_copy.insert(0, SystemMessage(content=thinking_instruction))
            logger.info("Added new system message with thinking instruction")
        
        return messages_copy
    
    def bind(self, **kwargs: Any) -> 'ZiyaBedrock':
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
            # Check if model supports top_k before setting it
            from app.agents.models import ModelManager
            endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
            model_name = os.environ.get("ZIYA_MODEL")
            model_config = ModelManager.get_model_config(endpoint, model_name)
            
            # Only set top_k if the model supports it
            if 'supported_parameters' in model_config and 'top_k' in model_config.get('supported_parameters', []):
                self.ziya_top_k = kwargs["top_k"]
                logger.debug(f"Updated ziya_top_k to {self.ziya_top_k} in bind")
            logger.debug(f"Updated ziya_top_k to {self.ziya_top_k} in bind")
        
        if "top_p" in kwargs:
            self.ziya_top_p = kwargs["top_p"]
            logger.debug(f"Updated ziya_top_p to {self.ziya_top_p} in bind")
        
        if "thinking_mode" in kwargs:
            self.ziya_thinking_mode = kwargs["thinking_mode"]
            logger.debug(f"Updated ziya_thinking_mode to {self.ziya_thinking_mode} in bind")
        
        # Create a new instance with updated parameters
        model_kwargs = {}
        
        # Only include top_k in model_kwargs if it's not None
        # (meaning the model supports it)
        if self.ziya_top_k is not None: 
            model_kwargs["top_k"] = self.ziya_top_k
        if self.ziya_top_p is not None:
            model_kwargs["top_p"] = self.ziya_top_p
            
        # Filter model kwargs based on the model's supported parameters
        from app.agents.models import ModelManager
        
        # Get the endpoint and model name
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        model_name = os.environ.get("ZIYA_MODEL")
        
        # Get the model config and filter the kwargs
        model_config = ModelManager.get_model_config(endpoint, model_name)
        filtered_kwargs = ModelManager.filter_model_kwargs(model_kwargs, model_config)
        logger.info(f"Filtered model_kwargs for {self.model_id}: {filtered_kwargs}")
            
        # Create a new bedrock model with updated parameters
        self.bedrock_model = ChatBedrock(
            model_id=self.model_id,
            region_name=self.region_name,
            credentials_profile_name=self.credentials_profile_name,
            temperature=self.ziya_temperature,
            max_tokens=self.ziya_max_tokens,
            model_kwargs=filtered_kwargs,
            streaming=self.streaming,
            callbacks=self.callbacks,
            verbose=self.verbose,
            **self.kwargs
        )
        
        # Wrap the client with our custom client
        if hasattr(self.bedrock_model, 'client') and self.bedrock_model.client is not None:
            # Get model config for extended context support
            from app.agents.models import ModelManager
            model_config = ModelManager.get_model_config('bedrock', ModelManager.get_model_alias())
            
            self.bedrock_model.client = CustomBedrockClient(
                self.bedrock_model.client, 
                max_tokens=self.ziya_max_tokens,
                model_config=model_config
            )
            logger.debug(f"Wrapped boto3 client with CustomBedrockClient, max_tokens={self.ziya_max_tokens}, model_config keys={list(model_config.keys())}")
        
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
    
    def stream(self, messages: List[Dict[str, Any]], system: Optional[str] = None, **kwargs) -> Iterator[str]:
        """
        Stream the model's response with the given messages and system prompt.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            system: Optional system prompt
            **kwargs: Additional parameters for the model
            
        Returns:
            An iterator of response chunks
        """
        # Convert the messages to LangChain format
        lc_messages = []
        
        # Add system message if provided
        if system:
            lc_messages.append(SystemMessage(content=system))
        
        # Add the rest of the messages
        for message in messages:
            if isinstance(message, dict):
                role = message.get("role", "")
                content = message.get("content", "")
                
                if role == "user":
                    lc_messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    lc_messages.append(AIMessage(content=content))
                elif role == "system":
                    lc_messages.append(SystemMessage(content=content))
            elif hasattr(message, 'type'):
                # Message is already a LangChain message object
                lc_messages.append(message)
            else:
                logger.warning(f"Skipping unsupported message type: {type(message)}")
        
        # Reset repetition detection state for this stream
        self._recent_lines = []
        
        # Set streaming to True for this call
        self.bedrock_model.streaming = True
        
        # Call the underlying model's stream method with retry logic
        stream_retries = 0
        max_stream_retries = 2
        
        while stream_retries <= max_stream_retries:
            try:
                for chunk in self.bedrock_model.stream(lc_messages, **kwargs):
                    if hasattr(chunk, 'content') and chunk.content:
                        # Check for repetitive lines
                        content = chunk.content
                        lines = content.split('\n')
                        
                        for line in lines:
                            if line.strip():  # Only track non-empty lines
                                self._recent_lines.append(line)
                                # Keep only recent lines
                                if len(self._recent_lines) > 100:
                                    self._recent_lines.pop(0)
                        
                        # Check if any line repeats too many times
                        if any(self._recent_lines.count(line) > self._max_repetitions for line in set(self._recent_lines)):
                            yield "\n\n**Warning: Response was interrupted because repetitive content was detected.**"
                            
                            # Log the repetitive content for debugging
                            repetitive_lines = [line for line in set(self._recent_lines) 
                                               if self._recent_lines.count(line) > self._max_repetitions]
                            logger.warning(f"Repetitive content detected. Repetitive lines: {repetitive_lines}")
                            
                            # Send a special marker to indicate the stream should end
                            yield "\n\n[STREAM_END_REPETITION_DETECTED]"
                            
                            # Break the streaming loop
                            logger.warning("Streaming response interrupted due to repetitive content")
                            return
                        
                        yield chunk.content
                    elif hasattr(chunk, 'message') and hasattr(chunk.message, 'content'):
                        yield chunk.message.content
                return  # Success, exit retry loop
                
            except Exception as e:
                error_str = str(e)
                if ("ThrottlingException" in error_str or "rate limit" in error_str.lower() or 
                    "timeout" in error_str.lower()) and stream_retries < max_stream_retries:
                    
                    stream_retries += 1
                    delay = 2 if stream_retries == 1 else 5  # 2s, 5s
                    logger.warning(f"🔄 STREAM_RETRY: Attempt {stream_retries}/{max_stream_retries} after {delay}s delay")
                    
                    import time
                    time.sleep(delay)
                    continue
                else:
                    raise  # Re-raise for higher-level retry or final failure
    
    async def astream(self, messages: List[Dict[str, Any]], system: Optional[str] = None, **kwargs) -> AsyncIterator[str]:
        """
        Asynchronously stream the model's response with the given messages and system prompt.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            system: Optional system prompt
            **kwargs: Additional parameters for the model
            
        Returns:
            An async iterator of response chunks
        """
        # Convert the messages to LangChain format
        lc_messages = []
        
        # Add system message if provided
        if system:
            lc_messages.append(SystemMessage(content=system))
        
        # Add the rest of the messages
        for message in messages:
            if isinstance(message, dict):
                role = message.get("role", "")
                content = message.get("content", "")
                
                if role == "user":
                    lc_messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    lc_messages.append(AIMessage(content=content))
                elif role == "system":
                    lc_messages.append(SystemMessage(content=content))
            elif hasattr(message, 'type'):
                # Message is already a LangChain message object
                lc_messages.append(message)
            else:
                logger.warning(f"Skipping unsupported message type: {type(message)}")
        
        # Reset repetition detection state for this stream
        self._recent_lines = []
        
        # Set streaming to True for this call
        self.bedrock_model.streaming = True
        
        # Call the underlying model's astream method
        async for chunk in self.bedrock_model.astream(lc_messages, **kwargs):
            if hasattr(chunk, 'content') and chunk.content:
                # Check for repetitive lines
                content = chunk.content
                lines = content.split('\n')
                
                for line in lines:
                    if line.strip():  # Only track non-empty lines
                        self._recent_lines.append(line)
                        # Keep only recent lines
                        if len(self._recent_lines) > 100:
                            self._recent_lines.pop(0)
                
                # Check if any line repeats too many times
                if any(self._recent_lines.count(line) > self._max_repetitions for line in set(self._recent_lines)):
                    yield "\n\n**Warning: Response was interrupted because repetitive content was detected.**"
                    
                    # Log the repetitive content for debugging
                    repetitive_lines = [line for line in set(self._recent_lines) 
                                       if self._recent_lines.count(line) > self._max_repetitions]
                    logger.warning(f"Repetitive content detected. Repetitive lines: {repetitive_lines}")
                    
                    # Send a special marker to indicate the stream should end
                    yield "\n\n[STREAM_END_REPETITION_DETECTED]"
                    
                    # Break the streaming loop
                    logger.warning("Streaming response interrupted due to repetitive content")
                    break
                
                yield chunk.content
            elif hasattr(chunk, 'message') and hasattr(chunk.message, 'content'):
                yield chunk.message.content
    
    # Forward LangChain BaseChatModel required methods
    def _extract_streaming_content(self, chunk):
        """Extract content from streaming chunks based on model type."""
        # Get model ID to determine provider
        model_id = self.model_id.lower() if hasattr(self, 'model_id') else ""
        
        # Handle DeepSeek format
        if "deepseek" in model_id and isinstance(chunk, dict):
            if "generation" in chunk:
                return chunk["generation"]
            
        # Default extraction for other models
        return chunk
    
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
    # Implement required Runnable methods
    def invoke(self, input: Any, config: Optional[Dict] = None, **kwargs: Any) -> Any:
        """
        Invoke the model with the given input.
        
        Args:
            input: The input to the model (messages or string)
            config: Optional configuration
            **kwargs: Additional parameters for the model
            
        Returns:
            The model's response
        """
        # Convert input to messages if needed
        if isinstance(input, str):
            messages = [HumanMessage(content=input)]
        elif isinstance(input, list) and all(isinstance(m, BaseMessage) for m in input):
            messages = input
        elif hasattr(input, 'to_messages'):
            messages = input.to_messages()
        else:
            messages = input

        # Extract conversation_id from various sources
        conversation_id = None
        if config and isinstance(config, dict):
            conversation_id = config.get("conversation_id")
        elif hasattr(input, 'get'):
            conversation_id = input.get('conversation_id')

        # Prepare messages with caching if supported
        messages = self._prepare_messages_with_smart_caching(messages, conversation_id, config)

        # Ensure system messages are properly ordered after caching
        messages = self._ensure_system_message_ordering(messages)
            
        # Add our stored parameters to kwargs if not already present
        if self.ziya_max_tokens is not None and "max_tokens" not in kwargs:
            kwargs["max_tokens"] = self.ziya_max_tokens
            
        if self.ziya_temperature is not None and "temperature" not in kwargs:
            kwargs["temperature"] = self.ziya_temperature
            
        if self.ziya_top_k is not None and "top_k" not in kwargs:
            kwargs["top_k"] = self.ziya_top_k
            
        if self.ziya_top_p is not None and "top_p" not in kwargs:
            kwargs["top_p"] = self.ziya_top_p
            
        # Apply thinking mode if needed
        if self.ziya_thinking_mode or kwargs.get("thinking_mode"):
            messages = self._apply_thinking_mode(messages)
            # Remove thinking_mode from kwargs to avoid confusion
            if "thinking_mode" in kwargs:
                del kwargs["thinking_mode"]
                
        # Call the underlying model's invoke method
        if conversation_id:
            with conversation_context(conversation_id):
                return self.bedrock_model.invoke(messages, config, **kwargs)
        else:
            return self.bedrock_model.invoke(messages, config, **kwargs)
        
    async def ainvoke(self, input: Any, config: Optional[Dict] = None, **kwargs: Any) -> Any:
        """
        Asynchronously invoke the model with the given input.
        
        Args:
            input: The input to the model (messages or string)
            config: Optional configuration
            **kwargs: Additional parameters for the model
            
        Returns:
            The model's response
        """
        
        # Convert input to messages if needed
        if isinstance(input, str):
            messages = [HumanMessage(content=input)]
        elif isinstance(input, list) and all(isinstance(m, BaseMessage) for m in input):
            messages = input
        elif hasattr(input, 'to_messages'):
            messages = input.to_messages()
        else:
            messages = input

        # Extract conversation_id from config before caching analysis  
        conversation_id = config.get("conversation_id") if config else None
        if not conversation_id and hasattr(input, 'get'):
            conversation_id = input.get('conversation_id')

        # Prepare messages with caching if supported
        messages = self._prepare_messages_with_smart_caching(messages, conversation_id, config)
        # Ensure system messages are properly ordered after caching
        messages = self._ensure_system_message_ordering(messages)

        # Add our stored parameters to kwargs if not already present
        if self.ziya_max_tokens is not None and "max_tokens" not in kwargs:
            kwargs["max_tokens"] = self.ziya_max_tokens
            
        if self.ziya_temperature is not None and "temperature" not in kwargs:
            kwargs["temperature"] = self.ziya_temperature
            
        if self.ziya_top_k is not None and "top_k" not in kwargs:
            kwargs["top_k"] = self.ziya_top_k
            
        if self.ziya_top_p is not None and "top_p" not in kwargs:
            kwargs["top_p"] = self.ziya_top_p
            
        # Apply thinking mode if needed
        if self.ziya_thinking_mode or kwargs.get("thinking_mode"):
            messages = self._apply_thinking_mode(messages)
            # Remove thinking_mode from kwargs to avoid confusion
            if "thinking_mode" in kwargs:
                del kwargs["thinking_mode"]
                
        # Call the underlying model's ainvoke method
        try:
            if conversation_id:
                with conversation_context(conversation_id):
                    return await self.bedrock_model.ainvoke(messages, config, **kwargs)
            else:
                return await self.bedrock_model.ainvoke(messages, config, **kwargs)
        except Exception as e:
            logger.error(f"Error in ainvoke: {str(e)}")
            raise
        
    def stream(self, input: Any, config: Optional[Dict] = None, **kwargs: Any) -> Iterator[Any]:
        """
        Stream the model's response with the given input.
        
        Args:
            input: The input to the model (messages or string)
            config: Optional configuration
            **kwargs: Additional parameters for the model
            
        Returns:
            An iterator of response chunks
        """
        # Convert input to messages if needed
        if isinstance(input, str):
            messages = [HumanMessage(content=input)]
        elif isinstance(input, list) and all(isinstance(m, BaseMessage) for m in input):
            messages = input
        elif hasattr(input, 'to_messages'):
            messages = input.to_messages()
        else:
            messages = input

        # Extract conversation_id from config if not in kwargs
        conversation_id = kwargs.get("conversation_id")
        if not conversation_id and config and isinstance(config, dict):
            conversation_id = config.get("conversation_id")
            logger.debug(f"Found conversation_id in config: {conversation_id}")

        # Prepare messages with caching if supported
        messages = self._prepare_messages_with_smart_caching(messages, conversation_id, config)
            
        # Add our stored parameters to kwargs if not already present
        if self.ziya_max_tokens is not None and "max_tokens" not in kwargs:
            kwargs["max_tokens"] = self.ziya_max_tokens
            
        if self.ziya_temperature is not None and "temperature" not in kwargs:
            kwargs["temperature"] = self.ziya_temperature
            
        if self.ziya_top_k is not None and "top_k" not in kwargs:
            kwargs["top_k"] = self.ziya_top_k
            
        if self.ziya_top_p is not None and "top_p" not in kwargs:
            kwargs["top_p"] = self.ziya_top_p
            
        # Apply thinking mode if needed
        if self.ziya_thinking_mode or kwargs.get("thinking_mode"):
            messages = self._apply_thinking_mode(messages)
            # Remove thinking_mode from kwargs to avoid confusion
            if "thinking_mode" in kwargs:
                del kwargs["thinking_mode"]
                
        # Set streaming to True for this call
        self.bedrock_model.streaming = True
        
        # Call the underlying model's stream method
        if conversation_id:
            with conversation_context(conversation_id):
                return self.bedrock_model.stream(messages, config, **kwargs)
        else:
            return self.bedrock_model.stream(messages, config, **kwargs)
        
    async def astream(self, input: Any, config: Optional[Dict] = None, **kwargs: Any) -> AsyncIterator[Any]:
        """
        Asynchronously stream the model's response with the given input.
        
        Args:
            input: The input to the model (messages or string)
            config: Optional configuration
            **kwargs: Additional parameters for the model
            
        Returns:
            An async iterator of response chunks
        """
        # Convert input to messages if needed
        if isinstance(input, str):
            messages = [HumanMessage(content=input)]
        elif isinstance(input, list) and all(isinstance(m, BaseMessage) for m in input):
            messages = input
        elif hasattr(input, 'to_messages'):
            messages = input.to_messages()
        else:
            messages = input
            
        # Prepare messages with caching if supported
        conversation_id = config.get("conversation_id") if config and isinstance(config, dict) else None
        messages = self._prepare_messages_with_smart_caching(messages, conversation_id, config)

        # Ensure system messages are properly ordered after caching
        messages = self._ensure_system_message_ordering(messages)        

        logger.info(f"ZiyaBedrock.astream called with model_id: {self.model_id}")

        # Extract conversation_id from config if not in kwargs
        if not conversation_id and config and isinstance(config, dict):
            conversation_id = config.get("conversation_id")
            logger.debug(f"Found conversation_id in config: {conversation_id}")
        elif hasattr(input, 'get'):
            conversation_id = input.get('conversation_id')
            logger.debug(f"Found conversation_id in input: {conversation_id}")

        # Add our stored parameters to kwargs if not already present
        if self.ziya_max_tokens is not None and "max_tokens" not in kwargs:
            kwargs["max_tokens"] = self.ziya_max_tokens
            
        if self.ziya_temperature is not None and "temperature" not in kwargs:
            kwargs["temperature"] = self.ziya_temperature
            
        if self.ziya_top_k is not None and "top_k" not in kwargs:
            kwargs["top_k"] = self.ziya_top_k
            
        if self.ziya_top_p is not None and "top_p" not in kwargs:
            kwargs["top_p"] = self.ziya_top_p
            
        # Apply thinking mode if needed
        if self.ziya_thinking_mode or kwargs.get("thinking_mode"):
            messages = self._apply_thinking_mode(messages)
            # Remove thinking_mode from kwargs to avoid confusion
            if "thinking_mode" in kwargs:
                del kwargs["thinking_mode"]
                
        # Set streaming to True for this call
        self.bedrock_model.streaming = True
        
        # Use conversation context if available
        if conversation_id:
            with conversation_context(conversation_id):
                # Call the underlying model's astream method and properly await it
                async for chunk in self.bedrock_model.astream(messages, config, **kwargs):
                    yield chunk
        else:
            # Call the underlying model's astream method and properly await it
            async for chunk in self.bedrock_model.astream(messages, config, **kwargs):
                yield chunk
    # Implement the Runnable protocol
    def transform(self, input: Any) -> Any:
        """Transform input according to Runnable protocol."""
        return self.invoke(input)
        
    async def atransform(self, input: Any) -> Any:
        """Transform input asynchronously according to Runnable protocol."""
        return await self.ainvoke(input)
        
    def batch(self, inputs: List[Any], config: Optional[RunnableConfig] = None, **kwargs: Any) -> List[Any]:
        """Process multiple inputs as a batch."""
        return [self.invoke(input, config, **kwargs) for input in inputs]
        
    async def abatch(self, inputs: List[Any], config: Optional[RunnableConfig] = None, **kwargs: Any) -> List[Any]:
        """Process multiple inputs as a batch asynchronously."""
        return [await self.ainvoke(input, config, **kwargs) for input in inputs]
