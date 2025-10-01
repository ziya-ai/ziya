"""
Nova wrapper for AWS Bedrock.

This module provides a wrapper for the Nova model in AWS Bedrock.
"""

import json
import os
from typing import Any, Dict, List, Optional, Iterator, AsyncIterator

import boto3
from botocore.client import BaseClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from app.utils.aws_utils import ThrottleSafeBedrock

from app.utils.logging_utils import logger
from app.agents.custom_message import ZiyaString, ZiyaMessageChunk
from app.utils.custom_bedrock import CustomBedrockClient
from app.agents.wrappers.nova_formatter import NovaFormatter
from pydantic import Field, field_validator


class NovaWrapper(BaseChatModel):
    """
    Wrapper for the Nova model in AWS Bedrock.
    """
    
    model_id: str
    client: Any = None  # Changed from Optional[BaseClient] to Any to accept any client type
    model_kwargs: Dict[str, Any] = {}
    
    def format_request_for_streaming(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Format request body for Nova models in streaming context."""
        # Remove anthropic_version parameter
        body.pop("anthropic_version", None)
        
        # Handle model-specific parameter restrictions
        if 'nova-micro' in self.model_id.lower():
            # Nova-micro doesn't support tools or max_tokens
            body.pop("max_tokens", None)
            body.pop("maxTokens", None)
            body.pop("tool_choice", None)
            body.pop("tools", None)
        elif 'nova-lite' in self.model_id.lower():
            # Nova-lite supports tools but not max_tokens
            body.pop("max_tokens", None)
            body.pop("tool_choice", None)
        
        # Nova models expect system message as array format
        if "system" in body and isinstance(body["system"], str):
            body["system"] = [{"text": body["system"]}]
        
        # Nova models expect message content as array format
        if "messages" in body:
            for message in body["messages"]:
                if "content" in message and isinstance(message["content"], str):
                    message["content"] = [{"text": message["content"]}]
        
        return body
    
    def __init__(self, model_id: str, **kwargs):
        """
        Initialize the NovaWrapper.
        
        Args:
            model_id: The model ID to use
            **kwargs: Additional arguments to pass to the model
        """
        super().__init__(model_id=model_id, **kwargs)
        
        # Initialize the client
        self.client = boto3.client("bedrock-runtime")
        
        # Get max_tokens from kwargs or model_kwargs
        max_tokens = kwargs.get("max_tokens")
        if max_tokens is None and "max_tokens" in self.model_kwargs:
            max_tokens = self.model_kwargs.get("max_tokens")
        
        # Wrap the client with our custom client to ensure max_tokens is correctly passed
        if max_tokens is not None:
            self.client = CustomBedrockClient(self.client, max_tokens=max_tokens)
            logger.info(f"Wrapped boto3 client with CustomBedrockClient in NovaWrapper, max_tokens={max_tokens}")
        
        # Set default model parameters - only include parameters that are provided
        self.model_kwargs = {}
        
        # Always include max_tokens and top_p which are supported by all Nova models
        # Check environment variable dynamically for frontend updates
        self.model_kwargs["max_tokens"] = kwargs.get("max_tokens", self._get_current_max_tokens())
        self.model_kwargs["top_p"] = kwargs.get("top_p", 0.9)
        
        # Only include temperature if it's provided (will be filtered by supported_parameters)
        if "temperature" in kwargs:
            self.model_kwargs["temperature"] = kwargs.get("temperature")
            
        # Only include top_k if it's provided (will be filtered by supported_parameters)
        if "top_k" in kwargs:
            self.model_kwargs["top_k"] = kwargs.get("top_k")
    
    def _get_current_max_tokens(self):
        """Get current max_tokens, checking environment variable first (for frontend updates)"""
        env_max_tokens = os.environ.get("ZIYA_MAX_OUTPUT_TOKENS")
        if env_max_tokens:
            try:
                return int(env_max_tokens)
            except ValueError:
                pass
        
        # Fallback to model config default
        return self._get_model_config_max_tokens()
    
    def _get_model_config_max_tokens(self):
        """Get max_tokens from model config like LangChain version did"""
        try:
            from app.agents.models import ModelManager
            
            # Get current model config
            state = ModelManager.get_state()
            current_model_alias = state.get('current_model_alias', 'nova-pro')
            
            model_config = ModelManager.get_model_config('bedrock', current_model_alias)
            
            # Use default_max_output_tokens from config, fallback to max_output_tokens, then 4096
            default_max = model_config.get('default_max_output_tokens')
            if default_max:
                return default_max
                
            return model_config.get('max_output_tokens', 4096)
            
        except Exception:
            return 4096  # Fallback only if config lookup fails
            
        logger.info(f"Initialized NovaWrapper with model_kwargs: {self.model_kwargs}")
    
    def _format_messages(self, messages: List[BaseMessage]) -> Dict[str, Any]:
        """
        Format messages for the Nova model.
        
        Args:
            messages: The messages to format
        
        Returns:
            Dict[str, Any]: The formatted messages
        """
        logger.info(f"Formatting {len(messages)} messages for Nova")
        
        # Convert messages to the format expected by Nova
        formatted_messages = []
        
        # Extract system message content to prepend to ALL user messages for conversational memory
        system_content = ""
        for message in messages:
            if isinstance(message, SystemMessage):
                system_content += message.content + "\n\n"
        
        # Convert messages to dict format first
        message_dicts = []
        for message in messages:
            if isinstance(message, HumanMessage):
                content = message.content
                # Prepend system content to ALL user messages to maintain conversational memory
                if system_content:
                    content = f"{system_content}\n\n{content}"
                
                message_dicts.append({
                    "role": "user",
                    "content": content
                })
            elif isinstance(message, AIMessage):
                message_dicts.append({
                    "role": "assistant",
                    "content": message.content
                })
            elif isinstance(message, SystemMessage):
                # Skip system messages - they're handled separately
                continue
            elif isinstance(message, ChatMessage):
                role = message.role
                if role == "human" or role == "user":
                    role = "user"
                elif role == "ai" or role == "assistant":
                    role = "assistant"
                else:
                    # Skip unsupported roles
                    logger.warning(f"Skipping unsupported role: {role}")
                    continue
                
                content = message.content
                # Prepend system content to user messages for conversational memory
                if role == "user" and system_content:
                    content = f"{system_content}\n\n{content}"
                
                message_dicts.append({
                    "role": role,
                    "content": content
                })
            else:
                content = str(message.content)
                # Prepend system content to user messages for conversational memory
                if system_content:
                    content = f"{system_content}\n\n{content}"
                
                message_dicts.append({
                    "role": "user",
                    "content": content
                })
        
        # Use NovaFormatter to format the messages
        formatted_messages = NovaFormatter.format_messages(message_dicts)
        
        # Add model parameters
        result = {
            "messages": formatted_messages,
            "temperature": self.model_kwargs.get("temperature", 0.7),
            "top_p": self.model_kwargs.get("top_p", 0.9),
            "max_tokens": self.model_kwargs.get("max_tokens", self._get_current_max_tokens()),
        }
        
        return result
    
    def _parse_response(self, response: Dict[str, Any]) -> ZiyaString:
        """
        Parse the response from the Nova model.
        
        Args:
            response: The response from the Nova model
        
        Returns:
            ZiyaString: The parsed response
        """
        logger.info("=== NOVA WRAPPER parse_response START ===")
        
        # Use NovaFormatter to parse the response
        text = NovaFormatter.parse_response(response)
        
        # Create a ZiyaString from the parsed text
        if text:
            result = ZiyaString(text, id=f"nova-{hash(text) % 10000}", message=text)
            logger.info(f"Parsed response text length: {len(text)}")
            logger.info("=== NOVA WRAPPER parse_response END ===")
            return result
        
        # If we couldn't extract the text, return an error message
        error_message = f"Failed to parse Nova response: {str(response)[:100]}..."
        logger.error(error_message)
        
        logger.info("=== NOVA WRAPPER parse_response END ===")
        return ZiyaString(error_message, id=f"nova-error-{hash(error_message) % 10000}", message=error_message)
    
    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """
        Generate a response from the Nova model.
        
        Args:
            messages: The messages to generate a response for
            stop: Optional stop sequences
            run_manager: Optional run manager
            **kwargs: Additional arguments
        
        Returns:
            ChatResult: The generated response
        """
        logger.info("=== NOVA WRAPPER _generate START ===")
        
        # Format the messages
        request_body = self._format_messages(messages)
        logger.info(f"Formatted {len(messages)} messages")
        
        # Use model_kwargs from the instance which have been filtered for supported parameters
        inference_params = {
            "max_tokens": self.model_kwargs.get("max_tokens", self._get_current_max_tokens()),
            "top_p": self.model_kwargs.get("top_p", 0.9),
            "stop_sequences": stop if stop else []
        }
        
        # Only include temperature if it's in model_kwargs
        if "temperature" in self.model_kwargs:
            inference_params["temperature"] = self.model_kwargs.get("temperature")
            
        logger.info(f"Using inference parameters: {inference_params}")
        
        # Format inference parameters using NovaFormatter
        inference_config, additional_fields = NovaFormatter.format_inference_params(inference_params)
        
        try:
            # Call the model
            logger.info(f"Calling Bedrock converse with model_id: {self.model_id}")
            response = self.client.converse(
                modelId=self.model_id,
                messages=request_body["messages"],
                inferenceConfig=inference_config
            )
            logger.info("Bedrock converse API call completed successfully")
            
            # Parse the response
            text = self._parse_response(response)
            
            # Clean any empty brackets from the response
            if isinstance(text, str) and ('[]' in text):
                cleaned_text = text.strip('[]')
                if cleaned_text != text:
                    logger.info(f"Cleaned empty brackets from Nova streaming response")
                    text = cleaned_text

            logger.info("About to parse response")
            text = self._parse_response(response)
            logger.info(f"Parsed response text length: {len(text)}")
            
            # Create an AIMessage with the text
            ai_message = AIMessage(content=text)
            
            # Create a Generation object
            logger.info("Creating Generation object directly")
            generation = ChatGeneration(
                message=ai_message,
                generation_info={"model_id": self.model_id}
            )
            logger.info(f"Created Generation with text length: {len(text)}")
            
            # Add id attribute to the Generation object
            if isinstance(text, ZiyaString):
                object.__setattr__(generation, 'id', text.id)
            else:
                object.__setattr__(generation, 'id', f"nova-{hash(text) % 10000}")
            
            logger.info(f"Generation type: {type(generation)}")
            logger.info(f"Generation has message attribute: {hasattr(generation, 'message')}")
            logger.info(f"Generation has id attribute: {hasattr(generation, 'id')}")
            
            # Create the result
            result = ChatResult(generations=[generation])
            
            logger.info("=== NOVA WRAPPER _generate END ===")
            return result
        except Exception as e:
            logger.error(f"Error calling Nova model: {str(e)}")
            logger.info("=== NOVA WRAPPER _generate END ===")
            raise
    
    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGeneration]:
        """
        Stream a response from the Nova model asynchronously.
        """
        logger.info("=== NOVA WRAPPER _astream START ===")
        
        # Format the messages
        request_body = self._format_messages(messages)
        
        # Use model_kwargs from the instance
        inference_params = {
            "max_tokens": self.model_kwargs.get("max_tokens", self._get_current_max_tokens()),
            "top_p": self.model_kwargs.get("top_p", 0.9),
            "stop_sequences": stop if stop else []
        }
        
        if "temperature" in self.model_kwargs:
            inference_params["temperature"] = self.model_kwargs.get("temperature")
            
        # Format inference parameters using NovaFormatter
        inference_config, additional_fields = NovaFormatter.format_inference_params(inference_params)
        
        try:
            # Call the streaming model
            response = self.client.converse_stream(
                modelId=self.model_id,
                messages=request_body["messages"],
                inferenceConfig=inference_config
            )
            
            # Process streaming response with thinking content detection
            accumulated_text = ""
            thinking_buffer = ""
            in_thinking = False
            thinking_content = ""
            final_text = ""
            
            for chunk in response.get('stream', []):
                if 'contentBlockDelta' in chunk:
                    delta = chunk.get('contentBlockDelta', {})
                    if 'delta' in delta and 'text' in delta['delta']:
                        text = delta['delta']['text']
                        accumulated_text += text
                        
                        # Handle thinking content detection
                        thinking_buffer += text
                        
                        # Check for thinking start
                        if '<thinking>' in thinking_buffer and not in_thinking:
                            in_thinking = True
                            logger.info("Nova _astream: Detected thinking start")
                            # Extract any content before <thinking>
                            parts = thinking_buffer.split('<thinking>', 1)
                            if parts[0].strip():
                                final_text += parts[0]
                            thinking_buffer = parts[1] if len(parts) > 1 else ""
                            continue
                        
                        # Check for thinking end
                        if '</thinking>' in thinking_buffer and in_thinking:
                            in_thinking = False
                            logger.info("Nova _astream: Detected thinking end")
                            # Extract thinking content
                            parts = thinking_buffer.split('</thinking>', 1)
                            thinking_content += parts[0]
                            # Continue with remaining content
                            thinking_buffer = parts[1] if len(parts) > 1 else ""
                            if thinking_buffer.strip():
                                final_text += thinking_buffer
                            thinking_buffer = ""
                            continue
                        
                        # If we're in thinking mode, don't add to final text
                        if in_thinking:
                            continue
                        
                        # If we have accumulated non-thinking content, add it to final text
                        if thinking_buffer and not in_thinking:
                            final_text += thinking_buffer
                            thinking_buffer = ""
                        
                elif 'messageStop' in chunk:
                    break
            
            # Handle any remaining content
            if thinking_buffer and not in_thinking:
                final_text += thinking_buffer
            
            # Create the final content with thinking tags if we found thinking content
            final_content = ""
            if thinking_content.strip():
                final_content += f"<thinking>{thinking_content}</thinking>\n\n"
            final_content += final_text
            
            # Yield the complete response as a single chunk
            if final_content:
                from langchain_core.messages import AIMessageChunk
                chunk_message = AIMessageChunk(content=final_content)
                generation = ChatGeneration(
                    message=chunk_message,
                    generation_info={"model_id": self.model_id}
                )
                yield generation
            
            logger.info("=== NOVA WRAPPER _astream END ===")
            
        except Exception as e:
            logger.error(f"Error calling Nova streaming model: {str(e)}")
            raise
    
    def format_nova_pro_request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format request body specifically for Nova Pro model.
        Nova Pro has very strict parameter requirements.
        
        Args:
            body: The original request body
            
        Returns:
            Dict[str, Any]: The formatted request body for Nova Pro
        """
        logger.info(f"Formatting request for Nova Pro model: {self.model_id}")
        
        # Nova Pro uses minimal format - only messages and system
        nova_pro_body = {
            "messages": body["messages"]
        }
        
        # Handle system message format - Nova Pro expects array format
        if "system" in body:
            system_content = body["system"]
            if isinstance(system_content, str):
                nova_pro_body["system"] = [{"text": system_content}]
            else:
                nova_pro_body["system"] = system_content
        
        # Handle message content format - Nova Pro expects array format for content
        formatted_messages = []
        for message in body["messages"]:
            formatted_message = {"role": message["role"]}
            
            # Convert content to array format if it's a string
            content = message.get("content", "")
            if isinstance(content, str):
                formatted_message["content"] = [{"text": content}]
            else:
                formatted_message["content"] = content
                
            formatted_messages.append(formatted_message)
        
        nova_pro_body["messages"] = formatted_messages
        
        logger.info(f"Nova Pro formatted body keys: {list(nova_pro_body.keys())}")
        return nova_pro_body
    
    async def parse_text_based_tools(self, text: str) -> List[Dict[str, Any]]:
        """Parse Nova Pro text-based tool calls and return tool execution results"""
        import re
        
        print(f"🔧 DEBUG: Parsing Nova Pro text for tool calls: {text[:200]}...")
        
        # Multiple patterns to match different Nova Pro tool call formats
        patterns = [
            r'```(?:mcp_)?(\w+)\s*\n([^`]+)\n```',  # ```mcp_run_shell_command\npwd\n```
            r'```\s*\n(?:mcp_)?(\w+)\s*\n```',      # ```\nmcp_get_current_time\n```
            r'```(?:mcp_)?(\w+)```'                  # ```mcp_tool_name```
        ]
        
        results = []
        for pattern in patterns:
            matches = re.findall(pattern, text, re.MULTILINE | re.DOTALL)
            
            for match in matches:
                if isinstance(match, tuple):
                    tool_name = match[0]
                    command = match[1] if len(match) > 1 else ""
                else:
                    tool_name = match
                    command = ""
                
                print(f"🔧 DEBUG: Found Nova Pro tool call: {tool_name} with command: '{command.strip()}'")
                
                # Map tool names and execute
                if tool_name in ['run_shell_command', 'mcp_run_shell_command']:
                    result = await self._execute_shell_command(command.strip() or 'pwd')
                    if result:
                        result = f"$ {command.strip() or 'pwd'}\n{result}"
                elif tool_name in ['get_current_time', 'mcp_get_current_time']:
                    result = await self._execute_mcp_tool('get_current_time', {})
                else:
                    print(f"🔧 DEBUG: Unknown Nova Pro tool: {tool_name}")
                    continue
                
                if result:
                    results.append({
                        'type': 'tool_display',
                        'tool_id': f'nova_text_{hash(f"{tool_name}_{command}") % 10000}',
                        'tool_name': f'mcp_{tool_name.replace("mcp_", "")}',
                        'result': result
                    })
                    print(f"🔧 DEBUG: Nova Pro text tool executed successfully: {result[:100]}...")
        
        return results
    
    async def _execute_shell_command(self, command: str) -> str:
        """Execute shell command safely"""
        if not command.strip():
            return "Empty command"
        
        dangerous_patterns = ['rm -rf /', 'sudo rm', 'mkfs', 'dd if=', ':(){ :|:& };:']
        if any(pattern in command.lower() for pattern in dangerous_patterns):
            return "Command blocked for safety reasons"
        
        try:
            import asyncio
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=1024*1024
            )
            
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
            output = stdout.decode('utf-8', errors='replace') + stderr.decode('utf-8', errors='replace')
            
            if len(output) > 10000:
                output = output[:10000] + "\n... (output truncated)"
            
            return output if output.strip() else "(command completed with no output)"
            
        except asyncio.TimeoutError:
            return f"Command timed out after 15 seconds"
        except Exception as e:
            return f"Error executing command '{command}': {str(e)}"
    
    async def _execute_mcp_tool(self, tool_name: str, args: dict) -> str:
        """Execute MCP tool"""
        try:
            from app.mcp.manager import get_mcp_manager
            
            actual_tool_name = tool_name[4:] if tool_name.startswith('mcp_') else tool_name
            mcp_manager = get_mcp_manager()
            
            result = await mcp_manager.call_tool(actual_tool_name, args)
            
            if isinstance(result, dict) and 'content' in result:
                content = result['content']
                if isinstance(content, list) and len(content) > 0:
                    if isinstance(content[0], dict) and 'text' in content[0]:
                        return content[0]['text']
                    else:
                        return str(content[0])
                else:
                    return str(content)
            else:
                return str(result)
                
        except Exception as e:
            return f"MCP tool '{tool_name}' failed: {str(e)}"

    async def stream_with_tools(self, body: Dict[str, Any], tools: List[Dict[str, Any]], bedrock_client: Any):
        """Handle Nova model streaming using Converse API."""
        logger.info(f"Nova stream_with_tools called with {len(tools)} tools")
        logger.info(f"Nova tools: {[tool.get('name', 'unnamed') for tool in tools]}")
        
        try:
            # Format messages for Converse API
            messages = body.get("messages", [])
            system_prompt = body.get("system", "")
            
            # Format system prompt for Nova
            system_config = None
            if system_prompt:
                if isinstance(system_prompt, str):
                    system_config = [{"text": system_prompt}]
                else:
                    system_config = system_prompt
            
            # Format messages for Nova Converse API
            formatted_messages = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                
                if role not in ["user", "assistant"]:
                    role = "user"
                
                if isinstance(content, str):
                    formatted_content = [{"text": content}]
                else:
                    formatted_content = content
                    
                formatted_messages.append({
                    "role": role,
                    "content": formatted_content
                })
            
            # Prepare inference config
            inference_config = {
                "maxTokens": 4096,
                "temperature": 0.7,
                "topP": 0.9
            }
            
            # Prepare converse request
            converse_params = {
                "modelId": self.model_id,
                "messages": formatted_messages,
                "inferenceConfig": inference_config
            }
            
            if system_config:
                converse_params["system"] = system_config
                
            # Add tools if supported (disable toolConfig for now due to Nova inconsistencies)
            # Nova models sometimes generate XML-style tools even with toolConfig
            if False and tools and any(model in self.model_id.lower() for model in ['nova-pro', 'nova-premier']):
                # Convert tools to Nova Converse API format
                nova_tools = []
                for tool in tools:
                    nova_tool = {
                        "toolSpec": {
                            "name": tool["name"],
                            "description": tool["description"],
                            "inputSchema": {
                                "json": tool["input_schema"]
                            }
                        }
                    }
                    nova_tools.append(nova_tool)
                
                converse_params["toolConfig"] = {
                    "tools": nova_tools,
                    "toolChoice": {"auto": {}}
                }
            
            logger.info(f"Nova Converse request keys: {list(converse_params.keys())}")
            
            # Use proper Nova tool execution
            logger.info("🔧 DEBUG: About to call execute_nova_tools_properly")
            from app.agents.wrappers.nova_tool_execution import execute_nova_tools_properly
            
            accumulated_content = ""
            chunks_buffer = []
            
            async for chunk in execute_nova_tools_properly(bedrock_client, converse_params, formatted_messages):
                chunks_buffer.append(chunk)
                
                # Check if Nova is using XML-style tools instead of Converse API
                if chunk.get('type') == 'text':
                    content = chunk.get('content', '')
                    accumulated_content += content
                    
                    # Detect XML-style tool formats
                    if any(pattern in accumulated_content for pattern in [
                        '{TOOL_SENTINEL', 'TOOL_SENTINEL_OPEN', '{mcp_'
                    ]):
                        logger.warning("Nova is using XML-style tools, falling back to regular streaming")
                        # Signal that we need to fall back to regular streaming
                        raise Exception("NOVA_FALLBACK_NEEDED")
                
                # Normal processing - yield the chunk
                yield chunk
                
        except Exception as e:
            # If XML tools detected, re-raise to let streaming executor handle fallback
            if "NOVA_FALLBACK_NEEDED" in str(e):
                logger.warning("XML tools detected, re-raising for streaming executor fallback")
                raise e
            
            # If any Nova model fails with toolConfig, fall back to XML-style tools
            elif "modelStreamErrorException" in str(e):
                logger.warning(f"Nova toolConfig failed, falling back to XML-style tools: {e}")
                
                # Remove toolConfig and let it fall back to XML-style tool processing
                converse_params_fallback = converse_params.copy()
                converse_params_fallback.pop('toolConfig', None)
                
                try:
                    response = bedrock_client.converse_stream(**converse_params_fallback)
                    
                    for chunk in response.get('stream', []):
                        if 'contentBlockDelta' in chunk:
                            delta = chunk.get('contentBlockDelta', {})
                            if 'delta' in delta and 'text' in delta['delta']:
                                yield {
                                    'type': 'text',
                                    'content': delta['delta']['text']
                                }
                        elif 'messageStop' in chunk:
                            break
                            
                except Exception as fallback_error:
                    logger.error(f"Nova fallback also failed: {fallback_error}")
                    yield {
                        'type': 'error',
                        'content': f"Nova streaming error: {str(fallback_error)}"
                    }
            else:
                logger.error(f"Nova streaming error: {str(e)}")
                yield {
                    'type': 'error',
                    'content': f"Nova streaming error: {str(e)}"
                }
            yield {
                'type': 'error',
                'content': f"Nova streaming error: {str(e)}"
            }
    


    @property
    def _llm_type(self) -> str:
        """
        Get the LLM type.
        
        Returns:
            str: The LLM type
        """
        return "nova"

class NovaBedrock(NovaWrapper):
    """
    Alias for NovaWrapper for backward compatibility.
    """
    pass
