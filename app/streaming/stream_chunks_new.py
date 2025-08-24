#!/usr/bin/env python3
"""
Stream Chunks - No LangChain Dependencies
Handles streaming responses without LangChain
"""

import asyncio
import json
import os
import time
import hashlib
from typing import Dict, List, Any, Optional, AsyncGenerator

from app.utils.logging_utils import logger
from app.streaming.message_builder import build_messages_for_streaming


async def stream_chunks(body):
    """Stream chunks from models without LangChain dependencies."""
    logger.info("🔍 STREAM_CHUNKS: Function called - NO LANGCHAIN")
    
    try:
        # Extract variables from request body
        question = body.get("question", "")
        chat_history = body.get("chat_history", [])
        config_data = body.get("config", {})
        files = config_data.get("files", [])
        conversation_id = body.get("conversation_id") or config_data.get("conversation_id")
        
        if not conversation_id:
            import uuid
            conversation_id = f"stream_{uuid.uuid4().hex[:8]}"
        
        logger.info(f"🔍 STREAM_CHUNKS: Processing conversation {conversation_id}")
        logger.info(f"🔍 STREAM_CHUNKS: Question: {question[:50]}...")
        logger.info(f"🔍 STREAM_CHUNKS: Chat history items: {len(chat_history)}")
        logger.info(f"🔍 STREAM_CHUNKS: Files: {len(files)}")
        
        # Handle frontend messages format conversion
        if (not chat_history or len(chat_history) == 0) and "messages" in body:
            messages = body.get("messages", [])
            if len(messages) > 1:
                raw_history = messages[:-1]
                for msg in raw_history:
                    if isinstance(msg, list) and len(msg) >= 2:
                        role, content = msg[0], msg[1]
                        if role in ['human', 'user']:
                            chat_history.append({'type': 'human', 'content': content})
                        elif role in ['assistant', 'ai']:
                            chat_history.append({'type': 'ai', 'content': content})
        
        # Build messages using our LangChain-free builder
        messages = build_messages_for_streaming(question, chat_history, files, conversation_id)
        
        logger.info(f"🔍 STREAM_CHUNKS: Built {len(messages)} messages")
        
        # Log complete context being sent to model
        logger.info("=" * 100)
        logger.info("COMPLETE MODEL CONTEXT - NO LANGCHAIN")
        logger.info("=" * 100)
        for i, message in enumerate(messages):
            logger.info(f"MESSAGE {i+1}: {message['role']}")
            content = message['content']
            if len(content) > 1000:
                logger.info(f"CONTENT: {content[:500]}...{content[-500:]}")
            else:
                logger.info(f"CONTENT: {content}")
            logger.info("-" * 40)
        
        # Route to appropriate streaming implementation
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        
        if endpoint == "bedrock":
            async for chunk in _stream_bedrock(messages, conversation_id):
                yield chunk
        elif endpoint == "google":
            async for chunk in _stream_google(messages, conversation_id):
                yield chunk
        else:
            yield f"data: {json.dumps({'type': 'error', 'content': f'Unsupported endpoint: {endpoint}'})}\n\n"
        
        # Send completion marker
        yield f"data: {json.dumps({'done': True})}\n\n"
        
    except Exception as e:
        logger.error(f"Error in stream_chunks: {e}")
        yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"


async def _stream_bedrock(messages: List[Dict], conversation_id: str) -> AsyncGenerator[str, None]:
    """Stream Bedrock response without LangChain."""
    from app.agents.direct_bedrock import DirectBedrockClient
    from app.agents.models import ModelManager
    
    # Get AWS configuration
    state = ModelManager.get_state()
    region = state.get('aws_region', 'us-east-1')
    profile = state.get('aws_profile')
    
    # Create direct client
    client = DirectBedrockClient(profile_name=profile, region=region)
    
    # Get tools
    tools = []
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        if mcp_manager.is_initialized:
            mcp_tools = mcp_manager.get_all_tools()
            for tool in mcp_tools:
                tools.append({
                    'name': tool.name,
                    'description': tool.description,
                    'input_schema': getattr(tool, 'inputSchema', getattr(tool, 'input_schema', {}))
                })
    except Exception as e:
        logger.warning(f"Could not get MCP tools: {e}")
    
    logger.info(f"🔍 BEDROCK: Streaming with {len(tools)} tools")
    
    # Stream response
    async for chunk in client.stream_with_tools(messages, tools):
        if chunk.get("type") == "text":
            ops = [{"op": "add", "path": "/streamed_output_str/-", "value": chunk["content"]}]
            yield f"data: {json.dumps({'ops': ops})}\n\n"
        elif chunk.get("type") == "error":
            yield f"data: {json.dumps({'type': 'error', 'content': chunk['content']})}\n\n"


async def _stream_google(messages: List[Dict], conversation_id: str) -> AsyncGenerator[str, None]:
    """Stream Google response without LangChain."""
    try:
        import google.generativeai as genai
        
        # Get API key
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY not set")
        
        genai.configure(api_key=api_key)
        
        # Get model
        from app.agents.models import ModelManager
        model_name = ModelManager.get_model_alias()
        
        # Map model names
        google_model_name = "gemini-1.5-pro" if "pro" in model_name.lower() else "gemini-1.5-flash"
        
        model = genai.GenerativeModel(google_model_name)
        
        # Convert messages to Google format
        google_messages = []
        system_instruction = None
        
        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "user":
                google_messages.append({"role": "user", "parts": [msg["content"]]})
            elif msg["role"] == "assistant":
                google_messages.append({"role": "model", "parts": [msg["content"]]})
        
        # Create chat with system instruction
        if system_instruction:
            chat = model.start_chat(history=google_messages[:-1])
            # Send the last user message
            response = chat.send_message(google_messages[-1]["parts"][0], stream=True)
        else:
            chat = model.start_chat(history=google_messages[:-1])
            response = chat.send_message(google_messages[-1]["parts"][0], stream=True)
        
        # Stream response
        for chunk in response:
            if chunk.text:
                ops = [{"op": "add", "path": "/streamed_output_str/-", "value": chunk.text}]
                yield f"data: {json.dumps({'ops': ops})}\n\n"
                
    except Exception as e:
        logger.error(f"Google streaming error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"


async def detect_and_execute_mcp_tools(full_response: str, processed_calls: Optional[set] = None) -> str:
    """
    Detect MCP tool calls in the complete response and execute them.
    """
    if processed_calls is None:
        processed_calls = set()

    from app.mcp.tools import parse_tool_call
    from app.mcp.manager import get_mcp_manager
    from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
    import re
    
    # Check if response contains tool calls
    if TOOL_SENTINEL_OPEN not in full_response:
        return full_response
    
    # Find all tool call blocks
    tool_call_pattern = re.escape(TOOL_SENTINEL_OPEN) + r'.*?' + re.escape(TOOL_SENTINEL_CLOSE)
    tool_calls = re.findall(tool_call_pattern, full_response, re.DOTALL)
    
    if not tool_calls:
        return full_response
    
    modified_response = full_response
    
    for tool_call_block in tool_calls:
        # Create a signature for this tool call to detect duplicates
        tool_signature = hashlib.md5(tool_call_block.encode()).hexdigest()
        
        if tool_signature in processed_calls:
            continue
        processed_calls.add(tool_signature)
        
        # Parse the tool call
        parsed_call = parse_tool_call(tool_call_block)
        if not parsed_call:
            continue
        
        tool_name = parsed_call["tool_name"]
        arguments = parsed_call["arguments"]
        
        logger.info(f"🔍 MCP TOOL CALL: {tool_name} with {arguments}")
        
        try:
            # Get MCP manager and execute the tool
            mcp_manager = get_mcp_manager()
            if not mcp_manager.is_initialized:
                continue
            
            # Execute the tool
            internal_tool_name = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
            result = await mcp_manager.call_tool(internal_tool_name, arguments)
            
            if result is None:
                continue
            
            # Format the result
            if isinstance(result, dict) and "content" in result:
                if isinstance(result["content"], list) and len(result["content"]) > 0:
                    tool_output = result["content"][0].get("text", str(result["content"]))
                else:
                    tool_output = str(result["content"])
            else:
                tool_output = str(result)
            
            # Replace the tool call with properly formatted tool block
            replacement = f"\n```tool:{tool_name}\n{tool_output.strip()}\n```\n"
            modified_response = modified_response.replace(tool_call_block, replacement)
            
        except Exception as e:
            logger.error(f"🔍 MCP: Error executing tool {tool_name}: {str(e)}")
            error_msg = f"\n\n**Tool Error:** {str(e)}\n\n"
            modified_response = modified_response.replace(tool_call_block, error_msg)
    
    return modified_response
