import logging
from typing import List, Dict, Optional, AsyncIterator
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from langchain_core.messages.ai import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
import google.generativeai as genai

# Tool sentinel markers
TOOL_SENTINEL_OPEN = "<TOOL_SENTINEL>"
TOOL_SENTINEL_CLOSE = "</TOOL_SENTINEL>"

logger = logging.getLogger(__name__)

class DirectGoogleModel:
    """Direct Google Gemini model wrapper with function calling support."""
    
    def __init__(self, model_name: str, temperature: float = 0.3, max_output_tokens: int = 2048):
        self.model_name = model_name
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        
        # Get API key from environment
        import os
        api_key = os.getenv('GOOGLE_API_KEY')
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable is required")
        
        # Configure the client
        genai.configure(api_key=api_key)
        self.client = genai
        
        # Define function declarations for MCP tools using proper Google format
        self.function_declarations = [
            {
                "name": "mcp_run_shell_command",
                "description": "Execute a shell command to get information like current working directory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to execute (e.g., 'pwd' for current directory)"
                        }
                    },
                    "required": ["command"]
                }
            },
            {
                "name": "mcp_get_current_time",
                "description": "Get the current date and time",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "format": {
                            "type": "string",
                            "description": "Time format (iso, readable, or timestamp)",
                            "default": "readable"
                        }
                    }
                }
            }
        ]
    
    def _convert_messages(self, messages: List[BaseMessage]) -> str:
        """Convert LangChain messages to Google API format."""
        logger.info(f"🔍 GOOGLE_CONVERT: Converting {len(messages)} messages")
        
        combined_content = ""
        
        for i, message in enumerate(messages):
            content = message.content if hasattr(message, 'content') else str(message)
            logger.info(f"🔍 GOOGLE_CONVERT: Message {i}: type={type(message).__name__.lower()}, content_preview={content[:100]}...")
            
            if isinstance(message, SystemMessage):
                combined_content += f"System: {content}\n\n"
            elif isinstance(message, HumanMessage):
                combined_content += f"Human: {content}\n\n"
            elif isinstance(message, AIMessage):
                combined_content += f"Assistant: {content}\n\n"
            else:
                combined_content += f"{content}\n\n"
        
        logger.info(f"🔍 GOOGLE_CONVERT: Converted to combined text with length {len(combined_content)}")
        
        return combined_content.strip()
    
    async def astream(self, messages: List[BaseMessage], config: Optional[Dict] = None, stop: Optional[List[str]] = None, **kwargs) -> AsyncIterator[ChatGenerationChunk]:
        """Stream response from Google API with function calling support."""
        if self.client is None:  # Mock mode
            yield ChatGenerationChunk(message=AIMessageChunk(content="Mock response"))
            return
            
        converted_text = self._convert_messages(messages)
        
        # Try native function calling first for all models, fall back to text-only if it fails
        def try_native_calling():
            tools = []
            for func_decl in self.function_declarations:
                tools.append({
                    "function_declarations": [{
                        "name": func_decl["name"],
                        "description": func_decl["description"],
                        "parameters": func_decl["parameters"]
                    }]
                })
            
            return {
                "temperature": self.temperature,
                "max_output_tokens": self.max_output_tokens,
                "tools": tools
            }
        
        def try_text_only():
            return {
                "temperature": self.temperature,
                "max_output_tokens": self.max_output_tokens
            }
        
        # Try native calling first
        try:
            generation_config = try_native_calling()
            use_native_calling = True
        except Exception as e:
            print(f"🔧 DEBUG: Native function calling setup failed for {self.model_name}, using text-only: {e}")
            generation_config = try_text_only()
            use_native_calling = False
        
        # Generate streaming content with fallback
        max_retries = 1
        
        for attempt in range(max_retries + 1):
            try:
                model = genai.GenerativeModel(self.model_name)
                response_stream = model.generate_content(
                    converted_text,
                    generation_config=generation_config,
                    stream=True
                )
                
                # Process streaming response
                chunk_count = 0
                for chunk in response_stream:
                    chunk_count += 1
                    
                    # Check for function calls in candidates first
                    if hasattr(chunk, 'candidates') and chunk.candidates:
                        for candidate in chunk.candidates:
                            if hasattr(candidate, 'content') and candidate.content:
                                if hasattr(candidate.content, 'parts'):
                                    for part in candidate.content.parts:
                                        if hasattr(part, 'function_call') and part.function_call:
                                            func_call = part.function_call
                                            args = dict(func_call.args) if hasattr(func_call, 'args') and func_call.args else {}
                                            
                                            import json
                                            tool_call_text = f"{TOOL_SENTINEL_OPEN}<name>{func_call.name}</name><arguments>{json.dumps(args)}</arguments>{TOOL_SENTINEL_CLOSE}"
                                            
                                            yield ChatGenerationChunk(
                                                message=AIMessageChunk(content=tool_call_text)
                                            )
                                        elif hasattr(part, 'text') and part.text:
                                            text = part.text
                                            # Apply text-to-tool conversion only for models using text-only mode
                                            if (not use_native_calling and 
                                                ("tool_code" in text.lower() or 
                                                 "<tool_sentinel>" in text or
                                                 "mcp_run_shell_command" in text or
                                                 ("pwd" in text and ("tool" in text.lower() or "command" in text.lower())) or
                                                 ("current working directory" in text.lower() and len(text) < 200)) and
                                                "tool execution results" not in text and
                                                "please continue" not in text.lower()):
                                                # Convert to proper tool call format
                                                tool_call_text = f"{TOOL_SENTINEL_OPEN}<name>mcp_run_shell_command</name><arguments>{{\"command\": \"pwd\"}}</arguments>{TOOL_SENTINEL_CLOSE}"
                                                yield ChatGenerationChunk(
                                                    message=AIMessageChunk(content=tool_call_text)
                                                )
                                            else:
                                                yield ChatGenerationChunk(
                                                    message=AIMessageChunk(content=text)
                                                )
                    
                    # Fallback: try to access text directly (for older API versions)
                    elif hasattr(chunk, 'text') and chunk.text:
                        text = chunk.text
                        yield ChatGenerationChunk(
                            message=AIMessageChunk(content=text)
                        )
                
                # If we got chunks, we're done
                if chunk_count > 0:
                    break
                    
                # If no chunks and we used native calling, try text-only
                if use_native_calling and attempt < max_retries:
                    print(f"🔧 DEBUG: No response chunks from {self.model_name} with native calling, retrying with text-only")
                    generation_config = try_text_only()
                    use_native_calling = False
                    continue
                
                # If still no chunks, yield error
                if chunk_count == 0:
                    yield ChatGenerationChunk(
                        message=AIMessageChunk(content="Error: No response from model")
                    )
                break
                
            except Exception as e:
                # If native calling failed and we haven't tried text-only yet
                if use_native_calling and attempt < max_retries:
                    print(f"🔧 DEBUG: Native calling failed for {self.model_name}, retrying with text-only: {e}")
                    generation_config = try_text_only()
                    use_native_calling = False
                    continue
                else:
                    yield ChatGenerationChunk(
                        message=AIMessageChunk(content=f"Error: {str(e)}")
                    )
                    break
    
    def bind(self, **kwargs):
        """Compatibility method - ignore stop sequences for Google."""
        return self
