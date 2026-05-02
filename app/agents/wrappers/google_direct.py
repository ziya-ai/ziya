import json
import asyncio
import re
from typing import List, Dict, Optional, AsyncIterator, Any, Tuple
import inspect
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from app.utils.logging_utils import logger
from langchain_core.tools import BaseTool
from google import genai
from google.genai import types
from app.providers.google_direct import _sanitize_schema_for_gemini

class DirectGoogleModel:
    """
    Direct Google Gemini model wrapper that uses the native google-generativeai
    SDK to support proper conversation history and native tool calling.
    """
    
    def __init__(self, model_name: str, temperature: float = 0.3, max_output_tokens: int = 8192, thinking_level: Optional[str] = None):
        self.model_name = model_name
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.thinking_level = thinking_level
        logger.info(f"DirectGoogleModel initialized: model={model_name}, temp={temperature}, max_output_tokens={max_output_tokens}")
        if thinking_level:
            logger.info(f"Gemini 3 thinking_level: {thinking_level}")
            logger.info(f"Using new Google GenAI SDK with thinking_level support")
        import os
        api_key = os.getenv('GOOGLE_API_KEY')
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
            return None
        
        # New SDK uses a different tool format        
        function_declarations = []
        for tool in tools:
            try:
                # Ensure schema is a dict
                schema = tool.args_schema.schema() if tool.args_schema else {"type": "object", "properties": {}}
                if not isinstance(schema, dict):
                    schema = schema.dict()

                # Strip JSON Schema keys Gemini rejects; see provider-level sanitizer.
                schema = _sanitize_schema_for_gemini(schema)
                
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
                part = types.Part(
                    function_response=types.FunctionResponse(
                        name=message.name,
                        response={"content": message.content}
                    )
                )
                # Merge into previous function message if it exists (for parallel tool calls)
                if google_messages and google_messages[-1]['role'] == 'tool':
                    google_messages[-1]['parts'].append(part)
                else:
                    google_messages.append({'role': 'tool', 'parts': [part]})
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
                    
                    elif part_type == 'image_url':
                        # LangChain standard format: {"type": "image_url", "image_url": {"url": "data:mime;base64,..."}}
                        import base64 as b64mod
                        url = part.get('image_url', {}).get('url', '')
                        if url.startswith('data:'):
                            # Parse data URI: data:mime_type;base64,data
                            header, data = url.split(',', 1)
                            mime_type = header.split(':')[1].split(';')[0]
                            google_parts.append({
                                'inline_data': {
                                    'mime_type': mime_type,
                                    'data': data
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
        Streams text responses from the Google Gemini model.

        Tool execution is handled upstream by StreamingToolExecutor +
        GoogleDirectProvider (app/providers/google_direct.py). This method
        is a pure text-streaming fallback used by _simple_invoke when no MCP
        manager is available. Any tools= kwarg is ignored.
        """
        tools = kwargs.get("tools", [])
        if tools:
            logger.warning(
                "DirectGoogleModel.astream() received tools but cannot dispatch them. "
                "Route tool-using calls through StreamingToolExecutor + GoogleDirectProvider."
            )
        history, system_instruction = self._convert_messages_to_google_format(messages)
        logger.info("Calling Google model (text-only fallback)...")
        try:
            gen_config_params = {
                "temperature": self.temperature,
                "max_output_tokens": self.max_output_tokens,
                "safety_settings": [
                    types.SafetySetting(
                        category=category,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE
                    ) for category in [
                        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                    ]
                ],
            }
            if system_instruction:
                gen_config_params["system_instruction"] = system_instruction
            supports_thinking = (
                "gemini-3" in self.model_name.lower() or
                "thinking" in self.model_name.lower()
            )
            if self.thinking_level and supports_thinking:
                gen_config_params["thinking_config"] = types.ThinkingConfig(
                    thinking_level=self.thinking_level.upper()
                )
            config = types.GenerateContentConfig(**gen_config_params)
            response = await self.client.aio.models.generate_content_stream(
                model=self.model_name,
                contents=history,
                config=config,
            )
            async for chunk in response:
                if chunk.parts:
                    for part in chunk.parts:
                        if part.text:
                            yield {"type": "text", "content": part.text}
        except Exception as e:
            error_message = f"Google API Error ({type(e).__name__}): {str(e)}"
            logger.error(error_message, exc_info=True)
            yield {"type": "error", "content": error_message}

    async def ainvoke(self, messages: List[BaseMessage], **kwargs) -> Dict[str, Any]:
        """
        Asynchronous invocation (non-streaming).
        Collects the full response and returns it.
        """
        response_content = ""
        async for chunk in self.astream(messages, **kwargs):
            if chunk.get("type") == "text":
                response_content += chunk.get("content", "")
        
        return {"content": response_content}

    def invoke(self, messages: List[BaseMessage], **kwargs) -> Dict[str, Any]:
        """
        Synchronous invocation.
        Wraps ainvoke in asyncio.run for compatibility.
        """
        return asyncio.run(self.ainvoke(messages, **kwargs))

    def bind(self, **kwargs):
        """Compatibility method - ignore stop sequences for Google."""
        return self
