import json
import asyncio
import re
from typing import List, Dict, Optional, AsyncIterator, Any, Tuple, Union
import inspect
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from app.utils.logging_utils import logger
from app.mcp.manager import get_mcp_manager
from langchain_core.tools import BaseTool
from google import generativeai as genai
from google.generativeai import types

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
            # Check if GenerationConfig supports thinking_level
            gen_config_params = inspect.signature(types.GenerationConfig.__init__).parameters
            if 'thinking_level' not in gen_config_params:
                logger.warning("Current google-generativeai SDK doesn't support thinking_level. Install google-genai package for Gemini 3 support.")
                logger.warning("Continuing without thinking_level parameter...")
                self.thinking_level = None  # Disable for compatibility
        
        # Get API key from environment and configure genai
        import os
        api_key = os.getenv('GOOGLE_API_KEY')
        if api_key:
            genai.configure(api_key=api_key)
            logger.info("Configured Google GenAI with API key from environment")
        else:
            logger.info("No GOOGLE_API_KEY found, will attempt to use Application Default Credentials")

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

    def _convert_langchain_tools_to_google(self, tools: List[BaseTool]) -> List[types.Tool]:
        """Converts LangChain tools to Google GenAI SDK Tool format."""
        if not tools:
            return []
        
        google_tools = []
        for tool in tools:
            try:
                schema = tool.args_schema.schema() if tool.args_schema else {}
                function_declaration = types.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters=schema,
                )
                google_tools.append(types.Tool(function_declarations=[function_declaration]))
            except Exception as e:
                logger.warning(f"Could not convert tool '{tool.name}' to Google format: {e}")
        return google_tools

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

            if role:
                content = message.content
                # Clean out tool blocks from historical AI messages to avoid confusing the model
                if role == "model":
                    # Remove XML-style tool blocks (legacy/sentinel)
                    content = re.sub(r'<TOOL_SENTINEL>.*?</TOOL_SENTINEL>', '', content, flags=re.DOTALL)
                    # Remove markdown-style tool blocks
                    content = re.sub(r'\`\`\`tool:.*?\`\`\`', '', content, flags=re.DOTALL).strip()
                
                # Skip empty messages to avoid API errors
                if not content.strip():
                    continue

                # Merge consecutive messages of the same role to satisfy API requirements
                if google_messages and google_messages[-1]['role'] == role:
                    google_messages[-1]['parts'][0]['text'] += f"\n\n{content}"
                else:
                    google_messages.append({'role': role, 'parts': [{'text': content}]})

        # The SDK expects the system instruction as a separate argument, not in the list.
        return google_messages, system_instruction
    async def astream(self, messages: List[BaseMessage], **kwargs) -> AsyncIterator[Dict]:
        """
        Streams responses from the Google Gemini model, handling native tool calls correctly.
        """
        tools = kwargs.get("tools", [])
        google_tools = self._convert_langchain_tools_to_google(tools)
        history, system_instruction = self._convert_messages_to_google_format(messages)

        model = genai.GenerativeModel(
            self.model_name,
            system_instruction=system_instruction
        )

        # The main loop for handling multi-turn tool calls
        while True:
            logger.info("Calling Google model with history...")
            try:
                # Build generation config
                gen_config_params = {
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_output_tokens,
                }
                # Add thinking_level for Gemini 3 models
                # Only add if SDK supports it (google-genai, not google-generativeai)
                if self.thinking_level and 'thinking_level' in inspect.signature(types.GenerationConfig.__init__).parameters:
                    gen_config_params["thinking_level"] = self.thinking_level
                
                response = await model.generate_content_async(
                    history,
                    generation_config=types.GenerationConfig(**gen_config_params),
                    tools=google_tools if google_tools else None,
                    stream=True
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
                if hasattr(chunk, 'candidates') and chunk.candidates:
                    for candidate in chunk.candidates:
                        if hasattr(candidate, 'finish_reason') and candidate.finish_reason:
                            finish_reason = candidate.finish_reason
                            # Decode finish reason
                            try:
                                from google.ai.generativelanguage_v1beta.types import Candidate
                                finish_reason_name = Candidate.FinishReason(finish_reason).name
                            except:
                                finish_reason_name = str(finish_reason)
                            logger.info(f"Google model finish_reason: {finish_reason_name} ({finish_reason})")
                        if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                            logger.info(f"Google model safety_ratings: {candidate.safety_ratings}")
                
                if chunk.parts:
                    for part in chunk.parts:
                        if part.text:
                            yield {"type": "text", "content": part.text}
                        if part.function_call:
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
                        {"function_response": {"name": tool_name, "response": {"content": tool_result_str}}}
                    )
                except Exception as e:
                    error_message = f"Error executing tool {tool_name}: {e}"
                    logger.error(error_message)
                    yield {"type": "error", "content": error_message}
                    tool_results.append(
                        {"function_response": {"name": tool_name, "response": {"error": error_message}}}
                    )

            history.append({'role': 'function', 'parts': tool_results})

    def bind(self, **kwargs):
        """Compatibility method - ignore stop sequences for Google."""
        return self
