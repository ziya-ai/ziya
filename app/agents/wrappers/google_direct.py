import json
import asyncio
import re
from typing import List, Dict, Optional, AsyncIterator, Any, Tuple, Union, TYPE_CHECKING
import inspect
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from app.utils.logging_utils import logger
from app.mcp.manager import get_mcp_manager
from langchain_core.tools import BaseTool
from google import genai
from google.genai import types

class DirectGoogleModel:
    """
    Direct Google Gemini model wrapper that uses the native google-generativeai
    SDK to support proper conversation history and native tool calling.
    """
    
    def __init__(self, model_name: str, temperature: float = 0.3, max_output_tokens: int = 8192, thinking_level: Optional[str] = None):
        self.model_name = model_name
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.thinking_level = thinking_level  # "low", "medium", "high", or None
        self.mcp_manager = get_mcp_manager()
        
        
        logger.info(f"DirectGoogleModel initialized: model={model_name}, temp={temperature}, max_output_tokens={max_output_tokens}")
        if thinking_level:
            logger.info(f"Gemini 3 thinking_level: {thinking_level}")
            # New SDK has full Gemini 3 support
            logger.info(f"Using new Google GenAI SDK with thinking_level support")
        
        # Get API key from environment and configure genai
        import os
        api_key = os.getenv('GOOGLE_API_KEY')
        
        # Create client with new SDK
        self.client = genai.Client(api_key=api_key) if api_key else genai.Client()
        logger.info("Created Google GenAI client")

    def _extract_text_from_mcp_result(self, result: Any) -> str:
        """Extracts the text content from a structured MCP tool result."""
        if not isinstance(result, dict) or 'content' not in result:
            return str(result)

        content = result['content']
        if not isinstance(content, list) or not content:
            return str(result)

        first_item = content[0]
        if isinstance(first_item, dict) and 'text' in first_item:
            return first_item['text']

        return str(result)

    def _convert_langchain_tools_to_google(self, tools: List[BaseTool]) -> Optional[types.Tool]:
        """Converts LangChain tools to Google GenAI SDK Tool format."""
        if not tools:
            return []
        
        # New SDK uses a different tool format
        from google.genai import types
        
        function_declarations = []
        for tool in tools:
            try:
                schema = tool.args_schema.schema() if tool.args_schema else {}
                
                # New SDK format
                func_decl = types.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters=schema
                )
                function_declarations.append(func_decl)
            except Exception as e:
                logger.warning(f"Could not convert tool '{tool.name}' to Google format: {e}")
        
        # Return single Tool with all function declarations
        return types.Tool(function_declarations=function_declarations) if function_declarations else None

    def _convert_messages_to_google_format(self, messages: List[BaseMessage]) -> Tuple[List[Dict], Optional[str]]:
        """
        Converts LangChain messages to the structured format required by the
        Google GenAI SDK, preserving the conversational turn structure.
        """
        google_messages = []
        system_instruction = None
        for message in messages:
            role = ""
            if isinstance(message, HumanMessage):
                role = "user"
            elif isinstance(message, AIMessage):
                role = "model"
            elif isinstance(message, SystemMessage):
                # The Google SDK handles a single system instruction separately.
                if not system_instruction:
                    system_instruction = message.content
                else:
                    # If multiple system messages exist, append to the user message for context.
                    if google_messages and google_messages[-1]['role'] == 'user':
                         google_messages[-1]['parts'][0]['text'] += f"\n\nSystem Note: {message.content}"
                    else:
                         google_messages.append({'role': 'user', 'parts': [{'text': f"System Note: {message.content}"}]})
                continue
            elif isinstance(message, ToolMessage):
                # Handle tool results (FunctionResponse)
                part = types.FunctionResponse(
                    name=message.name,
                    response={"content": message.content}
                )
                # Merge into previous function message if it exists (for parallel tool calls)
                if google_messages and google_messages[-1]['role'] == 'function':
                    google_messages[-1]['parts'].append(part)
                else:
                    google_messages.append({'role': 'function', 'parts': [part]})
                continue

            if role:
                content = message.content
                
                # Clean out tool blocks from historical AI messages to avoid confusing the model
                # CRITICAL: Only clean string content, not multimodal lists (images)
                if role == "model" and isinstance(content, str):
                    # Remove XML-style tool blocks (legacy/sentinel)
                    content = re.sub(r'<TOOL_SENTINEL>.*?</TOOL_SENTINEL>', '', content, flags=re.DOTALL)
                    # Remove markdown-style tool blocks
                    content = re.sub(r'```tool:.*?```', '', content, flags=re.DOTALL).strip()
                
                # Skip empty messages to avoid API errors
                # Handle both string content and multimodal content (list with images)
                if isinstance(content, str):
                    if not content.strip():
                        continue
                elif isinstance(content, list):
                    # Multimodal content - check if it has any parts
                    if not content:
                        continue
                else:
                    # Unknown content type
                    continue

                # Merge consecutive messages of the same role to satisfy API requirements
                if google_messages and google_messages[-1]['role'] == role:
                    # Only merge text content, not multimodal lists
                    if isinstance(content, str):
                        # Try to append to existing text part, or add new text part
                        last_parts = google_messages[-1]['parts']
                        text_part_found = False
                        for part in last_parts:
                            if 'text' in part:
                                part['text'] += f"\n\n{content}"
                                text_part_found = True
                                break
                        if not text_part_found:
                            # No text part exists, add a new one
                            last_parts.append({'text': content})
                    else:
                        # Can't merge multimodal content, add as new message
                        google_messages.append({'role': role, 'parts': self._format_content_parts(content)})
                else:
                    google_messages.append({'role': role, 'parts': self._format_content_parts(content)})

        # The SDK expects the system instruction as a separate argument, not in the list.
        return google_messages, system_instruction
    
    def _format_content_parts(self, content):
        """Format content into Google API parts format."""
        if isinstance(content, str):
            return [{'text': content}]
        elif isinstance(content, list):
            # Convert from Claude/LangChain format to Google format
            google_parts = []
            
            for part in content:
                if isinstance(part, str):
                    # Plain string part
                    google_parts.append({'text': part})
                elif isinstance(part, dict):
                    part_type = part.get('type')
                    
                    if part_type == 'text':
                        # Text part: {"type": "text", "text": "..."}
                        google_parts.append({'text': part.get('text', '')})
                    
                    elif part_type == 'image':
                        # Claude/Bedrock format: {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}
                        source = part.get('source', {})
                        if source.get('type') == 'base64':
                            google_parts.append({
                                'inline_data': {
                                    'mime_type': source.get('media_type', 'image/jpeg'),
                                    'data': source.get('data', '')
                                }
                            })
                    
                    elif 'text' in part:
                        # Direct text in dict
                        google_parts.append({'text': part['text']})
                    
                    elif 'inline_data' in part:
                        # Already in Google format
                        google_parts.append(part)
            
            return google_parts if google_parts else [{'text': ''}]
        else:
            return [{'text': str(content)}]
    async def astream(self, messages: List[BaseMessage], **kwargs) -> AsyncIterator[Dict]:
        """
        Streams responses from the Google Gemini model, handling native tool calls correctly.
        """
        from google.genai import types
        
        tools = kwargs.get("tools", [])
        google_tool = self._convert_langchain_tools_to_google(tools)
        history, system_instruction = self._convert_messages_to_google_format(messages)

        # The main loop for handling multi-turn tool calls
        while True:
            logger.info("Calling Google model with history...")
            try:
                # Build generation config
                gen_config_params = {
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_output_tokens,
                }
                
                # Add system instruction to config if available
                if system_instruction:
                    gen_config_params["system_instruction"] = system_instruction
                    logger.info(f"Added system instruction to config (length: {len(system_instruction)})")
                
                # Add thinking_config for Gemini 3 models (fully supported in new SDK)
                # Only apply if thinking_level is set AND model supports it
                supports_thinking = (
                    "gemini-3" in self.model_name.lower() or 
                    "thinking" in self.model_name.lower()
                )
                if self.thinking_level and supports_thinking:
                    # Convert thinking_level to ThinkingConfig format
                    gen_config_params["thinking_config"] = types.ThinkingConfig(
                        thinking_level=self.thinking_level.upper()  # Convert "low" to "LOW", etc.
                    )
                    logger.info(f"Applied thinking_config with level: {self.thinking_level}")
                
                # Use new SDK's async streaming API
                config = types.GenerateContentConfig(**gen_config_params)
                
                # Add tools if available
                if google_tool:
                    config.tools = [google_tool]
                    # Explicitly set tool config to AUTO to ensure the model knows it can use them
                    config.tool_config = types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(
                            mode=types.FunctionCallingConfigMode.AUTO
                        )
                    )
                # Build request parameters
                request_params = {
                    'model': self.model_name,
                    'contents': history,
                    'config': config
                }
                
                
                response = await self.client.aio.models.generate_content_stream(
                    **request_params
                )
            except Exception as e:
                error_message = f"Google API Error ({type(e).__name__}): {str(e)}"
                logger.error(error_message, exc_info=True)
                yield {"type": "error", "content": error_message}
                return

            tool_calls = []
            model_response_parts = []
            finish_reason = None
            finish_reason_name = None

            async for chunk in response:
                # Log finish reason if present
                # New SDK chunk structure
                if hasattr(chunk, 'candidates'):
                    for candidate in chunk.candidates:
                        if hasattr(candidate, 'finish_reason'):
                            finish_reason = candidate.finish_reason
                            finish_reason_name = str(finish_reason)
                            logger.info(f"Google model finish_reason: {finish_reason_name}")
                        if hasattr(candidate, 'safety_ratings'):
                            logger.info(f"Google model safety_ratings: {candidate.safety_ratings}")
                
                if chunk.parts:
                    for part in chunk.parts:
                        if part.text:
                            yield {"type": "text", "content": part.text}
                        if hasattr(part, 'function_call') and part.function_call:
                            tool_calls.append(part.function_call)

                if chunk.candidates:
                    for candidate in chunk.candidates:
                        if candidate.content and candidate.content.parts:
                            model_response_parts.extend(candidate.content.parts)
            
            logger.info(f"Stream ended. Tool calls: {len(tool_calls)}, Finish reason: {finish_reason_name or finish_reason}")

            if not tool_calls:
                logger.info("No tool calls from model. Ending loop.")
                break

            logger.info(f"Model returned {len(tool_calls)} tool call(s).")
            history.append({'role': 'model', 'parts': model_response_parts})

            tool_results = []
            for tool_call in tool_calls:
                tool_name = tool_call.name
                tool_args = dict(tool_call.args) if hasattr(tool_call, 'args') and tool_call.args else {}

                yield {"type": "tool_start", "tool_name": tool_name, "input": tool_args}

                try:
                    tool_result_obj = await self.mcp_manager.call_tool(tool_name, tool_args)
                    tool_result_str = self._extract_text_from_mcp_result(tool_result_obj)

                    yield {"type": "tool_display", "tool_name": tool_name, "result": tool_result_str}

                    tool_results.append(
                        types.FunctionResponse(name=tool_name, response={"content": tool_result_str})
                    )
                except Exception as e:
                    error_message = f"Error executing tool {tool_name}: {e}"
                    logger.error(error_message)
                    yield {"type": "error", "content": error_message}
                    tool_results.append(
                        types.FunctionResponse(name=tool_name, response={"error": error_message})
                    )

            # Add tool results to history in new format
            if tool_results:
                history.append({'role': 'function', 'parts': tool_results})

    def bind(self, **kwargs):
        """Compatibility method - ignore stop sequences for Google."""
        return self
