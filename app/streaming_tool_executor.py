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

    def _commands_similar(self, cmd1: str, cmd2: str) -> bool:
        """Check if two shell commands are functionally similar"""
        # Only consider commands similar if they are nearly identical
        # Remove minor variations like different head counts
        def normalize(cmd):
            return cmd.replace('head -20', 'head').replace('head -30', 'head').replace(' | head', '').strip()
        
        norm1, norm2 = normalize(cmd1), normalize(cmd2)
        
        # Only consider exact matches as similar to avoid blocking legitimate exploration
        return norm1 == norm2

    async def _execute_fake_tool(self, tool_name, command, assistant_text, tool_results, mcp_manager):
        """Execute a fake tool call detected in the text stream"""
        actual_tool_name = tool_name[4:] if tool_name.startswith('mcp_') else tool_name
        if actual_tool_name == 'run_shell_command':
            try:
                result = await mcp_manager.call_tool('run_shell_command', {'command': command.strip()})
                
                if isinstance(result, dict) and 'content' in result:
                    content = result['content']
                    if isinstance(content, list) and len(content) > 0:
                        result_text = content[0].get('text', str(result))
                    else:
                        result_text = str(result)
                else:
                    result_text = str(result)
                
                tool_results.append({
                    'tool_id': f'fake_{len(tool_results)}',
                    'tool_name': tool_name,
                    'result': result_text
                })
                
                return {
                    'type': 'tool_execution',
                    'tool_id': f'fake_{len(tool_results)}',
                    'tool_name': tool_name,
                    'result': result_text
                }
            except Exception as e:
                logger.error(f"Error executing intercepted tool call: {e}")
                return None

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
        recent_commands = []  # Track recent commands to prevent duplicates
        
        for iteration in range(50):  # Increased from 20 to support more complex tasks
            logger.info(f"🔍 ITERATION_START: Beginning iteration {iteration}")
            tools_executed_this_iteration = False  # Track if tools were executed in this iteration
            
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4000,
                "messages": conversation
            }

            if system_content:
                # Remove ALL tool instructions to prevent confusion
                import re
                system_content = re.sub(r'## MCP Tool Usage.*?(?=##|$)', '', system_content, flags=re.DOTALL)
                system_content = re.sub(r'<TOOL_SENTINEL>.*?</TOOL_SENTINEL>', '', system_content, flags=re.DOTALL)
                system_content = re.sub(r'<invoke.*?</invoke>', '', system_content, flags=re.DOTALL)
                system_content = re.sub(r'<tool_input.*?</tool_input>', '', system_content, flags=re.DOTALL)
                system_content = re.sub(r'<mcp_tool.*?</mcp_tool>', '', system_content, flags=re.DOTALL)
                # Remove any remaining XML-like tool patterns
                system_content = re.sub(r'<[^>]*tool[^>]*>.*?</[^>]*>', '', system_content, flags=re.DOTALL | re.IGNORECASE)
                # Remove markdown tool patterns that cause hallucinations
                system_content = re.sub(r'```tool:.*?```', '', system_content, flags=re.DOTALL)
                system_content = re.sub(r'```.*?mcp_.*?```', '', system_content, flags=re.DOTALL)
                body["system"] = system_content + "\n\nCRITICAL: Use ONLY native tool calling. Never generate markdown like ```tool:mcp_run_shell_command or ```bash. Use the provided tools directly."

            if bedrock_tools:
                body["tools"] = bedrock_tools
                # Use "auto" to allow model to decide when to stop
                body["tool_choice"] = {"type": "auto"}

            try:
                # Exponential backoff for rate limiting
                max_retries = 4
                base_delay = 2  # Start with 2 seconds
                
                for retry_attempt in range(max_retries + 1):
                    try:
                        response = self.bedrock.invoke_model_with_response_stream(
                            modelId=self.model_id,
                            body=json.dumps(body)
                        )
                        break  # Success, exit retry loop
                    except Exception as e:
                        error_str = str(e)
                        is_rate_limit = ("Too many tokens" in error_str or 
                                       "ThrottlingException" in error_str or
                                       "Too many requests" in error_str)
                        
                        if is_rate_limit and retry_attempt < max_retries:
                            # Exponential backoff: 2s, 4s, 8s, 16s, 32s (max >20s)
                            delay = base_delay * (2 ** retry_attempt)
                            logger.warning(f"Rate limit hit, retrying in {delay}s (attempt {retry_attempt + 1}/{max_retries + 1})")
                            await asyncio.sleep(delay)
                        else:
                            raise  # Re-raise if not rate limit or max retries exceeded

                # Process this iteration's stream - collect ALL tool calls first
                assistant_text = ""
                tool_results = []
                yielded_text_length = 0  # Track how much text we've yielded
                all_tool_calls = []  # Collect all tool calls from this response
                
                active_tools = {}
                completed_tools = set()
                expected_tools = set()
                skipped_tools = set()  # Track tools we're skipping due to limits
                executed_tool_signatures = set()  # Track tool name + args to prevent duplicates
                
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
                        logger.info(f"🔍 CHUNK_DEBUG: content_block_start - type: {content_block.get('type')}, id: {content_block.get('id')}")
                        if content_block.get('type') == 'tool_use':
                            tool_id = content_block.get('id')
                            tool_name = content_block.get('name')
                            if tool_id and tool_name:
                                # Collect tool call instead of executing immediately
                                all_tool_calls.append({
                                    'id': tool_id,
                                    'name': tool_name,
                                    'args': {}
                                })
                                logger.info(f"🔍 COLLECTED_TOOL: {tool_name} (id: {tool_id})")
                                
                                active_tools[tool_id] = {
                                    'name': tool_name,
                                    'partial_json': '',
                                    'index': chunk.get('index')
                                }

                    elif chunk['type'] == 'content_block_delta':
                        delta = chunk.get('delta', {})
                        tool_id = chunk.get('index')  # Get tool ID from chunk index
                        
                        # Skip processing if this tool is in our skipped set
                        if tool_id in skipped_tools:
                            continue
                            
                        if delta.get('type') == 'text_delta':
                            text = delta.get('text', '')
                            assistant_text += text
                            
                            # Check for fake tool calls in the text and intercept them
                            if ('```tool:' in assistant_text or 'run_shell_command\n$' in assistant_text or 
                                ':mcp_run_shell_command\n$' in assistant_text):
                                # Extract and execute fake tool calls with multiple patterns
                                import re
                                patterns = [
                                    r'```tool:(mcp_\w+)\n\$\s*([^`]+)```',  # Full markdown blocks
                                    r'run_shell_command\n\$\s*([^\n]+)',    # Partial patterns
                                    r':mcp_run_shell_command\n\$\s*([^\n]+)' # Alternative patterns
                                ]
                                
                                for pattern in patterns:
                                    if pattern.startswith('```tool:'):
                                        matches = re.findall(pattern, assistant_text)
                                        for tool_name, command in matches:
                                            result = await self._execute_fake_tool(tool_name, command, assistant_text, tool_results, mcp_manager)
                                            if result:
                                                yield result
                                    else:
                                        matches = re.findall(pattern, assistant_text)
                                        for command in matches:
                                            result = await self._execute_fake_tool('mcp_run_shell_command', command, assistant_text, tool_results, mcp_manager)
                                            if result:
                                                yield result
                            
                            # Only yield text if it doesn't contain fake tool calls
                            if not ('```tool:' in text):
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
                                
                                # Update the corresponding entry in all_tool_calls with parsed arguments
                                for tool_call in all_tool_calls:
                                    if tool_call['id'] == tool_id:
                                        tool_call['args'] = args
                                        break
                                
                                actual_tool_name = tool_name[4:] if tool_name.startswith('mcp_') else tool_name
                                
                                # Handle both run_shell_command and mcp_run_shell_command
                                if actual_tool_name in ['run_shell_command'] or tool_name in ['mcp_run_shell_command']:
                                    actual_tool_name = 'run_shell_command'
                                
                                # Create signature to detect duplicates
                                tool_signature = f"{actual_tool_name}:{json.dumps(args, sort_keys=True)}"
                                
                                # Skip if we've already executed this exact tool call
                                if tool_signature in executed_tool_signatures:
                                    logger.info(f"🔍 DUPLICATE_TOOL_SKIP: Skipping duplicate {actual_tool_name} with args {args}")
                                    completed_tools.add(tool_id)
                                    continue
                                
                                # For shell commands, check for similar recent commands
                                if actual_tool_name == 'run_shell_command':
                                    command = args.get('command', '')
                                    # Normalize command for similarity check
                                    normalized_cmd = ' '.join(command.split())
                                    
                                    # Check if a very similar command was run recently
                                    for recent_cmd in recent_commands:
                                        if self._commands_similar(normalized_cmd, recent_cmd):
                                            logger.info(f"🔍 SIMILAR_COMMAND_SKIP: Skipping similar command: {command}")
                                            completed_tools.add(tool_id)
                                            # Skip this tool entirely - don't execute it
                                            break
                                    else:
                                        # Only execute if we didn't break (no similar command found)
                                        recent_commands.append(normalized_cmd)
                                        # Keep only last 5 commands to prevent memory bloat
                                        if len(recent_commands) > 5:
                                            recent_commands.pop(0)
                                        
                                        executed_tool_signatures.add(tool_signature)
                                        
                                        # Execute the tool only if it's not similar to recent commands
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
                                        
                                        tools_executed_this_iteration = True  # Mark that tools were executed
                                completed_tools.add(tool_id)

                            except Exception as e:
                                error_msg = f"Tool error: {str(e)}"
                                yield {'type': 'error', 'content': error_msg}

                    elif chunk['type'] == 'message_stop':
                        # Just break out of chunk processing, handle completion logic below
                        break

                # Add assistant response to conversation
                if assistant_text.strip():
                    conversation.append({"role": "assistant", "content": assistant_text})

                # Skip duplicate execution - tools are already executed in content_block_stop
                # This section was causing duplicate tool execution

                # Add tool results and continue iteration
                if tool_results:
                    logger.info(f"🔍 TOOL_RESULTS_PROCESSING: Adding {len(tool_results)} tool results to conversation for iteration {iteration + 1}")
                    for tool_result in tool_results:
                        raw_result = tool_result['result']
                        if isinstance(raw_result, str) and '$ ' in raw_result:
                            lines = raw_result.split('\n')
                            clean_lines = [line for line in lines if not line.startswith('$ ')]
                            raw_result = '\n'.join(clean_lines).strip()
                        
                        conversation.append({
                            "role": "user", 
                            "content": f"Tool execution completed. Result: {raw_result}"
                        })
                    
                    logger.info(f"🔍 CONTINUING_ITERATION: Moving to iteration {iteration + 1} to let model respond to tool results")
                    continue
                else:
                    # No tools executed - end the stream
                    if assistant_text.strip():
                        logger.info(f"🔍 STREAM_END: Model produced text without tools, ending stream")
                        yield {'type': 'stream_end'}
                        break
                    elif iteration >= 2:  # Safety: end after 2 failed iterations
                        yield {'type': 'stream_end'}
                        break
                    else:
                        continue

            except Exception as e:
                yield {'type': 'error', 'content': f'Error: {e}'}
                return
