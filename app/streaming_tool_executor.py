#!/usr/bin/env python3
"""
Streaming Tool Executor - Direct Bedrock Implementation
Bypasses LangChain for real-time tool execution during streaming
"""

import asyncio
import json
import os
import subprocess
import time
from typing import Dict, List, Any, Optional, AsyncGenerator

from app.utils.logging_utils import logger

class StreamingToolExecutor:
    """
    Direct Bedrock streaming client that executes tools in real-time
    during the streaming response, not after completion.
    """
    
    def __init__(self, profile_name: Optional[str] = None, region: str = 'us-east-1'):
        # Create boto3 client first
        import boto3
        session = boto3.Session(profile_name=profile_name) if profile_name else boto3.Session()
        bedrock_client = session.client('bedrock-runtime', region_name=region)
        
        # Use CustomBedrockClient for extended context support
        from app.utils.custom_bedrock import CustomBedrockClient
        from app.agents.models import ModelManager
        
        # Get model config for extended context support
        model_config = ModelManager.get_model_config('bedrock', ModelManager.get_model_alias())
        
        self.bedrock = CustomBedrockClient(
            bedrock_client,
            model_config=model_config
        )
        
        # Store model information
        try:
            # Get current model info from ModelManager state
            state = ModelManager.get_state()
            current_model_id_dict = state.get('current_model_id', {})
            if current_model_id_dict:
                # Get the first available model ID from the dict
                self.model_id = next(iter(current_model_id_dict.values()), '')
            else:
                # Fallback: try to get from bedrock client config
                self.model_id = getattr(bedrock_client, '_model_id', '')
        except:
            self.model_id = ''
        
        print(f"🔧 DEBUG: StreamingToolExecutor initialized with model_id: '{self.model_id}'")
        
        # State management for tool execution
        self.active_tools: Dict[str, Dict[str, Any]] = {}
        self.completed_tools: set = set()
        
        # Configuration - don't cache max_tokens, check dynamically
        self.max_output_length = 10000  # Increased from 2000 to show more complete results
        
    def _get_current_max_tokens(self):
        """Get current max_tokens, checking environment variable first (for frontend updates)"""
        env_max_tokens = os.environ.get("ZIYA_MAX_OUTPUT_TOKENS")
        if env_max_tokens:
            try:
                return int(env_max_tokens)
            except ValueError:
                logger.warning(f"Invalid ZIYA_MAX_OUTPUT_TOKENS value: {env_max_tokens}, using model config default")
        
        # Fallback to model config default
        return self._get_model_config_max_tokens()
        
    def _get_model_config_max_tokens(self):
        """Get max_tokens from model config like LangChain version did"""
        try:
            from app.agents.models import ModelManager
            
            # Get current model config
            state = ModelManager.get_state()
            current_model_alias = state.get('current_model_alias', 'sonnet3.5')
            
            model_config = ModelManager.get_model_config('bedrock', current_model_alias)
            
            # Use default_max_output_tokens from config, fallback to max_output_tokens, then 4096
            default_max = model_config.get('default_max_output_tokens')
            if default_max:
                logger.info(f"Using model config default_max_output_tokens: {default_max}")
                return default_max
                
            max_output = model_config.get('max_output_tokens', 4096)
            logger.info(f"Using model config max_output_tokens: {max_output}")
            return max_output
            
        except Exception as e:
            logger.warning(f"Could not get model config max_tokens: {e}, using fallback")
            return 4096  # Fallback only if config lookup fails
        
    def reset_state(self):
        """Reset tool execution state for new streaming request"""
        self.active_tools.clear()
        self.completed_tools.clear()
    
    async def stream_with_tools(self, messages: List[Dict[str, Any]], 
                               tools: Optional[List[Dict[str, Any]]] = None) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream response from Bedrock while executing tools in real-time
        Support hundreds of chained tool calls within single stream like Q CLI
        """
        print(f"🔧 DEBUG: StreamingToolExecutor.stream_with_tools called with {len(messages)} messages")
        print("🔧 DEBUG: *** MODIFIED VERSION WITH MCP SUPPORT ***")
        print(f"🔧 DEBUG: Model ID: '{self.model_id}'")
        print(f"🔧 DEBUG: Tools parameter: {tools}")
        logger.info(f"🔧 STREAMING_TOOL_EXECUTOR: Starting stream_with_tools with {len(messages)} messages")
        
        self.reset_state()
        
        if tools is None:
            print("🔧 DEBUG: No tools provided, loading MCP tools")
            # Check if model supports tools before loading
            if self.model_id and ('deepseek' in self.model_id.lower() or 'openai' in self.model_id.lower()):
                print(f"🔧 DEBUG: Model {self.model_id} detected, skipping tool loading (uses LangChain path)")
                tools = []
            else:
                tools = self._get_available_tools()
        else:
            print(f"🔧 DEBUG: Using provided tools: {[t.get('name', 'unknown') for t in tools]}")
            # Check if model supports tools and disable if needed
            if self.model_id and ('deepseek' in self.model_id.lower() or 'openai' in self.model_id.lower()):
                print(f"🔧 DEBUG: Model {self.model_id} detected, disabling provided tools (uses LangChain path)")
                tools = []

        current_messages = messages.copy()
        max_rounds = 100  # Support hundreds of tool calls
        
        for round_num in range(max_rounds):
            print(f"🔄 DEBUG: Starting tool round {round_num + 1}")
            
            # Extract system messages and convert to Bedrock format
            system_messages = []
            user_messages = []
            
            for msg in current_messages:
                if isinstance(msg, dict):
                    role = msg.get('role', '')
                    content = msg.get('content', '')
                else:
                    # Handle LangChain message objects
                    role = getattr(msg, 'type', getattr(msg, 'role', ''))
                    content = getattr(msg, 'content', '')
                    
                if role in ['system']:
                    system_messages.append(content)
                elif role in ['user', 'human']:
                    user_messages.append({"role": "user", "content": content})
                elif role in ['assistant', 'ai']:
                    user_messages.append({"role": "assistant", "content": content})
            
            # Use the system messages but fix them for native tool calling
            system_content = "\n\n".join(system_messages) if system_messages else None
            
            if system_content:
                import re
                # Remove XML tool format examples that conflict with native tool calling
                system_content = re.sub(r'<TOOL_SENTINEL>.*?</TOOL_SENTINEL>', '', system_content, flags=re.DOTALL)
                system_content = re.sub(r'```\s*<TOOL_SENTINEL>.*?```', '', system_content, flags=re.DOTALL)
                # Remove any XML-style tool examples
                system_content = re.sub(r'<mcp_[^>]+>.*?</mcp_[^>]+>', '', system_content, flags=re.DOTALL)
                system_content = re.sub(r'```\s*<mcp_[^>]+>.*?```', '', system_content, flags=re.DOTALL)
                # Add explicit instruction for native tool calling
                system_content += "\n\nIMPORTANT: Use native tool calling format only. Do not output XML-style tool calls like <mcp_run_shell_command>. The tools will be called automatically when you invoke them through the native interface."
                # Clean up extra whitespace
                system_content = re.sub(r'\n\s*\n\s*\n+', '\n\n', system_content)

            # Check if model supports tools
            model_supports_tools = True
            if self.model_id and ('deepseek' in self.model_id.lower() or 'openai' in self.model_id.lower()):
                print(f"🔧 DEBUG: Model {self.model_id} detected, disabling tools (uses LangChain path)")
                model_supports_tools = False
                tools = []

            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "messages": user_messages
            }
            
            # Add max_tokens only for models that support it
            if not (self.model_id and ('nova-micro' in self.model_id.lower() or 'nova-lite' in self.model_id.lower())):
                body["max_tokens"] = self._get_current_max_tokens()
            
            # Only add tools if model supports them
            if model_supports_tools and tools:
                body.update({
                    "tools": tools,
                    "tool_choice": {"type": "auto"}  # Force native tool calling
                })
            
            # Debug: Print tools being sent to Bedrock
            print(f"🔧 DEBUG: Sending {len(tools)} tools to Bedrock: {[t.get('name', 'unnamed') for t in tools]}")
            print(f"🔧 DEBUG: First tool schema: {tools[0] if tools else 'No tools'}")
            
            if system_content:
                body["system"] = system_content

            try:
                # Get the current model ID from the session
                from app.agents.models import ModelManager
                model_id_result = ModelManager.get_model_id()
                
                # Handle dict format (region-specific model IDs)
                if isinstance(model_id_result, dict):
                    # Use the first available model ID
                    current_model_id = list(model_id_result.values())[0]
                else:
                    current_model_id = model_id_result
                
                print(f"🔄 DEBUG: Round {round_num + 1} - Calling Bedrock with model {current_model_id}")
                
                # Check if model supports tools and adjust parameters
                if current_model_id and 'deepseek' in current_model_id.lower():
                    print(f"🔧 DEBUG: Deepseek model detected ({current_model_id}), using minimal request format")
                    # Use minimal request body for Deepseek
                    body = {
                        "messages": body["messages"],
                        "max_tokens": body.get("max_tokens", 4096),
                        "temperature": body.get("temperature", 0.3)
                    }
                    print(f"🔧 DEBUG: Deepseek request body keys: {list(body.keys())}")
                elif current_model_id and any(nova_model in current_model_id.lower() for nova_model in ['nova-micro', 'nova-lite', 'nova-pro', 'nova-premier']):
                    print(f"🔧 DEBUG: Nova model detected ({current_model_id}), delegating to Nova wrapper")
                    # Delegate to Nova wrapper for proper handling
                    from app.agents.wrappers.nova_wrapper import NovaWrapper
                    nova_wrapper = NovaWrapper(model_id=current_model_id)
                    async for chunk in nova_wrapper.stream_with_tools(body, tools, self.bedrock):
                        yield chunk
                    return

                
                try:
                    response = self.bedrock.invoke_model_with_response_stream(
                        modelId=current_model_id,
                        body=json.dumps(body)
                    )
                except Exception as e:
                    error_message = str(e)
                    # Check if it's a context limit error and model supports extended context
                    if "Input is too long" in error_message:
                        from app.agents.models import ModelManager
                        model_config = ModelManager.get_model_config('bedrock', ModelManager.get_model_alias())
                        
                        if model_config.get('supports_extended_context', False):
                            print(f"🚀 STREAMING_EXTENDED_CONTEXT: Detected context limit error, retrying with extended context")
                            # Add extended context header and retry
                            header_value = model_config.get('extended_context_header')
                            if header_value:
                                body['anthropic_beta'] = [header_value]
                                
                                response = self.bedrock.invoke_model_with_response_stream(
                                    modelId=current_model_id,
                                    body=json.dumps(body)
                                )
                                print(f"🚀 STREAMING_EXTENDED_CONTEXT: Extended context retry successful")
                            else:
                                print(f"🔄 DEBUG: Round {round_num + 1} error: {e}")
                                raise
                        else:
                            print(f"🔄 DEBUG: Round {round_num + 1} error: {e}")
                            raise
                    else:
                        print(f"🔄 DEBUG: Round {round_num + 1} error: {e}")
                        raise
                
                print(f"🔧 DEBUG: Got response type: {type(response)}")
                print(f"🔧 DEBUG: Response keys: {list(response.keys()) if hasattr(response, 'keys') else 'No keys'}")
                
                round_text = ""
                round_tool_results = []
                has_tool_calls = False
                
                print(f"🔧 DEBUG: About to iterate over response['body']")
                chunk_count = 0
                for event in response['body']:
                    chunk_count += 1
                    if chunk_count <= 3:  # Only log first few chunks
                        print(f"🔧 DEBUG: Processing chunk {chunk_count}")
                    chunk = json.loads(event['chunk']['bytes'])
                    
                    # Debug: Print chunk structure for first few chunks
                    if chunk_count <= 2:
                        print(f"🔧 DEBUG: Chunk {chunk_count} structure: {chunk}")
                    
                    # Handle different response formats
                    if current_model_id and 'deepseek' in current_model_id.lower():
                        # Deepseek response format
                        if 'choices' in chunk:
                            for choice in chunk.get('choices', []):
                                message = choice.get('message', {})
                                # Handle content
                                if message.get('content'):
                                    text = message['content']
                                    round_text += text
                                    yield {'type': 'text', 'content': text}
                                # Handle reasoning content (Deepseek R1 specific)
                                elif message.get('reasoning_content'):
                                    text = message['reasoning_content']
                                    round_text += text
                                    yield {'type': 'text', 'content': text}
                    elif current_model_id and 'nova-pro' in current_model_id.lower():
                        # Nova Pro response format
                        if 'contentBlockDelta' in chunk:
                            delta = chunk.get('contentBlockDelta', {})
                            if delta.get('delta', {}).get('text'):
                                text = delta['delta']['text']
                                round_text += text
                                yield {'type': 'text', 'content': text}
                        elif 'messageStart' in chunk:
                            # Just log message start, no content to yield
                            print(f"🔧 DEBUG: Nova Pro message started with role: {chunk['messageStart'].get('role')}")
                        elif 'messageStop' in chunk:
                            # Message completed - check for text-based tool calls
                            print(f"🔧 DEBUG: Nova Pro message completed")
                            # Parse text-based tool calls from Nova Pro using wrapper
                            from app.agents.wrappers.nova_wrapper import NovaWrapper
                            nova_wrapper = NovaWrapper(model_id=current_model_id)
                            tool_results = await nova_wrapper.parse_text_based_tools(round_text)
                            
                            for tool_result in tool_results:
                                has_tool_calls = True
                                if tool_result.get('type') == 'tool_execution':
                                    round_tool_results.append(tool_result)
                                yield tool_result
                    else:
                        # Claude response format
                        if chunk.get('type') == 'content_block_delta':
                            delta = chunk.get('delta', {})
                            if delta.get('type') == 'text_delta':
                                text = delta.get('text', '')
                                round_text += text
                                yield {'type': 'text', 'content': text}
                    
                    # Handle tool execution during streaming (only for Claude, not for Deepseek or Nova Pro)
                    if not (current_model_id and ('deepseek' in current_model_id.lower() or 'nova-pro' in current_model_id.lower())):
                        async for tool_result in self._handle_tool_chunk(chunk):
                            has_tool_calls = True
                            if tool_result.get('type') == 'tool_execution':
                                round_tool_results.append(tool_result)
                            yield tool_result
                
                print(f"🔄 DEBUG: Round {round_num + 1} completed - text: {len(round_text)} chars, tools: {len(round_tool_results)}")
                
                # If no tool calls were made, we're done
                if not has_tool_calls:
                    print(f"🔄 DEBUG: No more tool calls, ending after round {round_num + 1}")
                    break
                
                # Add assistant response to conversation
                if round_text.strip():
                    current_messages.append({"role": "assistant", "content": round_text})
                
                # Add tool results as user messages for next round
                for tool_result in round_tool_results:
                    # Use clean format without XML tags to prevent leakage
                    tool_content = f"Tool {tool_result['tool_name']} returned: {tool_result['result']}"
                    current_messages.append({"role": "user", "content": tool_content})
                
                # Reset tool state for next round
                self.reset_state()
                
                # Add a small delay to prevent overwhelming the API
                await asyncio.sleep(0.1)
                
            except Exception as e:
                print(f"🔄 DEBUG: Round {round_num + 1} error: {str(e)}")
                import traceback
                traceback.print_exc()
                
                # Check if this is a validation error that should be retried or propagated
                error_str = str(e)
                if "ValidationException" in error_str or "Malformed input request" in error_str:
                    # This is a parameter format error, propagate immediately
                    yield {'type': 'error', 'content': f"Model parameter error: {error_str}"}
                    return
                else:
                    # Other errors, continue with retry logic
                    yield {'type': 'error', 'content': f"Streaming error: {str(e)}"}
                    break
        
        print(f"🔄 DEBUG: Completed after {round_num + 1} rounds")
    
    async def _handle_tool_chunk(self, chunk: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """Process Bedrock tool_use chunks and execute tools in real-time"""

        # Check if chunk has type field (Claude format) or handle Nova format
        if 'type' not in chunk:
            # Nova format - just yield as text
            yield {'type': 'text', 'content': ''}
            return

        if chunk['type'] == 'content_block_start':
            content_block = chunk.get('content_block', {})
            if content_block.get('type') == 'tool_use':
                tool_id = content_block.get('id')
                tool_name = content_block.get('name')
                print(f"🔧 DEBUG: Tool use start - id: {tool_id}, name: {tool_name}")
                if tool_id and tool_name:
                    self.active_tools[tool_id] = {
                        'name': tool_name,
                        'partial_json': '',
                        'index': chunk.get('index')
                    }
                    print(f"🔧 DEBUG: Registered tool: {self.active_tools[tool_id]}")
        
        elif chunk['type'] == 'content_block_delta':
            delta = chunk.get('delta', {})
            if delta.get('type') == 'input_json_delta':
                index = chunk.get('index')
                tool_id = self._find_tool_by_index(index)
                
                if tool_id and tool_id not in self.completed_tools:
                    # Accumulate partial JSON
                    partial_json = delta.get('partial_json', '')
                    self.active_tools[tool_id]['partial_json'] += partial_json
                    
                    # Try to execute when JSON is complete
                    result = await self._try_execute_tool(tool_id)
                    if result:
                        print(f"🔧 DEBUG: Tool execution successful: {result[:100]}...")
                        tool_name = self.active_tools[tool_id]['name']
                        
                        # Convert tool name to frontend format and detect special cases
                        display_name = tool_name
                        if tool_name in ['execute_shell_command', 'run_shell_command']:
                            # Check if this is a time-related command
                            tool_args = json.loads(self.active_tools[tool_id]['partial_json'])
                            command = tool_args.get('command', '').strip().lower()
                            if command in ['date', 'time'] or 'time' in command:
                                display_name = 'mcp_get_current_time'
                            else:
                                display_name = 'mcp_run_shell_command'  # Frontend expects this for shell commands
                        elif tool_name == 'get_current_time':
                            display_name = 'mcp_get_current_time'
                        elif not tool_name.startswith('mcp_') and tool_name not in ['execute_shell_command', 'run_shell_command', 'get_current_time']:
                            display_name = f'mcp_{tool_name}'  # Add mcp_ prefix for other tools
                        
                        yield {
                            'type': 'tool_execution', 
                            'tool_id': tool_id,
                            'tool_name': display_name,
                            'result': result
                        }
        
        elif chunk['type'] == 'content_block_stop':
            # Final attempt to execute tool
            index = chunk.get('index')
            tool_id = self._find_tool_by_index(index)
            
            print(f"🔧 DEBUG: Content block stop - index: {index}, tool_id: {tool_id}")
            
            if tool_id and tool_id not in self.completed_tools:
                result = await self._try_execute_tool(tool_id)
                if result:
                    print(f"🔧 DEBUG: Final tool execution successful: {result[:100]}...")
                    tool_name = self.active_tools[tool_id]['name']
                    
                    # Convert tool name to frontend format and detect special cases
                    display_name = tool_name
                    if tool_name in ['execute_shell_command', 'run_shell_command']:
                        # Check if this is a time-related command
                        tool_args = json.loads(self.active_tools[tool_id]['partial_json'])
                        command = tool_args.get('command', '').strip().lower()
                        if command in ['date', 'time'] or 'time' in command:
                            display_name = 'mcp_get_current_time'
                        else:
                            display_name = 'mcp_run_shell_command'  # Frontend expects this for shell commands
                    elif tool_name == 'get_current_time':
                        display_name = 'mcp_get_current_time'
                    elif not tool_name.startswith('mcp_') and tool_name not in ['execute_shell_command', 'run_shell_command', 'get_current_time']:
                        display_name = f'mcp_{tool_name}'  # Add mcp_ prefix for other tools
                    
                    yield {
                        'type': 'tool_execution',
                        'tool_id': tool_id, 
                        'tool_name': display_name,
                        'result': result
                    }
    
    def _find_tool_by_index(self, index: int) -> Optional[str]:
        """Find tool ID by chunk index"""
        for tool_id, tool_data in self.active_tools.items():
            if tool_data.get('index') == index:
                return tool_id
        return None
    
    async def _try_execute_tool(self, tool_id: str) -> Optional[str]:
        """Try to execute tool if JSON is complete"""
        if tool_id in self.completed_tools:
            return None
            
        try:
            complete_json = self.active_tools[tool_id]['partial_json']
            args = json.loads(complete_json)  # Validate JSON
            
            tool_name = self.active_tools[tool_id]['name']
            print(f"🔧 DEBUG: Executing tool '{tool_name}' with args: {args}")
            
            # Handle all tools through MCP
            if tool_name.startswith('mcp_') or tool_name in ['get_current_time', 'execute_shell_command', 'run_shell_command']:
                # Handle MCP tools - convert name to mcp_ format for frontend
                mcp_tool_name = tool_name if tool_name.startswith('mcp_') else f'mcp_{tool_name}'
                print(f"🔧 DEBUG: Calling MCP tool: {tool_name} -> {mcp_tool_name}")
                result = await self._execute_mcp_tool(tool_name, args)
                self.completed_tools.add(tool_id)
                return result
            else:
                print(f"🔧 DEBUG: Unknown tool type: {tool_name}")
                self.completed_tools.add(tool_id)
                return f"Unknown tool: {tool_name}"
            
        except json.JSONDecodeError:
            # JSON not complete yet
            return None
        except Exception as e:
            print(f"🔧 DEBUG: Tool execution exception: {str(e)}")
            import traceback
            traceback.print_exc()
            self.completed_tools.add(tool_id)
            return f"Tool execution error: {str(e)}"
    
    async def _execute_mcp_tool(self, tool_name: str, args: dict) -> str:
        """Execute MCP tool by calling the MCP manager"""
        try:
            print(f"🔧 DEBUG: _execute_mcp_tool called with tool_name='{tool_name}', args={args}")
            
            # Import MCP manager
            from app.mcp.manager import get_mcp_manager
            
            # Remove 'mcp_' prefix to get actual tool name
            actual_tool_name = tool_name[4:] if tool_name.startswith('mcp_') else tool_name
            print(f"🔧 DEBUG: Actual tool name: '{actual_tool_name}'")
            
            mcp_manager = get_mcp_manager()
            print(f"🔧 DEBUG: Got MCP manager: {mcp_manager}")
            
            # Execute the MCP tool - let the manager find the right server
            if actual_tool_name == 'run_shell_command':
                command = args.get('command', args.get('input', ''))
                print(f"🔧 DEBUG: Executing shell command: '{command}'")
                result = await mcp_manager.call_tool('run_shell_command', {'command': command})
            elif actual_tool_name == 'get_current_time':
                print(f"🔧 DEBUG: Getting current time")
                result = await mcp_manager.call_tool('get_current_time', {})
            else:
                # Generic MCP tool call
                print(f"🔧 DEBUG: Generic MCP tool call: '{actual_tool_name}' with args: {args}")
                result = await mcp_manager.call_tool(actual_tool_name, args)
            
            print(f"🔧 DEBUG: MCP tool result: {result}")
            
            if isinstance(result, dict) and 'content' in result:
                content = result['content']
                if isinstance(content, list) and len(content) > 0:
                    # Extract text from MCP content list format
                    if isinstance(content[0], dict) and 'text' in content[0]:
                        return content[0]['text']
                    else:
                        return str(content[0])
                else:
                    return str(content)
            else:
                return str(result)
                
        except ConnectionError as e:
            print(f"🔧 DEBUG: MCP connection error: {str(e)}")
            return f"MCP server connection failed for tool '{tool_name}'. Server may be unavailable."
        except TimeoutError as e:
            print(f"🔧 DEBUG: MCP timeout error: {str(e)}")
            return f"MCP tool '{tool_name}' timed out. Try a simpler operation or check server status."
        except json.JSONDecodeError as e:
            print(f"🔧 DEBUG: MCP JSON error: {str(e)}")
            return f"Invalid parameters for MCP tool '{tool_name}'. Check parameter format."
        except Exception as e:
            print(f"🔧 DEBUG: MCP tool execution exception: {str(e)}")
            import traceback
            traceback.print_exc()
            return f"MCP tool '{tool_name}' failed: {str(e)}. Check tool parameters and server status."
    
    def _get_available_tools(self) -> List[Dict[str, Any]]:
        """Get all available tools including MCP tools"""
        tools = []
        
        # Try to load MCP tools first
        try:
            from app.mcp.manager import get_mcp_manager
            mcp_manager = get_mcp_manager()
            print(f"🔧 DEBUG: MCP manager initialized: {mcp_manager.is_initialized}")
            if mcp_manager.is_initialized:
                mcp_tools = mcp_manager.get_all_tools()
                print(f"🔧 DEBUG: Found {len(mcp_tools)} MCP tools")
                for tool in mcp_tools:
                    print(f"🔧 DEBUG: MCP tool: {tool.name}")
                    # MCPTool objects use 'inputSchema' not 'input_schema'
                    tools.append({
                        'name': tool.name,
                        'description': tool.description,
                        'input_schema': getattr(tool, 'inputSchema', getattr(tool, 'input_schema', {}))
                    })
        except Exception as e:
            print(f"🔧 DEBUG: MCP tool loading error: {e}")
        
        # Return MCP tools only
        if not tools:
            print("🔧 DEBUG: No MCP tools found")
        else:
            print(f"🔧 DEBUG: Using {len(tools)} MCP tools: {[t['name'] for t in tools]}")
            
        return tools

# Example usage and testing
async def example_usage():
    """Example of how to use StreamingToolExecutor"""
    
    executor = StreamingToolExecutor(profile_name="ziya")  # Use your AWS profile
    
    messages = [
        {"role": "user", "content": "Check the current directory and list its contents"}
    ]
    
    print("🔄 Starting streaming with tools...")
    
    async for chunk in executor.stream_with_tools(messages):
        if chunk['type'] == 'text':
            print(chunk['content'], end='', flush=True)
        elif chunk['type'] == 'tool_result':
            print(f"\n🔧 Tool executed: {chunk['tool_name']}")
            print(f"📄 Result: {chunk['result'][:200]}...")
        elif chunk['type'] == 'error':
            print(f"\n❌ Error: {chunk['content']}")
    
    print("\n✅ Streaming completed")

if __name__ == "__main__":
    asyncio.run(example_usage())
