import json
import asyncio
from typing import List, Dict, Optional, AsyncIterator, Any, Any
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
    
    def __init__(self, model_name: str, temperature: float = 0.3, max_output_tokens: int = 8192):
        self.model_name = model_name
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.mcp_manager = get_mcp_manager()

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

    def _convert_messages_to_google_format(self, messages: List[BaseMessage]) -> List[Dict]:
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
                    import re
                    content = re.sub(r'```tool:.*?```', '', content, flags=re.DOTALL).strip()
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
                response = await model.generate_content_async(
                    history,
                    generation_config=types.GenerationConfig(
                        temperature=self.temperature,
                        max_output_tokens=self.max_output_tokens,
                    ),
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

            async for chunk in response:
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

                    yield {"type": "tool_execution", "tool_name": tool_name, "result": tool_result_str}

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
