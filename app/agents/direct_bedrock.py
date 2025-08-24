#!/usr/bin/env python3
"""
Direct Bedrock Client - No LangChain Dependencies
Handles all model interactions directly through AWS Bedrock API
"""

import asyncio
import json
import os
import time
from typing import Dict, List, Any, Optional, AsyncGenerator, Union
import boto3
from botocore.exceptions import ClientError

from app.utils.logging_utils import logger
from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE


class DirectBedrockClient:
    """Direct Bedrock client without LangChain dependencies."""
    
    def __init__(self, profile_name: Optional[str] = None, region: str = 'us-east-1'):
        """Initialize direct Bedrock client."""
        session = boto3.Session(profile_name=profile_name) if profile_name else boto3.Session()
        self.bedrock = session.client('bedrock-runtime', region_name=region)
        self.region = region
        
    def build_messages(self, question: str, chat_history: List, files: List, conversation_id: str) -> List[Dict]:
        """Build messages for Bedrock API."""
        from app.agents.prompts_manager import get_extended_prompt, get_model_info_from_config
        from app.agents.agent import get_combined_docs_from_files
        
        # Get model info and extended prompt
        model_info = get_model_info_from_config()
        extended_prompt = get_extended_prompt(
            model_name=model_info["model_name"],
            model_family=model_info["model_family"],
            endpoint=model_info["endpoint"]
        )
        
        # Get file context
        file_context = get_combined_docs_from_files(files) if files else ""
        
        # Get MCP tools
        tools_list = []
        try:
            from app.mcp.manager import get_mcp_manager
            mcp_manager = get_mcp_manager()
            if mcp_manager.is_initialized:
                tools_list = [f"- {tool.name}: {tool.description}" for tool in mcp_manager.get_all_tools()]
        except Exception as e:
            logger.warning(f"Could not get MCP tools: {e}")
        
        # Build system message
        system_content = extended_prompt.messages[0].prompt.template.format(
            codebase=file_context,
            ast_context="",
            tools="\n".join(tools_list) if tools_list else "No tools available",
            TOOL_SENTINEL_OPEN=TOOL_SENTINEL_OPEN,
            TOOL_SENTINEL_CLOSE=TOOL_SENTINEL_CLOSE
        )
        
        messages = [{"role": "system", "content": system_content}]
        
        # Add chat history
        for item in chat_history:
            if isinstance(item, dict):
                role = item.get('type', item.get('role', 'human'))
                content = item.get('content', '')
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                role, content = item[0], item[1]
            else:
                continue
                
            if role in ['human', 'user']:
                messages.append({"role": "user", "content": content})
            elif role in ['assistant', 'ai']:
                messages.append({"role": "assistant", "content": content})
        
        # Add current question
        messages.append({"role": "user", "content": question})
        
        return messages
    
    async def stream_with_tools(self, messages: List[Dict], tools: Optional[List[Dict]] = None) -> AsyncGenerator[Dict, None]:
        """Stream response with tool execution."""
        from app.agents.models import ModelManager
        
        # Get model ID
        model_id = ModelManager.get_model_id()
        if isinstance(model_id, dict):
            # Use region-specific model ID
            if self.region.startswith('eu-') and 'eu' in model_id:
                model_id = model_id['eu']
            elif self.region.startswith('us-') and 'us' in model_id:
                model_id = model_id['us']
            else:
                model_id = next(iter(model_id.values()))
        
        # Get model settings
        settings = ModelManager.get_model_settings()
        
        # Prepare request body
        body = {
            "messages": messages,
            "max_tokens": settings.get("max_output_tokens", 4096),
            "temperature": settings.get("temperature", 0.3),
            "anthropic_version": "bedrock-2023-05-31"
        }
        
        # Add tools if available
        if tools:
            bedrock_tools = []
            for tool in tools:
                bedrock_tools.append({
                    "name": tool["name"],
                    "description": tool["description"],
                    "input_schema": tool.get("input_schema", {})
                })
            body["tools"] = bedrock_tools
        
        try:
            # Stream response
            response = self.bedrock.invoke_model_with_response_stream(
                modelId=model_id,
                body=json.dumps(body)
            )
            
            current_response = ""
            
            for event in response['body']:
                chunk = json.loads(event['chunk']['bytes'])
                
                if chunk['type'] == 'content_block_delta':
                    if 'delta' in chunk and 'text' in chunk['delta']:
                        text = chunk['delta']['text']
                        current_response += text
                        
                        # Check for tool calls
                        if TOOL_SENTINEL_OPEN in current_response and TOOL_SENTINEL_CLOSE in current_response:
                            # Execute tools
                            processed_response = await self._execute_tools(current_response)
                            if processed_response != current_response:
                                # Stream tool results
                                tool_result = processed_response[len(current_response):]
                                yield {"type": "text", "content": tool_result}
                                current_response = processed_response
                        else:
                            # Stream regular text
                            yield {"type": "text", "content": text}
                
                elif chunk['type'] == 'message_stop':
                    break
                    
        except ClientError as e:
            logger.error(f"Bedrock API error: {e}")
            yield {"type": "error", "content": f"API Error: {str(e)}"}
    
    async def _execute_tools(self, response: str) -> str:
        """Execute tools found in response."""
        from app.mcp.tools import parse_tool_call
        from app.mcp.manager import get_mcp_manager
        import re
        
        # Find tool calls
        tool_call_pattern = re.escape(TOOL_SENTINEL_OPEN) + r'.*?' + re.escape(TOOL_SENTINEL_CLOSE)
        tool_calls = re.findall(tool_call_pattern, response, re.DOTALL)
        
        if not tool_calls:
            return response
        
        modified_response = response
        mcp_manager = get_mcp_manager()
        
        for tool_call_block in tool_calls:
            parsed_call = parse_tool_call(tool_call_block)
            if not parsed_call:
                continue
            
            tool_name = parsed_call["tool_name"]
            arguments = parsed_call["arguments"]
            
            try:
                # Execute tool
                internal_tool_name = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
                result = await mcp_manager.call_tool(internal_tool_name, arguments)
                
                if result and isinstance(result, dict) and "content" in result:
                    if isinstance(result["content"], list) and len(result["content"]) > 0:
                        tool_output = result["content"][0].get("text", str(result["content"]))
                    else:
                        tool_output = str(result["content"])
                else:
                    tool_output = str(result)
                
                # Replace tool call with result
                replacement = f"\n```tool:{tool_name}\n{tool_output.strip()}\n```\n"
                modified_response = modified_response.replace(tool_call_block, replacement)
                
            except Exception as e:
                logger.error(f"Tool execution error: {e}")
                error_msg = f"\n**Tool Error:** {str(e)}\n"
                modified_response = modified_response.replace(tool_call_block, error_msg)
        
        return modified_response
