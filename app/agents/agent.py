import os
import os.path
import re
import sys
from typing import Dict, List, Tuple, Set, Union, Optional, Any, cast

def _extract_content(x: Any) -> str:
    """Extract content from various response formats."""
    # If x has a content attribute (standard case)
    if hasattr(x, 'content'):
        return str(x.content)
        
    # If x is a list of dicts with text field (Nova-Lite case)
    if isinstance(x, list) and x:
        if isinstance(x[0], dict) and 'text' in x[0]:
            return str(x[0]['text'])
        # Combine all text fields if multiple chunks
        texts = []
        for chunk in x:
            if isinstance(chunk, dict) and 'text' in chunk:
                texts.append(str(chunk['text']))
        if texts:
            return ' '.join(texts)
            
    # If x is a dict with text field
    if isinstance(x, dict) and 'text' in x:
        return str(x['text'])
        
    # Last resort: convert to string
    return str(x)

import json
import time
import botocore
import asyncio
import tiktoken

# Import custom exceptions first to ensure they're available for error handling
from app.utils.custom_exceptions import KnownCredentialException, ThrottlingException, ExpiredTokenException

# Wrap imports in try/except to catch credential errors early
try:
    from langchain.agents import AgentExecutor
    from langchain.agents.format_scratchpad import format_xml
    from langchain_aws import ChatBedrock
    from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
    from google.api_core.exceptions import ResourceExhausted
    from langchain_community.document_loaders import TextLoader
    from langchain_core.agents import AgentFinish
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, BaseMessage
    from langchain_core.outputs import Generation
    from langchain_core.runnables import RunnablePassthrough, Runnable
except KnownCredentialException as e:
    # Print clean error message without traceback
    print("\n" + "=" * 80)
    print(str(e))
    print("=" * 80 + "\n")
    sys.exit(1)
from langserve import add_routes
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from botocore.exceptions import ClientError

from app.agents.prompts import conversational_prompt
from app.agents.prompts_manager import get_extended_prompt, get_model_info_from_config
from app.agents.models import ModelManager
from app.middleware import RequestSizeMiddleware
from app.utils.sanitizer_util import clean_backtick_sequences
from app.utils.context_enhancer import initialize_ast, enhance_query_context
from app.utils.logging_utils import logger
from app.utils.print_tree_util import print_file_tree
from app.utils.file_utils import is_binary_file
from app.utils.file_state_manager import FileStateManager
from app.utils.error_handlers import format_error_response, detect_error_type
from app.utils.custom_exceptions import KnownCredentialException, ThrottlingException, ExpiredTokenException

# Wrap model initialization in try/except to catch credential errors early
try:
    # Initialize the model
    model = ModelManager()
except KnownCredentialException as e:
    # Print clean error message without traceback
    print("\n" + "=" * 80)
    print(str(e))
    print("=" * 80 + "\n")
    sys.exit(1)


def clean_chat_history(chat_history: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Clean chat history by removing invalid messages and normalizing content."""
    if not chat_history or not isinstance(chat_history, list):
        return []
    try:
        cleaned = []
        for human, ai in chat_history:
            # Skip pairs with empty messages
            if not isinstance(human, str) or not isinstance(ai, str):
                logger.warning(f"Skipping invalid message pair: human='{human}', ai='{ai}'")
                continue
            human_clean = human.strip() if human else ""
            ai_clean = ai.strip() if ai else ""
            if not human_clean or not ai_clean:
                logger.warning(f"Skipping empty message pair")
                continue
            cleaned.append((human.strip(), ai.strip()))
        return cleaned
    except Exception as e:
        logger.error(f"Error cleaning chat history: {str(e)}")
        logger.error(f"Raw chat history: {chat_history}")
        return cleaned

def _format_chat_history(chat_history: List[Tuple[str, str]]) -> List[Union[HumanMessage, AIMessage]]:
    logger.info(f"Chat history type: {type(chat_history)}")
    cleaned_history = clean_chat_history(chat_history)
    buffer = []
    logger.debug("Message format before conversion:")
    try:
        for human, ai in cleaned_history:
            if human and isinstance(human, str):
                logger.debug(f"Human message type: {type(human)}, content: {human[:100]}")
                try:
                    buffer.append(HumanMessage(content=str(human)))
                except Exception as e:
                    logger.error(f"Error creating HumanMessage: {str(e)}")
            if ai and isinstance(ai, str):
                logger.debug(f"AI message type: {type(ai)}, content: {ai[:100]}")
                try:
                    buffer.append(AIMessage(content=str(ai)))
                except Exception as e:
                    logger.error(f"Error creating AIMessage: {str(e)}")
    except Exception as e:
        logger.error(f"Error formatting chat history: {str(e)}")
        logger.error(f"Problematic chat history: {chat_history}")
        return []

    logger.debug(f"Final formatted messages: {[type(m).__name__ for m in buffer]}")
    return buffer

def _extract_content(x: Any) -> str:
    """Extract content from various response formats."""
    # If x has a content attribute (standard case)
    if hasattr(x, 'content'):
        content = x.content
        # Handle callable content
        if callable(content):
            content = content()
        return _extract_content(content)  # Recursively extract from content
        
    # Handle None
    if x is None:
        return ""
    
    # Handle string
    if isinstance(x, str):
        return x
        
    # If x is a list of dicts with text field (Nova-Lite case)
    if isinstance(x, list) and x:
        if isinstance(x[0], dict) and 'text' in x[0]:
            return str(x[0]['text'])
        # Combine all text fields if multiple chunks
        texts = []
        for chunk in x:
            if isinstance(chunk, dict) and 'text' in chunk:
                texts.append(str(chunk['text']))
        if texts:
            return ''.join(texts)  # Direct concatenation without spaces
            
    # If x is a dict with text field
    if isinstance(x, dict) and 'text' in x:
        return str(x['text'])
    
    # If x is a dict with output structure (Nova format)
    if isinstance(x, dict) and 'output' in x:
        output = x['output']
        if isinstance(output, dict) and 'message' in output:
            message = output['message']
            if 'content' in message and isinstance(message['content'], list):
                texts = []
                for block in message['content']:
                    if 'text' in block:
                        texts.append(str(block['text']))
                if texts:
                    return ''.join(texts)
        
    # Last resort: convert to string
    return str(x)

def parse_output(message):
    """Parse and sanitize the output from the language model."""
    # Import the enhanced parse_output function
    from app.agents.parse_output import parse_output as enhanced_parse_output
    return enhanced_parse_output(message)

    try:
        # Check if this is an error message
        error_data = json.loads(content)
        if error_data.get('error') == 'validation_error':
            logger.info(f"Detected validation error in output: {content}")
            return AgentFinish(return_values={"output": content}, log=content)
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass

    try:
        # Extract diff content from markdown code fence if present
        if "```diff" in content:
            parts = content.split("```diff")
            logger.debug(f"Split parts: {len(parts)}")
            if len(parts) > 1:
                logger.debug("Processing diff parts:")
                logger.debug(f"Before diff: {parts[0]}")
                logger.debug(f"Diff part before cleanup: {parts[1]}")

                diff_content = parts[1].split("```")[0].strip()
                return AgentFinish(return_values={"output": diff_content}, log=diff_content)
        
        # If not a diff or error, clean and return the content
        text = clean_backtick_sequences(content)
        logger.info(f"parse_output extracted content size: {len(content)} chars, cleaned size: {len(text)} chars")
        return AgentFinish(return_values={"output": text}, log=text)
    except Exception as e:
        logger.error(f"Error in parse_output content processing: {str(e)}")
        # Provide a safe fallback
        return AgentFinish(return_values={"output": content}, log=content)

# Create a wrapper class that adds retries
class RetryingChatBedrock(Runnable):
    def __init__(self, model):
        self.model = model
        self.provider = None # to be set by ModelManager

    def _debug_input(self, input: Any):
        """Debug log input structure"""
        logger.info(f"Input type: {type(input)}")
        if hasattr(input, 'to_messages'):
            logger.info("ChatPromptValue detected, messages:")
            messages = input.to_messages()
            for i, msg in enumerate(messages):
                logger.info(f"Message {i}:")
                logger.info(f"  Type: {type(msg)}")
                logger.info(f"  Content type: {type(msg.content)}")
                logger.info(f"  Content: {msg.content}")
        elif isinstance(input, dict):
            logger.info(f"Input keys: {input.keys()}")
            if 'messages' in input:
                logger.info("Messages content:")
                for i, msg in enumerate(input['messages']):
                    logger.info(f"Message {i}: type={type(msg)}, content={msg}")
        else:
            logger.info(f"Raw input: {input}")

    def bind(self, **kwargs):
        # Filter kwargs to only include supported parameters for the current model
        # For Claude models, we need to handle binding differently
        endpoint = os.environ.get("ZIYA_ENDPOINT", ModelManager.DEFAULT_ENDPOINT)
        model_name = os.environ.get("ZIYA_MODEL")
        model_config = ModelManager.get_model_config(endpoint, model_name)
        model_id = model_config.get("model_id", model_name)
        
        # For Claude models, we need to be careful with certain parameters
        if model_id and "claude" in model_id.lower():
            # Filter out unsupported parameters for Claude models
            supported_kwargs = {}
            for key, value in kwargs.items():
                if key != "stop":  # Claude doesn't support stop parameter in this context
                    supported_kwargs[key] = value
            
            # Only log this once per process
            if not hasattr(self.__class__, '_binding_logged'):
                logger.info(f"Binding with filtered kwargs for Claude model: {supported_kwargs}")
                self.__class__._binding_logged = True
        else:
            supported_kwargs = kwargs
            if not hasattr(self.__class__, '_binding_logged'):
                logger.info(f"Binding with kwargs: {supported_kwargs}")
                self.__class__._binding_logged = True
            
        return RetryingChatBedrock(self.model.bind(**supported_kwargs))

    def get_num_tokens(self, text: str) -> int:
        return self.model.get_num_tokens(text)

    def __getattr__(self, name: str):
        # Delegate any unknown attributes to the underlying model
        return getattr(self.model, name)

    def _get_provider_format(self) -> str:
        """Get the message format requirements for current provider."""
        # Can be extended for other providers
        return os.environ.get("ZIYA_ENDPOINT", "bedrock")

    def _convert_to_messages(self, input_value: Any) -> Union[str, List[Dict[str, str]]]:
        """Convert input to messages format expected by provider."""
        if isinstance(input_value, (str, list)):
            return input_value

    async def _handle_stream_error(self, e: Exception):
        """Handle stream errors by yielding an error message."""
        error_type, detail, status_code, retry_after = detect_error_type(str(e))
        error_message = {
            "error": error_type,
            "detail": detail,
            "status_code": status_code
        }
        if retry_after:
            error_message["retry_after"] = retry_after
            
        logger.info(f"[ERROR_SSE] Preparing error message: {error_message}")
        
        # Format as proper SSE message
        sse_message = f"data: {json.dumps(error_message)}\n\n"
        logger.info(f"[ERROR_SSE] Sending error SSE message: {sse_message}")
        yield AIMessageChunk(content=sse_message)
        
        # Send DONE marker as proper SSE message
        done_message = "data: [DONE]\n\n"
        logger.info(f"[ERROR_SSE] Sending DONE marker: {done_message}")
        yield AIMessageChunk(content=done_message)
        return

    def _prepare_input(self, input: Any) -> Dict:
        """Convert input to format expected by Bedrock."""
        logger.info("Preparing input for Bedrock")

        if hasattr(input, 'to_messages'):
            # Handle ChatPromptValue
            messages = input.to_messages()
            logger.debug(f"Model type: {type(self.model)}")
            logger.debug(f"Original messages: {messages}")

            # Filter out empty messages but keep the original message types
            filtered_messages = [
                msg for msg in messages
                if self._format_message_content(msg)
            ]

            return filtered_messages

    async def _handle_validation_error(self, e: Exception):
        """Handle validation errors by yielding an error message."""
        error_type, detail, status_code, retry_after = detect_error_type(str(e))
        error_message = {
            "error": error_type,
            "detail": detail,
            "status_code": status_code
        }
        if retry_after:
            error_message["retry_after"] = retry_after
            
        logger.info(f"[ERROR_SSE] Preparing validation error message: {error_message}")
        
        # Format as proper SSE message
        sse_message = f"data: {json.dumps(error_message)}\n\n"
        logger.info(f"[ERROR_SSE] Sending validation error SSE message: {sse_message}")
        yield AIMessageChunk(content=sse_message)
        
        # Send DONE marker as proper SSE message
        done_message = "data: [DONE]\n\n"
        logger.info(f"[ERROR_SSE] Sending DONE marker: {done_message}")
        yield AIMessageChunk(content=done_message)
        return
    def _is_streaming(self, func) -> bool:
        """Check if this is a streaming operation."""
        return hasattr(func, '__name__') and func.__name__ == 'astream'
        
    def _get_model_config(self):
        """Get the configuration for the current model."""
        from app.config import MODEL_CONFIGS, MODEL_FAMILIES
        
        if not hasattr(self.model, 'model_id'):
            return {}
            
        model_id = self.model.model_id.lower()
        
        # Check each model configuration
        for endpoint, models in MODEL_CONFIGS.items():
            for model_name, config in models.items():
                if config.get('model_id', '').lower() == model_id:
                    # Get the base configuration
                    model_config = config.copy()
                    
                    # If the model has a family, merge with family configuration
                    if "family" in model_config:
                        family_name = model_config["family"]
                        if family_name in MODEL_FAMILIES:
                            family_config = MODEL_FAMILIES[family_name].copy()
                            
                            # Merge family config with model config (model config takes precedence)
                            for key, value in family_config.items():
                                if key not in model_config:
                                    model_config[key] = value
                                    
                            # If family has a parent, merge with parent configuration
                            if "parent" in family_config:
                                parent_name = family_config["parent"]
                                if parent_name in MODEL_FAMILIES:
                                    parent_config = MODEL_FAMILIES[parent_name].copy()
                                    
                                    # Merge parent config with model config (model and family configs take precedence)
                                    for key, value in parent_config.items():
                                        if key not in model_config and key not in family_config:
                                            model_config[key] = value
                    
                    return model_config
        
        # Default empty config if not found
        return {}

    def _format_message_content(self, message: Any) -> str:
        """Ensure message content is properly formatted as a string."""
        logger.info(f"Formatting message: type={type(message)}")
        if isinstance(message, dict):
            logger.info(f"Dict message keys: {message.keys()}")
            if 'content' in message:
                logger.info(f"Content type: {type(message['content'])}")
                logger.info(f"Content value: {message['content']}")
        try:
            # Handle different message formats
            if isinstance(message, dict):
                content = message.get('content', '')
            elif hasattr(message, 'content'):
                content = message.content
            else:
                content = str(message)
            # Ensure content is a string
            if not isinstance(content, str):
                if content is None:
                    return ""
                content = str(content)
 
            return content.strip()
        except Exception as e:
            logger.error(f"Error formatting message content: {str(e)}")
            return ""
 
    def _prepare_messages_for_provider(self, input: Any) -> List[Dict[str, str]]:
        formatted_messages = []
        
        # Convert input to messages list
        if hasattr(input, 'to_messages'):
            messages = list(input.to_messages())
            logger.debug(f"Converting ChatPromptValue to messages: {len(messages)} messages")
        elif isinstance(input, (list, tuple)):
            messages = list(input)
        else:
            messages = [input]
            
        # Process messages in order
        logger.debug(f"Processing {len(messages)} messages")
        for msg in messages:
            # Extract role and content
            if isinstance(msg, (SystemMessage, HumanMessage, AIMessage)):
                if isinstance(msg, SystemMessage):
                    role = 'system'
                elif isinstance(msg, HumanMessage):
                    role = 'user'
                else:
                    role = 'assistant'
                content = msg.content
            elif isinstance(msg, dict) and 'content' in msg:
                role = msg.get('role', 'user')
                content = msg['content']
            else:
                role = 'user'
                content = str(msg)

            logger.debug(f"Message type: {type(msg)}, role: {role}, content type: {type(content)}")

            # Skip empty assistant messages
            if role == 'assistant' and not content:
                continue

            # Ensure content is a non-empty string
            content = str(content).strip()
            if not content:
                continue

            formatted_messages.append({
                'role': role,
                'content': content
            })
 
        return formatted_messages
 
    @property
    def model_id(self):
        """Get model ID from ModelManager rather than underlying model."""
        if not self._model_id:
            self._model_id = ModelManager.get_model_id(self.model)
        return self._model_id

    @property
    def _is_chat_model(self):
        return isinstance(self.model, ChatBedrock)

    async def astream(self, input: Any, config: Optional[Dict] = None, **kwargs):
        """Stream responses with retries and proper message formatting."""
        max_retries = 3
        base_retry_delay = 1
        
        # Add AWS credential debugging
        from app.utils.aws_utils import debug_aws_credentials
        debug_aws_credentials()

        for attempt in range(max_retries):
            logger.info(f"Attempt {attempt + 1} of {max_retries}")
            try:
                # Convert input to messages if needed
                if hasattr(input, 'to_messages'):
                    messages = input.to_messages()
                    logger.debug(f"Using messages from ChatPromptValue: {len(messages)} messages")
                else:
                    messages = input
                    logger.debug(f"Using input directly: {type(input)}")

                # Filter out empty messages
                if isinstance(messages, list):
                    messages = [
                        msg for msg in messages 
                        if isinstance(msg, BaseMessage) and msg.content
                    ]
                    if not messages:
                        raise ValueError("No valid messages with content")
                    logger.debug(f"Filtered to {len(messages)} non-empty messages")
                
                # Debug the Bedrock client being used
                if hasattr(self.model, 'client'):
                    client = self.model.client
                    logger.info(f"Bedrock client type: {type(client)}")
                    if hasattr(client, '_request_signer'):
                        logger.info("Client has request signer")
                        signer = client._request_signer
                        if hasattr(signer, 'credentials'):
                            creds = signer.credentials
                            logger.info(f"Signer credential type: {type(creds)}")
                            if hasattr(creds, 'access_key'):
                                logger.info(f"Signer access key ends with: {creds.access_key[-4:]}")
                            if hasattr(creds, 'token') and creds.token:
                                token_length = len(creds.token) if creds.token else 0
                                logger.info(f"Signer has session token of length: {token_length}")

                async for chunk in self.model.astream(messages, config, **kwargs):
                    if isinstance(chunk, ChatGoogleGenerativeAIError):
                        # Format Gemini errors as structured error payload within a chunk
                        error_msg = {
                            "error": "server_error",
                            "detail": str(chunk),
                            "status_code": 500
                        }
                        # Yield the error payload as content in an AIMessageChunk
                        yield AIMessageChunk(content=json.dumps(error_msg))
                        # Signal termination by yielding a specific marker (optional, stream_chunks can also detect error type)
                        # yield AIMessageChunk(content="[STREAM_ERROR_SENTINEL]") # Alternative approach
                        return

                    elif isinstance(chunk, AIMessageChunk):
                        raw_chunk_content_repr = repr(chunk.content)[:200]
                        logger.debug(f"RetryingChatBedrock - Received AIMessageChunk, raw content preview: {raw_chunk_content_repr}")
                        content = chunk.content() if callable(chunk.content) else chunk.content
                        full_content_str = str(content) # Ensure it's a string
                        logger.debug(f"RetryingChatBedrock - Extracted FULL content string (len={len(full_content_str)}): '{full_content_str}'")
                        extracted_content_repr = repr(content)[:200]
                        logger.debug(f"RetryingChatBedrock - Extracted content preview: {extracted_content_repr}")
                        yield AIMessageChunk(content=content)
                    else:
                        yield chunk

                break  # Success, exit retry loop
                
                # Re-raise the exception for the middleware to handle
                raise
                # Format as proper SSE message
                sse_message = f"data: {error_json}\n\n"
                logger.info(f"[ERROR_SSE] Preparing throttling error message: {sse_message}")
                yield AIMessageChunk(content=sse_message)
                
                # Send DONE marker
                done_message = "data: [DONE]\n\n"
                logger.info(f"[ERROR_SSE] Preparing DONE marker after throttle error: {done_message}")
                yield AIMessageChunk(content=done_message)
                return

            except ChatGoogleGenerativeAIError as e:
                # Format Gemini errors as structured error payload within a chunk
                error_msg = {
                    "error": "server_error",
                    "detail": str(e),
                    "status_code": 500
                }
                # Yield the error payload as content in an AIMessageChunk
                yield AIMessageChunk(content=json.dumps(error_msg))
                return

            except ClientError as e:
                error_str = str(e)
                logger.warning(f"Bedrock client error: {error_str}")
                
                # Run credential debug again on error
                logger.info("Running credential debug after error")
                debug_aws_credentials()
                
                error_type, detail, status_code, retry_after = detect_error_type(error_str)
                logger.info(f"Detected error type: {error_type}, status: {status_code}")
                
                # Format error message
                error_message = {
                    "error": error_type,
                    "detail": detail,
                    "status_code": status_code
                }
                if retry_after:
                    error_message["retry_after"] = retry_after
                
                error_json = json.dumps(error_message)
                logger.info(f"[ERROR_TRACE] Preparing error response JSON: {error_json}")
                
                # Yield the error payload as content in an AIMessageChunk
                # sse_message = f"data: {error_json}\n\n" # Don't format here
                logger.info(f"[ERROR_SSE] Yielding structured error chunk: {error_json}")
                
                # Log the exact message we're about to yield
                logger.info("[ERROR_TRACE] About to yield AIMessageChunk with error content")
                yield AIMessageChunk(content=error_json)
                logger.info("[ERROR_TRACE] Yielded error chunk")
                
                # Send DONE marker as proper SSE message
                done_message = "data: [DONE]\n\n"
                logger.info(f"[ERROR_SSE] Sending DONE marker: {done_message}")
                yield AIMessageChunk(content=done_message)
                logger.info("[ERROR_TRACE] Yielded DONE marker")
                return

            except Exception as e:
                error_str = str(e)
                logger.warning(f"Error on attempt {attempt + 1}: {error_str}")

                # Check if this is a throttling error wrapped in another exception
                if "ThrottlingException" in error_str or "Too many requests" in error_str:
                    logger.warning("Detected throttling error in exception")
                    # Format error message for throttling
                    error_message = {
                        "error": "throttling_error",
                        "detail": "Too many requests to AWS Bedrock. Please wait a moment before trying again.",
                        "status_code": 429,
                        "retry_after": "5"
                    }
                    
                    error_json = json.dumps(error_message)
                    logger.info(f"[ERROR_TRACE] Yielding structured throttling error response: {error_json}")
                    
                    # Format as proper SSE message
                    # sse_message = f"data: {error_json}\n\n" # Don't format here
                    logger.info(f"[ERROR_SSE] Yielding throttling error chunk: {error_json}")
                    yield AIMessageChunk(content=error_json)
                    
                    # Send DONE marker chunk
                    done_message = "data: [DONE]\n\n"
                    logger.info(f"[ERROR_SSE] Sending DONE marker: {done_message}")
                    yield AIMessageChunk(content=done_message)
                    return

                # Check if this is a Bedrock error that was wrapped in another exception
                error_type, detail, status_code, retry_after = detect_error_type(error_str)
                logger.info(f"Detected error type: {error_type}, status: {status_code}")
                
                if attempt < max_retries - 1:
                    # Exponential backoff and retry
                    retry_delay = base_retry_delay * (2 ** attempt)
                    await asyncio.sleep(retry_delay)
                    continue
                
                # Final attempt failed, send error response
                error_message = {
                    "error": error_type,
                    "detail": detail,
                    "status_code": status_code
                }
                if retry_after:
                    error_message["retry_after"] = retry_after
                
                logger.info(f"Yielding final error response: {error_message}")
                # Yield the error payload as content in an AIMessageChunk
                yield AIMessageChunk(content=json.dumps(error_message))
                
                # Send DONE marker chunk
                done_message = "data: [DONE]\n\n"
                yield AIMessageChunk(content=done_message)
                return

    def _format_messages(self, input_messages: List[Any]) -> List[Dict[str, str]]:
        """Format messages according to provider requirements."""
        provider = self._get_provider_format()
        formatted = []

        try:
            for msg in input_messages:
                if isinstance(msg, (HumanMessage, AIMessage, SystemMessage)):
                    # Convert LangChain messages based on provider
                    if provider == "bedrock":
                        role = "user" if isinstance(msg, HumanMessage) else \
                              "assistant" if isinstance(msg, AIMessage) else \
                              "system"
                    else:
                        # Default/fallback format
                        role = msg.__class__.__name__.lower().replace('message', '')

                    content = self._format_message_content(msg)
                elif isinstance(msg, dict) and "role" in msg and "content" in msg:
                    # Already in provider format
                    role = msg["role"]
                    content = self._format_message_content(msg["content"])
                else:
                    logger.warning(f"Unknown message format: {type(msg)}")
                    role = "user"  # Default to user role
                    content = self._format_message_content(msg)

                formatted.append({"role": role, "content": content})
        except Exception as e:
            logger.error(f"Error formatting messages: {str(e)}")
            raise
    def _validate_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Remove any messages with empty content."""
        return [msg for msg in messages if msg.get('content')]

    def invoke(self, input: Any, config: Optional[Dict] = None, **kwargs) -> Any:
        """Invoke the model with retries and proper message formatting."""
        max_retries = 3
        base_retry_delay = 1.0
        
        for attempt in range(max_retries):
            try:
                # Get message format from model configuration
                model_config = self._get_model_config()
                message_format = model_config.get("message_format")
                model_id = getattr(self.model, 'model_id', 'unknown')
                logger.info(f"Using model: {model_id}")
                
                # Format messages based on configuration
                if message_format:
                    from app.utils.message_formatter import format_messages
                    formatted_input = format_messages(input, message_format)
                    logger.info(f"Formatted messages for {message_format} format")
                    
                    # Debug log the formatted input
                    if isinstance(formatted_input, list):
                        logger.info(f"Sending {len(formatted_input)} messages to model")
                        for i, msg in enumerate(formatted_input):
                            if hasattr(msg, 'content'):
                                logger.info(f"Message {i}: type={type(msg)}, content_type={type(msg.content)}")
                                # Log content details for debugging
                                content = msg.content
                                if isinstance(content, list) and len(content) > 0:
                                    logger.info(f"Content[0]: {content[0]}")
                            else:
                                logger.info(f"Message {i}: type={type(msg)}")
                else:
                    formatted_input = input
                
                # Apply model-specific inference parameters from config
                if model_config.get("inference_parameters"):
                    inference_params = model_config.get("inference_parameters", {})
                    logger.info(f"Applying model-specific inference parameters from config")
                    
                    # Ensure we have inferenceConfig
                    if "inferenceConfig" not in kwargs:
                        kwargs["inferenceConfig"] = {}
                    
                    # Apply parameters from config
                    for param_name, param_value in inference_params.items():
                        if param_name not in kwargs["inferenceConfig"]:
                            kwargs["inferenceConfig"][param_name] = param_value
                            logger.info(f"Setting {param_name}={param_value} from model config")
                    
                    logger.info(f"Updated inference parameters: {kwargs['inferenceConfig']}")
                
                # Log the kwargs being sent to the model
                logger.info(f"Model kwargs: {kwargs}")
                
                # Continue with the original method using the formatted messages
                response = self.model.invoke(formatted_input, config, **kwargs)
                
                # Log the response
                if hasattr(response, 'content'):
                    content = response.content
                    logger.info(f"Response content type: {type(content)}")
                    logger.info(f"Response content preview: {str(content)[:100]}...")
                else:
                    logger.info(f"Response type: {type(response)}")
                
                return response
                
            except Exception as e:
                # Handle retries and errors
                error_str = str(e)
                logger.error(f"Error in invoke (attempt {attempt+1}/{max_retries}): {error_str}")
                
                # Log more details about the error
                if "ValidationException" in error_str:
                    logger.error("Validation error detected. Checking messages...")
                    
                    # Check for content field errors
                    if "content field" in error_str:
                        if isinstance(input, list):
                            for i, msg in enumerate(input):
                                if hasattr(msg, 'content'):
                                    content = msg.content
                                    logger.error(f"Message {i}: content_type={type(content)}, empty={not bool(content)}")
                    
                    # Check for text field errors
                    elif "text field" in error_str:
                        if isinstance(formatted_input, list):
                            for i, msg in enumerate(formatted_input):
                                if hasattr(msg, 'content'):
                                    content = msg.content
                                    logger.error(f"Message {i}: content={content}")
                                    if isinstance(content, list) and len(content) > 0:
                                        text = content[0].get('text', '')
                                        logger.error(f"Message {i} text: '{text}', empty={not bool(text)}")
                    
                    # Print the full formatted input for debugging
                    logger.error("Full formatted input:")
                    if isinstance(formatted_input, list):
                        for i, msg in enumerate(formatted_input):
                            logger.error(f"Message {i}: {msg}")
                    else:
                        logger.error(f"Input: {formatted_input}")
                
                if attempt < max_retries - 1:
                    # Exponential backoff
                    retry_delay = base_retry_delay * (2 ** attempt)
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    # Last attempt failed, raise the exception
                    logger.error(f"All {max_retries} attempts failed")
                    raise

    async def ainvoke(self, input: Any, config: Optional[Dict] = None, **kwargs) -> Any:
        return await self.model.ainvoke(input, config, **kwargs)

class LazyLoadedModel:
    def __init__(self):
        self._model = None
        self._model_with_stop = None
        self._binding_logged = False
 
    def get_model(self):
        """Get the underlying model instance"""
        if self._model is None:
            # Initialize the model on first use
            logger.info("Initializing model on first use")
            from app.agents.models import ModelManager
            model_instance = ModelManager.initialize_model(force_reinit=True)
            self._model = RetryingChatBedrock(model_instance)
        return self._model
        
    def __call__(self):
        """Maintain backwards compatibility but log deprecation"""
        logger.warning("Direct call to model() is deprecated, use get_model() instead")
        return self.get_model()
        
    def reset(self):
        """Force a complete reset of the model instance"""
        self._model = None
        self._model_with_stop = None
        self._binding_logged = False
 
    def bind(self, **kwargs):
        if self._model is None:
            self._model_with_stop = self.get_model().bind(**kwargs)
        return self.get_model().bind(**kwargs)
 
model = LazyLoadedModel()
llm_with_stop = model.bind(stop=["</tool_input>"])

file_state_manager = FileStateManager()

def get_combined_docs_from_files(files, conversation_id: str = "default") -> str:
    logger.info("=== get_combined_docs_from_files called ===")
    logger.info(f"Called with files: {files}")
    combined_contents: str = ""
    logger.debug("Processing files:")
    print_file_tree(files if isinstance(files, list) else files.get("config", {}).get("files", []))
    
    # Log the raw files input
    logger.info(f"Raw files input type: {type(files)}")
    logger.info(f"Files to process: {files}")
    
    logger.info(f"Processing files with conversation_id: {conversation_id}")

    # Initialize AST capabilities if enabled
    if os.environ.get("ZIYA_ENABLE_AST") == "true":
        try:
            logger.info("AST initialization requested via ZIYA_ENABLE_AST=true")
            codebase_dir = os.environ["ZIYA_USER_CODEBASE_DIR"]
            logger.info(f"Using codebase directory: {codebase_dir}")
            
            ignored_patterns = get_ignored_patterns(codebase_dir)
            logger.info(f"Using ignored patterns: {ignored_patterns}")
            
            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
            logger.info(f"Using max depth: {max_depth}")
            
            ast_init_result = initialize_ast(codebase_dir, ignored_patterns, max_depth)
            logger.info(f"AST initialization result: {ast_init_result}")
        except Exception as e:
            logger.error(f"Failed to initialize AST capabilities: {e}")
            import traceback
            logger.error(f"AST initialization traceback: {traceback.format_exc()}")

    user_codebase_dir: str = os.environ["ZIYA_USER_CODEBASE_DIR"]
    for file_path in files:
        full_path = os.path.join(user_codebase_dir, file_path)
        # Skip directories
        if os.path.isdir(full_path):
            logger.debug(f"Skipping directory: {full_path}")
            continue
        try:
            # Get annotated content with change tracking
            logger.info(f"Getting annotated content for {file_path}")
            annotated_lines, success = file_state_manager.get_annotated_content(conversation_id, file_path)
            logger.info(f"Got {len(annotated_lines) if annotated_lines else 0} lines for {file_path}, success={success}")
            if success:
                # Log a preview of the content
                preview = "\n".join(annotated_lines[:5]) if annotated_lines else "NO CONTENT"
                logger.info(f"Content preview for {file_path}:\n{preview}\n...")
                combined_contents += f"File: {file_path}\n" + "\n".join(annotated_lines) + "\n\n"
        except Exception as e:
            logger.error(f"Error processing {file_path}: {str(e)}")

    print(f"Codebase word count: {len(combined_contents.split()):,}")
    token_count = len(tiktoken.get_encoding("cl100k_base").encode(combined_contents))
    print(f"Codebase token count: {token_count:,}")
    
    # Log the first and last part of combined contents
    logger.info(f"Combined contents starts with:\n{combined_contents[:500]}")
    logger.info(f"Combined contents ends with:\n{combined_contents[-500:]}")
    print(f"Max Claude Token limit: 200,000")
    print("--------------------------------------------------------")
    return combined_contents



class AgentInput(BaseModel):
    question: str
    config: dict = Field({})
    chat_history: List[Tuple[str, str]] = Field(..., extra={"widget": {"type": "chat"}})
    conversation_id: str = Field(default="default", description="Unique identifier for the conversation")

def extract_codebase(x):
    files = x["config"].get("files", [])
    conversation_id = x.get("conversation_id", "default")
    logger.debug(f"Extracting codebase for files: {files}")
    logger.info(f"Processing with conversation_id: {conversation_id}")

    file_contents = {}
    for file_path in files:

        try:
            full_path = os.path.join(os.environ["ZIYA_USER_CODEBASE_DIR"], file_path)
            if os.path.isdir(full_path):
                logger.debug(f"Skipping directory: {file_path}")
                continue
            if is_binary_file(full_path):
                logger.debug(f"Skipping binary file: {file_path}")
                continue

            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
                file_contents[file_path] = content
                logger.info(f"Successfully loaded {file_path} with {len(content.splitlines())} lines")
        except (UnicodeDecodeError, IOError) as e:
                logger.error(f"Error reading file {file_path}: {str(e)}")
                continue

    # Initialize or update file states
    if conversation_id not in file_state_manager.conversation_states:
        file_state_manager.initialize_conversation(conversation_id, file_contents)

    # Update any new files that weren't in the initial state
    file_state_manager.update_files_in_state(conversation_id, file_contents)

    # Get changes since last message
    overall_changes, recent_changes = file_state_manager.format_context_message(conversation_id)

    codebase = get_combined_docs_from_files(files, conversation_id)
    logger.info(f"Changes detected - Overall: {bool(overall_changes)}, Recent: {bool(recent_changes)}")

    result = []

    # Add recent changes first if any
    if recent_changes:
        result.append(recent_changes)
        result.append("")

    # Add overall changes if any
    if overall_changes:
        result.extend([
            "SYSTEM: Overall Code Changes",
            "------------------------",
            overall_changes
        ])
        result.append("")

    # Add the codebase content
    result.append(codebase)

    final_string = "\n".join(result)
    file_markers = [line for line in final_string.split('\n') if line.startswith('File: ')]
    logger.info(f"Final string assembly:")
    logger.info(f"Total length: {len(final_string)} chars")
    logger.info(f"Number of File: markers: {len(file_markers)}")
    logger.info(f"First 500 chars:\n{final_string[:500]}")
    logger.info(f"Last 500 chars:\n{final_string[-500:]}")

    # Debug the content at each stage
    logger.info("Content flow tracking:")
    logger.info(f"1. Number of files in file_contents: {len(file_contents)}")
    logger.info(f"2. Number of files in conversation state: {len(file_state_manager.conversation_states.get(conversation_id, {}))}")

    # Check content before joining
    file_headers = [line for line in codebase.split('\n') if line.startswith('File: ')]
    logger.info(f"3. Files in codebase string:\n{chr(10).join(file_headers)}")

    logger.info(f"Final assembled context length: {len(result)} sections, {sum(len(s) for s in result)} total characters")
    file_headers = [line for line in codebase.split('\n') if line.startswith('File: ')]
    logger.info(f"Number of files in codebase: {len(file_headers)}")
    if file_headers:
        logger.info(f"First few files in codebase:\n{chr(10).join(file_headers[:5])}")

    if result:
        return final_string
    return codebase

def log_output(x):
    """Log output in a consistent format."""
    try:
        output = x.content if hasattr(x, 'content') else str(x)
        logger.info(f"Final output size: {len(output)} chars, first 100 chars: {output[:100]}")
    except Exception as e:
        logger.error(f"Error in log_output: {str(e)}")
        output = str(x)
    return x

def log_codebase_wrapper(x):
    codebase = extract_codebase(x)
    logger.info(f"Codebase before prompt: {len(codebase)} chars")
    file_count = len([l for l in codebase.split('\n') if l.startswith('File: ')])
    logger.info(f"Number of files in codebase before prompt: {file_count}")
    logger.info(f"Files in codebase before prompt:\n{chr(10).join([l for l in codebase.split('\n') if l.startswith('File: ')])}")
    return codebase

def create_agent_chain(chat_model: BaseChatModel):
    """Create a new agent chain with the given model."""
    llm_with_stop = model.bind(stop=["</tool_input"])
    
    # Check if AST is enabled
    ast_enabled = os.environ.get("ZIYA_ENABLE_AST") == "true"
    logger.info(f"Creating agent chain with AST enabled: {ast_enabled}")
    
    # Get model information for prompt extensions
    model_info = get_model_info_from_config()
    model_name = model_info["model_name"]
    model_family = model_info["model_family"]
    endpoint = model_info["endpoint"]
    
    logger.info(f"Creating agent chain for model: {model_name}, family: {model_family}, endpoint: {endpoint}")
    
    # Get the extended prompt with model-specific extensions
    prompt_template = get_extended_prompt(
        model_name=model_name,
        model_family=model_family,
        endpoint=endpoint
    )
    
    # Define the input mapping with conditional AST context
    input_mapping = {
        "codebase": log_codebase_wrapper,
        "question": lambda x: x["question"],
        "chat_history": lambda x: _format_chat_history(x.get("chat_history", [])),
        "agent_scratchpad": lambda x: [
            AIMessage(content=format_xml([]))
        ]
    }
    
    # Add AST context enhancement if enabled
    if ast_enabled:
        logger.info("Adding AST context to agent chain input mapping")
        input_mapping["ast_context"] = lambda x: enhance_query_context(x["question"])
        logger.info(f"AST context lambda added to input mapping: {input_mapping.get('ast_context')}")
    else:
        logger.info("AST context not added to agent chain (disabled)")
        # Add empty AST context to avoid template errors
        input_mapping["ast_context"] = lambda x: {}
    
    chain = (
        input_mapping
        | prompt_template  # Use the extended prompt template with model-specific extensions
        | chat_model.bind(stop=["</tool_input>"])
        | (lambda x: AgentFinish(
            return_values={"output": _extract_content(x)},
            log=_extract_content(x)
        ))
    )
    
    # Log information about AST context if enabled
    if ast_enabled:
        logger.info("AST context enhancement enabled for agent chain")
    
    return chain
 
# Initialize the agent chain
agent = create_agent_chain(model)

logger.info("Agent chain defined with parse_output")
def update_conversation_state(conversation_id: str, file_paths: List[str]) -> None:
    """Update file states after a response has been generated"""
    logger.info(f"Updating conversation state for {conversation_id} with {len(file_paths)} files")
    # Read current file contents, skipping directories
    file_contents = {}
    for file_path in file_paths:
        full_path = os.path.join(os.environ["ZIYA_USER_CODEBASE_DIR"], file_path)
        if not os.path.isdir(full_path):
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    file_contents[file_path] = f.read()
                logger.debug(f"Read current content for {file_path}")
            except Exception as e:
                logger.error(f"Error reading file {file_path}: {str(e)}")
                continue

    # Update states and get changes
    changes = file_state_manager.update_files(conversation_id, file_contents)
    logger.info(f"File state update complete. Changes detected: {bool(changes)}")
    if changes:
        logger.info("Changes detected during update:")
        logger.info(json.dumps(changes, indent=2))
        logger.info(f"Files changed during conversation {conversation_id}:")
        for file_path, changed_lines in changes.items():
            logger.info(f"- {file_path}: {len(changed_lines)} lines changed")

def update_and_return(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Update file state and preserve the full response structure"""
    update_conversation_state(input_data.get("conversation_id", "default"),
                            input_data.get("config", {}).get("files", []))
    return input_data

# Finally create the executor
def create_agent_executor(agent_chain: Runnable):
    """Create a new agent executor with the given agent."""
    from langchain_core.runnables import RunnableConfig, Runnable as LCRunnable
    from langchain_core.tracers.log_stream import RunLogPatch
    
    # Create the original executor
    original_executor = AgentExecutor(
        agent=agent_chain,
        tools=[],
        verbose=False,
        handle_parsing_errors=True
    ).with_types(input_type=AgentInput) | RunnablePassthrough.assign(output=update_and_return)
    
    # Create a Runnable wrapper class that adds our safe streaming
    class SafeAgentExecutor(LCRunnable):
        def __init__(self, wrapped_executor):
            self.executor = wrapped_executor
            
        async def astream_log(self, input_data, config=None, **kwargs):
            """Safe wrapper for astream_log that ensures chunks have id attributes."""
            try:
                # Remove any unexpected parameters that might cause errors
                if isinstance(input_data, dict) and "diff" in input_data:
                    logger.warning("Removing unexpected 'diff' parameter from input data")
                    input_data = {k: v for k, v in input_data.items() if k != "diff"}
                
                # Call the original astream and convert to RunLogPatch
                from langchain_core.tracers.log_stream import RunLogPatch
                async for chunk in self.executor.astream(input_data, config, **kwargs):
                    # Process the chunk safely
                    safe_chunk = self._ensure_safe_chunk(chunk)
                    # Convert to RunLogPatch format
                    if isinstance(safe_chunk, (AIMessageChunk, str)):
                        content = safe_chunk.content if hasattr(safe_chunk, 'content') else str(safe_chunk)
                        log_patch = RunLogPatch(
                            ops=[{
                                'op': 'add',
                                'path': '/streamed_output',
                                'value': content
                            }]
                        )
                        yield log_patch
                    else:
                        # If it's already a RunLogPatch, yield it directly
                        yield safe_chunk
            except Exception as e:
                logger.error(f"Error in safe_astream_log: {str(e)}")
                # Create an error chunk
                error_content = f"Error in streaming: {str(e)}"
                error_chunk = AIMessageChunk(content=error_content)
                object.__setattr__(error_chunk, 'id', f"error-{hash(error_content) % 10000}")
                object.__setattr__(error_chunk, 'message', error_content)
                yield error_chunk
        
        def _ensure_safe_chunk(self, chunk):
            """Ensure the chunk has all required attributes."""
            try:
                # Special handling for RunLogPatch objects
                if isinstance(chunk, RunLogPatch):
                    logger.info(f"Processing RunLogPatch: {type(chunk)}")
                    # Add id attribute if it doesn't exist
                    if not hasattr(chunk, 'id'):
                        # Generate a unique ID based on the object's hash
                        chunk_id = f"log-{hash(str(chunk)) % 10000}"
                        try:
                            object.__setattr__(chunk, 'id', chunk_id)
                            logger.info(f"Added id to RunLogPatch: {chunk_id}")
                        except Exception as e:
                            logger.warning(f"Could not add id to RunLogPatch: {str(e)}")
                            # Create a new object with the same data but with an id
                            if hasattr(chunk, 'data'):
                                new_chunk = AIMessageChunk(content=str(chunk.data))
                                object.__setattr__(new_chunk, 'id', chunk_id)
                                object.__setattr__(new_chunk, 'message', str(chunk.data))
                                logger.info(f"Created new chunk with id: {new_chunk.id}")
                                return new_chunk
                    return chunk
                # If chunk is a string, wrap it in an AIMessageChunk
                elif isinstance(chunk, str):
                    logger.info(f"Converting string chunk to AIMessageChunk: {chunk[:50]}...")
                    # Use ZiyaString to preserve attributes
                    from app.agents.custom_message import ZiyaString
                    ziya_str = ZiyaString(chunk, id=f"str-{hash(chunk) % 10000}", message=chunk)
                    message_chunk = AIMessageChunk(content=ziya_str)
                    object.__setattr__(message_chunk, 'id', ziya_str.id)
                    object.__setattr__(message_chunk, 'message', ziya_str.message)
                    
                    # Add to_generation method for compatibility
                    def to_generation():
                        from langchain_core.outputs import Generation
                        gen = Generation(text=chunk, generation_info={})
                        object.__setattr__(gen, 'id', message_chunk.id)
                        object.__setattr__(gen, 'message', chunk)
                        return gen
                    object.__setattr__(message_chunk, 'to_generation', to_generation)
                    
                    logger.info(f"Created AIMessageChunk with id: {message_chunk.id}")
                    return message_chunk
                # If chunk is None, create an empty chunk
                elif chunk is None:
                    logger.info("Received None chunk, creating empty AIMessageChunk")
                    empty_chunk = AIMessageChunk(content="")
                    object.__setattr__(empty_chunk, 'id', "empty-chunk")
                    object.__setattr__(empty_chunk, 'message', "")
                    return empty_chunk
                # If chunk doesn't have an id attribute, add one
                elif not hasattr(chunk, 'id'):
                    logger.info(f"Adding id to chunk of type: {type(chunk)}")
                    try:
                        content = chunk.content if hasattr(chunk, 'content') else str(chunk)
                        if callable(content):
                            content = content()
                        object.__setattr__(chunk, 'id', f"exec-{hash(str(content)) % 10000}")
                        object.__setattr__(chunk, 'message', content)
                        return chunk
                    except Exception as e:
                        logger.warning(f"Could not add id to chunk: {str(e)}")
                        # Create a new chunk with the content
                        new_content = str(chunk)
                        new_chunk = AIMessageChunk(content=new_content)
                        object.__setattr__(new_chunk, 'id', f"fallback-{hash(new_content) % 10000}")
                        object.__setattr__(new_chunk, 'message', new_content)
                        logger.info(f"Created fallback chunk with id: {new_chunk.id}")
                        return new_chunk
                else:
                    # Chunk already has an id, ensure it has a message attribute
                    if not hasattr(chunk, 'message'):
                        try:
                            content = chunk.content if hasattr(chunk, 'content') else str(chunk)
                            if callable(content):
                                content = content()
                            object.__setattr__(chunk, 'message', content)
                        except Exception as e:
                            logger.warning(f"Could not add message to chunk: {str(e)}")
                    return chunk
            except Exception as e:
                logger.error(f"Error in _ensure_safe_chunk: {str(e)}")
                # Create a fallback chunk
                fallback_content = f"Error processing chunk: {str(e)}"
                fallback_chunk = AIMessageChunk(content=fallback_content)
                object.__setattr__(fallback_chunk, 'id', f"error-{hash(fallback_content) % 10000}")
                object.__setattr__(fallback_chunk, 'message', fallback_content)
                return fallback_chunk
        
        # Forward all required Runnable methods to the wrapped executor
        async def ainvoke(self, input, config=None, **kwargs):
            return await self.executor.ainvoke(input, config, **kwargs)
            
        def invoke(self, input, config=None, **kwargs):
            return self.executor.invoke(input, config, **kwargs)
            
        async def astream(self, input, config=None, **kwargs):
            async for chunk in self.executor.astream(input, config, **kwargs):
                yield chunk
                
        def stream(self, input, config=None, **kwargs):
            for chunk in self.executor.stream(input, config, **kwargs):
                yield chunk
                
        # Forward any other methods or attributes
        def __getattr__(self, name):
            return getattr(self.executor, name)
    
    # Return a new instance of our safe executor
    return SafeAgentExecutor(original_executor)

agent_executor = create_agent_executor(agent)

def initialize_langserve(app, executor):
    """Initialize or reinitialize langserve routes with the given executor."""

    # Create a new FastAPI app instance to ensure clean state
    new_app = FastAPI(
        title=app.title,
        description=app.description,
        version=app.version,
        docs_url=app.docs_url,
        redoc_url=app.redoc_url,
        openapi_url=app.openapi_url
    )  

    # Store original routes that aren't /ziya routes
    original_routes = [
        route for route in app.routes 
        if not route.path.startswith("/ziya")
    ]

    logger.info(f"Preserved {len(original_routes)} non-/ziya routes")
 
     # Add required middleware
    new_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
 
    # Add request size middleware
    new_app.add_middleware(
        RequestSizeMiddleware,
        # Add the same max request size as in the original app
        max_request_size=10 * 1024 * 1024  # 10MB
    )

    # Restore original routes
    for route in original_routes:
        new_app.routes.append(route)
 
    # Add new routes with executor
    add_routes(
        new_app,
        executor,
        disabled_endpoints=["playground"],
        path="/ziya"
    )
    
    logger.info("Added new routes with updated executor")

    # Clear all routes from original app
    while app.routes:
        app.routes.pop()
 
    logger.info("Cleared existing routes")
    
    # Copy routes from new app to original app
    for route in new_app.routes:
        app.routes.append(route)
        
    logger.info(f"Successfully reinitialized app with {len(new_app.routes)} routes")
    return True
