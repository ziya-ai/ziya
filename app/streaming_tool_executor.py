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
        # Only initialize Bedrock client for Bedrock endpoints
        from app.agents.models import ModelManager
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        model_name = os.environ.get("ZIYA_MODEL")
        self.model_config = ModelManager.get_model_config(endpoint, model_name)
        
        # Use provided model_id or get from ModelManager (which handles region-specific IDs)
        if model_id:
            self.model_id = model_id
            logger.debug(f"StreamingToolExecutor: Using provided model_id: {self.model_id}")
        else:
            config_model_id = self.model_config.get('model_id') if self.model_config else None
            if config_model_id:
                # Use ModelManager's region-aware resolution
                self.model_id, _ = ModelManager._get_region_specific_model_id_with_region_update(
                    config_model_id, region, self.model_config, model_name
                )
                logger.debug(f"StreamingToolExecutor: Resolved model_id from config: {self.model_id} (config was: {config_model_id})")
            else:
                raise ValueError("No model_id configured. Set ZIYA_MODEL or provide model_id parameter.")
        
        if endpoint == "bedrock":
            # Use ModelManager's wrapped bedrock client for proper extended context handling
            try:
                self.bedrock = ModelManager._get_persistent_bedrock_client(
                    aws_profile=profile_name,
                    region=region,
                    model_id=self.model_id,
                    model_config=self.model_config
                )
                logger.debug(f"🔍 Using ModelManager's wrapped bedrock client with extended context support")
            except Exception as e:
                logger.warning(f"🔍 Could not get wrapped client, falling back to direct client: {e}")
                # Fallback to direct client creation
                session = boto3.Session(profile_name=profile_name)
                self.bedrock = session.client('bedrock-runtime', region_name=region)
        else:
            # Non-Bedrock endpoints don't need a bedrock client
            self.bedrock = None
            logger.debug(f"🔍 Skipping Bedrock client initialization for endpoint: {endpoint}")

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
            
            logger.debug(f"🔍 TOOL_SCHEMA: Converting tool '{name}', input_schema type: {type(input_schema)}")
            
            # Handle different input_schema types
            if isinstance(input_schema, dict):
                # Already a dict, use as-is
                logger.debug(f"🔍 TOOL_SCHEMA: Tool '{name}' has dict schema with keys: {list(input_schema.keys())}")
            elif hasattr(input_schema, 'model_json_schema'):
                # Pydantic class - convert to JSON schema
                input_schema = input_schema.model_json_schema()
                logger.debug(f"🔍 TOOL_SCHEMA: Converted Pydantic schema for '{name}'")
            elif input_schema:
                # Some other object - try to convert
                try:
                    input_schema = input_schema.model_json_schema()
                    logger.debug(f"🔍 TOOL_SCHEMA: Converted object schema for '{name}'")
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
            logger.debug(f"🔍 TOOL_SCHEMA: Final schema for '{name}': {json.dumps(result, indent=2)}")
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

    def _format_tool_result(self, tool_name: str, result_text: str, args: dict) -> str:
        """Format tool result based on tool type."""
        actual_tool_name = tool_name[4:] if tool_name.startswith('mcp_') else tool_name
        
        if actual_tool_name == 'run_shell_command':
            # For shell commands, return result as-is - frontend will add command to header
            return result_text
        elif actual_tool_name == 'get_current_time':
            # For time tool, clean up the result format
            clean_result = result_text
            # Remove "Input: {}" prefix if present
            clean_result = clean_result.replace('Input: {}\n\nResult:\n', '').strip()
            clean_result = clean_result.replace('Input: {}\n\n', '').strip()
            clean_result = clean_result.replace('Result:\n', '').strip()
            # Remove any remaining "Result:" prefix
            if clean_result.startswith('Result:'):
                clean_result = clean_result[7:].strip()
            return clean_result
        else:
            # For other tools, return result as-is
            return result_text
    
    def _get_tool_header(self, tool_name: str, args: dict) -> str:
        """Get appropriate header for tool display."""
        actual_tool_name = tool_name[4:] if tool_name.startswith('mcp_') else tool_name
        
        if actual_tool_name == 'run_shell_command':
            return 'Shell Command'
        elif actual_tool_name == 'get_current_time':
            return 'Current Time'
        else:
            return actual_tool_name.replace('_', ' ').title()

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
            logger.debug(f"🔍 EXTENDED_CONTEXT: Processing conversation_id = {conversation_id}")
            # Set conversation_id in custom_bedrock module global so CustomBedrockClient can use it
            try:
                import app.utils.custom_bedrock as custom_bedrock_module
                custom_bedrock_module._current_conversation_id = conversation_id
                logger.debug(f"🔍 EXTENDED_CONTEXT: Set module global conversation_id")
            except Exception as e:
                logger.warning(f"🔍 EXTENDED_CONTEXT: Could not set conversation_id: {e}")
        
        # Get MCP tools
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        if not mcp_manager.is_initialized:
            await mcp_manager.initialize()
        from app.mcp.enhanced_tools import DirectMCPTool

        # Get ALL tools (both MCP server tools and builtin tools)
        from app.mcp.enhanced_tools import create_secure_mcp_tools
        all_tools = create_secure_mcp_tools()
        
        # Separate builtin from external MCP tools for proper naming
        builtin_tool_names = {tool.name for tool in all_tools if isinstance(tool, DirectMCPTool)}
        
        logger.info(f"🔍 TOOL_LOADING: Total tools={len(all_tools)}, builtin={len(builtin_tool_names)}, external={len(all_tools)-len(builtin_tool_names)}")
        logger.info(f"🔍 BUILTIN_TOOLS: {sorted(builtin_tool_names)}")
        
        # Convert ALL tools to JSON-serializable format and deduplicate by name
        converted_tools = [self._convert_tool_schema(tool) for tool in all_tools]
        
        # Deduplicate tools by name (keep first occurrence)
        seen_names = set()
        bedrock_tools = []
        for tool in converted_tools:
            tool_name = tool.get('name', 'unknown')
            if tool_name not in seen_names:
                seen_names.add(tool_name)
                # Add mcp_ prefix only for actual MCP tools, not builtin tools
                if not tool_name.startswith('mcp_') and tool_name not in builtin_tool_names:
                    tool['name'] = f'mcp_{tool_name}'
                bedrock_tools.append(tool)

        # Build conversation
        conversation = []
        system_content = None

        logger.debug(f"🔍 STREAMING_TOOL_EXECUTOR: Received {len(messages)} messages")
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
            
            logger.debug(f"🔍 STREAMING_TOOL_EXECUTOR: Message {i}: role={role}, content_length={len(content)}")
            
            # CRITICAL: Preserve list content for multi-modal (images)
            if isinstance(content, list):
                logger.debug(f"🖼️ STREAMING_TOOL_EXECUTOR: Message {i} has multi-modal content with {len(content)} blocks")
            
            if role == 'system':
                system_content = content
                logger.debug(f"🔍 STREAMING_TOOL_EXECUTOR: Found system message with {len(content)} characters")
            elif role in ['user', 'assistant', 'ai']:
                # Normalize ai role to assistant for Bedrock
                bedrock_role = 'assistant' if role == 'ai' else role
                conversation.append({"role": bedrock_role, "content": content})

        # Iterative execution with proper tool result handling
        recent_commands = []  # Track recent commands to prevent duplicates
        using_extended_context = False  # Track if we've enabled extended context
        consecutive_empty_tool_calls = 0  # Track empty tool calls to break loops
        
        for iteration in range(50):  # Increased from 20 to support more complex tasks
            logger.debug(f"🔍 ITERATION_START: Beginning iteration {iteration}")
            
            # Check for user feedback at the start of each iteration
            if conversation_id and iteration > 0:  # Skip check on first iteration
                try:
                    from app.server import active_feedback_connections
                    if conversation_id in active_feedback_connections:
                        feedback_queue = active_feedback_connections[conversation_id]['feedback_queue']
                        try:
                            feedback_data = feedback_queue.get_nowait()
                            if feedback_data.get('type') == 'tool_feedback':
                                feedback_message = feedback_data.get('message', '')
                                logger.info(f"🔄 FEEDBACK_INTEGRATION: Iteration-level feedback: {feedback_message}")
                                if any(stop_word in feedback_message.lower() for stop_word in ['stop', 'halt', 'abort', 'cancel', 'quit']):
                                    yield track_yield({'type': 'text', 'content': f"\n\n**User feedback:** {feedback_message}\n**Stopping execution as requested.**\n\n"})
                                    yield track_yield({'type': 'stream_end'})
                                    return
                                else:
                                    # Handle directive feedback at iteration level
                                    logger.info(f"🔄 FEEDBACK_INTEGRATION: Iteration-level directive: {feedback_message}")
                                    
                                    # Add feedback to conversation so model can respond
                                    conversation.append({
                                        "role": "user",
                                        "content": f"[User feedback]: {feedback_message}"
                                    })
                                    logger.info(f"🔄 FEEDBACK_DELIVERED: Added iteration-level feedback to conversation at iteration {iteration}")
                                    
                                    # Let user know feedback was received
                                    yield track_yield({
                                        'type': 'text',
                                        'content': f"\n\n**Feedback received:** {feedback_message}\n**Adjusting approach...**\n\n"
                                    })
                                    
                                    # Continue with the iteration, but now the conversation includes user feedback
                                    logger.info(f"🔄 FEEDBACK_INTEGRATION: Added feedback to conversation, continuing iteration")
                        except asyncio.QueueEmpty:
                            pass
                except Exception as e:
                    logger.debug(f"Error checking iteration feedback: {e}")
            
            # Log last 2 messages to debug conversation state
            if len(conversation) >= 2:
                for i, msg in enumerate(conversation[-2:]):
                    role = msg.get('role', msg.get('type', 'unknown'))
                    content = msg.get('content', '')
                    content_preview = str(content)[:150] if content else 'empty'
                    logger.debug(f"🔍 CONV_DEBUG: Message -{2-i}: role={role}, content_preview={content_preview}")
            
            tools_executed_this_iteration = False  # Track if tools were executed in this iteration
            blocked_tools_this_iteration = 0  # Track blocked tools to prevent runaway loops
            commands_this_iteration = []  # Track commands executed in this specific iteration
            empty_tool_calls_this_iteration = 0  # Track empty tool calls in this iteration
            
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": self.model_config.get('max_output_tokens', 4000),
                "messages": conversation
            }

            if system_content:
                # With precision prompts, system content is already clean - no regex needed
                logger.debug(f"🔍 SYSTEM_DEBUG: Using clean system content length: {len(system_content)}")
                logger.debug(f"🔍 SYSTEM_DEBUG: File count in system content: {system_content.count('File:')}")
                
                system_text = system_content + "\n\nCRITICAL: Use ONLY native tool calling. Never generate fake tool markdown like ```tool:mcp_run_shell_command. Use the provided tools directly.\n\nIMPORTANT: Only use tools when you must interact with the system to fulfill a request (execute commands, read files that aren't in context). For questions, analysis, explanations, or information you can provide from your knowledge or the provided context, respond directly WITHOUT using any tools. Avoid unnecessary tool calls."
                
                # Use prompt caching for large system prompts to speed up iterations
                if len(system_text) > 1024:
                    body["system"] = [
                        {
                            "type": "text",
                            "text": system_text,
                            "cache_control": {"type": "ephemeral"}
                        }
                    ]
                    logger.debug(f"🔍 CACHE: Enabled prompt caching for {len(system_text)} char system prompt")
                else:
                    body["system"] = system_text
                
                logger.debug(f"🔍 SYSTEM_DEBUG: Final system prompt length: {len(system_text)}")
                logger.debug(f"🔍 SYSTEM_CONTENT_DEBUG: First 500 chars of system prompt: {system_text[:500]}")
                logger.debug(f"🔍 SYSTEM_CONTENT_DEBUG: System prompt contains 'File:' count: {system_text.count('File:')}")
                logger.debug(f"🔍 SYSTEM_CONTENT_DEBUG: Last 500 chars of system prompt: {system_text[-500:]}")
            
            # If we've already enabled extended context, keep using it
            if using_extended_context and self.model_config:
                header_value = self.model_config.get('extended_context_header')
                if header_value:
                    body['anthropic_beta'] = [header_value]
                    logger.debug(f"🔍 EXTENDED_CONTEXT: Continuing with extended context header")

            if bedrock_tools:
                # Don't send tools if we've had too many consecutive empty calls
                if consecutive_empty_tool_calls >= 5:
                    logger.warning(f"🔍 TOOL_SUPPRESSION: Suppressing tools due to {consecutive_empty_tool_calls} consecutive empty calls")
                    # Don't add tools to body - force model to respond without them
                else:
                    body["tools"] = bedrock_tools
                    # Use "auto" to allow model to decide when to stop
                    body["tool_choice"] = {"type": "auto"}
                    logger.debug(f"🔍 TOOL_DEBUG: Sending {len(bedrock_tools)} tools to model: {[t['name'] for t in bedrock_tools]}")

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
                        
                        logger.debug(f"🔍 API_PARAMS: Calling invoke_model_with_response_stream with modelId={self.model_id}")
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
                                    logger.debug(f"🔍 EXTENDED_CONTEXT: Context limit hit, enabling extended context with header {header_value}")
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
                        logger.debug(f"🔍 CHUNK_DEBUG: content_block_start - type: {content_block.get('type')}, id: {content_block.get('id')}")
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
                                    logger.debug(f"🔍 DUPLICATE_SKIP: Tool {tool_signature} already executed")
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
                                logger.debug(f"🔍 COLLECTED_TOOL: {tool_name} (id: {tool_id})")
                                
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
                            
                            # Buffer incomplete code block openings to prevent malformed types
                            if not hasattr(self, '_block_opening_buffer'):
                                self._block_opening_buffer = ""
                            
                            # Check if we have a buffered incomplete opening
                            if self._block_opening_buffer:
                                text = self._block_opening_buffer + text
                                self._block_opening_buffer = ""
                            
                            # Check if text ends with incomplete code block opening
                            if text.endswith('```') or (text.endswith('`') and text[-3:] != '```'):
                                # Might be incomplete, buffer it
                                self._block_opening_buffer = text
                                continue
                            elif '```' in text:
                                # Has opening backticks, check if line is complete
                                lines = text.split('\n')
                                last_line = lines[-1]
                                if last_line.strip().startswith('```') and not last_line.strip().endswith('```'):
                                    # Incomplete opening line (e.g., "```vega-" without newline)
                                    # Buffer the last line, process the rest
                                    if len(lines) > 1:
                                        text = '\n'.join(lines[:-1]) + '\n'
                                        self._block_opening_buffer = last_line
                                    else:
                                        self._block_opening_buffer = text
                                        continue
                            
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
                                        # FLUSH optimizer before skipping fake tool patterns
                                        if hasattr(self, '_content_optimizer'):
                                            remaining = self._content_optimizer.flush_remaining()
                                            if remaining:
                                                yield track_yield({
                                                    'type': 'text',
                                                    'content': remaining,
                                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                                })
                            if '```tool:' in text or '`tool:' in text:
                                # FLUSH optimizer before skipping fake tool patterns
                                if hasattr(self, '_content_optimizer'):
                                    remaining = self._content_optimizer.flush_remaining()
                                    if remaining:
                                        yield track_yield({
                                            'type': 'text',
                                            'content': remaining,
                                            'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                        })
                                continue
                            
                            # Initialize content optimizer if not exists
                            if not hasattr(self, '_content_optimizer'):
                                from app.utils.streaming_optimizer import StreamingContentOptimizer
                                self._content_optimizer = StreamingContentOptimizer()
                            
                            
                            if '```tool:' in text or '`tool:' in text:
                                if hasattr(self, '_content_optimizer'):
                                    remaining = self._content_optimizer.flush_remaining()
                                    if remaining:
                                        yield track_yield({
                                            'type': 'text',
                                            'content': remaining,
                                            'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                        })
                                continue
                            
                            # Check for visualization block boundaries - ensure proper markdown format
                            viz_patterns = ['```vega-lite', '```mermaid', '```graphviz', '```d3']
                            has_viz_pattern = any(pattern in text for pattern in viz_patterns) or (viz_buffer and any(pattern in viz_buffer + text for pattern in viz_patterns))
                            
                            if has_viz_pattern:
                                # If we're already in a viz block and see a new opening, send the previous one first
                                if in_viz_block and any(pattern in text for pattern in viz_patterns):
                                    # New viz block starting - send accumulated buffer first
                                    if viz_buffer.strip():
                                        self._update_code_block_tracker(viz_buffer, code_block_tracker)
                                        yield track_yield({
                                            'type': 'text',
                                            'content': viz_buffer,
                                            'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                        })
                                    viz_buffer = text
                                    in_viz_block = True
                                elif not in_viz_block:
                                    # FLUSH optimizer before starting viz block
                                    if hasattr(self, '_content_optimizer'):
                                        remaining = self._content_optimizer.flush_remaining()
                                        if remaining:
                                            yield track_yield({
                                                'type': 'text',
                                                'content': remaining,
                                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                            })
                                    in_viz_block = True
                                    viz_buffer = text
                                else:
                                    viz_buffer += text
                                continue
                            elif in_viz_block:
                                viz_buffer += text
                                # Check for closing ``` in accumulated buffer
                                has_closing = any(line.strip() == '```' for line in viz_buffer.split('\n'))
                                if has_closing:
                                    # Complete visualization block - send immediately
                                    self._update_code_block_tracker(viz_buffer, code_block_tracker)
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
                            
                            logger.debug(f"🔍 TOOL_ARGS: Tool '{tool_name}' (id: {tool_id}) has args_json: '{args_json}'")

                            try:
                                args = json.loads(args_json) if args_json.strip() else {}
                                
                                # Fix parameter type conversion issues
                                if 'raw' in args and isinstance(args['raw'], str):
                                    args['raw'] = args['raw'].lower() in ('true', '1', 'yes')
                                if 'max_length' in args and isinstance(args['max_length'], str):
                                    try:
                                        args['max_length'] = int(args['max_length'])
                                    except ValueError:
                                        pass
                                
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
                                    logger.debug(f"🔍 TOOL_EXECUTED_FLAG: Set tools_executed_this_iteration = True for tool {tool_id}")
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
                                
                                # Check for recently executed similar commands to prevent duplicates across iterations
                                if actual_tool_name == 'run_shell_command' and args.get('command'):
                                    current_command = args['command']
                                    
                                    # Check if this command is similar to recent commands
                                    skip_execution = False
                                    for recent_cmd in recent_commands[-10:]:  # Check last 10 commands
                                        if self._commands_similar(current_command, recent_cmd):
                                            logger.debug(f"🔍 DUPLICATE_COMMAND_SKIP: Skipping duplicate command '{current_command}' (similar to recent '{recent_cmd}')")
                                            
                                            # Add a helpful message instead of executing
                                            duplicate_result = f"Command '{current_command}' was already executed recently. Result should be available above."
                                            tool_results.append({
                                                'tool_id': tool_id,
                                                'tool_name': tool_name,
                                                'result': duplicate_result
                                            })
                                            
                                            completed_tools.add(tool_id)
                                            tools_executed_this_iteration = True
                                            skip_execution = True
                                            break
                                    
                                    if skip_execution:
                                        continue  # Skip to next tool in the content_block_stop processing
                                
                                # Execute the tool (already checked for duplicates at collection)
                                logger.debug(f"🔍 EXECUTING_TOOL: {actual_tool_name} with args {args}")
                                
                                # Send tool_start event with complete arguments
                                yield {
                                    'type': 'tool_start',
                                    'tool_id': tool_id,
                                    'tool_name': tool_name,
                                    'display_header': self._get_tool_header(tool_name, args),
                                    'args': args,
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                }
                                
                                # Check for user feedback before executing tool
                                if conversation_id:
                                    try:
                                        from app.server import active_feedback_connections
                                        if conversation_id in active_feedback_connections:
                                            feedback_queue = active_feedback_connections[conversation_id]['feedback_queue']
                                            # Check for feedback without blocking
                                            try:
                                                feedback_data = feedback_queue.get_nowait()
                                                if feedback_data.get('type') == 'tool_feedback':
                                                    feedback_message = feedback_data.get('message', '')
                                                    logger.info(f"🔄 FEEDBACK_INTEGRATION: Received feedback: {feedback_message}")
                                                    # If feedback suggests stopping, break out of tool execution
                                                    if any(stop_word in feedback_message.lower() for stop_word in ['stop', 'halt', 'abort', 'cancel', 'quit']):
                                                        logger.info(f"🔄 FEEDBACK_INTEGRATION: Feedback indicates stop - ending tool execution")
                                                        yield track_yield({'type': 'text', 'content': f"\n\n**User feedback received:** {feedback_message}\n**Stopping tool execution as requested.**\n\n"})
                                                        # Flush any remaining content
                                                        await asyncio.sleep(0.1)  # Give frontend time to process
                                                        yield track_yield({'type': 'stream_end'})
                                                        return
                                                    else:
                                                        # Handle directive feedback - add to conversation for model to see
                                                        logger.info(f"🔄 FEEDBACK_INTEGRATION: Adding directive feedback to conversation: {feedback_message}")
                                                        
                                                        # Add user feedback as a message to the conversation
                                                        conversation.append({
                                                            "role": "user", 
                                                            "content": f"[Real-time feedback]: {feedback_message}"
                                                        })
                                                        logger.info(f"🔄 FEEDBACK_DELIVERED: Added tool-level feedback to conversation before tool execution")
                                                        
                                                        # Acknowledge the feedback to user
                                                        yield track_yield({
                                                            'type': 'text', 
                                                            'content': f"\n\n**Feedback received:** {feedback_message}\n\n"
                                                        })
                                                        
                                                        # Skip the current planned tool and let the model respond to feedback
                                                        logger.info(f"🔄 FEEDBACK_INTEGRATION: Skipping planned tool to respond to feedback")
                                                        completed_tools.add(tool_id)
                                                        tools_executed_this_iteration = True
                                                        continue
                                            except asyncio.QueueEmpty:
                                                pass  # No feedback available, continue normally
                                    except Exception as e:
                                        logger.debug(f"Error checking feedback: {e}")
                               
                                # Execute the tool immediately
                                try:
                                   # Check if this is a builtin DirectMCPTool
                                   logger.info(f"🔍 BUILTIN_CHECK: Looking for tool '{actual_tool_name}' in {len(tools) if tools else 0} tools")
                                   builtin_tool = None
                                   if tools:
                                       for tool in tools:
                                           logger.debug(f"🔍 BUILTIN_CHECK: Checking tool {tool.name}, type={type(tool).__name__}, isinstance DirectMCPTool={isinstance(tool, DirectMCPTool)}")
                                           if isinstance(tool, DirectMCPTool) and tool.name == actual_tool_name:
                                               builtin_tool = tool
                                               logger.info(f"🔧 BUILTIN_FOUND: Found builtin tool {actual_tool_name}")
                                               break
                                   
                                   if not builtin_tool:
                                       logger.info(f"🔍 BUILTIN_NOT_FOUND: Tool '{actual_tool_name}' not found in builtin tools, routing to MCP manager")
                                   
                                   if builtin_tool:
                                        # Call builtin tool directly
                                        logger.info(f"🔧 Calling builtin tool directly: {actual_tool_name}")
                                        result = builtin_tool._run(**args)
                                   else:
                                        # Call through MCP manager for external tools
                                        result = await mcp_manager.call_tool(actual_tool_name, args)
                                    
                                    # Add successfully executed command to recent commands for deduplication
                                   if actual_tool_name == 'run_shell_command' and args.get('command'):
                                        recent_commands.append(args['command'])
                                        # Keep only last 20 commands to prevent memory bloat
                                        recent_commands = recent_commands[-20:]
                                    
                                    # Process result
                                   if isinstance(result, dict) and result.get('error') and result.get('error') != False:
                                        error_msg = result.get('message', 'Unknown error')
                                        if 'repetitive execution' in error_msg:
                                            result_text = f"BLOCKED: {error_msg} Previous attempts may have succeeded - check the results above before retrying."
                                        elif 'non-zero exit status' in error_msg:
                                            result_text = f"COMMAND FAILED: {error_msg}. The external tool encountered an error."
                                        elif 'Content truncated' in error_msg:
                                            result_text = f"PARTIAL RESULT: {error_msg}. Use start_index parameter to get more content."
                                        elif 'validation error' in error_msg.lower():
                                            result_text = f"PARAMETER ERROR: {error_msg}. Check the tool's parameter requirements."
                                        else:
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
                                        'result': self._format_tool_result(tool_name, result_text, args),
                                        'args': args,  # Pass args so frontend can access command
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
                                   logger.debug(f"🔍 TOOL_EXECUTED_FLAG: Set tools_executed_this_iteration = True for tool {tool_id}")
                                    
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
                            
                            except json.JSONDecodeError as e:
                                logger.error(f"🔍 JSON_PARSE_ERROR: Failed to parse tool arguments: {e}")
                                completed_tools.add(tool_id)

                    elif chunk['type'] == 'message_stop':
                        # Flush any remaining content from buffers before stopping  
                        # Flush block opening buffer first
                        if hasattr(self, '_block_opening_buffer') and self._block_opening_buffer:
                            assistant_text += self._block_opening_buffer
                            self._update_code_block_tracker(self._block_opening_buffer, code_block_tracker)
                            yield track_yield({
                                'type': 'text',
                                'content': self._block_opening_buffer,
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                            })
                            self._block_opening_buffer = ""
                        
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
                        
                        # Check if code block is still incomplete
                        # ENHANCED BLOCK COMPLETION CHECK
                        final_assistant_text = assistant_text.strip()
                        
                        # Check for unclosed code blocks using tracker
                        logger.debug(f"🔍 COMPLETION_CHECK: tracker_in_block={code_block_tracker.get('in_block', False)}")
                        
                        continuation_count = 0
                        max_continuations = 10
                        
                        while code_block_tracker.get('in_block') and continuation_count < max_continuations:
                            continuation_count += 1
                            block_type = code_block_tracker.get('block_type', 'code')
                            logger.info(f"🔄 INCOMPLETE_BLOCK: Detected incomplete {block_type} block, auto-continuing (attempt {continuation_count})")
                            
                            # Mark rewind boundary before auto-continuation
                            assistant_lines = assistant_text.split('\n')
                            # Remove the incomplete last line - rewind to last complete line
                            if assistant_lines and assistant_lines[-1].strip():
                                # Last line is incomplete, remove it
                                assistant_lines = assistant_lines[:-1]
                                logger.info(f"🔄 REWIND: Removed incomplete last line, rewinding to line {len(assistant_lines)}")
                            
                            last_complete_line = len(assistant_lines)
                            rewind_marker = f"<!-- REWIND_MARKER: {last_complete_line} -->"
                            rewind_chunk = {
                                'type': 'text',
                                'content': f"{rewind_marker}\n**🔄 Block continues...**\n",
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                            }
                            logger.info(f"🔄 YIELDING_REWIND: Rewinding to line {last_complete_line}")
                            yield track_yield(rewind_chunk)
                            
                            # CRITICAL: Add delay to ensure rewind marker is sent before continuation
                            await asyncio.sleep(0.1)
                            
                            # Send heartbeat before continuation to keep connection alive
                            yield {
                                'type': 'heartbeat',
                                'heartbeat': True,
                                'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                            }
                            
                            await asyncio.sleep(0.1)  # Ensure heartbeat is sent
                            
                            continuation_had_content = False
                            try:
                                async for continuation_chunk in self._continue_incomplete_code_block(
                                    conversation, code_block_tracker, mcp_manager, iteration_start_time, assistant_text
                                ):
                                    if continuation_chunk.get('content'):
                                        continuation_had_content = True
                                        logger.info(f"🔄 YIELDING_CONTINUATION: {repr(continuation_chunk.get('content', '')[:50])}")
                                        self._update_code_block_tracker(continuation_chunk['content'], code_block_tracker)
                                        assistant_text += continuation_chunk['content']
                                        
                                        if code_block_tracker['in_block']:
                                            continuation_chunk['code_block_continuation'] = True
                                            continuation_chunk['block_type'] = code_block_tracker['block_type']
                                    
                                    yield continuation_chunk
                            except Exception as continuation_error:
                                logger.error(f"Continuation failed: {continuation_error}")
                                # Send continuation failure marker
                                yield {
                                    'type': 'continuation_failed',
                                    'reason': str(continuation_error),
                                    'can_retry': 'ThrottlingException' in str(continuation_error),
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                }
                                break
                            
                            if not continuation_had_content:
                                logger.info("🔄 CONTINUATION: No content generated, stopping continuation attempts")
                                break
                            
                            # Log tracker state after continuation
                            logger.info(f"🔄 CONTINUATION_RESULT: After attempt {continuation_count}, in_block={code_block_tracker['in_block']}, had_content={continuation_had_content}")
                        
                        # Just break out of chunk processing, handle completion logic below
                        break

                # CRITICAL: Validate tool_results match tool_use blocks before building conversation
                # Remove any tool_use blocks that don't have corresponding results
                valid_tool_ids = {tr['tool_id'] for tr in tool_results}
                if all_tool_calls:
                    # Filter all_tool_calls to only include those with results
                    all_tool_calls = [tc for tc in all_tool_calls if tc['id'] in valid_tool_ids]
                    
                    if len(all_tool_calls) != len(tool_results):
                        logger.warning(f"🔍 TOOL_MISMATCH: {len(all_tool_calls)} tool calls but {len(tool_results)} results - filtered orphaned calls")
                
                # Add assistant response to conversation with proper tool_use blocks
                # ONLY include tool_use blocks that have corresponding tool_results
                if assistant_text.strip() or tools_executed_this_iteration:
                    # Build content as list with text and tool_use blocks
                    content_blocks = []
                    if assistant_text.strip():
                        content_blocks.append({"type": "text", "text": assistant_text.rstrip()})
                    
                    # Add tool_use blocks ONLY for tools that have results
                    for tool_result in tool_results:
                        # Find the corresponding tool call to get the actual args
                        tool_args = {}
                        for tool_call in all_tool_calls:
                            if tool_call['id'] == tool_result['tool_id']:
                                tool_args = tool_call.get('args', {})
                                break
                        
                        # Ensure tool_use block has the correct name format
                        tool_name = tool_result['tool_name']
                        if tool_name.startswith('mcp_'):
                            tool_name = tool_name[4:]  # Remove mcp_ prefix for Bedrock
                        
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tool_result['tool_id'],
                            "name": tool_name,
                            "input": tool_args
                        })
                    
                    conversation.append({"role": "assistant", "content": content_blocks})
            
                # Add tool results to conversation BEFORE filtering
                logger.debug(f"🔍 ITERATION_END_CHECK: tools_executed_this_iteration = {tools_executed_this_iteration}, tool_results count = {len(tool_results)}")
                if tools_executed_this_iteration:
                    logger.debug(f"🔍 TOOL_RESULTS_PROCESSING: Adding {len(tool_results)} tool results to conversation")
                    for tool_result in tool_results:
                        raw_result = tool_result['result']
                        if isinstance(raw_result, str) and '$ ' in raw_result:
                            lines = raw_result.split('\n')
                            clean_lines = [line for line in lines if not line.startswith('$ ')]
                            raw_result = '\n'.join(clean_lines).strip()
                        
                        # Add in tool_result_for_model format so filter can convert to proper Bedrock format
                        conversation.append({
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_result['tool_id'],
                                    "content": raw_result
                                }
                            ]
                        })
                
                # SAFETY CHECK: Ensure conversation is in valid Bedrock format
                # Verify that every tool_use in assistant messages has a corresponding tool_result
                if conversation:
                    tool_use_ids = set()
                    tool_result_ids = set()
                    
                    for msg in conversation:
                        if msg.get('role') == 'assistant' and isinstance(msg.get('content'), list):
                            for block in msg['content']:
                                if block.get('type') == 'tool_use':
                                    tool_use_ids.add(block.get('id'))
                        elif msg.get('role') == 'user' and isinstance(msg.get('content'), list):
                            for block in msg['content']:
                                if block.get('type') == 'tool_result':
                                    tool_result_ids.add(block.get('tool_use_id'))
                    
                    orphaned_ids = tool_use_ids - tool_result_ids
                    if orphaned_ids:
                        logger.error(f"🚨 ORPHANED_TOOL_USE: Found {len(orphaned_ids)} tool_use blocks without results: {orphaned_ids}")
                
                # The conversation should now be in proper Bedrock format
                # Remove the filter call since we're constructing messages correctly
                logger.info(f"🤖 MODEL_RESPONSE: {assistant_text}")
                logger.info(f"Conversation length: {len(conversation)} messages")

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
                    
                    logger.debug(f"🔍 CONTINUING_ROUND: Tool results added, model will continue in same stream (round {iteration + 1})")
                    # Yield heartbeat to flush stream before next iteration
                    yield {'type': 'iteration_continue', 'iteration': iteration + 1}
                    await asyncio.sleep(0)
                    continue  # Immediately start next iteration
                else:
                    # CRITICAL: Check for pending feedback BEFORE deciding to end stream
                    # This ensures feedback sent during the last tool execution is not lost
                    pending_feedback_before_end = []
                    if conversation_id:
                        try:
                            from app.server import active_feedback_connections
                            if conversation_id in active_feedback_connections:
                                feedback_queue = active_feedback_connections[conversation_id]['feedback_queue']
                                
                                # Drain any pending feedback
                                try:
                                    while True:
                                        try:
                                            feedback_data = feedback_queue.get_nowait()
                                            if feedback_data.get('type') == 'tool_feedback':
                                                pending_feedback_before_end.append(feedback_data.get('message', ''))
                                            elif feedback_data.get('type') == 'interrupt':
                                                logger.info(f"🔄 PRE-END FEEDBACK: Received interrupt before stream end")
                                                yield track_yield({'type': 'text', 'content': '\n\n**User requested stop.**\n\n'})
                                                yield track_yield({'type': 'stream_end'})
                                                return
                                        except asyncio.QueueEmpty:
                                            break
                                except Exception as queue_error:
                                    logger.debug(f"Error draining pre-end feedback queue: {queue_error}")
                        except Exception as e:
                            logger.debug(f"Error checking pre-end feedback: {e}")
                    
                    # If we found pending feedback, deliver it before ending
                    if pending_feedback_before_end:
                        combined_feedback = ' '.join(pending_feedback_before_end)
                        logger.info(f"🔄 PRE-END FEEDBACK: Processing {len(pending_feedback_before_end)} feedback message(s) before stream end")
                        
                        # Add feedback to conversation
                        conversation.append({
                            "role": "user",
                            "content": f"[User feedback]: {combined_feedback}"
                        })
                        
                        # Notify user
                        yield track_yield({
                            'type': 'text',
                            'content': f"\n\n**📝 Feedback received:** {combined_feedback}\n\n",
                            'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                        })
                        
                        # Continue to next iteration so model can respond
                        logger.debug(f"🔄 PRE-END FEEDBACK: Continuing to next iteration to process feedback")
                        continue
                    
                    # Check if too many tools were blocked (indicates runaway loop)
                    if blocked_tools_this_iteration >= 3:
                        logger.warning(f"🔍 RUNAWAY_LOOP_DETECTED: {blocked_tools_this_iteration} tools blocked in iteration {iteration}, ending stream")
                        yield {'type': 'stream_end'}
                        break
                    
                    # No tools executed - check if we should end the stream
                    if assistant_text.strip():
                        # FIRST: Check if code block is still incomplete - if so, continue
                        if code_block_tracker.get('in_block'):
                            logger.debug(f"🔍 INCOMPLETE_BLOCK_REMAINING: Code block still open, continuing to next iteration")
                            continue
                        
                        # Check if there's already substantial commentary after the last tool/diff/code block
                        text_after_last_block = self._get_text_after_last_structured_content(assistant_text)
                        word_count_after_block = len(text_after_last_block.split()) if text_after_last_block else 0
                        
                        # If we have 20+ words after the last block and it ends properly, consider it complete
                        if (word_count_after_block >= 20 and 
                            text_after_last_block.rstrip().endswith(('.', '!', '?'))):
                            logger.debug(f"🔍 COMPLETE_RESPONSE: Found {word_count_after_block} words after last block, ending stream: '{text_after_last_block[-50:]}'")
                            yield {'type': 'stream_end'}
                            break
                        
                        # Otherwise check if we should continue
                        text_end = assistant_text[-200:].strip()
                        suggests_continuation = (
                            text_end.endswith((':')) or  # About to make tool call  
                            assistant_text.endswith('```') or  # Just finished code block - might add explanation
                            (word_count_after_block < 20 and not text_after_last_block.rstrip().endswith(('.', '!', '?')))  # Not enough commentary AND doesn't end properly
                        )
                        
                        if suggests_continuation and iteration < 5:
                            logger.debug(f"🔍 CONTINUE_RESPONSE: Only {word_count_after_block} words after last block, continuing: '{text_after_last_block[-30:] if text_after_last_block else text_end}'")
                            continue
                        else:
                            logger.debug(f"🔍 STREAM_END: Model produced text without tools, ending stream")
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
                        logger.debug(f"🔍 MAX_ITERATIONS: Reached maximum iterations ({iteration}), ending stream")
                        yield {'type': 'stream_end'}
                        break
                    else:
                        continue
                
                # CRITICAL: Check for pending feedback after the iteration loop completes
                # This ensures feedback that arrived during the last iteration or after completion
                # is not lost and gives the model a chance to respond
                if conversation_id:
                    try:
                        from app.server import active_feedback_connections
                        if conversation_id in active_feedback_connections:
                            feedback_queue = active_feedback_connections[conversation_id]['feedback_queue']
                            
                            # Collect ALL pending feedback messages
                            pending_feedback = []
                            try:
                                while True:
                                    try:
                                        feedback_data = feedback_queue.get_nowait()
                                        feedback_type = feedback_data.get('type')
                                        if feedback_type == 'tool_feedback':
                                            pending_feedback.append(feedback_data.get('message', ''))
                                            logger.info(f"🔄 POST-LOOP FEEDBACK: Queued tool_feedback: {feedback_data.get('message', '')[:50]}...")
                                        elif feedback_type == 'interrupt':
                                            # Handle interrupt - stop processing
                                            logger.info(f"🔄 POST-LOOP FEEDBACK: Received interrupt after tool chain")
                                            yield track_yield({'type': 'text', 'content': '\n\n**User requested stop.**\n\n'})
                                            yield track_yield({'type': 'stream_end'})
                                            return
                                    except asyncio.QueueEmpty:
                                        break
                            except Exception as queue_error:
                                logger.debug(f"Error draining feedback queue: {queue_error}")
                            
                            # If we have pending feedback, send it to the model
                            if pending_feedback:
                                combined_feedback = ' '.join(pending_feedback)
                                logger.info(f"🔄 POST-LOOP FEEDBACK: Processing {len(pending_feedback)} feedback message(s) after tool chain completion")
                                
                                # Add feedback to conversation
                                conversation.append({
                                    "role": "user",
                                    "content": f"[User feedback after tool execution]: {combined_feedback}"
                                })
                                logger.info(f"🔄 FEEDBACK_DELIVERED: Added post-loop feedback to conversation: {combined_feedback[:50]}...")
                                
                                # Notify user that feedback is being processed
                                yield track_yield({
                                    'type': 'text',
                            'content': f"\n\n**📝 Feedback received:** {combined_feedback}\n\n",
                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                })
                                
                                # Make ONE additional API call to get model's response to feedback
                                try:
                                    body = {
                                        "anthropic_version": "bedrock-2023-05-31",
                                        "max_tokens": self.model_config.get('max_output_tokens', 4000),
                                        "messages": conversation
                                    }
                                    
                                    if system_content:
                                        body["system"] = system_content
                                    
                                    # Don't send tools for feedback response - just let model respond
                                    response = self.bedrock.invoke_model_with_response_stream(
                                        modelId=self.model_id,
                                        body=json.dumps(body)
                                    )
                                    
                                    # Stream the feedback response
                                    for event in response['body']:
                                        chunk = json.loads(event['chunk']['bytes'])
                                        
                                        if chunk['type'] == 'content_block_delta':
                                            delta = chunk.get('delta', {})
                                            if delta.get('type') == 'text_delta':
                                                text = delta.get('text', '')
                                                yield track_yield({
                                                    'type': 'text',
                                                    'content': text,
                                                    'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                                                })
                                        elif chunk['type'] == 'message_stop':
                                            break
                                            
                                except Exception as feedback_error:
                                    logger.error(f"Error processing post-loop feedback: {feedback_error}")
                    except Exception as e:
                        logger.debug(f"Error checking post-loop feedback: {e}")

            except Exception as e:
                error_str = str(e)
                logger.error(f"Error in stream_with_tools iteration {iteration}: {error_str}", exc_info=True)
                
                # Check if this is a throttling error
                is_throttling = any(indicator in error_str for indicator in [
                    "ThrottlingException", 
                    "Too many tokens",
                    "Too many requests", 
                    "Rate exceeded",
                ])
                
                # Check for authentication/credential errors
                from app.plugins import get_active_auth_provider
                from app.utils.custom_exceptions import KnownCredentialException

                auth_provider = get_active_auth_provider()
                is_auth_error = (
                    isinstance(e, KnownCredentialException) or
                    (auth_provider and auth_provider.is_auth_error(error_str))
                )
                
                if is_throttling:
                    # Extract suggested wait time if available
                    suggested_wait = 60  # Default 60 seconds
                    if "please wait" in error_str.lower():
                        # Try to extract time from error message
                        import re
                        wait_match = re.search(r'wait (\d+)', error_str.lower())
                        if wait_match:
                            suggested_wait = int(wait_match.group(1))
                    
                    # Check if this is a token-based throttling (more severe)
                    is_token_throttling = "Too many tokens" in error_str
                    
                    # Yield a special throttling error chunk with all info needed for inline display
                    yield {
                        'type': 'throttling_error',
                        'error': 'throttling_error',
                        'detail': error_str,
                        'suggested_wait': suggested_wait,
                        'is_token_throttling': is_token_throttling,
                        'iteration': iteration,
                        'tools_executed': len(tool_results),
                        'can_retry': True,
                        'retry_message': f"AWS rate limit exceeded after {len(tool_results)} tool execution(s). "
                                       f"Please wait {suggested_wait} seconds before retrying.",
                        'timestamp': f"{int((time.time() - iteration_start_time) * 1000)}ms"
                    }
                    logger.info(f"🔄 THROTTLING: Yielded throttling error chunk after {len(tool_results)} tools")
                    return
                elif is_auth_error:
                    # For authentication errors, yield a detailed error with helpful message
                    logger.error(f"Authentication error in iteration {iteration}: {error_str}")
                    
                    # Extract the most relevant part of the error message
                    error_message = auth_provider.get_credential_help_message() if auth_provider else "AWS credentials have expired."
                    
                    auth_error_chunk = {
                        'type': 'error',
                        'error': 'authentication_error',
                        'error_type': 'authentication_error',
                        'content': error_message,
                        'detail': error_str,
                        'can_retry': True,
                        'retry_message': error_message
                    }
                    logger.info(f"🔐 AUTH_ERROR: Yielding authentication error chunk: {auth_error_chunk}")
                    yield auth_error_chunk
                    logger.info(f"🔐 AUTH_ERROR: Successfully yielded authentication error chunk")
                    return
                else:
                    # For non-throttling errors, yield generic error
                    logger.error(f"Non-throttling error in iteration {iteration}: {error_str}")
                    yield {'type': 'error', 'content': f'Error: {error_str}'}
                    return

    def _update_code_block_tracker(self, text: str, tracker: Dict[str, Any]) -> None:
        """Update code block tracking state based on text content."""
        if not text:
            return
            
        # Debug logging to track state changes
        was_in_block = tracker.get('in_block', False)
        was_block_type = tracker.get('block_type')
            
        lines = text.split('\n')
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('```'):
                # Extract potential language/type after ```
                lang_or_type = stripped[3:].strip()
                
                if lang_or_type:
                    # Has a language specifier - this is ALWAYS an opening, even if we're in a block
                    # This handles cases like: ```mermaid\n...\n```vega-lite (no closing ```)
                    if tracker['in_block']:
                        logger.debug(f"🔍 TRACKER: Implicitly closing {tracker['block_type']} block, opening {lang_or_type} block")
                    tracker['in_block'] = True
                    tracker['block_type'] = lang_or_type
                    tracker['accumulated_content'] = line + '\n'
                    logger.debug(f"🔍 TRACKER: Opened {lang_or_type} block")
                elif tracker['in_block']:
                    # No language specifier and we're in a block - this is a closing ```
                    tracker['in_block'] = False
                    tracker['block_type'] = None
                    logger.debug(f"🔍 TRACKER: Closed block")
        
        # Log state changes for debugging
        if was_in_block != tracker.get('in_block') or was_block_type != tracker.get('block_type'):
            logger.debug(f"🔍 TRACKER_STATE_CHANGE: {was_block_type or 'none'}[{was_in_block}] → {tracker.get('block_type') or 'none'}[{tracker.get('in_block')}]")
            logger.debug(f"🔍 TRACKER_TEXT: Processing text: {repr(text[:100])}")

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
            
            # Use text truncated to last complete line
            if assistant_text.strip():
                lines = assistant_text.split('\n')
                # Remove incomplete last line if present
                if lines and lines[-1].strip():
                    lines = lines[:-1]
                cleaned_text = '\n'.join(lines)
                
                if continuation_conversation and continuation_conversation[-1].get('role') == 'assistant':
                    continuation_conversation[-1]['content'] = [{"type": "text", "text": cleaned_text}]
                else:
                    continuation_conversation.append({"role": "assistant", "content": [{"type": "text", "text": cleaned_text}]})
            
            continuation_conversation.append({"role": "user", "content": continuation_prompt})
            
            body = {
                "messages": continuation_conversation,
                "max_tokens": self.model_config.get('max_output_tokens', 2000),
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
            continuation_buffer = ""  # Buffer for continuation chunks
            
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
                        
                        # Buffer continuation text to avoid tiny chunks
                        continuation_buffer += text
                        
                        # Only yield when we have a substantial amount or hit a major boundary
                        should_yield = (
                            len(continuation_buffer) >= 200 or  # Substantial chunk size
                            '```\n' in continuation_buffer or  # Complete code block boundary
                            continuation_buffer.count('\n') >= 5  # Multiple complete lines
                        )
                        
                        if not should_yield:
                            continue
                        
                        text = continuation_buffer
                        continuation_buffer = ""
                        
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
            
            # Flush any remaining buffered content
            if continuation_buffer:
                yield {
                    'type': 'text',
                    'content': continuation_buffer,
                    'timestamp': f"{int((time.time() - start_time) * 1000)}ms",
                    'continuation': True
                }
        
        except Exception as e:
            logger.error(f"🔄 CONTINUATION: Error in continuation: {e}")
