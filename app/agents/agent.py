import os
import os.path
import re
import sys
from typing import Dict, List, Tuple, Set, Union, Optional, Any, cast
import json
import time
import botocore
import asyncio

# Lazy import tiktoken - it's optional with fallbacks
try:
    from app.utils.tiktoken_compat import tiktoken
except ImportError:
    tiktoken = None  # Will be handled by tiktoken_compat's fallback mechanism

# Import custom exceptions first to ensure they're available for error handling
from app.utils.custom_exceptions import KnownCredentialException, ThrottlingException, ExpiredTokenException

# Import logger early so it's available for exception handling
from app.utils.logging_utils import logger

# Wrap imports in try/except to catch credential errors early
try:
    from langchain_classic.agents import AgentExecutor
    from langchain_classic.agents.format_scratchpad import format_xml
    from langchain_aws import ChatBedrock
    from google.api_core.exceptions import ResourceExhausted
    from langchain_community.document_loaders import TextLoader
    
    # Import Google AI error for proper handling - optional for compatibility
    try:
        from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
    except (ImportError, AttributeError) as e:
        logger.warning(f"Could not import ChatGoogleGenerativeAIError: {e}")
        ChatGoogleGenerativeAIError = None
    from langchain_core.agents import AgentFinish, AgentAction
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, BaseMessage
    from langchain_core.outputs import Generation
    from langchain_core.output_parsers import BaseOutputParser
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
from app.utils.context_enhancer import enhance_context_with_ast, get_ast_indexing_status
from app.utils.print_tree_util import print_file_tree
from app.utils.file_utils import is_binary_file, is_processable_file
from app.utils.file_utils import read_file_content
from app.utils.prompt_cache import get_prompt_cache
from app.utils.file_state_manager import FileStateManager
from app.utils.error_handlers import format_error_response, detect_error_type
from app.utils.custom_exceptions import KnownCredentialException, ThrottlingException, ExpiredTokenException

from app.mcp.manager import get_mcp_manager
from app.config.models_config import TOOL_SENTINEL_CLOSE
from app.mcp.tools import create_mcp_tools, parse_tool_call
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

prompt_cache = get_prompt_cache()

def clean_chat_history(chat_history: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Clean chat history by removing invalid messages and normalizing content."""
    if not chat_history or not isinstance(chat_history, list):
        return []
    try:
        cleaned = []
        for human, ai in chat_history:
            # Handle case where the tuple is actually (role, content) instead of (human_content, ai_content)
            if human == "human" or human == "user":
                # This is a role indicator, skip this malformed entry
                logger.warning(f"Skipping malformed chat history entry: role='{human}', content='{ai}'")
                continue
            elif human == "ai" or human == "assistant":
                # This is a role indicator, skip this malformed entry  
                logger.warning(f"Skipping malformed chat history entry: role='{human}', content='{ai}'")
                continue
            
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
    # chat_history is already cleaned by the stream endpoint, don't clean again
    buffer = []
    logger.debug("Message format before conversion:")
    try:
        # Handle the case where chat_history is a list of dicts with 'type' and 'content'
        for item in chat_history:
            if isinstance(item, dict) and 'type' in item and 'content' in item:
                msg_type = item['type']
                content = item['content']
                logger.debug(f"Processing message: type={msg_type}, content={content[:100]}...")
                try:
                    if msg_type in ['human', 'user']:
                        buffer.append(HumanMessage(content=str(content)))
                    elif msg_type in ['ai', 'assistant']:
                        buffer.append(AIMessage(content=str(content)))
                except Exception as e:
                    logger.error(f"Error creating message: {str(e)}")
            elif isinstance(item, (list, tuple)):
                # Handle tuple formats
                if len(item) == 2:
                    # Handle legacy tuple format (human_content, ai_content)
                    human_content, ai_content = item
                    if human_content and isinstance(human_content, str):
                        buffer.append(HumanMessage(content=str(human_content)))
                    if ai_content and isinstance(ai_content, str):
                        buffer.append(AIMessage(content=str(ai_content)))
                elif len(item) == 3:
                    # Handle 3-element tuple with images: [role, content, images_json]
                    role, content, images_json = item
                    
                    try:
                        # Parse the images JSON
                        images = json.loads(images_json) if isinstance(images_json, str) else images_json
                        
                        if not images or not isinstance(images, list):
                            # No images, treat as regular message
                            if role == 'human':
                                buffer.append(HumanMessage(content=str(content)))
                            elif role == 'ai':
                                buffer.append(AIMessage(content=str(content)))
                            continue
                        
                        # Build multi-modal content for messages with images
                        message_content = [
                            {"type": "text", "text": str(content)}
                        ]
                        
                        # Add each image to the content array
                        for img in images:
                            if isinstance(img, dict) and 'data' in img and 'mediaType' in img:
                                message_content.append({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": img['mediaType'],
                                        "data": img['data']
                                    }
                                })
                        
                        # Create message with multi-modal content
                        if role == 'human':
                            buffer.append(HumanMessage(content=message_content))
                        elif role == 'ai':
                            buffer.append(AIMessage(content=message_content))
                            
                    except Exception as e:
                        logger.error(f"Error processing message with images: {str(e)}")
                        # Fallback: create message without images
                        if role == 'human':
                            buffer.append(HumanMessage(content=str(content)))
                        elif role == 'ai':
                            buffer.append(AIMessage(content=str(content)))
                else:
                    logger.warning(f"Unknown tuple length: {len(item)} - {item}")
            else:
                logger.warning(f"Unknown chat history format: {type(item)} - {item}")
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
            
        # Handle Nova's array of text chunks
        if all(isinstance(chunk, dict) and 'text' in chunk for chunk in x):
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

class MCPToolOutputParser(BaseOutputParser):
    """
    Parses LLM output for MCP tool calls or final answers.
    Handles both <name>/<arguments> and <invoke>/<parameter> formats.
    """
    def parse(self, llm_output: str) -> Union[AgentAction, AgentFinish]:
        text_to_log = llm_output  # Log the raw output

        logger.debug(f"🔍 MCPToolOutputParser.parse() called with output length: {len(llm_output)}")
        logger.debug(f"🔍 MCPToolOutputParser raw output preview: {llm_output[:500]}...")
        logger.info(f"MCPToolOutputParser parsing output: {llm_output[:200]}...")
        
        # Use the dual-format parser from app.mcp.tools
        parsed_call = parse_tool_call(llm_output)

        if parsed_call:
            tool_name = parsed_call["tool_name"] # This should be the name registered with AgentExecutor (e.g., "mcp_run_shell_command")
            logger.debug(f"🔍 MCPToolOutputParser detected tool call: {tool_name}")
            tool_input_dict = parsed_call["arguments"]
            
            # Log the actual tool call for debugging
            logger.info(f"MCPToolOutputParser: Executing tool call: {tool_name} with args: {tool_input_dict}")
            
            # Ensure the tool name has the mcp_ prefix for consistency
            if not tool_name.startswith("mcp_"):
                tool_name = f"mcp_{tool_name}"
            
            tool_input_dict = parsed_call["arguments"]
            
            logger.info(f"MCPToolOutputParser: Detected tool call: {tool_name} with args: {tool_input_dict}")
            return AgentAction(tool=tool_name, tool_input=tool_input_dict, log=text_to_log)
        else:
            logger.debug(f"🔍 MCPToolOutputParser: No tool call detected, treating as final answer")
            # If no tool call is found, assume it's a final answer.
            # The llm_output here is the raw string from the model, which might be complex.
            # It needs to be processed by _extract_content.
            logger.info(f"MCPToolOutputParser: No tool call detected in: {llm_output[:100]}...")
            logger.info("MCPToolOutputParser: No tool call detected, treating as final answer.")
            final_answer = _extract_content(llm_output) # _extract_content is already defined in agent.py
            return AgentFinish(return_values={"output": final_answer}, log=text_to_log)

    @property
    def _type(self) -> str:
        return "mcp_tool_output_parser"

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
        # Get model configuration
        endpoint = os.environ.get("ZIYA_ENDPOINT", ModelManager.DEFAULT_ENDPOINT)
        model_name = os.environ.get("ZIYA_MODEL")
        model_config = ModelManager.get_model_config(endpoint, model_name)
        model_id = model_config.get("model_id", model_name)
        
        # Filter kwargs based on supported parameters
        supported_kwargs = ModelManager.filter_model_kwargs(kwargs, model_config)
        
        # Handle dictionary model_id (region-specific configuration)
        if isinstance(model_id, dict):
            # Get the appropriate region
            region = os.environ.get("AWS_REGION", "us-west-2")
            region_prefix = "eu" if region.startswith("eu-") else "us"
            # Use the region-specific model ID if available, otherwise fall back to first available
            model_id = model_id.get(region_prefix, next(iter(model_id.values())))
            logger.info(f"Using region-specific model ID: {model_id}")
        

        # Special handling for stop parameter
        if "stop" in kwargs:
            # For Claude models, we need to be careful with certain parameters
            if model_id and "claude" in model_id.lower():
                # Claude doesn't support stop parameter in this context
                if "stop" in supported_kwargs:
                    del supported_kwargs["stop"]
            else:
                # For other models, keep the stop parameter
                supported_kwargs["stop"] = kwargs["stop"]
        
        # Only log this once per process
        if not hasattr(self.__class__, '_binding_logged'):
            logger.info(f"Binding with filtered kwargs: {supported_kwargs}")
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
                    "status_code": status_code,
                    "stream_id": stream_id
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
                    "status_code": status_code,
                    "stream_id": stream_id
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
        from app.config.models_config import MODEL_CONFIGS, MODEL_FAMILIES
        
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
        logger.error(f"🔍 AGENT_ASTREAM: Starting astream with input type: {type(input)}")
        # Reset MCP tool execution counter for new request cycle
        try:
            from app.mcp.tools import _reset_counter_async
            await _reset_counter_async()
        except Exception as e:
            logger.warning(f"Failed to reset MCP tool counter: {e}")
        
        max_retries = 4  # Allow 4 retries for throttling
        throttling_base_delay = 5  # Start with 5 seconds for throttling

        # Get max_tokens from environment variables
        max_tokens = int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 0)) or int(os.environ.get("ZIYA_MAX_TOKENS", 0)) or None
        
        # Create a copy of kwargs to avoid modifying the original
        filtered_kwargs = {}

        # If max_tokens is specified in kwargs, use that instead
        if "max_tokens" in kwargs:
            max_tokens = kwargs["max_tokens"]
        elif max_tokens:
            # Add max_tokens to kwargs if it's not already there
            filtered_kwargs["max_tokens"] = max_tokens
            logger.info(f"Added max_tokens={max_tokens} to astream kwargs from environment")
        from app.agents.models import ModelManager
        endpoint = os.environ.get("ZIYA_ENDPOINT", ModelManager.DEFAULT_ENDPOINT)
        model_name = os.environ.get("ZIYA_MODEL", ModelManager.DEFAULT_MODELS.get(endpoint))
        model_config = ModelManager.get_model_config(endpoint, model_name)

        for key, value in kwargs.items():
            if key != "max_tokens":  # We've already handled max_tokens
                filtered_kwargs[key] = value
        # Filter kwargs based on supported parameters
        filtered_kwargs = ModelManager.filter_model_kwargs(filtered_kwargs, model_config)
        logger.info(f"Filtering model kwargs: {filtered_kwargs}")
        
        # Extract conversation_id from config for caching
        conversation_id = None
        if config and isinstance(config, dict):
            conversation_id = config.get('conversation_id')
            if conversation_id:
                filtered_kwargs["conversation_id"] = conversation_id
                logger.info(f"Added conversation_id to astream kwargs for caching: {conversation_id}")
        
        # Use filtered kwargs for the rest of the method
        kwargs = filtered_kwargs
        # Extract conversation_id from config for caching, but don't pass it to the model
        conversation_id = None
        if config and isinstance(config, dict):
            conversation_id = config.get('conversation_id')
            if conversation_id:
                logger.info(f"Added conversation_id to astream kwargs for caching: {conversation_id}")
        
        # Remove conversation_id from kwargs if it exists (it's not a valid model parameter)
        # But store it for CustomBedrockClient to access via module global
        logger.debug(f"🔍 CONVERSATION_ID: filtered_kwargs keys = {list(filtered_kwargs.keys())}")
        if "conversation_id" in filtered_kwargs:
            conversation_id = filtered_kwargs["conversation_id"]
            # Store in a global variable that CustomBedrockClient can access
            import app.utils.custom_bedrock as custom_bedrock_module
            custom_bedrock_module._current_conversation_id = conversation_id
            logger.info(f"Stored conversation_id in module global: {conversation_id}")
            del filtered_kwargs["conversation_id"]
            logger.info("Removed conversation_id from model kwargs (not a valid model parameter)")
        else:
            logger.debug("🔍 CONVERSATION_ID: No conversation_id found in filtered_kwargs")
        
        # Use filtered kwargs for the model call
        kwargs = filtered_kwargs
        
        accumulated_content = []
        accumulated_text = ""  # String accumulation for preservation
        
        # Limits to prevent context bloat
        MAX_PRESERVED_TOOL_RESULTS = 10
        successful_tool_results = []  # Track successful tool executions
        tool_execution_count = 0
        pre_streaming_work = []  # Track work done before streaming starts
        processing_context = {}  # Track processing context for preservation
        # Add AWS credential debugging
        from app.utils.aws_utils import debug_aws_credentials
        # debug_aws_credentials()
        
        # Ensure each stream has its own conversation tracking
        #stream_id = f"stream_{id(self)}_{hash(str(messages))}"
        # Extract conversation_id from config for proper error routing
        conversation_id = config.get('conversation_id') if config else None
        stream_id = conversation_id or f"stream_{id(self)}_{int(time.time())}"
        logger.info(f"Using stream_id for error routing: {stream_id}")

        logger.info(f"RETRYING_CHAT_BEDROCK.astream: Input type: {type(input)}")
        if hasattr(self.model, 'tools'): # Check if the underlying model has 'tools'
            logger.info(f"RETRYING_CHAT_BEDROCK.astream: Underlying model tools: {[tool.name for tool in self.model.tools] if self.model.tools else 'No tools attribute or empty'}")
        elif hasattr(self.model, 'agent') and hasattr(self.model.agent, 'tools'): # Check if it's an AgentExecutor like structure
             logger.info(f"RETRYING_CHAT_BEDROCK.astream: Agent tools: {[tool.name for tool in self.model.agent.tools] if self.model.agent.tools else 'No tools'}")
        elif hasattr(self.model, '_lc_kwargs') and 'tools' in self.model._lc_kwargs: # LangChain sometimes stores tools in _lc_kwargs
            logger.info(f"RETRYING_CHAT_BEDROCK.astream: Tools from _lc_kwargs: {[tool.name for tool in self.model._lc_kwargs['tools']] if self.model._lc_kwargs['tools'] else 'No tools in _lc_kwargs'}")
        else:
            logger.info(f"RETRYING_CHAT_BEDROCK.astream: No direct 'tools' attribute found. Model type: {type(self.model)}")
 
        if isinstance(input, list) and all(isinstance(m, BaseMessage) for m in input):
            logger.info("RETRYING_CHAT_BEDROCK.astream: Final messages to LLM:")
            for i, msg in enumerate(input):
                logger.info(f"  Msg {i} ({type(msg).__name__}): {str(msg.content)[:200]}...")
        elif hasattr(input, 'to_messages'): # Handle ChatPromptValue
             final_messages_for_llm = input.to_messages()
             logger.info("RETRYING_CHAT_BEDROCK.astream: Final messages to LLM (from ChatPromptValue):")
             for i, msg in enumerate(final_messages_for_llm):
                logger.info(f"  Msg {i} ({type(msg).__name__}): {str(msg.content)[:200]}...")
        else:
            logger.info(f"RETRYING_CHAT_BEDROCK.astream: Input to LLM is not a list of BaseMessages or ChatPromptValue. Type: {type(input)}")

        # Get max_tokens from environment variables
        max_tokens = int(os.environ.get("ZIYA_MAX_OUTPUT_TOKENS", 0)) or int(os.environ.get("ZIYA_MAX_TOKENS", 0)) or None
        
        # Filter kwargs based on model's supported parameters
        from app.agents.models import ModelManager
        endpoint = os.environ.get("ZIYA_ENDPOINT", ModelManager.DEFAULT_ENDPOINT)
        model_name = os.environ.get("ZIYA_MODEL", ModelManager.DEFAULT_MODELS.get(endpoint))
        model_config = ModelManager.get_model_config(endpoint, model_name)
        
        # Create a copy of kwargs to avoid modifying the original
        filtered_kwargs = {}

        # If max_tokens is specified in kwargs, use that instead
        if "max_tokens" in kwargs:
            max_tokens = kwargs["max_tokens"]
        elif max_tokens:
            # Add max_tokens to kwargs if it's not already there
            filtered_kwargs["max_tokens"] = max_tokens
            logger.info(f"Added max_tokens={max_tokens} to astream kwargs from environment")

        # Add other kwargs
        for key, value in kwargs.items():
            if key != "max_tokens":  # We've already handled max_tokens
                filtered_kwargs[key] = value
                
        # Filter kwargs based on supported parameters
        filtered_kwargs = ModelManager.filter_model_kwargs(filtered_kwargs, model_config)
        logger.info(f"Filtering model kwargs: {filtered_kwargs}")
        
        # Use filtered kwargs for the rest of the method
        kwargs = filtered_kwargs
        
        # Add AWS credential debugging
        from app.utils.aws_utils import debug_aws_credentials
        # debug_aws_credentials()

        for attempt in range(max_retries):
            logger.info(f"Attempt {attempt + 1} of {max_retries}")
            try:
                # Track pre-streaming work
                pre_streaming_work.append(f"🔄 Starting attempt {attempt + 1}/{max_retries}")
                
                # Try to capture cache information from recent logs
                try:
                    # Look for cache benefit information in the conversation context
                    if conversation_id and hasattr(input, 'to_messages'):
                        messages = input.to_messages()
                        # Check if any message contains cache information
                        for msg in messages:
                            if hasattr(msg, 'content') and 'CACHE BENEFIT' in str(msg.content):
                                import re
                                cache_match = re.search(r'CACHE BENEFIT: ~([\d,]+) tokens', str(msg.content))
                                if cache_match:
                                    tokens = cache_match.group(1)
                                    pre_streaming_work.append(f"💾 Cache benefit: ~{tokens} tokens will be reused")
                                    processing_context["cache_benefit"] = f"~{tokens} tokens cached"
                except Exception as e:
                    logger.debug(f"Could not extract cache info: {e}")
                
                # Convert input to messages if needed
                if hasattr(input, 'to_messages'):
                    messages = input.to_messages()
                    pre_streaming_work.append(f"📝 Prepared {len(messages)} messages for model")
                    logger.debug(f"Using messages from ChatPromptValue: {len(messages)} messages")
                else:
                    messages = input
                    logger.debug(f"Using input directly: {type(input)}")

                # Filter out empty messages
                if isinstance(messages, list):
                    logger.debug(f"🔍 FILTERING: Before filtering - {len(messages)} messages")
                    for i, msg in enumerate(messages):
                        # Handle both BaseMessage objects and dictionaries safely
                        try:
                            if hasattr(msg, 'content'):
                                content = str(msg.content)[:100]
                                empty = not msg.content
                            elif isinstance(msg, dict) and 'content' in msg:
                                content = str(msg['content'])[:100]
                                empty = not msg['content']
                            else:
                                content = str(msg)[:100]
                                empty = True
                            logger.debug(f"🔍 FILTERING: Message {i}: {type(msg).__name__} - content: '{content}...' - empty: {empty}")
                        except Exception as e:
                            logger.warning(f"🔍 FILTERING: Error accessing message {i} content: {e} - type: {type(msg)}")
                    
                    # Filter messages - handle both BaseMessage objects and dictionaries
                    filtered_messages = []
                    for msg in messages:
                        if isinstance(msg, BaseMessage) and msg.content:
                            filtered_messages.append(msg)
                        elif isinstance(msg, dict) and msg.get('content'):
                            filtered_messages.append(msg)
                    
                    messages = filtered_messages
                    
                    logger.debug(f"🔍 FILTERING: After filtering - {len(messages)} messages")
                    for i, msg in enumerate(messages):
                        try:
                            if hasattr(msg, 'content'):
                                content = str(msg.content)[:100]
                            elif isinstance(msg, dict) and 'content' in msg:
                                content = str(msg['content'])[:100]
                            else:
                                content = str(msg)[:100]
                            logger.debug(f"🔍 FILTERING: Kept Message {i}: {type(msg).__name__} - content: '{content}...'")
                        except Exception as e:
                            logger.warning(f"🔍 FILTERING: Error accessing kept message {i} content: {e}")
                    
                    if not messages:
                        raise ValueError("No valid messages with content")
                    logger.debug(f"Filtered to {len(messages)} non-empty messages")
                    pre_streaming_work.append(f"✅ Validated {len(messages)} messages ready for processing")

                logger.info("LLM_INPUT_DEBUG: Preparing to call LLM.") # ADD THIS BLOCK
                for i, msg in enumerate(messages): # ADD THIS BLOCK
                    if hasattr(msg, 'content'): # ADD THIS BLOCK
                        logger.info(f"LLM_INPUT_DEBUG: Message {i} ({type(msg)}): Content length {len(msg.content)}") # ADD THIS BLOCK
                        if isinstance(msg, SystemMessage): # ADD THIS BLOCK
                            logger.info(f"LLM_INPUT_DEBUG: System Message Content: ...{msg.content[-1000:]}") # ADD THIS BLOCK
                    else: # ADD THIS BLOCK
                        logger.info(f"LLM_INPUT_DEBUG: Message {i} ({type(msg)}) has no content attribute.") # ADD THIS BLOCK



                
                pre_streaming_work.append("🚀 Initiating model stream connection")
                
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
                    logger.debug(f"Filtered to {len(messages)} non-empty messages")

                # Debug the Bedrock client being used
                # Pass conversation_id through config to the model for caching
                model_config = config.copy() if config else {}
                if conversation_id:
                    model_config["conversation_id"] = conversation_id
                
                # Merge model_config into kwargs for compatibility with all model types
                merged_kwargs = {**kwargs, **model_config}
                    
                async for chunk in self.model.astream(messages, **merged_kwargs):
                    logger.debug(f"🔍 AGENT_MODEL_ASTREAM: Received chunk type: {type(chunk)}, content: {getattr(chunk, 'content', str(chunk))[:100]}")
                    # Check if this is an error chunk that should terminate this specific stream
                    # If we reach here, we've successfully started streaming
                    
                    if not processing_context.get("streaming_started"):
                        processing_context["streaming_started"] = True
                    if isinstance(chunk, AIMessageChunk):
                        # Check if this chunk contains a tool result
                        content = chunk.content() if callable(chunk.content) else chunk.content
                        if content and isinstance(content, str):
                            # Look for tool execution patterns
                            if self._is_tool_execution_content(content):
                                tool_execution_count += 1
                                if not any(error_indicator in content.lower() for error_indicator in ["error", "timeout", "failed"]):
                                    # Limit size of individual tool results
                                    tool_result = content
                                    if len(tool_result) > 5000:
                                        tool_result = tool_result[:5000] + f"\n... [Tool result truncated - {len(content)} total chars]"
                                    successful_tool_results.append(tool_result)
                                    
                                    # Limit total number of preserved results
                                    if len(successful_tool_results) > MAX_PRESERVED_TOOL_RESULTS:
                                        successful_tool_results = successful_tool_results[-MAX_PRESERVED_TOOL_RESULTS:]
                                
                                # Notify frontend about tool execution (but don't await to avoid blocking)
                                try:
                                    asyncio.create_task(self._notify_tool_execution_state(content))
                                except Exception as e:
                                    logger.debug(f"Could not notify tool execution state: {e}")
                        
                        content = chunk.content() if callable(chunk.content) else chunk.content
                        if content:
                            accumulated_content.append(content)
                            if isinstance(content, str):
                                accumulated_text += content
                            logger.debug(f"Accumulated content size: {len(''.join(accumulated_content))} chars")

                    if ChatGoogleGenerativeAIError and isinstance(chunk, ChatGoogleGenerativeAIError):
                        error_response = {
                            "error": "server_error",
                            "detail": str(chunk),
                            "status_code": 500
                        }
                        # Create a special error chunk that the streaming middleware can detect
                        error_chunk = AIMessageChunk(content=json.dumps(error_response))
                        error_chunk.response_metadata = {"error_response": True}
                        yield error_chunk
                        return

                    elif isinstance(chunk, AIMessageChunk):
                        raw_chunk_content_repr = repr(chunk.content)[:200]
                        content = chunk.content() if callable(chunk.content) else chunk.content

                        # Check if content is in Nova format (list of dicts with text field)
                        if isinstance(content, list) and content and isinstance(content[0], dict) and 'text' in content[0]:
                            # Use NovaFormatter to extract text from Nova format
                            from app.agents.wrappers.nova_formatter import NovaFormatter
                            content = NovaFormatter.parse_response({"output": {"message": {"content": content}}})
                            logger.info(f"Used NovaFormatter to extract text from Nova format: {content[:50] if content else 'empty'}")
                        
                        full_content_str = str(content) # Ensure it's a string
                        extracted_content_repr = repr(content)[:200]
                        yield AIMessageChunk(content=content)
                    else:
                        yield chunk

                break  # Success, exit retry loop
                
                # Re-raise the exception for the middleware to handle
                # Create error response while preserving any existing content
                error_response = {
                    "error": "throttling_error",
                    "detail": "Too many requests to AWS Bedrock. Please wait a moment before trying again.",
                    "status_code": 429,
                    "retry_after": "5",
                    "conversation_id": conversation_id,  # Add conversation_id for frontend routing
                    "stream_id": stream_id,
                    "preserved_content": ''.join(accumulated_content) if accumulated_content else None,
                    "preserved_text": accumulated_text if accumulated_text else None,
                    "successful_tool_results": successful_tool_results if successful_tool_results else None,
                    "tool_execution_summary": {
                        "total_attempts": tool_execution_count,
                        "successful_executions": len(successful_tool_results),
                        "has_partial_success": len(successful_tool_results) > 0
                    }
                }
                
                error_json = json.dumps(error_response)
                logger.info(f"[ERROR_SSE] Preparing throttling error with preserved content: {error_json}")
                
                # Create a special error chunk that includes preserved content
                error_chunk = AIMessageChunk(content=error_json)
                error_chunk.response_metadata = {
                    "error_response": True,
                    "has_preserved_content": True
                }
                yield error_chunk
                
                # Send DONE marker
                done_message = "data: [DONE]\n\n"
                logger.info(f"[ERROR_SSE] Sending DONE marker after throttle error: {done_message}")
                yield AIMessageChunk(content=done_message)
                return
            except Exception as e:
                # Check if this is a Gemini error that should be handled specially
                if ChatGoogleGenerativeAIError and isinstance(e, ChatGoogleGenerativeAIError):
                    # Handle as Gemini-specific error
                    pass
                else:
                    # Check if it's a generic Gemini error even without the import
                    if "google" in str(type(e).__module__).lower() and "generative" in str(e).lower():
                        pass  # Treat as Gemini error
                
                # Format Gemini errors as structured error payload within a chunk
                logger.error(f"ChatGoogleGenerativeAI error: {str(e)}")
                
                # Check for specific error types
                error_message = str(e)
                if "exceeds the maximum number of tokens" in error_message:
                    error_response = {
                        "error": "context_size_error",
                        "detail": "The selected content is too large for this model. Please reduce the number of files or use a model with a larger context window.",
                        "status_code": 413
                    }
                else:
                    error_response = {
                    "error": "server_error",
                    "detail": str(e),
                    "status_code": 500
                    }
                
                # Create a special error chunk that the streaming middleware can detect
                error_chunk = AIMessageChunk(content=json.dumps(error_response))
                error_chunk.response_metadata = {"error_response": True}
                # Log the error message we're about to send
                logger.info(f"Sending Gemini error as structured chunk: {error_response}")
                
                # Yield the error payload as content in an AIMessageChunk
                yield AIMessageChunk(content=json.dumps(error_response))
                return

            except ClientError as e:
                error_str = str(e)
                logger.warning(f"Bedrock client error: {error_str}")
                
                # Check for token-based throttling (different from rate throttling)
                is_token_throttling = ("Too many tokens" in error_str and 
                                     "ThrottlingException" in error_str)
                
                # Handle throttling with proper backoff
                if "ThrottlingException" in error_str or "Too many requests" in error_str:
                    if attempt < max_retries - 1:
                        # Throttling-specific backoff: 5s, 10s, 20s, 40s
                        if attempt == 0:
                            retry_delay = 5
                        elif attempt == 1:
                            retry_delay = 10
                        elif attempt == 2:
                            retry_delay = 20
                        else:
                            retry_delay = 40
                        
                        logger.info(f"Throttling detected, retrying in {retry_delay} seconds (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(retry_delay)
                        continue
                
                # Handle token throttling with fresh connection retry
                if is_token_throttling and attempt < max_retries - 1:
                    logger.info(f"Token throttling detected, creating fresh connection after 20s wait (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(20)  # Wait 20 seconds as you suggested
                    
                    # Force a fresh connection by reinitializing the model
                    try:
                        from app.agents.models import ModelManager
                        fresh_model = ModelManager.initialize_model(force_reinit=True)
                        if fresh_model:
                            self.model = fresh_model
                            logger.info("Successfully created fresh model connection for token throttling retry")
                            continue
                    except Exception as reinit_error:
                        logger.warning(f"Failed to reinitialize model for token throttling retry: {reinit_error}")
                        # Continue with original model if reinit fails
                        continue
                
                # Check for validation errors first (these are more important than throttling)
                if "ValidationException" in error_str and "Input is too long" in error_str:
                    error_message = {
                        "error": "context_size_error",
                        "detail": "The selected content is too large for this model. Please reduce the number of files or use a model with a larger context window.",
                        "status_code": 413
                    }
                    error_json = json.dumps(error_message)
                    error_chunk = AIMessageChunk(content=error_json)
                    error_chunk.response_metadata = {"error_response": True}
                    yield error_chunk
                    return
                
                # Log any accumulated content before error handling
                if hasattr(self, '_accumulated_content') and self._accumulated_content:
                    logger.info(f"PARTIAL RESPONSE DEBUG: {len(self._accumulated_content)} characters in _accumulated_content before error")
                    print(f"_ACCUMULATED_CONTENT BEFORE ERROR:\n{self._accumulated_content}")
                
                if accumulated_text:
                    logger.info(f"PARTIAL RESPONSE DEBUG: {len(accumulated_text)} characters in accumulated_text before error")
                    print(f"ACCUMULATED_TEXT BEFORE ERROR:\n{accumulated_text}")
                
                
                # Run credential debug again on error
                
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
                # The error_json will be properly formatted by the streaming middleware
                logger.info(f"[ERROR_SSE] Yielding structured error chunk: {error_json}")
                
                # Log the exact message we're about to yield
                logger.info(f"[ERROR_TRACE] About to yield AIMessageChunk with error content")
                # Create a special error chunk that the streaming middleware can detect
                error_chunk = AIMessageChunk(content=error_json)
                error_chunk.response_metadata = {"error_response": True}
                yield error_chunk
                logger.info("[ERROR_TRACE] Yielded error chunk")
                
                return

            except Exception as e:
                error_str = str(e)
                logger.warning(f"Error on attempt {attempt + 1}: {error_str}")
                
                # Check for validation errors first (these are more important than throttling)
                if "ValidationException" in error_str and "Input is too long" in error_str:
                    error_message = {
                        "error": "context_size_error", 
                        "detail": "The selected content is too large for this model. Please reduce the number of files or use a model with a larger context window.",
                        "status_code": 413
                    }
                    error_json = json.dumps(error_message)
                    error_chunk = AIMessageChunk(content=error_json)
                    error_chunk.response_metadata = {"error_response": True}
                    yield error_chunk
                    return

                # Log any accumulated content before error handling
                if hasattr(self, '_accumulated_content') and self._accumulated_content:
                    logger.info(f"PARTIAL RESPONSE DEBUG: {len(self._accumulated_content)} characters in _accumulated_content before exception")
                    print(f"_ACCUMULATED_CONTENT BEFORE EXCEPTION:\n{self._accumulated_content}")
                
                if accumulated_text:
                    logger.info(f"PARTIAL RESPONSE DEBUG: {len(accumulated_text)} characters in accumulated_text before exception")
                    print(f"ACCUMULATED_TEXT BEFORE EXCEPTION:\n{accumulated_text}")
                

                # Check if this is a throttling error wrapped in another exception
                logger.error(f"🔍 ACTUAL_ERROR: {error_str}")
                logger.error(f"🔍 ERROR_TYPE: {type(e)}")
                if "ThrottlingException" in error_str or "Too many requests" in error_str:
                    logger.warning("Detected throttling error in exception")
                    # Simple error message for frontend
                    error_message = {
                        "error": "⚠️ AWS rate limit exceeded. Please wait a moment and try again.",
                        "type": "throttling"
                    }
                else:
                    # Show the actual error instead of masking it
                    error_message = {
                        "conversation_id": conversation_id,  # Add conversation_id for frontend routing
                        "error": f"⚠️ Error: {error_str}",
                        "type": "general"
                    }
                    
                    # Include pre-streaming work in preservation
                    if pre_streaming_work:
                        error_message["pre_streaming_work"] = pre_streaming_work
                        error_message["processing_context"] = processing_context
                        error_message["has_pre_streaming_work"] = True
                        logger.info(f"[ERROR_SSE] Including pre-streaming work: {len(pre_streaming_work)} steps")
                        logger.info(f"[ERROR_SSE] Pre-streaming work details: {pre_streaming_work}")
                    else:
                        logger.info("[ERROR_SSE] No pre-streaming work to preserve")
                    
                    error_json = json.dumps(error_message)
                    logger.info(f"[ERROR_TRACE] Yielding structured throttling error response: {error_json}")
                    
                    # Let the streaming middleware handle SSE formatting
                    # Add any accumulated content to the error response
                    if accumulated_content:
                        error_message["preserved_content"] = ''.join(accumulated_content)
                        error_message["preserved_text"] = accumulated_text
                        error_message["successful_tool_results"] = successful_tool_results
                        error_message["has_preserved_content"] = True
                        error_message["tool_execution_summary"] = {
                            "pre_streaming_work": pre_streaming_work,
                            "processing_context": processing_context,
                            "total_attempts": tool_execution_count,
                            "successful_executions": len(successful_tool_results),
                            "has_partial_success": len(successful_tool_results) > 0
                        }
                        logger.info(f"[ERROR_SSE] Including preserved content with {len(successful_tool_results)} successful tool results")
                    else:
                        logger.info("[ERROR_SSE] No content to preserve")
                        # Even if no streaming content, preserve pre-streaming work
                        if pre_streaming_work:
                            error_message["pre_streaming_work"] = pre_streaming_work
                            error_message["processing_context"] = processing_context
                            logger.info(f"[ERROR_SSE] Preserving pre-streaming work: {len(pre_streaming_work)} steps")
                        
                    # Create a special error chunk that the streaming middleware can detect
                    error_chunk = AIMessageChunk(content=error_json)
                    error_chunk.response_metadata = {"error_response": True}
                    yield error_chunk
                    return

                # Check if this is a Bedrock error that was wrapped in another exception
                error_type, detail, status_code, retry_after = detect_error_type(error_str)
                logger.info(f"Detected error type: {error_type}, status: {status_code}")
                # Handle other exceptions
                error_type, detail, status_code, retry_after = detect_error_type(error_str)
            
                # Handle throttling with proper backoff for generic exceptions too
                if "ThrottlingException" in error_str or "Too many requests" in error_str or "Too many tokens" in error_str:
                    if attempt < max_retries - 1:
                        # Throttling-specific backoff: 5s, 10s, 20s, 40s
                        if attempt == 0:
                            retry_delay = 5
                        elif attempt == 1:
                            retry_delay = 10
                        elif attempt == 2:
                            retry_delay = 20
                        else:
                            retry_delay = 40
                        
                        logger.info(f"Throttling detected in exception, retrying in {retry_delay} seconds (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(retry_delay)
                        continue

                # For non-throttling errors, use shorter backoff
                if attempt < max_retries - 1:
                    retry_delay = 1 * (2 ** attempt)  # 1s, 2s, 4s for other errors
                    logger.info(f"Non-throttling error, retrying in {retry_delay} seconds (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(retry_delay)
                    continue
                # Final attempt failed, send error response
                error_message = {
                    "error": error_type,
                    "detail": detail,
                    "conversation_id": conversation_id,  # Add conversation_id for frontend routing
                    "status_code": status_code
                }
                if retry_after:
                    error_message["retry_after"] = retry_after
                
                error_json = json.dumps(error_message)
                logger.info(f"Yielding final error response: {error_json}")
                
                # Yield the error payload as content in an AIMessageChunk
                # Create a special error chunk that the streaming middleware can detect
                error_chunk = AIMessageChunk(content=error_json)
                error_chunk.response_metadata = {"error_response": True}
                yield error_chunk
                return

    def _is_tool_execution_content(self, content: str) -> bool:
        """Check if content indicates tool execution."""
        # Check for tool sentinel markers
        from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
        
        # Check for complete tool calls
        if TOOL_SENTINEL_OPEN in content and TOOL_SENTINEL_CLOSE in content:
            return True
        
        # Check for hardcoded sentinel markers
        if "<TOOL_SENTINEL>" in content and "</TOOL_SENTINEL>" in content:
            return True
            
        # Check for other tool indicators
        tool_indicators = [
            "MCP Tool",
            "Tool:",
            "$ ",  # Shell command indicator
            "```tool:",
            "```shell",
            "```bash",
            "Executing tool",
            "Running command",
        ]
        
        return any(indicator in content for indicator in tool_indicators)

    async def _execute_tool_call(self, content: str) -> str:
        """
        Execute a tool call in the content and return the result.
        
        Args:
            content: The content containing a tool call
            
        Returns:
            The content with the tool call replaced by the result
        """
        try:
            # Import the MCP tool execution function
            from app.mcp.consolidated import execute_mcp_tools_with_status
            
            # Execute the tool call
            logger.info(f"Executing tool call during streaming: {content[:100]}...")
            result = await execute_mcp_tools_with_status(content)
            logger.info(f"Tool execution result: {result[:100]}...")
            
            return result
        except Exception as e:
            logger.error(f"Error executing tool call: {e}")
            # Return error message
            return f"\n\n```tool:error\n❌ **Tool Error:** {str(e)}\n```\n\n"

    async def _notify_tool_execution_state(self, content: str):
        """Notify about tool execution state changes."""
        logger.info(f"🔧 Tool execution detected in content: {content[:100]}...")

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
        max_retries = 4
        throttling_base_delay = 5.0
        
        # Apply post-instructions if input is a user query
        if isinstance(input, str):
            from app.utils.post_instructions import PostInstructionManager
            # Post-instructions are now applied in the centralized message construction
            # This eliminates duplication since they're applied once during template extension

        # Extract conversation_id from config for caching
        conversation_id = None
        if config and isinstance(config, dict):
            conversation_id = config.get("conversation_id")
            if conversation_id:
                kwargs["conversation_id"] = conversation_id
                logger.debug(f"Added conversation_id to invoke kwargs for caching")
        
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
                        logger.error(f"Message {i}: {msg}")
                
                # Handle throttling with proper backoff
                if "ThrottlingException" in error_str or "Too many requests" in error_str or "Too many tokens" in error_str:
                    if attempt < max_retries - 1:
                        # Throttling-specific backoff: 5s, 10s, 20s, 40s
                        if attempt == 0:
                            retry_delay = 5.0
                        elif attempt == 1:
                            retry_delay = 10.0
                        elif attempt == 2:
                            retry_delay = 20.0
                        else:
                            retry_delay = 40.0
                        
                        logger.info(f"Throttling detected, retrying in {retry_delay} seconds (attempt {attempt + 1}/{max_retries})")
                        time.sleep(retry_delay)
                        continue
                    else:
                        # Simple error response for frontend
                        error_message = {
                            "error": "⚠️ AWS rate limit exceeded. Please wait a moment and try again.",
                            "type": "throttling"
                        }
                        # Let this fall through to the final error handling

                if attempt < max_retries - 1:
                    # For non-throttling errors, use shorter backoff
                    retry_delay = 1.0 * (2 ** attempt)  # 1s, 2s, 4s for other errors
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    # Last attempt failed, raise the exception
                    logger.error(f"All {max_retries} attempts failed")
                    raise
 
        return formatted_messages
 

class LazyLoadedModel:
    def __init__(self):
        self._model = None
        self._model_with_stop = None
        self._binding_logged = False

    def get_model(self):
        """Get the underlying model instance"""
        # Always check the ModelManager state first
        from app.agents.models import ModelManager
        if ModelManager._state.get('model') is not None:
            logger.info("Using model instance from ModelManager state")
            # Ensure it's wrapped if necessary (though it should be already)
            if not isinstance(ModelManager._state['model'], RetryingChatBedrock):
                 self._model = RetryingChatBedrock(ModelManager._state['model'])
            else:
                 self._model = ModelManager._state['model']
        elif self._model is None: # Only initialize if both state and self._model are None
            logger.warning("ModelManager state is empty, initializing model on first use")
            model_instance = ModelManager.initialize_model(force_reinit=True) # Initialize without override here
            if model_instance is None:
                logger.error("Model initialization failed - returning None")
                return None
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
        model = self.get_model()
        if model is None:
            logger.error("Cannot bind model - model initialization failed")
            return None
        return model.bind(**kwargs)
 
model = LazyLoadedModel()
llm_with_stop = model.bind(stop=["</tool_input>"])
if llm_with_stop is None:
    logger.warning("Model binding failed during initialization - will retry later")

# Store the initial llm_with_stop in ModelManager
from app.agents.models import ModelManager
ModelManager._state['llm_with_stop'] = llm_with_stop

file_state_manager = FileStateManager()

def escape_backticks_for_llm(text: str) -> str:
    """Escape backticks to prevent LLM confusion when generating diffs."""
    return text.replace('`', '\\`')

def get_combined_docs_from_files(files, conversation_id: str = "default") -> str:
    logger.info("=== get_combined_docs_from_files called ===")
    logger.debug(f"🔍 FILES_DEBUG: Called with {len(files)} files: {files[:5]}..." if len(files) > 5 else f"🔍 FILES_DEBUG: Called with files: {files}")
    logger.info(f"Called with files: {files}")
    print(f"🔍 FILE_CONTENT_DEBUG: get_combined_docs_from_files called with {len(files)} files")
    combined_contents: str = ""
    logger.debug("Processing files:")
    print_file_tree(files if isinstance(files, list) else files.get("config", {}).get("files", []))
    
    # Log the raw files input
    logger.info(f"Raw files input type: {type(files)}")
    logger.info(f"Files to process: {files}")
    
    logger.info(f"Processing files with conversation_id: {conversation_id}")

    # Initialize AST capabilities if enabled
    ast_context = ""
    ast_token_count = 0

    user_codebase_dir: str = os.environ["ZIYA_USER_CODEBASE_DIR"]
    for file_path in files:
        full_path = os.path.join(user_codebase_dir, file_path)
        
        # Check if this is an MCP server file that shouldn't be in the codebase
        if 'mcp_servers' in file_path:
            logger.warning(f"🔍 FILES_DEBUG: MCP server file detected in file list: {file_path}")
        
        # Skip directories
        if os.path.isdir(full_path):
            logger.debug(f"Skipping directory: {full_path}")
            continue
        try:
            from app.utils.file_utils import read_file_content
            # Get annotated content with change tracking
            logger.debug(f"Getting annotated content for {file_path}")
            annotated_lines, success = file_state_manager.get_annotated_content(conversation_id, file_path)
            logger.debug(f"First few annotated lines: {annotated_lines[:3] if annotated_lines else []}")
            logger.debug(f"Got {len(annotated_lines) if annotated_lines else 0} lines for {file_path}, success={success}")
            if success:
                # Log a preview of the content
                preview = "\n".join(annotated_lines[:5]) if annotated_lines else "NO CONTENT"
                logger.debug(f"Content preview for {file_path}:\n{preview}\n...")
                escaped_lines = [escape_backticks_for_llm(line) for line in annotated_lines]
                combined_contents += f"File: {file_path}\n" + "\n".join(escaped_lines) + "\n\n"
            else:
                # Add file directly to FileStateManager when not found
                logger.debug(f"File {file_path} not in FileStateManager, adding it directly")
                content = read_file_content(full_path)
                if content:
                    # Ensure conversation exists
                    if conversation_id not in file_state_manager.conversation_states:
                        file_state_manager.conversation_states[conversation_id] = {}
                    
                    # Add file directly to state
                    from app.utils.file_state_manager import FileState
                    lines = content.splitlines()
                    file_state_manager.conversation_states[conversation_id][file_path] = FileState(
                        path=file_path,
                        content_hash=file_state_manager._compute_hash(lines),
                        line_states={},
                        original_content=lines.copy(),
                        current_content=lines.copy(),
                        last_seen_content=lines.copy(),
                        last_context_submission_content=lines.copy()
                    )
                    
                    # Get annotated content
                    annotated_lines, success = file_state_manager.get_annotated_content(conversation_id, file_path)
                    escaped_lines = [escape_backticks_for_llm(line) for line in annotated_lines]
                    combined_contents += f"File: {file_path}\n" + "\n".join(escaped_lines) + "\n\n"
        except Exception as e:
            logger.error(f"Error processing {file_path}: {str(e)}")
    
    print(f"🔍 FILE_CONTENT_DEBUG: get_combined_docs_from_files returning {len(combined_contents)} chars")

    # Log the first and last part of combined contents
    logger.info(f"Combined contents starts with:\n{combined_contents[:500]}")
    logger.info(f"Combined contents ends with:\n{combined_contents[-500:]}")
    
    return combined_contents

def extract_file_paths_from_input(x) -> List[str]:
    """Extract file paths from agent input for cache tracking."""
    files = x["config"].get("files", [])
    user_codebase_dir = os.environ["ZIYA_USER_CODEBASE_DIR"]
    
    file_paths = []
    for file_path in files:
        full_path = os.path.join(user_codebase_dir, file_path)
        if os.path.exists(full_path) and not os.path.isdir(full_path):
            file_paths.append(full_path)
    
    return file_paths
 
def get_conversation_id_from_input(x) -> str:
    """Extract conversation ID from agent input."""
    return x.get("conversation_id", "default")
 
def estimate_token_count(text: str) -> int:
    """Estimate token count for caching purposes."""
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:
        # Fallback estimation: roughly 4 characters per token
        return len(text) // 4
 
def log_codebase_wrapper_with_cache(x):
    """Enhanced codebase wrapper that integrates with prompt caching."""
    # Extract conversation_id from multiple possible locations
    conversation_id = (
        x.get("conversation_id") or
        x.get("config", {}).get("conversation_id") or
        get_conversation_id_from_input(x)
    )
    file_paths = extract_file_paths_from_input(x)

    logger.info(f"log_codebase_wrapper_with_cache using conversation_id: {conversation_id}")
    
    # Generate the codebase context
    codebase = extract_codebase(x)
    
    # Get AST context if available
    ast_context = ""
    if os.environ.get("ZIYA_ENABLE_AST") == "true":
        from app.utils.context_enhancer import get_ast_context
        if get_ast_context:
            ast_context = get_ast_context() or ""
    
    # Check for cached version
    full_context = codebase + ast_context
    cached_context = prompt_cache.get_cached_prompt(
        conversation_id=conversation_id,
        full_prompt=full_context,
        file_paths=file_paths,
        ast_context=ast_context
    )
    
    if cached_context:
        logger.info(f"Using cached context for conversation {conversation_id}")
        return cached_context
    
    # Cache the new context
    token_count = estimate_token_count(full_context)
    prompt_cache.cache_prompt(
        conversation_id=conversation_id,
        full_prompt=full_context,
        file_paths=file_paths,
        token_count=token_count,
        ast_context=ast_context
    )
    
    logger.info(f"Cached new context for conversation {conversation_id} ({token_count} tokens)")
    return codebase

class AgentInput(BaseModel):
    question: str
    config: dict = Field({})
    chat_history: List[Tuple[str, str]] = Field(..., json_schema_extra={"widget": {"type": "chat"}})
    conversation_id: str = Field(default="default", description="Unique identifier for the conversation")

def extract_codebase(x):
    files = x["config"].get("files", [])
    # Extract conversation_id from multiple possible sources
    
    # If no files are selected, return a placeholder message
    # This ensures the system template is still properly formatted
    if not files:
        logger.info("No files selected, returning placeholder codebase message")
        return "No files have been selected for context analysis."
    
    logger.debug(f"🔍 EXTRACT_CODEBASE_DEBUG: extract_codebase called with {len(files)} files")
    logger.debug(f"🔍 EXTRACT_CODEBASE_DEBUG: First 5 files: {files[:5]}")
    print(f"🔍 EXTRACT_CODEBASE_DEBUG: extract_codebase called with {len(files)} files")
    conversation_id = (
        x.get("conversation_id") or 
        x.get("config", {}).get("conversation_id") or
        "default"
    )

    logger.debug(f"extract_codebase using conversation_id: {conversation_id}")
    logger.debug(f"extract_codebase input keys: {list(x.keys()) if isinstance(x, dict) else type(x)}")

    logger.debug(f"Extracting codebase for files: {files}")
    logger.info(f"Processing with conversation_id: {conversation_id}")

    # Initialize conversation state FIRST, before any file processing
    # This ensures the context cache system can find the conversation state
    file_contents = {}
    for file_path in files:
        try:
            full_path = os.path.join(os.environ["ZIYA_USER_CODEBASE_DIR"], file_path)
            if os.path.isdir(full_path):
                logger.debug(f"Skipping directory: {file_path}")
                continue
            if not is_processable_file(full_path):
                logger.debug(f"Skipping binary file: {file_path}")
                continue
            
            # Use the new read_file_content function
            content = read_file_content(full_path)
            if content:
                file_contents[file_path] = content
                lines = len(content.splitlines()) if isinstance(content, str) else 0
                logger.debug(f"Successfully loaded {file_path} with {lines} lines")
            else:
                logger.warning(f"Failed to read content from {file_path}")
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {str(e)}")
            continue
    
    # CRITICAL: Check if this is a new conversation or an existing one
    is_new_conversation = conversation_id not in file_state_manager.conversation_states
    
    if is_new_conversation:
        # Initialize conversation state for NEW conversations only
        logger.debug(f"🔍 FILE_STATE: Initializing NEW conversation {conversation_id} with {len(file_contents)} files")
        file_state_manager.initialize_conversation(conversation_id, file_contents, force_reset=True)
        logger.info(f"Initialized new conversation {conversation_id} with {len(file_contents)} files")
        # Set initial context submission baseline for new conversations
        file_state_manager.mark_context_submission(conversation_id)
        logger.info(f"Set initial context submission baseline for conversation {conversation_id}")
    else:
        # For EXISTING conversations, refresh files from disk to catch external changes
        logger.info(f"🔍 FILE_STATE: Refreshing existing conversation {conversation_id} from disk")
        base_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", "")
        refresh_results = file_state_manager.refresh_all_files_from_disk(conversation_id, base_dir)
        changed_count = sum(1 for changed in refresh_results.values() if changed)
        logger.info(f"Refreshed conversation {conversation_id}: {changed_count} files changed on disk")
    
    # Update any files that may have changed
    file_state_manager.update_files_in_state(conversation_id, file_contents)

    # Get layered changes (cumulative + recent)
    overall_changes, recent_changes = file_state_manager.format_layered_context_message(conversation_id)

    # Get file authority message to help model understand actual file state
    authority_message = file_state_manager.format_file_authority_message(conversation_id)
    logger.debug(f"Authority message: {authority_message[:200] if authority_message else 'None'}...")

    codebase = get_combined_docs_from_files(files, conversation_id)

    # Enhance with AST context if available
    question = x.get("question", "")
    if question:
        codebase = enhance_context_with_ast(question, {"codebase": codebase}).get("codebase", codebase)
        logger.info("Enhanced codebase context with AST information")

    logger.info(f"Changes detected - Overall: {bool(overall_changes)}, Recent: {bool(recent_changes)}")

    result = []

    # Add file authority message first (most important for model to see)
    if authority_message:
        result.append(authority_message)

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
    logger.debug(f"Final string assembly:")
    logger.debug(f"Total length: {len(final_string)} chars")
    logger.debug(f"Number of File: markers: {len(file_markers)}")
    logger.debug(f"First 500 chars:\n{final_string[:500]}")
    logger.debug(f"Last 500 chars:\n{final_string[-500:]}")

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
    return codebase if codebase.strip() else "No files have been selected for context analysis."
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
    logger.debug(f"Codebase before prompt: {len(codebase)} chars")
    file_count = len([l for l in codebase.split('\n') if l.startswith('File: ')])
    logger.info(f"Number of files in codebase before prompt: {file_count}")
    file_lines = [l for l in codebase.split('\n') if l.startswith('File: ')]
    logger.info("Files in codebase before prompt:\n" + "\n".join(file_lines))
    return codebase

def create_agent_chain(chat_model: BaseChatModel):
    """Create a new agent chain with the given model."""
    from langchain_classic.agents import create_xml_agent
    import hashlib
    from app.agents.models import ModelManager
    logger.debug("Creating agent chain")
    
    # Create cache key based on model configuration
    model_id = ModelManager.get_model_id() or getattr(chat_model, 'model_id', 'unknown')
    ast_enabled = os.environ.get("ZIYA_ENABLE_AST") == "true"
    mcp_enabled = os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes")
    
    # Get MCP tools first to include in cache key
    mcp_tools = []
    if mcp_enabled:
        try:
            from app.mcp.manager import get_mcp_manager
            from app.mcp.enhanced_tools import create_secure_mcp_tools
            mcp_manager = get_mcp_manager()
            # Ensure MCP is initialized before creating tools
            if not mcp_manager.is_initialized:
                # Don't initialize during startup - let server startup handle it
                logger.info("MCP manager not yet initialized, will use available tools when ready")
                mcp_tools = []
            else:
                mcp_tools = create_secure_mcp_tools()
                logger.info(f"Created {len(mcp_tools)} MCP tools for agent chain: {[tool.name for tool in mcp_tools]}")
        
        except Exception as e:
            logger.warning(f"Failed to get MCP tools for agent: {str(e)}")
    else:
        logger.debug("MCP is disabled, no tools will be created for agent chain")
    
    # Include MCP tool count in cache key to ensure different chains for different tool availability
    cache_key = f"{model_id}_{ast_enabled}_{mcp_enabled}_{len(mcp_tools)}"
    cache_key_hash = hashlib.md5(cache_key.encode()).hexdigest()[:8]
    
    # Check ModelManager cache
    from app.agents.models import ModelManager
    cached_chain = ModelManager._state.get('agent_chain_cache', {}).get(cache_key_hash)
    if cached_chain:
        logger.info(f"Using cached agent chain for {cache_key_hash}")
        return cached_chain
    
    logger.info(f"Creating new agent chain for {cache_key_hash}")
    
    # Get model information for prompt extensions
    model_info = get_model_info_from_config()
    model_name = model_info["model_name"]
    model_family = model_info["model_family"]
    endpoint = model_info["endpoint"]
    
    logger.info(f"Creating agent chain for model: {model_name}, family: {model_family}, endpoint: {endpoint}")
    
    # Define the input mapping with conditional AST context
    input_mapping = {
        "codebase": log_codebase_wrapper_with_cache,
        "question": lambda x: x.get("question", ""),
        "conversation_id": lambda x: x.get("conversation_id", "default"),
        "chat_history": lambda x: _format_chat_history(x.get("chat_history", [])),
        "agent_scratchpad": lambda x: [
            AIMessage(content=format_xml([]))
        ]
    }
    
    # Add AST context enhancement if enabled
    if ast_enabled:
        logger.info("Adding AST context to agent chain input mapping")
        def get_ast_context_for_prompt(x):
            from app.utils.context_enhancer import get_ast_context
            if get_ast_context:
                ast_context = get_ast_context()
                logger.info(f"Retrieved AST context: {len(ast_context) if ast_context else 0} chars")
                return ast_context or ""
            return ""
        input_mapping["ast_context"] = get_ast_context_for_prompt
        logger.info(f"AST context lambda added to input mapping: {input_mapping.get('ast_context')}")
    else:
        logger.info("AST context not added to agent chain (disabled)")
        # Add empty AST context to avoid template errors
        input_mapping["ast_context"] = lambda x: {}

    # Disable Google native function calling for now - will migrate off langchain later
    if False: # endpoint == "google" and len(mcp_tools) > 0:
        logger.info(f"Detected Google model with {len(mcp_tools)} tools - enabling native function calling")
        try:
            # Import Google wrapper
            from app.agents.wrappers.ziya_google_genai import ZiyaChatGoogleGenerativeAI
            
            # Check if the model is our custom wrapper (handle RetryingChatBedrock wrapper)
            underlying_model = chat_model.model if hasattr(chat_model, 'model') else chat_model
            if isinstance(underlying_model, ZiyaChatGoogleGenerativeAI):
                # Get prompt template for system message
                mcp_context = {
                     "mcp_tools_available": len(mcp_tools) > 0,
                     "available_mcp_tools": [tool.name for tool in mcp_tools],
                     "model_id": model_id
                }
                
                prompt_template = get_extended_prompt(
                    model_name=model_name,
                    model_family=model_family,
                    endpoint=endpoint,
                    context=mcp_context
                )
                
                # Bind tools using Google's native function calling
                llm_with_tools = underlying_model.bind_tools(mcp_tools)
                logger.info(f"Successfully bound {len(mcp_tools)} tools to Google model")
                
                # Create a simple chain that just calls the model with tools
                def google_agent_call(input_data):
                    """Handle Google function calling."""
                    # Apply input mapping
                    mapped_input = {}
                    for key, mapper in input_mapping.items():
                        try:
                            mapped_input[key] = mapper(input_data)
                        except Exception as e:
                            logger.error(f"Error applying input mapping for {key}: {e}")
                            mapped_input[key] = ""
                    
                    # Format messages for Google
                    messages = []
                    
                    # Add system message from prompt
                    if hasattr(prompt_template, 'messages') and prompt_template.messages:
                        system_msg = prompt_template.messages[0]
                        # Escape curly braces but preserve template literals like ${variable}
                        import re
                        def escape_braces_preserve_template_literals(text):
                            if not isinstance(text, str) or '{{' in text or '}}' in text:
                                return text
                            # Temporarily replace template literals with placeholders
                            placeholders = []
                            def save_template(match):
                                placeholders.append(match.group(0))
                                return f'__TEMPLATE_{len(placeholders)-1}__'
                            
                            # Save template literals
                            text = re.sub(r'\$\{[^}]*\}', save_template, text)
                            
                            # Escape remaining braces
                            text = text.replace('{', '{{').replace('}', '}}')
                            
                            # Restore template literals
                            for i, placeholder_text in enumerate(placeholders):
                                text = text.replace(f'__TEMPLATE_{i}__', placeholder_text)
                            
                            return text
                        
                        safe_mapped_input = {
                            k: escape_braces_preserve_template_literals(v) if isinstance(v, str) and k != 'question' else v
                            for k, v in mapped_input.items()
                        }
                        formatted_content = system_msg.format(**safe_mapped_input)
                        # Don't escape codebase content as it may already contain properly escaped MCP examples
                        # Only escape user-provided content (questions and chat history)
                        formatted_content = system_msg.format(**safe_mapped_input)
                        messages.append(SystemMessage(content=formatted_content))
                    
                    # Add user question
                    question = mapped_input.get("question", "")
                    if question:
                        messages.append(HumanMessage(content=question))
                    
                    # Call model with function calling
                    try:
                        result = llm_with_tools.invoke(messages)
                        return {"output": result.content}
                    except Exception as e:
                        logger.error(f"Google function calling error: {e}")
                        # Fall back to regular model without tools
                        result = chat_model.invoke(messages)
                        return {"output": result.content}
                
                from langchain_core.runnables import RunnableLambda
                agent_chain = RunnableLambda(google_agent_call)
                
                logger.info("Created Google function calling agent chain")
                
                # Cache and return
                if 'agent_chain_cache' not in ModelManager._state:
                    ModelManager._state['agent_chain_cache'] = {}
                ModelManager._state['agent_chain_cache'][cache_key_hash] = agent_chain
                logger.info(f"Cached Google agent chain for {cache_key_hash}")
                
                return agent_chain
                
        except Exception as e:
            logger.warning(f"Failed to create Google function calling agent, falling back to XML: {e}")
    
    # Bind the stop sequence to the model for XML agents
    llm_with_stop = chat_model.bind(stop=[TOOL_SENTINEL_CLOSE])
    
    # Store the model with stop in the ModelManager state
    ModelManager._state['llm_with_stop'] = llm_with_stop
    
    # Add MCP context for prompt extensions
    mcp_context = {
         "mcp_tools_available": len(mcp_tools) > 0,
         "available_mcp_tools": [tool.name for tool in mcp_tools],
     "model_id": model_id,
     "endpoint": endpoint
    }
    logger.info(f"AGENT_DEBUG: Passing mcp_context to get_extended_prompt: {mcp_context}")
 
    prompt_template = get_extended_prompt(
     model_name=model_name,
        model_family=model_family,
        endpoint=endpoint,
        context=mcp_context
    )
    
    # Check if model is available before creating agent
    if llm_with_stop is None:
        logger.error("Cannot create XML agent - model is not available (likely due to credential issues)")
        return None
        
    # Create the XML agent directly with input preprocessing
    # Use custom output parser for MCP tool detection
    agent = create_xml_agent(llm_with_stop, mcp_tools, prompt_template)
    # Log the tools that were actually passed to the agent
    logger.info(f"XML agent created with {len(mcp_tools)} tools: {[tool.name for tool in mcp_tools]}")
    
    # Check if agent has output_parser attribute before logging it
    if hasattr(agent, 'output_parser'):
        logger.info(f"Created XML agent with output parser: {agent.output_parser.__class__.__name__}")
    else:
        # For RunnableSequence objects that don't have output_parser
        logger.info(f"Created XML agent of type: {type(agent).__name__}")
    
    # Create a preprocessing chain that applies input mapping
    def preprocess_input(input_data):
        """Apply input mapping to transform input data."""
        mapped_input = {}
        for key, mapper in input_mapping.items():
            try:
                mapped_input[key] = mapper(input_data)
            except Exception as e:
                logger.error(f"Error applying input mapping for {key}: {e}")
                mapped_input[key] = ""
        return mapped_input
    
    # Create a chain that preprocesses input then calls the agent
    from langchain_core.runnables import RunnableLambda
    preprocessing_chain = RunnableLambda(preprocess_input)
    agent_chain = preprocessing_chain | agent
    
    logger.info(f"Created XML agent with {len(mcp_tools)} tools and input mapping")
    logger.info(f"Input mapping keys: {list(input_mapping.keys())}")
    
    # Cache the agent chain
    if 'agent_chain_cache' not in ModelManager._state:
        ModelManager._state['agent_chain_cache'] = {}
    ModelManager._state['agent_chain_cache'][cache_key_hash] = agent_chain
    logger.info(f"Cached agent chain for {cache_key_hash}")
    
    return agent_chain
 
# Initialize the agent chain lazily
agent = None

def get_or_create_agent():
    """Get the agent, creating it if necessary."""
    global agent
    if agent is None:
        agent = create_agent_chain(model)
        if agent is None:
            logger.warning("Agent creation failed - will retry when credentials are available")
    return agent

def reset_mcp_tool_counter():
    """Reset the MCP tool execution counter for a new request cycle."""
    from app.mcp.tools import _tool_execution_counter, _tool_execution_lock
    import asyncio
    asyncio.create_task(_reset_counter_async())

logger.info("Agent chain defined with parse_output")
def update_conversation_state(conversation_id: str, file_paths: List[str]) -> None:
    """Update file states after a response has been generated"""
    logger.info(f"Updating conversation state for {conversation_id} with {len(file_paths)} files")
    # Read current file contents, skipping directories
    file_contents = {}
    for file_path in file_paths:
        full_path = os.path.join(os.environ["ZIYA_USER_CODEBASE_DIR"], file_path)
        if os.path.isdir(full_path):
            # Skip directories silently
            continue
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

    # Mark context submission after the response is complete
    from app.utils.context_cache import get_context_cache_manager
    cache_manager = get_context_cache_manager()
    cache_manager.mark_context_submitted(conversation_id)
    logger.info(f"Marked context submission for conversation {conversation_id}")

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

    # Get MCP tools for the executor
    mcp_tools = []
    # Check if MCP is enabled before creating tools
    if os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
        try:
            logger.info("Attempting to get MCP tools for agent executor...")
            
            from app.mcp.manager import get_mcp_manager
            mcp_manager = get_mcp_manager()
            
            if mcp_manager.is_initialized:
                mcp_tools = create_mcp_tools()
                logger.info(f"Created agent executor with {len(mcp_tools)} MCP tools")
                for tool in mcp_tools:
                    logger.info(f"  - {tool.name}: {tool.description}")
            else:
                logger.info("MCP not initialized, no MCP tools available")
        except Exception as e:
            logger.warning(f"Failed to initialize MCP tools: {str(e)}", exc_info=True)
            from app.mcp.manager import get_mcp_manager
    else:
        logger.debug("MCP is disabled, no tools will be created for agent executor")
        mcp_manager = get_mcp_manager()

    logger.info(f"AGENT_EXECUTOR: Tools being passed to AgentExecutor: {[tool.name for tool in mcp_tools] if mcp_tools else 'No tools'}")
        
    # Create the original executor
    logger.info(f"Creating AgentExecutor with agent type: {type(agent_chain)}")
    
    # Check if agent is available before creating executor
    if agent_chain is None:
        logger.error("Cannot create AgentExecutor - agent is not available")
        return None
        
    original_executor = AgentExecutor(
        verbose=True,  # Enable verbose logging
        agent=agent_chain,
        tools=mcp_tools,
        handle_parsing_errors=True,
        return_intermediate_steps=True,  # This helps with debugging
        max_iterations=15,  # Allow more iterations for complex tasks
    ).with_types(input_type=AgentInput) | RunnablePassthrough.assign(output=update_and_return)
    
    # Wrap the executor to add debugging
    class DebuggingAgentExecutor:
        def __init__(self, wrapped_executor):
            self.executor = wrapped_executor
            
        async def astream(self, input_data, config=None, **kwargs):
            logger.debug(f"🔍 DebuggingAgentExecutor.astream called with question: {input_data.get('question', 'N/A')}")
            async for chunk in self.executor.astream(input_data, config, **kwargs):
                logger.debug(f"🔍 DebuggingAgentExecutor yielding chunk type: {type(chunk)}")
                yield chunk
                
        def __getattr__(self, name):
            return getattr(self.executor, name)
    
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
                async for chunk in self.executor.astream(input_data, config=config, **kwargs):
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

# Initialize agent_executor lazily
agent_executor = None

def get_or_create_agent_executor():
    """Get the agent executor, creating it if necessary."""
    global agent_executor
    if agent_executor is None:
        current_agent = get_or_create_agent()
        if current_agent is not None:
            agent_executor = create_agent_executor(current_agent)
            if agent_executor is None:
                logger.warning("Agent executor creation failed - will retry when credentials are available")
    return agent_executor

def initialize_langserve(app, executor):
    """Initialize or reinitialize langserve routes with the given executor."""
    import gc
    
    # Force garbage collection to clean up any lingering references
    gc.collect()

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
 
    # Add LangServe routes for non-Bedrock models (Gemini, Nova, etc.)
    # DISABLED: LangServe /ziya routes cause duplicate execution with /api/chat
    # add_routes(
    #     new_app,
    #     executor,
    #     disabled_endpoints=["playground"],  # Keep stream and invoke for non-Bedrock models
    #     path="/ziya"
    # )
    
    logger.info("DISABLED LangServe /ziya routes - using /api/chat only to prevent duplicate execution")

    # Clear all routes from original app
    while app.routes:
        app.routes.pop()
 
    logger.info("Cleared existing routes")
    
    # Copy routes from new app to original app
    for route in new_app.routes:
        app.routes.append(route)
        
    # Force another garbage collection after route replacement
    gc.collect()
        
    logger.info(f"Successfully reinitialized app with {len(new_app.routes)} routes")
    return True
