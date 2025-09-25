import json
import asyncio
from typing import List, Dict, Optional, AsyncIterator, Any, Any
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from langchain_core.tools import BaseTool
from google import generativeai as genai
from google.generativeai import types
from app.utils.logging_utils import logger
from app.mcp.manager import get_mcp_manager

def _extract_text_from_mcp_result(result: Any) -> str:
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

    def _convert_langchain_tools_to_google(self, tools: List[BaseTool]) -> List[types.Tool]:
        """Converts LangChain tools to Google GenAI SDK Tool format."""
        if not tools:
            return []

        google_tools = []
        for tool in tools:
            try:
                schema = tool.args_schema.schema()
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
                    system_instruction = types.Content(parts=[types.Part(text=message.content)], role="system")
                else:
                    # If multiple system messages exist, append to the user message for context.
                    if google_messages and google_messages[-1]['role'] == 'user':
                         google_messages[-1]['parts'].append(types.Part(text=f"\n\nSystem Note: {message.content}"))
                    else:
                         google_messages.append({'role': 'user', 'parts': [types.Part(text=f"System Note: {message.content}")]})
                continue

            if role:
                google_messages.append({'role': role, 'parts': [types.Part(text=message.content)]})

        # The SDK expects the system instruction as a separate argument, not in the list.
        return google_messages, system_instruction

    def bind(self, **kwargs):
        """Compatibility method - ignore stop sequences for Google."""
        return self
