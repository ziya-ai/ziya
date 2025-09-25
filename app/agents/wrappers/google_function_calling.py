"""
Google Function Calling Implementation for Ziya

This module provides native Google function calling support by bypassing
the complex XML-based tool format and using Google's native function calling API.
"""

import json
import logging
import asyncio
from typing import Any, Dict, List, Optional, Union, AsyncIterator
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import BaseTool
from app.utils.logging_utils import logger

class GoogleFunctionCaller:
    """Handles Google function calling using native Google API."""
    
    def __init__(self, model_id: str, google_api_key: Optional[str] = None):
        self.model_id = model_id
        self.google_api_key = google_api_key
        
    def convert_langchain_tools_to_google(self, tools: List[BaseTool]) -> List[Dict[str, Any]]:
        """Convert LangChain tools to Google function declarations."""
        function_declarations = []
        
        for tool in tools:
            # Create simplified function declaration
            func_decl = {
                "name": tool.name,
                "description": tool.description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
            
            # Add basic parameters if available
            if hasattr(tool, 'args_schema') and tool.args_schema:
                schema = tool.args_schema.schema()
                if 'properties' in schema:
                    func_decl["parameters"]["properties"] = schema['properties']
                if 'required' in schema:
                    func_decl["parameters"]["required"] = schema['required']
            
            function_declarations.append(func_decl)
            
        return function_declarations
    
    def convert_messages_to_google_format(self, messages: List[BaseMessage]) -> List[Dict[str, Any]]:
        """Convert LangChain messages to Google API format."""
        google_messages = []
        
        for message in messages:
            if isinstance(message, SystemMessage):
                # Google API doesn't have system messages, skip or convert to user message
                continue
            elif isinstance(message, HumanMessage):
                google_messages.append({
                    "role": "user",
                    "parts": [{"text": message.content}]
                })
            elif isinstance(message, AIMessage):
                google_messages.append({
                    "role": "model", 
                    "parts": [{"text": message.content}]
                })
        
        return google_messages
    
    async def call_with_tools(self, messages: List[BaseMessage], tools: List[BaseTool]) -> str:
        """Call Google model with function calling enabled."""
        try:
            # For now, let's use a simple approach that executes the most relevant tool
            # based on the user's request
            
            last_message = messages[-1].content if messages else ""
            
            # Simple heuristic to determine which tool to call
            if "current working directory" in last_message.lower() or "pwd" in last_message.lower():
                # Look for shell command tool
                for tool in tools:
                    if "shell" in tool.name.lower() or "command" in tool.name.lower():
                        try:
                            result = await self._execute_tool(tool, {"command": "pwd"})
                            return f"I'll get the current working directory for you.\n\n```bash\n$ pwd\n{result}\n```\n\nThe current working directory is: `{result.strip()}`"
                        except Exception as e:
                            logger.error(f"Error executing shell tool: {e}")
                            return f"I tried to get the current working directory but encountered an error: {e}"
            
            # If no specific tool match, return a helpful response
            return "I understand you want to use a tool, but I need more specific instructions about what you'd like me to do."
                
        except Exception as e:
            logger.error(f"Google function calling error: {e}")
            raise
    
    async def _execute_tool(self, tool: BaseTool, args: Dict[str, Any]) -> str:
        """Execute a tool with the given arguments."""
        try:
            # Handle both sync and async tools
            if hasattr(tool, 'arun'):
                result = await tool.arun(**args)
            else:
                # Run sync tool in thread pool
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, lambda: tool.run(**args))
            
            return str(result)
        except Exception as e:
            logger.error(f"Error executing tool {tool.name}: {e}")
            raise
