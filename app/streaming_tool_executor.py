#!/usr/bin/env python3
import asyncio
import json
import boto3
import logging
import os
import time
from typing import Dict, Any, List, AsyncGenerator, Optional
from app.utils.conversation_filter import filter_conversation_for_model

logger = logging.getLogger(__name__)

class StreamingToolExecutor:
    def __init__(self, profile_name: str = 'ziya', region: str = 'us-west-2', model_id: str = None):
        session = boto3.Session(profile_name=profile_name)
        self.bedrock = session.client('bedrock-runtime', region_name=region)
        self.model_id = model_id or os.environ.get('DEFAULT_MODEL_ID', 'us.anthropic.claude-sonnet-4-20250514-v1:0')

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
                    'type': 'tool_display',
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

        logger.info(f"🔍 STREAMING_TOOL_EXECUTOR: Received {len(messages)} messages")
        for i, msg in enumerate(messages):
            # Handle both dict format and LangChain message objects
            if hasattr(msg, 'type') and hasattr(msg, 'content'):
                # LangChain message object
                role = msg.type if msg.type != 'human' else 'user'
                content = msg.content
            elif isinstance(msg, str):
                # String format - treat as user message
                role = 'user'
                content = msg
            else:
                # Dict format
                role = msg.get('role', '')
                content = msg.get('content', '')
            
            logger.info(f"🔍 STREAMING_TOOL_EXECUTOR: Message {i}: role={role}, content_length={len(content)}")
            if role == 'system':
                system_content = content
                logger.info(f"🔍 STREAMING_TOOL_EXECUTOR: Found system message with {len(content)} characters")
            elif role in ['user', 'assistant', 'ai']:
                # Normalize ai role to assistant for Bedrock
                bedrock_role = 'assistant' if role == 'ai' else role
                conversation.append({"role": bedrock_role, "content": content})

        # Iterative execution with proper tool result handling
        recent_commands = []  # Track recent commands to prevent duplicates
        
        for iteration in range(50):  # Increased from 20 to support more complex tasks
            logger.info(f"🔍 ITERATION_START: Beginning iteration {iteration}")
            tools_executed_this_iteration = False  # Track if tools were executed in this iteration
            blocked_tools_this_iteration = 0  # Track blocked tools to prevent runaway loops
            commands_this_iteration = []  # Track commands executed in this specific iteration
            
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
                # Remove only MCP tools list, not visualization tools
                system_content = re.sub(r'- mcp_[^:]+:[^\n]*\n?', '', system_content)
                body["system"] = system_content + "\n\nCRITICAL: Use ONLY native tool calling. Never generate markdown like ```tool:mcp_run_shell_command or ```bash. Use the provided tools directly."
                logger.info(f"🔍 SYSTEM_DEBUG: Final system prompt length: {len(body['system'])}")

            if bedrock_tools:
                body["tools"] = bedrock_tools
                # Use "auto" to allow model to decide when to stop
                body["tool_choice"] = {"type": "auto"}
                logger.info(f"🔍 TOOL_DEBUG: Sending {len(bedrock_tools)} tools to model: {[t['name'] for t in bedrock_tools]}")

            try:
                # Exponential backoff for rate limiting
                max_retries = 4
                base_delay = 2  # Start with 2 seconds
                iteration_start_time = time.time()
                
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
                
                # Timeout protection - use configured timeout from shell config
                start_time = time.time()
                from app.config.shell_config import DEFAULT_SHELL_CONFIG
                chunk_timeout = int(os.environ.get('COMMAND_TIMEOUT', DEFAULT_SHELL_CONFIG["timeout"]))

                # Initialize content buffer and visualization detector
                content_buffer = ""
                viz_buffer = ""  # Track potential visualization blocks
                in_viz_block = False
                
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
                            # FLUSH any buffered content before tool starts
                            if hasattr(self, '_content_optimizer'):
                                remaining = self._content_optimizer.flush_remaining()
                                if remaining:
                                    yield {
                                        'type': 'text',
                                        'content': remaining,
                                        'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                    }
                            if content_buffer.strip():
                                yield {
                                    'type': 'text',
                                    'content': content_buffer,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                }
                                content_buffer = ""
                            
                            tool_id = content_block.get('id')
                            tool_name = content_block.get('name')
                            if tool_id and tool_name:
                                # Check for duplicates FIRST
                                tool_signature = f"{tool_name}_{tool_id}"
                                if tool_signature in executed_tool_signatures:
                                    logger.info(f"🔍 DUPLICATE_SKIP: Tool {tool_signature} already executed")
                                    skipped_tools.add(chunk.get('index'))
                                    continue
                                
                                # Send tool_start event to frontend only (not to model)
                                # This prevents contamination of model training data
                                
                                # Mark as executed to prevent duplicates
                                executed_tool_signatures.add(tool_signature)
                                
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
                            # DISABLED: This was causing premature execution of incomplete commands
                            if False and (('```tool:' in assistant_text and '```' in assistant_text[assistant_text.find('```tool:') + 8:]) or \
                               ('run_shell_command\n$' in assistant_text and '\n' in assistant_text[assistant_text.find('run_shell_command\n$') + 20:]) or \
                               (':mcp_run_shell_command\n$' in assistant_text and '\n' in assistant_text[assistant_text.find(':mcp_run_shell_command\n$') + 23:])):
                                # Extract and execute fake tool calls with multiple patterns
                                import re
                                patterns = [
                                    r'```tool:(mcp_\w+)\n\$\s*([^`]+)```',  # Full markdown blocks only
                                    r'run_shell_command\n\$\s*([^\n]+)\n',    # Complete lines only
                                    r':mcp_run_shell_command\n\$\s*([^\n]+)\n' # Complete lines only
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
                                
                                for pattern in patterns:
                                    if re.search(pattern, text):
                                        logger.warning(f"🚫 Intercepted fake tool call: {pattern}")
                            if 'tool:' in text:
                                # Skip fake tool patterns
                                continue
                            
                            # Initialize content optimizer if not exists
                            if not hasattr(self, '_content_optimizer'):
                                from app.utils.streaming_optimizer import StreamingContentOptimizer
                                self._content_optimizer = StreamingContentOptimizer()
                            
                            # Skip fake tool patterns
                            if 'tool:' in text:
                                continue
                            
                            # Check for visualization block boundaries - ensure proper markdown format
                            viz_patterns = ['```vega-lite', '```mermaid', '```graphviz', '```d3']
                            if any(pattern in text for pattern in viz_patterns):
                                in_viz_block = True
                                viz_buffer = text
                                continue
                            elif in_viz_block:
                                viz_buffer += text
                                # Check for closing ``` - ensure complete block
                                if '```' in text and viz_buffer.count('```') >= 2:
                                    # Complete visualization block - ensure it ends with newline for proper markdown
                                    if not viz_buffer.endswith('\n'):
                                        viz_buffer += '\n'
                                    
                                    # Flush any pending content first
                                    if hasattr(self, '_content_optimizer'):
                                        remaining = self._content_optimizer.flush_remaining()
                                        if remaining:
                                            yield {
                                                'type': 'text',
                                                'content': remaining,
                                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                            }
                                    if content_buffer.strip():
                                        yield {
                                            'type': 'text',
                                            'content': content_buffer,
                                            'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                        }
                                        content_buffer = ""
                                    
                                    # Send complete visualization block
                                    yield {
                                        'type': 'text',
                                        'content': viz_buffer,
                                        'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                    }
                                    viz_buffer = ""
                                    in_viz_block = False
                                continue
                            
                            # Use content optimizer to prevent mid-word splits
                            for optimized_chunk in self._content_optimizer.add_content(text):
                                yield {
                                    'type': 'text',
                                    'content': optimized_chunk,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                }
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
                                
                                # Execute the tool (already checked for duplicates at collection)
                                logger.info(f"🔍 EXECUTING_TOOL: {actual_tool_name} with args {args}")
                                
                                # Send tool_start event with complete arguments
                                yield {
                                    'type': 'tool_start',
                                    'tool_id': tool_id,
                                    'tool_name': tool_name,
                                    'args': args,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                }
                                
                                # Execute the tool immediately
                                try:
                                    result = await mcp_manager.call_tool(actual_tool_name, args)
                                    
                                    # Process result
                                    if isinstance(result, dict) and result.get('error') and result.get('error') != False:
                                        error_msg = result.get('message', 'Unknown error')
                                        result_text = f"ERROR: {error_msg}. Please try a different approach or fix the command."
                                    elif isinstance(result, dict) and 'content' in result:
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
                                        'type': 'tool_display',
                                        'tool_id': tool_id,
                                        'tool_name': tool_name,
                                        'result': result_text,
                                        'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                    }

                                    # Add clean tool result for model conversation
                                    yield {
                                        'type': 'tool_result_for_model',
                                        'tool_use_id': tool_id,
                                        'content': result_text.strip()
                                    }
                                                    
                                    # Immediate flush to reduce delay
                                    import asyncio
                                    await asyncio.sleep(0)
                                    
                                    tools_executed_this_iteration = True
                                    logger.info(f"🔍 TOOL_EXECUTED_FLAG: Set tools_executed_this_iteration = True for tool {tool_id}")
                                    
                                except Exception as e:
                                    error_msg = f"Tool error: {str(e)}"
                                    logger.error(f"🔍 TOOL_EXECUTION_ERROR: {error_msg}")
                                    tool_results.append({
                                        'tool_id': tool_id,
                                        'tool_name': tool_name,
                                        'result': f"ERROR: {error_msg}. Please try a different approach or fix the command."
                                    })

                                    # Frontend error display
                                    yield {'type': 'tool_display', 'tool_name': tool_name, 'result': f"ERROR: {error_msg}"}
                                    
                                    # Clean error for model
                                    yield {
                                        'type': 'tool_result_for_model',
                                        'tool_use_id': tool_id,
                                        'content': f"ERROR: {error_msg}. Please try a different approach or fix the command."
                                    }
                                completed_tools.add(tool_id)

                            except Exception as e:
                                error_msg = f"Tool error: {str(e)}"
                                
                                # Add error to tool_results so it gets fed back to the model
                                tool_results.append({
                                    'tool_id': tool_id,
                                    'tool_name': tool_name,
                                    'result': f"ERROR: {error_msg}. Please try a different approach or fix the command."
                                })
                                
                                # Frontend error display
                                yield {'type': 'tool_display', 'tool_name': 'unknown', 'result': f"ERROR: {error_msg}"}
                                
                                # Clean error for model
                                yield {
                                    'type': 'tool_result_for_model',
                                    'tool_use_id': tool_id or 'unknown',
                                    'content': f"ERROR: {error_msg}. Please try a different approach or fix the command."
                                }

                    elif chunk['type'] == 'message_stop':
                        # Flush any remaining content from buffers before stopping
                        if viz_buffer.strip():
                            yield {
                                'type': 'text',
                                'content': viz_buffer,
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                            }
                        # Flush any remaining content from optimizer
                        if hasattr(self, '_content_optimizer'):
                            remaining = self._content_optimizer.flush_remaining()
                            if remaining:
                                yield {
                                    'type': 'text',
                                    'content': remaining,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                }
                        if content_buffer.strip():
                            yield {
                                'type': 'text',
                                'content': content_buffer,
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                            }
                        # Just break out of chunk processing, handle completion logic below
                        break

                # Add assistant response to conversation
                if assistant_text.strip():
                    conversation.append({"role": "assistant", "content": assistant_text})
            
                # Filter conversation before next iteration to prevent contamination
                original_length = len(conversation)
                conversation = filter_conversation_for_model(conversation)
                logger.info(f"🤖 MODEL_RESPONSE: {assistant_text}")
                logger.info(f"Filtered conversation: {original_length} -> {len(conversation)} messages")

                # Skip duplicate execution - tools are already executed in content_block_stop
                # This section was causing duplicate tool execution

                # Add tool results to conversation - continue rounds until throttle hit
                logger.info(f"🔍 ITERATION_END_CHECK: tools_executed_this_iteration = {tools_executed_this_iteration}, tool_results count = {len(tool_results)}")
                if tools_executed_this_iteration:
                    logger.info(f"🔍 TOOL_RESULTS_PROCESSING: Adding {len(tool_results)} tool results to conversation")
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
                    
                    logger.info(f"🔍 CONTINUING_ROUND: Tool results added, model will continue in same stream (round {iteration + 1})")
                    # Let model continue in same stream until throttle hit
                else:
                    # Check if too many tools were blocked (indicates runaway loop)
                    if blocked_tools_this_iteration >= 3:
                        logger.warning(f"🔍 RUNAWAY_LOOP_DETECTED: {blocked_tools_this_iteration} tools blocked in iteration {iteration}, ending stream")
                        yield {'type': 'stream_end'}
                        break
                    
                    # No tools executed - check if we should end the stream
                    if assistant_text.strip():
                        # Check if the text suggests the model is about to make a tool call
                        # Only check the last 200 characters to avoid issues with long accumulated text
                        text_end = assistant_text[-200:].lower().strip()
                        suggests_tool_call = text_end.endswith(':')
                        
                        if suggests_tool_call and iteration < 3:  # More conservative limit
                            logger.info(f"🔍 POTENTIAL_TOOL_CALL: Text suggests model wants to make a tool call, continuing: '{assistant_text[-50:]}'")
                            continue
                        else:
                            logger.info(f"🔍 STREAM_END: Model produced text without tools, ending stream")
                            yield {'type': 'stream_end'}
                            break
                    elif iteration >= 5:  # Safety: end after 5 iterations total
                        logger.info(f"🔍 MAX_ITERATIONS: Reached maximum iterations ({iteration}), ending stream")
                        yield {'type': 'stream_end'}
                        break
                    else:
                        continue

            except Exception as e:
                yield {'type': 'error', 'content': f'Error: {e}'}
                return
