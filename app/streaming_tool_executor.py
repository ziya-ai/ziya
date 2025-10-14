#!/usr/bin/env python3
import asyncio
import json
import re
import boto3
import logging
import os
import time
from typing import Dict, Any, List, AsyncGenerator, Optional
from app.utils.conversation_filter import filter_conversation_for_model

logger = logging.getLogger(__name__)

class StreamingToolExecutor:
    def __init__(self, profile_name: str = 'ziya', region: str = 'us-west-2', model_id: str = None):
        self.model_id = model_id or os.environ.get('DEFAULT_MODEL_ID', 'us.anthropic.claude-sonnet-4-20250514-v1:0')
        
        # Only initialize Bedrock client for Bedrock endpoints
        from app.agents.models import ModelManager
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        model_name = os.environ.get("ZIYA_MODEL")
        self.model_config = ModelManager.get_model_config(endpoint, model_name)
        
        if endpoint == "bedrock":
            # Use ModelManager's wrapped bedrock client for proper extended context handling
            try:
                self.bedrock = ModelManager._get_persistent_bedrock_client(
                    aws_profile=profile_name,
                    region=region,
                    model_id=self.model_id,
                    model_config=self.model_config
                )
                logger.info(f"🔍 Using ModelManager's wrapped bedrock client with extended context support")
            except Exception as e:
                logger.warning(f"🔍 Could not get wrapped client, falling back to direct client: {e}")
                # Fallback to direct client creation
                session = boto3.Session(profile_name=profile_name)
                self.bedrock = session.client('bedrock-runtime', region_name=region)
        else:
            # Non-Bedrock endpoints don't need a bedrock client
            self.bedrock = None
            logger.info(f"🔍 Skipping Bedrock client initialization for endpoint: {endpoint}")

    def _convert_tool_schema(self, tool):
        """Convert tool schema to JSON-serializable format"""
        if isinstance(tool, dict):
            # Already a dict, but check input_schema
            result = tool.copy()
            input_schema = result.get('input_schema')
            if isinstance(input_schema, dict):
                # Already a dict, use as-is
                pass
            elif hasattr(input_schema, 'model_json_schema'):
                # Pydantic class - convert to JSON schema
                result['input_schema'] = input_schema.model_json_schema()
            elif input_schema is not None:
                # Some other object - try to convert
                try:
                    result['input_schema'] = input_schema.model_json_schema()
                except:
                    logger.warning(f"🔍 TOOL_SCHEMA: Could not convert input_schema, using fallback")
                    result['input_schema'] = {"type": "object", "properties": {}}
            return result
        else:
            # Tool object - extract properties
            name = getattr(tool, 'name', 'unknown')
            description = getattr(tool, 'description', 'No description')
            input_schema = getattr(tool, 'input_schema', getattr(tool, 'inputSchema', {}))
            
            logger.info(f"🔍 TOOL_SCHEMA: Converting tool '{name}', input_schema type: {type(input_schema)}")
            
            # Handle different input_schema types
            if isinstance(input_schema, dict):
                # Already a dict, use as-is
                logger.info(f"🔍 TOOL_SCHEMA: Tool '{name}' has dict schema with keys: {list(input_schema.keys())}")
            elif hasattr(input_schema, 'model_json_schema'):
                # Pydantic class - convert to JSON schema
                input_schema = input_schema.model_json_schema()
                logger.info(f"🔍 TOOL_SCHEMA: Converted Pydantic schema for '{name}'")
            elif input_schema:
                # Some other object - try to convert
                try:
                    input_schema = input_schema.model_json_schema()
                    logger.info(f"🔍 TOOL_SCHEMA: Converted object schema for '{name}'")
                except:
                    logger.warning(f"🔍 TOOL_SCHEMA: Failed to convert schema for '{name}', using empty schema")
                    input_schema = {"type": "object", "properties": {}}
            else:
                logger.warning(f"🔍 TOOL_SCHEMA: Tool '{name}' has no input_schema, using empty schema")
                input_schema = {"type": "object", "properties": {}}
            
            result = {
                'name': name,
                'description': description,
                'input_schema': input_schema
            }
            logger.info(f"🔍 TOOL_SCHEMA: Final schema for '{name}': {json.dumps(result, indent=2)}")
            return result

    def _commands_similar(self, cmd1: str, cmd2: str) -> bool:
        """Check if two shell commands are functionally similar"""
        # Only consider commands similar if they are nearly identical
        # Remove minor variations like different head counts
        def normalize(cmd):
            return cmd.replace('head -20', 'head').replace('head -30', 'head').replace(' | head', '').strip()
        
        norm1, norm2 = normalize(cmd1), normalize(cmd2)
        
        # Only consider exact matches as similar to avoid blocking legitimate exploration
        return norm1 == norm2

    def _get_text_after_last_structured_content(self, text: str) -> str:
        """Get text that appears after the last tool result, diff block, or code block."""
        # Find the last occurrence of structured content markers
        last_positions = []
        
        # Check for tool blocks
        tool_pattern = r'```?```'
        for match in re.finditer(tool_pattern, text, re.DOTALL):
            last_positions.append(match.end())
        
        # Check for diff blocks  
        diff_pattern = r'```diff.*?```'
        for match in re.finditer(diff_pattern, text, re.DOTALL):
            last_positions.append(match.end())
            
        # Check for any code blocks
        code_pattern = r'```.*?```'
        for match in re.finditer(code_pattern, text, re.DOTALL):
            last_positions.append(match.end())
        
        if last_positions:
            # Return text after the last structured content block
            last_pos = max(last_positions)
            return text[last_pos:].strip()
        else:
            # No structured content found, return the entire text
            return text.strip()

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

    async def stream_with_tools(self, messages: List[Dict[str, Any]], tools: Optional[List] = None, conversation_id: Optional[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
        # Initialize streaming metrics
        stream_metrics = {
            'events_sent': 0,
            'bytes_sent': 0,
            'chunk_sizes': [],
            'start_time': time.time()
        }
        
        def track_yield(event_data):
            """Track metrics for yielded events"""
            chunk_size = len(json.dumps(event_data))
            stream_metrics['events_sent'] += 1
            stream_metrics['bytes_sent'] += chunk_size
            stream_metrics['chunk_sizes'].append(chunk_size)
            
            if stream_metrics['events_sent'] % 100 == 0:
                logger.info(f"📊 Stream metrics: {stream_metrics['events_sent']} events, "
                           f"{stream_metrics['bytes_sent']} bytes, "
                           f"avg={stream_metrics['bytes_sent']/stream_metrics['events_sent']:.2f}")
            return event_data
        
        # Extended context handling for sonnet4.5
        if conversation_id:
            logger.info(f"🔍 EXTENDED_CONTEXT: Processing conversation_id = {conversation_id}")
            # Set conversation_id in custom_bedrock module global so CustomBedrockClient can use it
            try:
                import app.utils.custom_bedrock as custom_bedrock_module
                custom_bedrock_module._current_conversation_id = conversation_id
                logger.info(f"🔍 EXTENDED_CONTEXT: Set module global conversation_id")
            except Exception as e:
                logger.warning(f"🔍 EXTENDED_CONTEXT: Could not set conversation_id: {e}")
        
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
        using_extended_context = False  # Track if we've enabled extended context
        consecutive_empty_tool_calls = 0  # Track empty tool calls to break loops
        
        for iteration in range(50):  # Increased from 20 to support more complex tasks
            logger.info(f"🔍 ITERATION_START: Beginning iteration {iteration}")
            
            # Log last 2 messages to debug conversation state
            if len(conversation) >= 2:
                for i, msg in enumerate(conversation[-2:]):
                    role = msg.get('role', msg.get('type', 'unknown'))
                    content = msg.get('content', '')
                    content_preview = str(content)[:150] if content else 'empty'
                    logger.info(f"🔍 CONV_DEBUG: Message -{2-i}: role={role}, content_preview={content_preview}")
            
            tools_executed_this_iteration = False  # Track if tools were executed in this iteration
            blocked_tools_this_iteration = 0  # Track blocked tools to prevent runaway loops
            commands_this_iteration = []  # Track commands executed in this specific iteration
            empty_tool_calls_this_iteration = 0  # Track empty tool calls in this iteration
            
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4000,
                "messages": conversation
            }

            if system_content:
                # With precision prompts, system content is already clean - no regex needed
                logger.info(f"🔍 SYSTEM_DEBUG: Using clean system content length: {len(system_content)}")
                logger.info(f"🔍 SYSTEM_DEBUG: File count in system content: {system_content.count('File:')}")
                
                system_text = system_content + "\n\nCRITICAL: Use ONLY native tool calling. Never generate markdown like ```tool:mcp_run_shell_command or ```bash. Use the provided tools directly.\n\nIMPORTANT: Only use tools when you need to interact with the system (run commands, check time, etc). If you can answer from the provided context or your reasoning, do so directly without using tools. Don't use echo commands just to show your thinking - just answer directly."
                
                # Use prompt caching for large system prompts to speed up iterations
                if len(system_text) > 1024:
                    body["system"] = [
                        {
                            "type": "text",
                            "text": system_text,
                            "cache_control": {"type": "ephemeral"}
                        }
                    ]
                    logger.info(f"🔍 CACHE: Enabled prompt caching for {len(system_text)} char system prompt")
                else:
                    body["system"] = system_text
                
                logger.info(f"🔍 SYSTEM_DEBUG: Final system prompt length: {len(system_text)}")
                logger.info(f"🔍 SYSTEM_CONTENT_DEBUG: First 500 chars of system prompt: {system_text[:500]}")
                logger.info(f"🔍 SYSTEM_CONTENT_DEBUG: System prompt contains 'File:' count: {system_text.count('File:')}")
                logger.info(f"🔍 SYSTEM_CONTENT_DEBUG: Last 500 chars of system prompt: {system_text[-500:]}")
            
            # If we've already enabled extended context, keep using it
            if using_extended_context and self.model_config:
                header_value = self.model_config.get('extended_context_header')
                if header_value:
                    body['anthropic_beta'] = [header_value]
                    logger.info(f"🔍 EXTENDED_CONTEXT: Continuing with extended context header")

            if bedrock_tools:
                # Don't send tools if we've had too many consecutive empty calls
                if consecutive_empty_tool_calls >= 5:
                    logger.warning(f"🔍 TOOL_SUPPRESSION: Suppressing tools due to {consecutive_empty_tool_calls} consecutive empty calls")
                    # Don't add tools to body - force model to respond without them
                else:
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
                        api_params = {
                            'modelId': self.model_id,
                            'body': json.dumps(body)
                        }
                        
                        response = self.bedrock.invoke_model_with_response_stream(**api_params)
                        break  # Success, exit retry loop
                    except Exception as e:
                        error_str = str(e)
                        is_rate_limit = ("Too many tokens" in error_str or 
                                       "ThrottlingException" in error_str or
                                       "Too many requests" in error_str)
                        is_context_limit = "Input is too long" in error_str or "too large" in error_str
                        
                        # On context limit error, enable extended context and retry
                        if is_context_limit and not using_extended_context and self.model_config:
                            if self.model_config.get('supports_extended_context'):
                                header_value = self.model_config.get('extended_context_header')
                                if header_value:
                                    logger.info(f"🔍 EXTENDED_CONTEXT: Context limit hit, enabling extended context with header {header_value}")
                                    body['anthropic_beta'] = [header_value]
                                    api_params['body'] = json.dumps(body)
                                    using_extended_context = True  # Set flag to keep using it
                                    try:
                                        response = self.bedrock.invoke_model_with_response_stream(**api_params)
                                        break
                                    except Exception as retry_error:
                                        logger.error(f"🔍 EXTENDED_CONTEXT: Retry with extended context failed: {retry_error}")
                                        raise
                        
                        if is_rate_limit and retry_attempt < max_retries:
                            # Exponential backoff with longer delays to allow token bucket refill
                            # boto3 already did fast retries, so we need longer waits
                            delay = base_delay * (2 ** retry_attempt) + 4  # Add 4s base to account for boto3 retries
                            logger.warning(f"Rate limit hit, retrying in {delay}s (attempt {retry_attempt + 1}/{max_retries + 1})")
                            await asyncio.sleep(delay)
                        else:
                            raise  # Re-raise if not rate limit or max retries exceeded

                # Process this iteration's stream - collect ALL tool calls first
                assistant_text = ""
                tool_results = []
                tool_use_blocks = []  # Store actual tool_use blocks from Bedrock
                yielded_text_length = 0  # Track how much text we've yielded
                all_tool_calls = []  # Collect all tool calls from this response
                
                active_tools = {}
                completed_tools = set()
                expected_tools = set()
                skipped_tools = set()  # Track tools we're skipping due to limits
                executed_tool_signatures = set()  # Track tool name + args to prevent duplicates
                
                # Timeout protection - use configured timeout from shell config
                last_activity_time = time.time()
                from app.config.shell_config import DEFAULT_SHELL_CONFIG
                chunk_timeout = int(os.environ.get('COMMAND_TIMEOUT', DEFAULT_SHELL_CONFIG["timeout"]))

                # Initialize content buffer and visualization detector
                content_buffer = ""
                viz_buffer = ""  # Track potential visualization blocks
                in_viz_block = False
                
                # Code block continuation tracking
                code_block_tracker = {
                    'in_block': False,
                    'block_type': None,
                    'accumulated_content': ''
                }
                
                for event in response['body']:
                    # Timeout protection - only timeout if NO activity for chunk_timeout seconds
                    if time.time() - last_activity_time > chunk_timeout:
                        logger.warning(f"🚨 STREAM TIMEOUT after {chunk_timeout}s of inactivity - ending this iteration")
                        # Add timeout message to assistant text so model knows what happened
                        if not assistant_text.strip():
                            assistant_text = f"[Stream timeout after {chunk_timeout}s - no response received]"
                        break  # Break from chunk loop, but continue to next iteration
                    
                    # Reset activity timer on any event
                    last_activity_time = time.time()
                        
                    chunk = json.loads(event['chunk']['bytes'])

                    if chunk['type'] == 'content_block_start':
                        content_block = chunk.get('content_block', {})
                        logger.info(f"🔍 CHUNK_DEBUG: content_block_start - type: {content_block.get('type')}, id: {content_block.get('id')}")
                        if content_block.get('type') == 'tool_use':
                            # FLUSH any buffered content before tool starts
                            if hasattr(self, '_content_optimizer'):
                                remaining = self._content_optimizer.flush_remaining()
                                if remaining:
                                    yield track_yield({
                                        'type': 'text',
                                        'content': remaining,
                                        'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                    })
                            if content_buffer.strip():
                                yield track_yield({
                                    'type': 'text',
                                    'content': content_buffer,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                })
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
                                            yield track_yield({
                                                'type': 'text',
                                                'content': remaining,
                                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                            })
                                    if content_buffer.strip():
                                        yield track_yield({
                                            'type': 'text',
                                            'content': content_buffer,
                                            'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                        })
                                        content_buffer = ""
                                    
                                    # Send complete visualization block
                                    yield track_yield({
                                        'type': 'text',
                                        'content': viz_buffer,
                                        'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                    })
                                    viz_buffer = ""
                                    in_viz_block = False
                                continue
                            
                            # Use content optimizer to prevent mid-word splits
                            for optimized_chunk in self._content_optimizer.add_content(text):
                                self._update_code_block_tracker(optimized_chunk, code_block_tracker)
                                yield track_yield({
                                    'type': 'text',
                                    'content': optimized_chunk,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                })
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
                            
                            logger.info(f"🔍 TOOL_ARGS: Tool '{tool_name}' (id: {tool_id}) has args_json: '{args_json}'")

                            try:
                                args = json.loads(args_json) if args_json.strip() else {}
                                
                                # Detect empty tool calls for tools that require arguments
                                actual_tool_name = tool_name[4:] if tool_name.startswith('mcp_') else tool_name
                                if actual_tool_name == 'run_shell_command' and not args.get('command'):
                                    logger.warning(f"🔍 EMPTY_TOOL_CALL: Model called {tool_name} without required 'command' argument")
                                    logger.warning(f"🔍 EMPTY_TOOL_CONTEXT: Assistant text before call: '{assistant_text[-200:]}'")
                                    empty_tool_calls_this_iteration += 1
                                    consecutive_empty_tool_calls += 1
                                    
                                    # Return helpful error immediately without executing
                                    error_result = f"Error: Tool call failed - the 'command' parameter is required but was not provided. You must call run_shell_command with a JSON object containing the command string. Example: {{\"command\": \"ls -la\"}}. Please retry with the correct format."
                                    
                                    tool_results.append({
                                        'tool_id': tool_id,
                                        'tool_name': tool_name,
                                        'result': error_result
                                    })
                                    
                                    completed_tools.add(tool_id)
                                    tools_executed_this_iteration = True
                                    logger.info(f"🔍 TOOL_EXECUTED_FLAG: Set tools_executed_this_iteration = True for tool {tool_id}")
                                    continue
                                
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
                            self._update_code_block_tracker(viz_buffer, code_block_tracker)
                            yield track_yield({
                                'type': 'text',
                                'content': viz_buffer,
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                            })
                        # Flush any remaining content from optimizer
                        if hasattr(self, '_content_optimizer'):
                            remaining = self._content_optimizer.flush_remaining()
                            if remaining:
                                self._update_code_block_tracker(remaining, code_block_tracker)
                                yield track_yield({
                                    'type': 'text',
                                    'content': remaining,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                })
                        if content_buffer.strip():
                            self._update_code_block_tracker(content_buffer, code_block_tracker)
                            yield track_yield({
                                'type': 'text',
                                'content': content_buffer,
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                            })
                        
                        # Check if we ended mid-code-block and auto-continue
                        continuation_count = 0
                        max_continuations = 10  # Increased for large diagrams/code blocks
                        
                        # Log tracker state before checking
                        backtick_count = assistant_text.count('```')
                        logger.info(f"🔍 TRACKER_STATE: in_block={code_block_tracker['in_block']}, block_type={code_block_tracker.get('block_type')}, backtick_count={backtick_count}, last_50_chars='{assistant_text[-50:]}'")
                        
                        while code_block_tracker['in_block'] and continuation_count < max_continuations:
                            continuation_count += 1
                            logger.info(f"🔄 INCOMPLETE_BLOCK: Detected incomplete {code_block_tracker['block_type']} block, auto-continuing (attempt {continuation_count})")
                            
                            # Mark rewind boundary before auto-continuation
                            assistant_lines = assistant_text.split('\n')
                            last_complete_line = len(assistant_lines) - 2 if assistant_lines[-1].strip() == '' else len(assistant_lines) - 1
                            partial_content = assistant_lines[-1] if assistant_lines else ""
                            rewind_marker = f"<!-- REWIND_MARKER: {last_complete_line}|PARTIAL:{partial_content} -->"
                            yield track_yield({
                                'type': 'text',
                                'content': f"{rewind_marker}\n**🔄 Block continues...**\n",
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                            })
                            
                            # Send heartbeat before continuation to keep connection alive
                            yield {
                                'type': 'heartbeat',
                                'heartbeat': True,
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                            }
                            
                            continuation_had_content = False
                            async for continuation_chunk in self._continue_incomplete_code_block(
                                conversation, code_block_tracker, mcp_manager, iteration_start_time, assistant_text
                            ):
                                if continuation_chunk.get('content'):
                                    continuation_had_content = True
                                    self._update_code_block_tracker(continuation_chunk['content'], code_block_tracker)
                                    assistant_text += continuation_chunk['content']
                                    
                                    if code_block_tracker['in_block']:
                                        continuation_chunk['code_block_continuation'] = True
                                        continuation_chunk['block_type'] = code_block_tracker['block_type']
                                
                                yield continuation_chunk
                            
                            if not continuation_had_content:
                                logger.info("🔄 CONTINUATION: No content generated, stopping continuation attempts")
                                break
                            
                            # Log tracker state after continuation
                            logger.info(f"🔄 CONTINUATION_RESULT: After attempt {continuation_count}, in_block={code_block_tracker['in_block']}, had_content={continuation_had_content}")
                        
                        # Just break out of chunk processing, handle completion logic below
                        break

                # Add assistant response to conversation with proper tool_use blocks
                if assistant_text.strip() or tools_executed_this_iteration:
                    # Build content as list with text and tool_use blocks
                    content_blocks = []
                    if assistant_text.strip():
                        content_blocks.append({"type": "text", "text": assistant_text})
                    
                    # Add tool_use blocks for each tool that was executed with actual args
                    for tool_result in tool_results:
                        # Find the corresponding tool call to get the actual args
                        tool_args = {}
                        for tool_call in all_tool_calls:
                            if tool_call['id'] == tool_result['tool_id']:
                                tool_args = tool_call.get('args', {})
                                break
                        
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tool_result['tool_id'],
                            "name": tool_result['tool_name'],
                            "input": tool_args
                        })
                    
                    conversation.append({"role": "assistant", "content": content_blocks})
            
                # Add tool results to conversation BEFORE filtering
                logger.info(f"🔍 ITERATION_END_CHECK: tools_executed_this_iteration = {tools_executed_this_iteration}, tool_results count = {len(tool_results)}")
                if tools_executed_this_iteration:
                    logger.info(f"🔍 TOOL_RESULTS_PROCESSING: Adding {len(tool_results)} tool results to conversation")
                    for tool_result in tool_results:
                        raw_result = tool_result['result']
                        if isinstance(raw_result, str) and '$ ' in raw_result:
                            lines = raw_result.split('\n')
                            clean_lines = [line for line in lines if not line.startswith('$ ')]
                            raw_result = '\n'.join(clean_lines).strip()
                        
                        # Add in tool_result_for_model format so filter can convert to proper Bedrock format
                        conversation.append({
                            'type': 'tool_result_for_model',
                            'tool_use_id': tool_result['tool_id'],
                            'content': raw_result
                        })
                
                # Filter conversation to convert tool results to proper format
                original_length = len(conversation)
                conversation = filter_conversation_for_model(conversation)
                logger.info(f"🤖 MODEL_RESPONSE: {assistant_text}")
                logger.info(f"Filtered conversation: {original_length} -> {len(conversation)} messages")

                # Skip duplicate execution - tools are already executed in content_block_stop
                # This section was causing duplicate tool execution

                # Continue to next iteration if tools were executed
                if tools_executed_this_iteration:
                    # Warn about consecutive empty tool calls but don't break
                    if consecutive_empty_tool_calls >= 5:
                        logger.warning(f"🔍 EMPTY_TOOL_WARNING: {consecutive_empty_tool_calls} consecutive empty tool calls detected")
                        # Add a message to guide the model to respond without tools
                        conversation.append({
                            "role": "user",
                            "content": "Please provide your response based on the information available. Do not attempt to use tools."
                        })
                    elif consecutive_empty_tool_calls >= 3:
                        logger.warning(f"🔍 EMPTY_TOOL_WARNING: {consecutive_empty_tool_calls} consecutive empty tool calls detected, adding delay")
                        # Add a small delay to slow down the loop
                        await asyncio.sleep(0.5)
                    
                    # Reset consecutive counter if we had successful tool calls
                    if empty_tool_calls_this_iteration == 0:
                        consecutive_empty_tool_calls = 0
                    
                    logger.info(f"🔍 CONTINUING_ROUND: Tool results added, model will continue in same stream (round {iteration + 1})")
                    # Yield heartbeat to flush stream before next iteration
                    yield {'type': 'iteration_continue', 'iteration': iteration + 1}
                    await asyncio.sleep(0)
                    continue  # Immediately start next iteration
                else:
                    # Check if too many tools were blocked (indicates runaway loop)
                    if blocked_tools_this_iteration >= 3:
                        logger.warning(f"🔍 RUNAWAY_LOOP_DETECTED: {blocked_tools_this_iteration} tools blocked in iteration {iteration}, ending stream")
                        yield {'type': 'stream_end'}
                        break
                    
                    # No tools executed - check if we should end the stream
                    if assistant_text.strip():
                        # Check if code block is still incomplete
                        if code_block_tracker.get('in_block'):
                            logger.warning(f"🔍 INCOMPLETE_BLOCK_REMAINING: Code block still incomplete after max continuations, ending stream anyway")
                        
                        # Check if there's already substantial commentary after the last tool/diff/code block
                        text_after_last_block = self._get_text_after_last_structured_content(assistant_text)
                        word_count_after_block = len(text_after_last_block.split()) if text_after_last_block else 0
                        
                        # If we have 20+ words after the last block and it ends properly, consider it complete
                        if (word_count_after_block >= 20 and 
                            text_after_last_block.rstrip().endswith(('.', '!', '?'))):
                            logger.info(f"🔍 COMPLETE_RESPONSE: Found {word_count_after_block} words after last block, ending stream: '{text_after_last_block[-50:]}'")
                            yield {'type': 'stream_end'}
                            break
                        
                        # Otherwise check if we should continue
                        text_end = assistant_text[-200:].strip()
                        suggests_continuation = (
                            text_end.endswith((':')) or  # About to make tool call  
                            assistant_text.endswith('```') or  # Just finished code block - might add explanation
                            word_count_after_block < 20 or  # Not enough commentary yet
                            not text_after_last_block.rstrip().endswith(('.', '!', '?'))  # Doesn't end properly
                        )
                        
                        if suggests_continuation and iteration < 5:
                            logger.info(f"🔍 CONTINUE_RESPONSE: Only {word_count_after_block} words after last block, continuing: '{text_after_last_block[-30:] if text_after_last_block else text_end}'")
                            continue
                        else:
                            logger.info(f"🔍 STREAM_END: Model produced text without tools, ending stream")
                            # Log final metrics
                            logger.info(f"📊 Final stream metrics: events={stream_metrics['events_sent']}, "
                                       f"bytes={stream_metrics['bytes_sent']}, "
                                       f"avg_size={stream_metrics['bytes_sent']/max(stream_metrics['events_sent'],1):.2f}, "
                                       f"min={min(stream_metrics['chunk_sizes']) if stream_metrics['chunk_sizes'] else 0}, "
                                       f"max={max(stream_metrics['chunk_sizes']) if stream_metrics['chunk_sizes'] else 0}, "
                                       f"duration={time.time()-stream_metrics['start_time']:.2f}s")
                            yield {'type': 'stream_end'}
                            break
                    elif iteration >= 5:  # Safety: end after 5 iterations total
                        logger.info(f"🔍 MAX_ITERATIONS: Reached maximum iterations ({iteration}), ending stream")
                        yield {'type': 'stream_end'}
                        break
                    else:
                        continue

            except Exception as e:
                logger.error(f"Error in stream_with_tools iteration {iteration}: {str(e)}", exc_info=True)
                yield {'type': 'error', 'content': f'Error: {e}'}
                return

    def _update_code_block_tracker(self, text: str, tracker: Dict[str, Any]) -> None:
        """Update code block tracking state based on text content."""
        if not text:
            return
            
        lines = text.split('\n')
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('```'):
                if not tracker['in_block']:
                    # Opening a new block
                    block_type = stripped[3:].strip() or 'code'
                    tracker['in_block'] = True
                    tracker['block_type'] = block_type
                    tracker['accumulated_content'] = line + '\n'
                    logger.debug(f"🔍 TRACKER: Opened {block_type} block")
                else:
                    # Closing the current block - any ``` closes it
                    # Don't require type to match since closing ``` often has no type
                    tracker['in_block'] = False
                    tracker['block_type'] = None
                    tracker['accumulated_content'] = ''
                    logger.debug(f"🔍 TRACKER: Closed block")
            elif tracker['in_block']:
                tracker['accumulated_content'] += line + '\n'

    async def _continue_incomplete_code_block(
        self, 
        conversation: List[Dict[str, Any]], 
        code_block_tracker: Dict[str, Any],
        mcp_manager,
        start_time: float,
        assistant_text: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Continue an incomplete code block by making a new API call."""
        try:
            block_type = code_block_tracker['block_type']
            # Preserve diff context in continuation prompt
            if block_type == 'diff':
                continuation_prompt = f"Continue the incomplete diff block from where it left off. Maintain all + and - line prefixes. Output ONLY the continuation of the diff content, preserving the exact diff format."
            else:
                continuation_prompt = f"Continue the incomplete {block_type} code block from where it left off and close it with ```. Output ONLY the continuation of the code block, no explanations."
            
            continuation_conversation = conversation.copy()
            
            # Remove incomplete last line
            if assistant_text.strip():
                lines = assistant_text.split('\n')
                if len(lines) > 1:
                    last_line = lines[-1].strip()
                    if not last_line or ('```' in last_line and not last_line.endswith('```')):
                        cleaned_text = '\n'.join(lines[:-1])
                        logger.info(f"🔄 CONTEXT_CLEANUP: Removed incomplete last line: '{last_line}'")
                    else:
                        cleaned_text = assistant_text
                    
                    if continuation_conversation and continuation_conversation[-1].get('role') == 'assistant':
                        # Update the last assistant message with cleaned text in proper format
                        continuation_conversation[-1]['content'] = [{"type": "text", "text": cleaned_text}]
                    else:
                        continuation_conversation.append({"role": "assistant", "content": [{"type": "text", "text": cleaned_text}]})
            
            continuation_conversation.append({"role": "user", "content": continuation_prompt})
            
            body = {
                "messages": continuation_conversation,
                "max_tokens": 2000,
                "temperature": 0.1,
                "anthropic_version": "bedrock-2023-05-31"
            }
            
            logger.info(f"🔄 CONTINUATION: Making API call to continue {block_type} block")
            
            # Yield initial heartbeat
            yield {
                'type': 'heartbeat',
                'heartbeat': True,
                'timestamp': f"{int((time.time() - start_time) * 1000)}ms"
            }
            
            # Make the Bedrock call - this returns immediately with a stream
            response = self.bedrock.invoke_model_with_response_stream(
                modelId=self.model_id,
                body=json.dumps(body)
            )
            
            # Send heartbeat after getting response object (before first chunk)
            yield {
                'type': 'heartbeat',
                'heartbeat': True,
                'timestamp': f"{int((time.time() - start_time) * 1000)}ms"
            }
            
            accumulated_start = ""
            header_filtered = False
            chunk_count = 0
            
            for event in response['body']:
                # Send heartbeat every 10 chunks to keep connection alive
                chunk_count += 1
                if chunk_count % 10 == 0:
                    yield {
                        'type': 'heartbeat',
                        'heartbeat': True,
                        'timestamp': f"{int((time.time() - start_time) * 1000)}ms"
                    }
                
                chunk = json.loads(event['chunk']['bytes'])
                
                if chunk['type'] == 'content_block_delta':
                    delta = chunk.get('delta', {})
                    if delta.get('type') == 'text_delta':
                        text = delta.get('text', '')
                        
                        if not header_filtered:
                            accumulated_start += text
                            
                            if '\n' in accumulated_start or len(accumulated_start) > 20:
                                if accumulated_start.strip().startswith('```'):
                                    lines = accumulated_start.split('\n', 1)
                                    if len(lines) > 1:
                                        remaining_text = '\n' + lines[1]  # Preserve the newline
                                        header_type = lines[0].strip()
                                        logger.info(f"🔄 FILTERED: Removed redundant {header_type} from continuation")
                                    else:
                                        remaining_text = ""
                                    
                                    if remaining_text:
                                        yield {
                                            'type': 'text',
                                            'content': remaining_text,
                                            'timestamp': f"{int((time.time() - start_time) * 1000)}ms",
                                            'continuation': True
                                        }
                                else:
                                    yield {
                                        'type': 'text',
                                        'content': accumulated_start,
                                        'timestamp': f"{int((time.time() - start_time) * 1000)}ms",
                                        'continuation': True
                                    }
                                
                                header_filtered = True
                        else:
                            if text:
                                yield {
                                    'type': 'text',
                                    'content': text,
                                    'timestamp': f"{int((time.time() - start_time) * 1000)}ms",
                                    'continuation': True
                                }
        
        except Exception as e:
            logger.error(f"🔄 CONTINUATION: Error in continuation: {e}")
