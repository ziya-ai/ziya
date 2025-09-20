#!/usr/bin/env python3
import asyncio
import json
import boto3
import logging
from typing import Dict, Any, List, AsyncGenerator, Optional

logger = logging.getLogger(__name__)

class StreamingToolExecutor:
    def __init__(self, profile_name: str = 'ziya', region: str = 'us-west-2'):
        session = boto3.Session(profile_name=profile_name)
        self.bedrock = session.client('bedrock-runtime', region_name=region)
        self.model_id = 'us.anthropic.claude-sonnet-4-20250514-v1:0'

    def _convert_tool_schema(self, tool):
        """Convert tool schema to JSON-serializable format"""
        if isinstance(tool, dict):
            # Already a dict, but check input_schema
            result = tool.copy()
            input_schema = result.get('input_schema')
            if hasattr(input_schema, 'model_json_schema'):
                # Pydantic class - convert to JSON schema
                result['input_schema'] = input_schema.model_json_schema()
            elif hasattr(input_schema, '__dict__') and not isinstance(input_schema, dict):
                # Some other class object - try to convert
                try:
                    result['input_schema'] = input_schema.model_json_schema()
                except:
                    # Fallback to basic schema
                    result['input_schema'] = {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"]
                    }
            return result
        else:
            # Tool object - extract properties
            name = getattr(tool, 'name', 'unknown')
            description = getattr(tool, 'description', 'No description')
            input_schema = getattr(tool, 'input_schema', getattr(tool, 'inputSchema', {}))
            
            # Convert input_schema if it's a Pydantic class
            if hasattr(input_schema, 'model_json_schema'):
                input_schema = input_schema.model_json_schema()
            elif hasattr(input_schema, '__dict__') and not isinstance(input_schema, dict):
                # Some other class object
                try:
                    input_schema = input_schema.model_json_schema()
                except:
                    input_schema = {
                        "type": "object", 
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"]
                    }
            
            return {
                'name': name,
                'description': description,
                'input_schema': input_schema
            }

    async def stream_with_tools(self, messages: List[Dict[str, Any]], tools: Optional[List] = None) -> AsyncGenerator[Dict[str, Any], None]:
        # Get MCP tools
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        if not mcp_manager.is_initialized:
            await mcp_manager.initialize()

        mcp_tools = mcp_manager.get_all_tools()
        # Convert tools to JSON-serializable format and deduplicate by name
        converted_tools = [self._convert_tool_schema(tool) for tool in mcp_tools]
        # Deduplicate tools by name (keep first occurrence)
        seen_names = set()
        bedrock_tools = []
        for tool in converted_tools:
            tool_name = tool.get('name', 'unknown')
            if tool_name not in seen_names:
                seen_names.add(tool_name)
                # Add mcp_ prefix if not already present
                if not tool_name.startswith('mcp_'):
                    tool['name'] = f'mcp_{tool_name}'
                bedrock_tools.append(tool)

        # Build conversation
        conversation = []
        system_content = None

        for msg in messages:
            role = msg.get('role', '')
            content = msg.get('content', '')
            if role == 'system':
                system_content = content
            elif role in ['user', 'assistant']:
                conversation.append({"role": role, "content": content})

        # Iterative execution with proper tool result handling
        for iteration in range(50):  # Increased from 20 to support more complex tasks
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4000,
                "messages": conversation
            }

            if system_content:
                # Remove ALL XML tool instructions to prevent confusion
                import re
                system_content = re.sub(r'## MCP Tool Usage.*?(?=##|$)', '', system_content, flags=re.DOTALL)
                system_content = re.sub(r'<TOOL_SENTINEL>.*?</TOOL_SENTINEL>', '', system_content, flags=re.DOTALL)
                system_content = re.sub(r'<invoke.*?</invoke>', '', system_content, flags=re.DOTALL)
                system_content = re.sub(r'<tool_input.*?</tool_input>', '', system_content, flags=re.DOTALL)
                system_content = re.sub(r'<mcp_tool.*?</mcp_tool>', '', system_content, flags=re.DOTALL)
                # Remove any remaining XML-like tool patterns
                system_content = re.sub(r'<[^>]*tool[^>]*>.*?</[^>]*>', '', system_content, flags=re.DOTALL | re.IGNORECASE)
                body["system"] = system_content + "\n\nCRITICAL: Use ONLY native tool calling. Never generate XML like <invoke>, <TOOL_SENTINEL>, or <tool_input>. Use the provided tools directly."

            if bedrock_tools:
                body["tools"] = bedrock_tools
                # Use "auto" to allow model to decide when to stop
                body["tool_choice"] = {"type": "auto"}

            try:
                response = self.bedrock.invoke_model_with_response_stream(
                    modelId=self.model_id,
                    body=json.dumps(body)
                )

                # Process this iteration's stream
                assistant_text = ""
                tool_results = []
                yielded_text_length = 0  # Track how much text we've yielded
                
                active_tools = {}
                completed_tools = set()
                expected_tools = set()
                
                # Timeout protection
                import time
                start_time = time.time()
                chunk_timeout = 30

                for event in response['body']:
                    # Timeout protection
                    if time.time() - start_time > chunk_timeout:
                        logger.warning(f"🚨 STREAM TIMEOUT after {chunk_timeout}s")
                        yield {'type': 'stream_end'}
                        break
                        
                    chunk = json.loads(event['chunk']['bytes'])

                    if chunk['type'] == 'content_block_start':
                        content_block = chunk.get('content_block', {})
                        if content_block.get('type') == 'tool_use':
                            tool_id = content_block.get('id')
                            tool_name = content_block.get('name')
                            if tool_id and tool_name:
                                expected_tools.add(tool_id)  # Track expected tools
                                active_tools[tool_id] = {
                                    'name': tool_name,
                                    'partial_json': '',
                                    'index': chunk.get('index')
                                }
                                
                                # CRITICAL FIX: Flush any unyielded text before tool_start
                                if len(assistant_text) > yielded_text_length:
                                    missing_text = assistant_text[yielded_text_length:]
                                    yield {'type': 'text', 'content': missing_text}
                                    yielded_text_length = len(assistant_text)
                                
                                yield {
                                    'type': 'tool_start',
                                    'tool_id': tool_id,
                                    'tool_name': tool_name,
                                    'input': {}
                                }

                    elif chunk['type'] == 'content_block_delta':
                        delta = chunk.get('delta', {})
                        if delta.get('type') == 'text_delta':
                            text = delta.get('text', '')
                            assistant_text += text
                            yield {'type': 'text', 'content': text}
                            yielded_text_length += len(text)  # Track yielded text
                        elif delta.get('type') == 'input_json_delta':
                            # Find tool by index
                            tool_id = None
                            for tid, tdata in active_tools.items():
                                if tdata.get('index') == chunk.get('index'):
                                    tool_id = tid
                                    break
                            if tool_id:
                                active_tools[tool_id]['partial_json'] += delta.get('partial_json', '')

                    elif chunk['type'] == 'content_block_stop':
                        # Find and execute tool
                        tool_id = None
                        for tid, tdata in active_tools.items():
                            if tdata.get('index') == chunk.get('index'):
                                tool_id = tid
                                break
                        
                        if tool_id and tool_id not in completed_tools:
                            tool_data = active_tools[tool_id]
                            tool_name = tool_data['name']
                            args_json = tool_data['partial_json']

                            try:
                                args = json.loads(args_json) if args_json.strip() else {}
                                actual_tool_name = tool_name[4:] if tool_name.startswith('mcp_') else tool_name
                                
                                # Handle both run_shell_command and mcp_run_shell_command
                                if actual_tool_name in ['run_shell_command'] or tool_name in ['mcp_run_shell_command']:
                                    actual_tool_name = 'run_shell_command'
                                    
                                result = await mcp_manager.call_tool(actual_tool_name, args)

                                if isinstance(result, dict) and 'content' in result:
                                    content = result['content']
                                    if isinstance(content, list) and len(content) > 0:
                                        result_text = content[0].get('text', str(result))
                                    else:
                                        result_text = str(result)
                                else:
                                    result_text = str(result)

                                tool_results.append({
                                    'tool_id': tool_id,
                                    'tool_name': tool_name,
                                    'result': result_text
                                })

                                yield {
                                    'type': 'tool_execution',
                                    'tool_id': tool_id,
                                    'tool_name': tool_name,
                                    'result': result_text
                                }
                                completed_tools.add(tool_id)

                            except Exception as e:
                                error_msg = f"Tool error: {str(e)}"
                                yield {'type': 'error', 'content': error_msg}

                    elif chunk['type'] == 'message_stop':
                        # End stream when all expected tools are completed
                        if expected_tools and len(completed_tools) >= len(expected_tools):
                            yield {'type': 'stream_end'}
                            break

                # Add assistant response to conversation
                if assistant_text.strip():
                    conversation.append({"role": "assistant", "content": assistant_text})

                # Add tool results and continue iteration
                if tool_results:
                    for tool_result in tool_results:
                        conversation.append({
                            "role": "user", 
                            "content": f"Tool result: {tool_result['result']}. Continue with next step using this result."
                        })
                    # Continue to next iteration
                    continue
                else:
                    # No tools executed - only end if model produced meaningful completion text
                    if assistant_text.strip() and len(assistant_text.strip()) > 10:
                        # Model produced text but no tools - likely completion
                        break
                    elif iteration >= 3:  # Safety: end after 3 failed iterations
                        break
                    else:
                        # Try to continue with a prompt
                        conversation.append({
                            "role": "user",
                            "content": "Continue with the next step to reach the objective."
                        })
                        continue

            except Exception as e:
                yield {'type': 'error', 'content': f'Error: {e}'}
                return
