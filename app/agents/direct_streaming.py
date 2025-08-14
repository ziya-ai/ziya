"""
Direct Streaming Integration for Ziya
Replaces LangChain with StreamingToolExecutor for real-time tool execution
"""

import asyncio
import json
import sys
import os
from typing import Dict, List, Any, Optional, AsyncGenerator

# Add the root directory to Python path to import streaming_tool_executor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.utils.logging_utils import logger
from app.streaming_tool_executor import StreamingToolExecutor

class DirectStreamingAgent:
    """
    Direct streaming agent that bypasses LangChain for tool execution
    """
    
    def __init__(self, profile_name: Optional[str] = None, region: str = 'us-east-1'):
        self.executor = StreamingToolExecutor(profile_name=profile_name, region=region)
        
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
                # Handle dict format messages
                if msg.get('role') in ['user', 'assistant', 'system']:
                    openai_messages.append(msg)
        
        return openai_messages
    
    async def stream_with_tools(self, messages: List[Any], tools: Optional[List[Dict[str, Any]]] = None, conversation_id: Optional[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
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
            # Set conversation_id in module global for CustomBedrockClient
            if conversation_id:
                import app.utils.custom_bedrock as custom_bedrock_module
                custom_bedrock_module._current_conversation_id = conversation_id
                logger.info(f"🔍 DIRECT_STREAMING: Set conversation_id in module global: {conversation_id}")
                
                # Also set it on the executor for direct extended context handling
                self.executor.conversation_id = conversation_id
                logger.info(f"🔍 DIRECT_STREAMING: Set conversation_id on executor: {conversation_id}")
            
            # Convert messages to OpenAI format
            openai_messages = self.convert_langchain_to_openai(messages)
            
            logger.info(f"[DIRECT_STREAMING] Starting stream with {len(openai_messages)} messages")
            print(f"DEBUG: About to call executor.stream_with_tools")
            
            # Stream with tools using StreamingToolExecutor
            chunk_count = 0
            async for chunk in self.executor.stream_with_tools(openai_messages, tools):
                chunk_count += 1
                if chunk_count <= 3:
                    print(f"🔍 DIRECT_STREAMING: Got chunk {chunk_count}: {chunk.get('type', 'unknown')}")
                yield chunk
            
            print(f"🔍 DIRECT_STREAMING: Finished streaming, total chunks: {chunk_count}")
                
        except Exception as e:
            print(f"DEBUG: DirectStreamingAgent exception: {str(e)}")
            import traceback
            traceback.print_exc()
            logger.error(f"[DIRECT_STREAMING] Error: {str(e)}")
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
