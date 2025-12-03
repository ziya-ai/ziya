"""
Direct Streaming Integration for Ziya
Replaces LangChain with StreamingToolExecutor for real-time tool execution
"""

import asyncio
import json
import sys
import os
from typing import Dict, List, Any, Optional, AsyncGenerator
from app.streaming_tool_executor import StreamingToolExecutor
from app.agents.wrappers.google_direct import DirectGoogleModel
from app.utils.logging_utils import logger

# Add the root directory to Python path to import streaming_tool_executor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.utils.logging_utils import logger
from app.streaming_tool_executor import StreamingToolExecutor

class DirectStreamingAgent:
    """
    Direct streaming agent that bypasses LangChain for tool execution
    """
    
    def __init__(self, profile_name: Optional[str] = None, region: str = 'us-east-1'):
        from app.agents.models import ModelManager
        
        # Initialize all attributes first to prevent AttributeError
        self.google_model = None
        self.executor = None
        self.is_bedrock = False
        
        # Get current model info to determine which streaming approach to use
        try:
            state = ModelManager.get_state()
            current_model = state.get('current_model_id', '')
            
            # Handle dict-type model IDs (region-specific)
            if isinstance(current_model, dict):
                # Get the actual model ID from the dict
                current_model = list(current_model.values())[0] if current_model else ''
            
            # Determine endpoint based on model name
            if current_model.startswith('gemini') or current_model.startswith('google'):
                # Use DirectGoogleModel for Google models
                self.google_model = DirectGoogleModel(model_name=current_model)
                self.is_bedrock = False
            elif 'openai' in current_model.lower():
                # OpenAI models on Bedrock should use LangChain path, not direct streaming
                raise ValueError("OpenAI models should use LangChain path")
            else:
                # Use StreamingToolExecutor for other Bedrock models
                state = ModelManager.get_state()
                profile_name = state.get('aws_profile', profile_name)
                region = state.get('aws_region', region)
                self.executor = StreamingToolExecutor(profile_name=profile_name, region=region)
                self.is_bedrock = True
                
        except ValueError as e:
            if "OpenAI models should use LangChain path" in str(e):
                # Re-raise this specific error so server.py can handle it
                raise
        except Exception as e:
            # For any other exception, log it but don't leave object in incomplete state
            logger.error(f"Error initializing DirectStreamingAgent: {e}")
            # Default to Bedrock mode if initialization fails
            self.executor = StreamingToolExecutor(profile_name=profile_name, region=region)
            self.is_bedrock = True
        except Exception as e:
            # Fallback to Bedrock if we can't determine the model type
            logger.warning(f"Could not determine model type, defaulting to Bedrock: {e}")
            self.executor = StreamingToolExecutor(profile_name=profile_name, region=region)
            self.google_model = None
            self.is_bedrock = True
        
    def convert_langchain_to_openai(self, langchain_messages: List[Any]) -> List[Dict[str, str]]:
        """Convert LangChain messages to OpenAI format"""
        openai_messages = []
        
        for msg in langchain_messages:
            if hasattr(msg, 'type'):
                if msg.type == 'human':
                    openai_messages.append({"role": "user", "content": msg.content})
                elif msg.type == 'ai':
                    openai_messages.append({"role": "assistant", "content": msg.content})
                elif msg.type == 'system':
                    openai_messages.append({"role": "system", "content": msg.content})
            elif isinstance(msg, dict):
                # Handle dict format messages with 'role' key
                if msg.get('role') in ['user', 'assistant', 'system']:
                    openai_messages.append(msg)
                # Handle dict format messages with 'type' key
                elif msg.get('type') == 'human':
                    openai_messages.append({"role": "user", "content": msg.get('content', '')})
                elif msg.get('type') == 'ai':
                    openai_messages.append({"role": "assistant", "content": msg.get('content', '')})
                elif msg.get('type') == 'system':
                    openai_messages.append({"role": "system", "content": msg.get('content', '')})
    
        return openai_messages

    async def stream_with_tools(self, messages: List[Any], tools: Optional[List[Any]] = None, conversation_id: Optional[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream response with real-time tool execution
            
            Args:
                messages: Conversation history (LangChain or OpenAI format)
                tools: Tool definitions (optional)
                conversation_id: Conversation ID for extended context support
                
            Yields:
                Dict with 'type' field indicating 'text', 'tool_result', or 'error'
        """
        try:
            logger.debug(f"DirectStreamingAgent received {len(messages)} messages")
            logger.info(f"🔍 DIRECT_STREAMING: Received {len(tools) if tools else 0} tools")
            if tools:
                logger.info(f"🔍 DIRECT_STREAMING: Tool types: {[type(t).__name__ for t in tools[:5]]}")
            
            # Log user messages at INFO level
            for i, msg in enumerate(messages):
                if msg.get('type') == 'human' and msg.get('content'):
                    logger.info(f"👤 USER MESSAGE: {msg.get('content')}")
                elif i < 3:  # Only debug log first 3 message structures
                    logger.debug(f"Message {i}: type={msg.get('type', 'unknown')}")
            
            if self.is_bedrock:
                # Use StreamingToolExecutor for Bedrock models
                if conversation_id:
                    import app.utils.custom_bedrock as custom_bedrock_module
                    custom_bedrock_module._current_conversation_id = conversation_id
                    logger.debug(f"🔍 DIRECT_STREAMING: Set conversation_id in module global: {conversation_id}")
                    
                    self.executor.conversation_id = conversation_id
                    logger.debug(f"🔍 DIRECT_STREAMING: Set conversation_id on executor: {conversation_id}")
                
                # Convert messages to OpenAI format for Bedrock
                openai_messages = self.convert_langchain_to_openai(messages)
                
                logger.info(f"[DIRECT_STREAMING] Starting Bedrock stream with {len(openai_messages)} messages")
                
                # DEBUGGING: Track streaming metrics
                chunk_count = 0
                tool_results_sent = 0
                largest_chunk = 0
                logger.info(f"🔍 BEFORE_EXECUTOR_CALL: About to call executor.stream_with_tools with {len(tools) if tools else 0} tools")
                if tools:
                    logger.info(f"🔍 BEFORE_EXECUTOR_CALL: Tool names: {[t.name for t in tools[:5]]}")
                async for chunk in self.executor.stream_with_tools(openai_messages, tools, conversation_id=conversation_id):
                    chunk_count += 1
                    chunk_size = len(str(chunk))
                    largest_chunk = max(largest_chunk, chunk_size)
                    
                    if chunk.get('type') == 'tool_execution':
                        tool_results_sent += 1
                        logger.debug(f"🔍 STREAMING_TOOL_RESULT: #{tool_results_sent}, tool={chunk.get('tool_name')}, size={chunk_size}")
                    
                    if chunk_count <= 3:
                        logger.debug(f"DIRECT_STREAMING: Got Bedrock chunk {chunk_count}: {chunk.get('type', 'unknown')}")
                    yield chunk
            
                logger.debug(f"DIRECT_STREAMING: Finished Bedrock streaming, total chunks: {chunk_count}")
                logger.debug(f"🔍 STREAMING_SUMMARY: total_chunks={chunk_count}, tool_results_sent={tool_results_sent}, largest_chunk={largest_chunk}")

            else:
                # Use the new DirectGoogleModel for Google models, which handles native tool calling
                from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
                
                # Convert dict messages to LangChain format for the Google model wrapper
                langchain_messages = []
                for msg in messages:
                    if isinstance(msg, dict):
                        role = msg.get('type', msg.get('role', 'user'))
                    content = msg.get('content', '')
                    if role in ['system']:
                        langchain_messages.append(SystemMessage(content=content))
                    elif role in ['human', 'user']:
                        langchain_messages.append(HumanMessage(content=content))
                    elif role in ['ai', 'assistant']:
                        langchain_messages.append(AIMessage(content=content))
            
                logger.info(f"[DIRECT_STREAMING] Starting Google stream with {len(langchain_messages)} messages")
                
                # The new google_model.astream will yield dicts in our standard format
                async for chunk in self.google_model.astream(langchain_messages, tools=tools):
                    yield chunk

        except Exception as e:
            logger.debug(f"DirectStreamingAgent exception: {str(e)}")
            import traceback
            traceback.print_exc()
            logger.error(f"[DIRECT_STREAMING] Error: {str(e)}")
            
            # Check if this is a throttling error
            error_str = str(e)
            if any(indicator in error_str for indicator in [
                "ThrottlingException", "Too many requests", "Rate exceeded", 
                "Throttling", "throttling", "TooManyRequestsException"
            ]):
                yield {
                    'type': 'error',
                    'error': 'throttling_error',
                    'detail': 'Too many requests to AWS Bedrock. Please wait a moment before trying again.',
                    'status_code': 429
                }
            else:
                yield {'type': 'error', 'content': f"Direct streaming error: {str(e)}"}

def get_shell_tool_schema() -> Dict[str, Any]:
    """Get shell tool schema for Bedrock"""
    return {
        "name": "execute_shell_command",
        "description": "Execute a shell command and return the output",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute"
                }
            },
            "required": ["command"]
        }
    }

# Global instance for reuse
_direct_streaming_agent = None

def get_direct_streaming_agent(profile_name: Optional[str] = None) -> DirectStreamingAgent:
    """Get or create direct streaming agent instance"""
    global _direct_streaming_agent
    if _direct_streaming_agent is None:
        _direct_streaming_agent = DirectStreamingAgent(profile_name=profile_name)
    return _direct_streaming_agent
