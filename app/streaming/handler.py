#!/usr/bin/env python3
"""
Simplified Streaming Handler - No LangChain Dependencies
Handles all streaming responses directly through model APIs
"""

import asyncio
import json
import os
import time
from typing import Dict, List, Any, Optional, AsyncGenerator

from app.utils.logging_utils import logger


class StreamingHandler:
    """Unified streaming handler for all model types."""
    
    def __init__(self):
        """Initialize streaming handler."""
        self.endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        
    async def stream_response(self, body: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """Stream response based on endpoint type."""
        try:
            if self.endpoint == "bedrock":
                async for chunk in self._stream_bedrock(body):
                    yield chunk
            elif self.endpoint == "google":
                async for chunk in self._stream_google(body):
                    yield chunk
            else:
                yield f"data: {json.dumps({'type': 'error', 'content': f'Unsupported endpoint: {self.endpoint}'})}\n\n"
                
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
        finally:
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
    
    async def _stream_bedrock(self, body: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """Stream Bedrock response."""
        from app.agents.direct_bedrock import DirectBedrockClient
        from app.agents.models import ModelManager
        
        # Get AWS configuration
        state = ModelManager.get_state()
        region = state.get('aws_region', 'us-east-1')
        profile = state.get('aws_profile')
        
        # Create client
        client = DirectBedrockClient(profile_name=profile, region=region)
        
        # Extract request data
        question = body.get("question", "")
        chat_history = body.get("chat_history", [])
        files = body.get("config", {}).get("files", [])
        conversation_id = body.get("conversation_id", "")
        
        # Build messages
        messages = client.build_messages(question, chat_history, files, conversation_id)
        
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
        
        # Stream response
        async for chunk in client.stream_with_tools(messages, tools):
            if chunk.get("type") == "text":
                ops = [{"op": "add", "path": "/streamed_output_str/-", "value": chunk["content"]}]
                yield f"data: {json.dumps({'ops': ops})}\n\n"
            elif chunk.get("type") == "error":
                yield f"data: {json.dumps({'type': 'error', 'content': chunk['content']})}\n\n"
    
    async def _stream_google(self, body: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """Stream Google response."""
        from app.agents.wrappers.google_direct import GoogleDirectClient
        
        # Create client
        client = GoogleDirectClient()
        
        # Extract request data
        question = body.get("question", "")
        chat_history = body.get("chat_history", [])
        files = body.get("config", {}).get("files", [])
        conversation_id = body.get("conversation_id", "")
        
        # Build messages
        messages = client.build_messages(question, chat_history, files, conversation_id)
        
        # Stream response
        async for chunk in client.stream_response(messages):
            if chunk.get("type") == "text":
                ops = [{"op": "add", "path": "/streamed_output_str/-", "value": chunk["content"]}]
                yield f"data: {json.dumps({'ops': ops})}\n\n"
            elif chunk.get("type") == "error":
                yield f"data: {json.dumps({'type': 'error', 'content': chunk['content']})}\n\n"
