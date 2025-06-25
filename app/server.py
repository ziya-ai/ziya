import os
import os.path
import re
import asyncio
import signal
import time
import threading
import json
import hashlib
import asyncio
import uuid
import traceback
from typing import Dict, Any, List, Tuple, Optional, Union
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from starlette.background import BackgroundTask
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import signal
from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketDisconnect

import tiktoken
from fastapi import FastAPI, Request, HTTPException, APIRouter, routing
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langserve import add_routes
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from app.agents.agent import model, RetryingChatBedrock, initialize_langserve
from app.agents.agent import agent, agent_executor, create_agent_chain, create_agent_executor
from app.agents.agent import update_conversation_state, update_and_return, parse_output
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError 
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

# Import configuration
import app.config as config
from app.agents.models import ModelManager
from app.config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
from app.agents.wrappers.nova_wrapper import NovaBedrock  # Import NovaBedrock for isinstance check
from botocore.exceptions import ClientError, BotoCoreError, CredentialRetrievalError
from botocore.exceptions import EventStreamError
import botocore.errorfactory
from starlette.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from langchain_core.outputs import Generation

# import pydevd_pycharm
from google.api_core.exceptions import ResourceExhausted
import uvicorn

from app.utils.code_util import use_git_to_apply_code_diff, correct_git_diff
from app.utils.code_util import PatchApplicationError, split_combined_diff, extract_target_file_from_diff
from app.utils.directory_util import get_ignored_patterns

# Initialize extensions
from app.extensions import init_extensions
init_extensions()
from app.utils.logging_utils import logger
from app.utils.gitignore_parser import parse_gitignore_patterns
from app.utils.error_handlers import (
    create_json_response, create_sse_error_response, 
    is_streaming_request, ValidationError, handle_request_exception,
    handle_streaming_error
)
from app.utils.diff_utils import apply_diff_pipeline
from app.utils.custom_exceptions import ThrottlingException, ExpiredTokenException
from app.utils.custom_exceptions import ValidationError
from app.utils.file_utils import read_file_content
from app.middleware import RequestSizeMiddleware, ModelSettingsMiddleware, ErrorHandlingMiddleware, HunkStatusMiddleware
from app.utils.context_enhancer import initialize_ast_if_enabled
from fastapi.websockets import WebSocketState

def build_messages_for_streaming(question: str, chat_history: List, files: List, conversation_id: str) -> List[BaseMessage]:
    """
    Build messages for streaming using the extended prompt template.
    This centralizes message construction to avoid duplication.
    """
    
    # Prevent duplicate calls by checking if we're already processing this conversation
    cache_key = f"building_{conversation_id}"
    if hasattr(build_messages_for_streaming, cache_key):
        logger.warning(f"Preventing duplicate message construction for {conversation_id}")
        return getattr(build_messages_for_streaming, cache_key)

    from app.agents.prompts_manager import get_extended_prompt, get_model_info_from_config
    from app.agents.agent import get_combined_docs_from_files, _format_chat_history
    
    model_info = get_model_info_from_config()
    
    # Get MCP context
    mcp_context = {}
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        if mcp_manager.is_initialized:
            available_tools = [tool.name for tool in mcp_manager.get_all_tools()]
            mcp_context = {
                "mcp_tools_available": len(available_tools) > 0,
                "available_mcp_tools": available_tools
            }
    except Exception as e:
        logger.warning(f"Could not get MCP tools: {e}")
    
    # Get file context
    # Don't load file context here - it will be loaded by the template's codebase parameter
    file_context = ""
    
    # Apply post-instructions to the question once here
    from app.utils.post_instructions import PostInstructionManager
    modified_question = PostInstructionManager.apply_post_instructions(
        query=question,
        model_name=model_info["model_name"],
        model_family=model_info["model_family"],
        endpoint=model_info["endpoint"]
    )
    
    # Get the extended prompt and format it properly
    extended_prompt = get_extended_prompt(
        model_name=model_info["model_name"],
        model_family=model_info["model_family"],
        endpoint=model_info["endpoint"],
        context=mcp_context
    )
    
    # Get available tools for the template
    tools_list = []
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        if mcp_manager.is_initialized:
            tools_list = [f"- {tool.name}: {tool.description}" for tool in mcp_manager.get_all_tools()]
    except Exception as e:
        logger.warning(f"Could not get tools for template: {e}")
    
    # Format messages using the extended prompt template
    formatted_messages = extended_prompt.format_messages(
        codebase=file_context,  # This will be empty, template will call extract_codebase
        question=modified_question,
        chat_history=_format_chat_history(chat_history),
        ast_context="",  # Will be enhanced if AST is enabled
        tools="\n".join(tools_list) if tools_list else "No tools available",
        TOOL_SENTINEL_OPEN=TOOL_SENTINEL_OPEN,
        TOOL_SENTINEL_CLOSE=TOOL_SENTINEL_CLOSE
    )
    
    # Cache the result to prevent duplicate calls
    setattr(build_messages_for_streaming, cache_key, formatted_messages)
    # Clean up cache after a short delay to prevent memory leaks
    
    return formatted_messages
    logger.info("CONTEXT CONSTRUCTION DETAILS:")
    logger.info(f"File context length: {len(file_context)} characters")
    logger.info(f"Modified question length: {len(modified_question)} characters")
    logger.info(f"Chat history items: {len(chat_history)}")
    logger.info(f"Available tools: {len(tools_list)}")
    logger.info(f"MCP tools available: {mcp_context.get('mcp_tools_available', False)}")

    # DEBUG: Check template substitution
    print(f"=== TEMPLATE SUBSTITUTION DEBUG ===")
    print(f"Template variables being substituted:")
    print(f"- codebase length: {len(file_context)}")
    print(f"- question length: {len(modified_question)}")
    print(f"- chat_history items: {len(_format_chat_history(chat_history))}")
    print(f"- tools count: {len(tools_list)}")
    
    formatted_messages = extended_prompt.format_messages(
        codebase=file_context,
        question=modified_question,
        chat_history=_format_chat_history(chat_history),
        ast_context="",  # Will be enhanced if AST is enabled
        tools="\n".join(tools_list) if tools_list else "No tools available",
        TOOL_SENTINEL_OPEN=TOOL_SENTINEL_OPEN,
        TOOL_SENTINEL_CLOSE=TOOL_SENTINEL_CLOSE
    )
    
    # DEBUG: Check if template substitution caused duplication
    for i, msg in enumerate(formatted_messages):
        if hasattr(msg, 'content'):
            file_markers_count = msg.content.count('File: ')
            if file_markers_count > 0:
                print(f"Message {i} after template substitution has {file_markers_count} file markers")
    
    return formatted_messages

# Dictionary to track active streaming tasks
active_streams = {}

# Use configuration from config module
# For model configurations, see app/config.py

class SetModelRequest(BaseModel):
    model_id: str

class PatchRequest(BaseModel):
    diff: str
    file_path: Optional[str] = None
    
class FolderRequest(BaseModel):
    directory: str
    max_depth: int = 3
    
class FileRequest(BaseModel):
    file_path: str
    
class FileContentRequest(BaseModel):
    file_path: str
    content: str

# Create the FastAPI app
app = FastAPI(
    title="Ziya API",
    description="API for Ziya, a code assistant powered by LLMs",
    version="0.1.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add request size middleware
app.add_middleware(
    RequestSizeMiddleware,
    default_max_size_mb=20  # 20MB
)

# Add model settings middleware
app.add_middleware(ModelSettingsMiddleware)

# Add error handling middleware
app.add_middleware(ErrorHandlingMiddleware)

# Add hunk status middleware
app.add_middleware(HunkStatusMiddleware)
# Import and include AST routes
from app.routes.ast_routes import router as ast_router
app.include_router(ast_router)

# Add connection state tracking middleware
@app.middleware("http")
async def connection_state_middleware(request: Request, call_next):
    """Track connection state to handle disconnections gracefully."""
    try:
        # Initialize connection state
        request.state.disconnected = False
        
        response = await call_next(request)
        return response
    except Exception as e:
        # Check if this is a connection-related error
        error_str = str(e).lower()
        if any(term in error_str for term in ['connection', 'broken pipe', 'client disconnect']):
            logger.debug(f"Connection error detected: {e}")
            request.state.disconnected = True
        raise


# Import and include MCP routes
from app.routes.mcp_routes import router as mcp_router
app.include_router(mcp_router)

# Import and include AST routes
from app.routes.ast_routes import router as ast_router
initialize_ast_if_enabled()

# Dictionary to track active WebSocket connections
active_websockets = set()
hunk_status_updates = []

def get_templates_dir():
    """Get the templates directory."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    app_templates_dir = os.path.join(current_dir, "templates")
    
    if os.path.exists(app_templates_dir):
        logger.info(f"Found templates in app package: {app_templates_dir}")
        return app_templates_dir
    
    # Create minimal templates if none exist
    os.makedirs(app_templates_dir, exist_ok=True)
    index_html = os.path.join(app_templates_dir, 'index.html')
    if not os.path.exists(index_html):
        with open(index_html, 'w') as f:
            f.write("""<!DOCTYPE html>
<html><head><title>Ziya</title></head>
<body><h1>Ziya</h1><p>API available at <a href="/docs">/docs</a></p></body>
</html>""")
    
    return app_templates_dir

templates_dir = get_templates_dir()
templates = Jinja2Templates(directory=templates_dir)

# Mount static files from templates directory
static_dir = os.path.join(templates_dir, "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    logger.info(f"Mounted static files from {static_dir}")

# Initialize MCP manager on startup
@app.on_event("startup")
async def startup_event():
    """Initialize MCP manager when the server starts."""
    # Check if MCP is enabled
    if not os.environ.get("ZIYA_ENABLE_MCP", "false").lower() in ("true", "1", "yes"):
        logger.info("MCP integration is disabled. Use --mcp flag to enable.")
        return
        
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        await mcp_manager.initialize()
        
        # Log MCP initialization status
        if mcp_manager.is_initialized:
            status = mcp_manager.get_server_status()
            connected_servers = sum(1 for s in status.values() if s["connected"])
            total_tools = sum(s["tools"] for s in status.values())
            logger.info(f"MCP initialized: {connected_servers} servers connected, {total_tools} tools available")
            
            # Reinitialize the agent chain now that MCP is available
            logger.info("Reinitializing agent chain with MCP tools...")
            global agent, agent_executor
            # Force garbage collection to ensure clean state
            import gc; gc.collect()
            from app.agents.agent import create_agent_chain, create_agent_executor, model
            agent = create_agent_chain(model.get_model())
            agent_executor = create_agent_executor(agent)
            
            # Reinitialize langserve routes with the updated agent
            initialize_langserve(app, agent_executor)
            logger.info("Agent chain reinitialized with MCP tools")
        else:
            logger.warning("MCP initialization failed or no servers configured")
        logger.info("MCP manager initialized successfully during startup")
    except Exception as e:
        logger.warning(f"MCP initialization failed during startup: {str(e)}")

# Cleanup MCP manager on shutdown
@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup MCP manager when the server shuts down."""
    # Only shutdown if MCP was enabled
    if not os.environ.get("ZIYA_ENABLE_MCP", "false").lower() in ("true", "1", "yes"):
        return
        
    try:
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        await mcp_manager.shutdown()
        logger.info("MCP manager shutdown completed")
    except Exception as e:
        logger.warning(f"MCP shutdown failed: {str(e)}")

# Add a route for the frontend
add_routes(app, agent_executor, disabled_endpoints=["playground", "stream_log", "stream", "invoke"], path="/ziya")

# Add custom stream_log endpoint for compatibility
@app.post("/ziya/stream_log")
async def stream_log_endpoint(request: Request, body: dict):
    """Stream log endpoint with proper diff parameter handling."""
    try:
        # Debug logging
        logger.info("Stream log endpoint request body:")
        
        # Extract and store diff parameter if present
        diff_content = None
        if 'diff' in body:
            diff_content = body['diff']
            # Create a copy of the body without the diff parameter
            body_copy = {k: v for k, v in body.items() if k != 'diff'}
        else:
            body_copy = body
            
        # Extract input from body if present
        if 'input' in body_copy:
            input_data = body_copy['input']
            
            # Get the question from input_data
            question = input_data.get('question', 'EMPTY')
            logger.info(f"Question from input: '{question}'")
            
            # Handle chat_history
            chat_history = input_data.get('chat_history', [])
            if not isinstance(chat_history, list):
                logger.warning(f"Chat history is not a list: {type(chat_history)}")
                chat_history = []
            
            # Log chat history details for debugging
            logger.info(f"Chat history length: {len(chat_history)}")
            for i, msg in enumerate(chat_history):
                if isinstance(msg, dict):
                    logger.info(f"Input chat history item {i}: type={msg.get('type', 'unknown')}")
                else:
                    logger.info(f"Input chat history item {i}: type={type(msg)}")
            
            input_data['chat_history'] = chat_history
            
            # Handle config and files
            config = input_data.get('config', {})
            files = []
            if isinstance(config, dict):
                files = config.get("files", [])
            elif isinstance(config, list):
                logger.warning("Config is a list, assuming it's the files list")
                files = config
            
            if not isinstance(files, list):
                logger.warning(f"Files is not a list: {type(files)}")
                files = []
                
            # Count string files for summary logging
            string_file_count = sum(1 for f in files if isinstance(f, str))
            if string_file_count > 0:
                logger.info(f"Files count: {len(files)} ({string_file_count} are strings)")
            else:
                logger.info(f"Files count: {len(files)}")
            # Don't log individual file details here - too verbose
            
            # Update input_data with normalized values
            input_data['chat_history'] = chat_history
            input_data['config'] = {'files': files} if isinstance(config, list) else config
            
            # Ensure we use the current question from input_data
            input_data['question'] = question
            body_copy = input_data
        
        # Use direct streaming with StreamingResponse
        return StreamingResponse(
            stream_chunks(body_copy),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                "Content-Type": "text/event-stream"
            }
        )
    except Exception as e:
        logger.error(f"Error in stream_log_endpoint: {str(e)}")
        # Return error as streaming response
        error_json = json.dumps({"error": str(e)})
        return StreamingResponse(
            (f"data: {error_json}\n\ndata: {json.dumps({'done': True})}\n\n" for _ in range(1)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                "Content-Type": "text/event-stream"
            }
        )

async def cleanup_stream(conversation_id: str):
    """Clean up resources when a stream ends or is aborted."""
    if conversation_id in active_streams:
        logger.info(f"Cleaning up stream for conversation: {conversation_id}")
        # Remove only this specific stream from active streams
        del active_streams[conversation_id]
        # Any other cleanup needed
        logger.info(f"Stream cleanup complete for conversation: {conversation_id}")
    else:
        logger.warning(f"Attempted to clean up non-existent stream: {conversation_id}")

async def detect_and_execute_mcp_tools(full_response: str, processed_calls: Optional[set] = None) -> str:
    def clean_internal_sentinels(text: str) -> str:
        """Remove any tool sentinel fragments that might have leaked into the response."""
        import re
        # Remove complete tool sentinels
        text = text.replace(TOOL_SENTINEL_OPEN, "")
        text = text.replace(TOOL_SENTINEL_CLOSE, "")
        # Remove partial fragments
        text = re.sub(r'<TOOL_[^>]*>', '', text)
        text = re.sub(r'<name>[^<]*</name>', '', text)
        text = re.sub(r'<arguments>[^<]*</arguments>', '', text)
        # Preserve ```tool: blocks - they are the expected frontend format
        return text
    """
    Detect MCP tool calls in the complete response and execute them.
    
    Args:
        full_response: The complete response text from the model
        
    Returns:
        Response with tool calls executed and results inserted
    """
    # Initialize processed_calls if not provided
    if processed_calls is None:
        processed_calls = set()

    from app.mcp.tools import parse_tool_call
    from app.mcp.manager import get_mcp_manager
    import re
    
    # Check if response contains tool calls
    if TOOL_SENTINEL_OPEN not in full_response:
        return full_response
    
    # Find all tool call blocks
    tool_call_pattern = re.escape(TOOL_SENTINEL_OPEN) + r'.*?' + re.escape(TOOL_SENTINEL_CLOSE)
    tool_calls = re.findall(tool_call_pattern, full_response, re.DOTALL)
    
    if not tool_calls:
        return full_response
    
    modified_response = full_response
    
    for tool_call_block in tool_calls:
        logger.debug(f"🔍 MCP: Processing tool call block: {tool_call_block[:100]}...")

        # Create a signature for this tool call to detect duplicates
        tool_signature = hashlib.md5(tool_call_block.encode()).hexdigest()
        
        # Skip if we've already processed this exact tool call
        if tool_signature in processed_calls:
            logger.debug(f"🔍 MCP: Skipping previously processed tool call: {tool_signature[:8]}")
            continue
        processed_calls.add(tool_signature)
        
        # Parse the tool call
        parsed_call = parse_tool_call(tool_call_block)
        if not parsed_call:
            logger.warning("🔍 MCP: Could not parse tool call")
            continue
        
        tool_name = parsed_call["tool_name"]
        arguments = parsed_call["arguments"]
        
        logger.debug(f"🔍 MCP: Executing tool {tool_name} with args: {arguments}")
        
        try:
            # Get MCP manager and execute the tool
            mcp_manager = get_mcp_manager()
            if not mcp_manager.is_initialized:
                logger.error("🔍 MCP: Manager not initialized")
                continue
            
            # Execute the tool (remove mcp_ prefix if present for internal lookup)
            internal_tool_name = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
            result = await mcp_manager.call_tool(internal_tool_name, arguments)
            
            if result is None:
                logger.error(f"🔍 MCP: Tool {internal_tool_name} returned None")
                continue
            
            # Format the result
            if isinstance(result, dict) and "content" in result:
                if isinstance(result["content"], list) and len(result["content"]) > 0:
                    tool_output = result["content"][0].get("text", str(result["content"]))
                else:
                    tool_output = str(result["content"])
            else:
                tool_output = str(result)
            
            logger.debug(f"🔍 MCP: Tool executed successfully, output: {tool_output[:100]}...")
            
            # Replace the tool call with properly formatted tool block
            clean_output = clean_internal_sentinels(tool_output)
            replacement = f"\n```tool:{tool_name}\n{clean_output.strip()}\n```\n"
            modified_response = modified_response.replace(tool_call_block, replacement)
            
        except Exception as e:
            logger.error(f"🔍 MCP: Error executing tool {tool_name}: {str(e)}")
            # Replace tool call with error message
            error_msg = f"\n\n**Tool Error:** {str(e)}\n\n"
            modified_response = modified_response.replace(tool_call_block, error_msg)
    
    # Final cleanup to ensure no fragments remain
    return clean_internal_sentinels(modified_response)
async def stream_chunks(body):
    """Stream chunks from the agent executor."""
    logger.error("🔍 EXECUTION_TRACE: stream_chunks() called - ENTRY POINT")
    logger.info("🔍 STREAM_CHUNKS: Function called")
    # Send heartbeat to keep connection alive (don't send processing message as it appears in the UI)
    yield f"data: {json.dumps({'heartbeat': True, 'type': 'heartbeat'})}\n\n"
    
    # Track if we've successfully sent any data
    data_sent = False

    # Prepare messages for the model
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

    # Extract all needed variables from request body
    question = body.get("question", "")
    chat_history = body.get("chat_history", [])
    config_data = body.get("config", {})
    files = config_data.get("files", [])
    
    # Extract conversation_id from multiple possible locations
    conversation_id = body.get("conversation_id")
    if not conversation_id:
        conversation_id = config_data.get("conversation_id")
    if not conversation_id:
        conversation_id = f"stream_{uuid.uuid4().hex[:8]}"
        
    logger.info(f"🔍 STREAM_CHUNKS: Using conversation_id: {conversation_id}")

    # Use centralized message construction to eliminate all duplication
    messages = build_messages_for_streaming(question, chat_history, files, conversation_id)
    
    # COMPLETE CONTEXT OUTPUT - Log the entire context being sent to the model
    logger.info("=" * 100)
    logger.info("COMPLETE MODEL CONTEXT OUTPUT - FINAL STAGE BEFORE MODEL")
    logger.info("=" * 100)
    logger.info(f"Conversation ID: {conversation_id}")
    logger.info(f"Question: {question}")
    logger.info(f"Total Messages: {len(messages)}")
    logger.info(f"Files Count: {len(files)}")
    logger.info(f"Chat History Length: {len(chat_history)}")
    logger.info("-" * 80)
    
    for i, message in enumerate(messages):
        logger.info(f"MESSAGE {i+1}/{len(messages)} - TYPE: {message.type}")
        logger.info(f"ROLE: {getattr(message, 'role', 'N/A')}")
        logger.info(f"CONTENT LENGTH: {len(message.content) if hasattr(message, 'content') and message.content else 0} characters")
        logger.info("CONTENT START:")
        logger.info("▼" * 50)
        if hasattr(message, 'content') and message.content:
            # Log first 500 and last 500 characters for very long content
            content = message.content
            if len(content) > 1000:
                logger.info(content[:500])
                logger.info(f"... [TRUNCATED - {len(content) - 1000} characters omitted] ...")
                logger.info(content[-500:])
            else:
                logger.info(content)
        logger.info("▲" * 50)
        logger.info("CONTENT END")
        logger.info("-" * 40)
    
    logger.info(f"Built {len(messages)} messages using centralized construction")
 
    # Create config dict with conversation_id for caching
    config = {"conversation_id": conversation_id} if conversation_id else {}
    
    # Set up connection monitoring
    connection_active = True
    
    try:
        # Get the question from the request body
        question = body.get("question", "")

        # Extract conversation ID from the request body
        conversation_id = body.get("conversation_id")
        if not conversation_id:
            # Also check if it's nested in config
            logger.info("🔍 STREAM: No conversation_id in body, checking config...")
            config = body.get("config", {})
            conversation_id = config.get("conversation_id")
        if not conversation_id:
            # Check if it's in the config
            config = body.get("config", {})
            conversation_id = config.get("conversation_id")
        
        # Only generate a stream ID as last resort
        if not conversation_id:
            import uuid
            conversation_id = f"stream_{uuid.uuid4().hex[:8]}"
            logger.warning(f"No conversation_id provided, generated: {conversation_id}")
        else:
            logger.info(f"Using provided conversation_id: {conversation_id}")
            logger.info(f"🔍 STREAM: Final conversation_id: {conversation_id}")
        # Register this stream as active
        active_streams[conversation_id] = {
            "start_time": time.time(),
            "question": question[:100] + "..." if len(question) > 100 else question
        }

        chat_history = body.get("chat_history", [])
        
        # Get the files from the config if available
        config = body.get("config", {})
        files = []
        
        # Handle different possible formats of config and files
        if isinstance(config, dict):
            files = config.get("files", [])
        elif isinstance(config, list):
            # If config is a list, assume it's the files list
            files = config
        
        # Ensure files is a list
        if not isinstance(files, list):
            files = []
        
        
        # Get the model instance using the proper method
        from app.agents.agent import model
        model_instance = model.get_model()
        
        # Prepare the messages for the model
        messages = []
        
        # Add system message with file context if available
        if files:
            from langchain_core.messages import SystemMessage
            # Get the extended system template instead of creating a simple file context
            from app.agents.prompts_manager import get_extended_prompt, get_model_info_from_config
            model_info = get_model_info_from_config()
            
            # Get MCP tools for context
            mcp_tools_available = False
            available_mcp_tools = []
            try:
                from app.mcp.manager import get_mcp_manager
                mcp_manager = get_mcp_manager()
                if mcp_manager.is_initialized:
                    available_mcp_tools = [tool.name for tool in mcp_manager.get_all_tools()]
                    mcp_tools_available = len(available_mcp_tools) > 0
            except Exception as e:
                logger.warning(f"Could not get MCP tools for system template: {e}")
            
            # Apply all extensions to get the complete system template
            from app.utils.prompt_extensions import PromptExtensionManager
            from app.agents.prompts import original_template
            
            system_template = PromptExtensionManager.apply_extensions(
                prompt=original_template,
                model_name=model_info["model_name"],
                model_family=model_info["model_family"],
                endpoint=model_info["endpoint"],
                context={
                    "mcp_tools_available": mcp_tools_available,
                    "available_mcp_tools": available_mcp_tools
                }
            )
            
            # Add file context to the extended template
            # Use the existing codebase extraction logic
            from app.agents.agent import extract_codebase
            file_context = extract_codebase({"config": {"files": files}, "conversation_id": conversation_id})
            complete_system_content = system_template.replace("{codebase}", file_context)
            
            messages.append(SystemMessage(content=complete_system_content))
        
        # Add chat history if available
        if chat_history:
            from langchain_core.messages import HumanMessage, AIMessage
            
            for msg in chat_history:
                # Handle case where msg is a tuple (role, content) from cleaned chat history
                if isinstance(msg, tuple) and len(msg) == 2:
                    role, content = msg
                    
                    if role == "human":
                        messages.append(HumanMessage(content=content))
                    elif role == "ai":
                        messages.append(AIMessage(content=content))
                # Handle case where msg is a dictionary with 'type' and 'content' keys
                elif isinstance(msg, dict) and 'type' in msg and 'content' in msg:
                    msg_type = msg["type"]
                    msg_content = msg["content"]
                    
                    if msg_type == "human":
                        messages.append(HumanMessage(content=msg_content))
                    elif msg_type == "ai":
                        messages.append(AIMessage(content=msg_content))
                # Handle other formats
                else:
                    # Try other formats as a fallback
                    try:
                        if hasattr(msg, 'role') and hasattr(msg, 'content'):
                            role = getattr(msg, 'role')
                            content = getattr(msg, 'content')
                            if role == "human" or role == "user":
                                messages.append(HumanMessage(content=content))
                            elif role == "ai" or role == "assistant":
                                messages.append(AIMessage(content=content))
                    except Exception as e:
                        logger.error(f"[INSTRUMENTATION] stream_chunks failed to process chat history message: {e}")
                        # Continue with other messages
        # Add the current question
        from langchain_core.messages import HumanMessage
        
        # Only add the current question if it's not already in the chat history
        # Check if the last message in chat history is the same as the current question
        should_add_current_question = True
        if chat_history and len(chat_history) > 0:
            last_msg = chat_history[-1]
            if isinstance(last_msg, tuple) and len(last_msg) == 2 and last_msg[0] == "human" and last_msg[1] == question:
                should_add_current_question = False
        
        if should_add_current_question and not question:
            if isinstance(body, dict) and 'input' in body and isinstance(body['input'], dict):
                input_question = body['input'].get('question', '')
                if input_question:
                    question = input_question
        
        # Apply post-instructions to the question
        from app.utils.post_instructions import PostInstructionManager
        from app.agents.prompts_manager import get_model_info_from_config
        
        # Get model information
        model_info = get_model_info_from_config()
        model_name = model_info.get("model_name")
        model_family = model_info.get("model_family")
        endpoint = model_info.get("endpoint")
        
        # Apply post-instructions
        modified_question = PostInstructionManager.apply_post_instructions(
            query=question,
            model_name=model_name,
            model_family=model_family,
            endpoint=endpoint
        )
        
        logger.debug(f"Original question: {question[:100]}...")
        logger.debug(f"Modified question with post-instructions: {modified_question[:100]}...")
        
        if should_add_current_question:
            messages.append(HumanMessage(content=modified_question))
        
        # Stream directly from the model
        
        # Enhance context with AST if available
        from app.utils.context_enhancer import enhance_context_with_ast
        enhanced_context = enhance_context_with_ast(question, {"codebase": "current"})
        if enhanced_context.get("ast_context"):
            logger.info(f"Enhanced context with AST: {len(enhanced_context['ast_context'])} chars")
        
        chunk_count = 0
        full_response = ""

        # Initialize variables for agent iteration loop
        processed_tool_calls = set()
        max_iterations = 10
        iteration = 0
        messages_for_model = []
        all_tool_results = []  # Track all tool results across iterations

        logger.info(f"🔍 STREAM_CHUNKS: Using model instance type: {type(model.get_model())}")
        logger.info(f"🔍 STREAM_CHUNKS: Model has tools: {hasattr(model.get_model(), 'tools') if hasattr(model.get_model(), 'tools') else 'No tools attribute'}")
        logger.info("🔍 STREAM_CHUNKS: About to start model streaming")

        done_marker_sent = False
        
        processed_tool_calls = set()  # Track which tool calls we've already processed
        # Create a background task for cleanup when the stream ends
        # Set up the model with the stop sequence
        # This ensures the model will properly stop at the sentinel
        model_with_stop = model_instance.bind(stop=["</tool_input>"])

        # Get MCP tools for the iteration
        mcp_tools = []
        try:
            from app.mcp.tools import create_mcp_tools
            mcp_tools = create_mcp_tools()
            logger.info(f"🔍 STREAM_CHUNKS: Created {len(mcp_tools)} MCP tools for iteration")
        except Exception as e:
            logger.warning(f"Failed to get MCP tools for iteration: {e}")
        logger.info(f"🔍 STREAM_CHUNKS: model_with_stop type: {type(model_with_stop)}")

        # Agent iteration loop for tool execution
        while iteration < max_iterations:
            iteration += 1
            logger.info(f"🔍 AGENT ITERATION {iteration}: Starting iteration")

            current_response = ""
            tool_executed = False
        
            try:                
                # Use model with stop sequence for tool detection
                model_to_use = model_with_stop
                logger.info(f"🔍 AGENT ITERATION {iteration}: Available tools: {[tool.name for tool in mcp_tools] if mcp_tools else 'No tools'}")

                async for chunk in model_to_use.astream(messages, config=config):
                    # Log the actual messages being sent to model on first iteration
                    if iteration == 1 and not hasattr(stream_chunks, '_logged_model_input'):
                        stream_chunks._logged_model_input = True
                        logger.info("🔥" * 50)
                        logger.info("FINAL MODEL INPUT - ACTUAL MESSAGES SENT TO MODEL")
                        logger.info("🔥" * 50)
                        for idx, msg in enumerate(messages):
                            logger.info(f"FINAL MESSAGE {idx+1}: {type(msg).__name__}")
                            logger.info(f"CONTENT: {msg.content}")
                            if hasattr(msg, 'additional_kwargs') and msg.additional_kwargs:
                                logger.info(f"ADDITIONAL_KWARGS: {msg.additional_kwargs}")
                            logger.info("-" * 30)
                        logger.info("🔥" * 50)

                    # Check connection status
                    if not connection_active:
                        logger.info("Connection lost during agent iteration")
                        break
                    # Process chunk content

                    if hasattr(chunk, 'content'):
                        # Check if this is an error response chunk
                        if (hasattr(chunk, 'response_metadata') and 
                            chunk.response_metadata and 
                            chunk.response_metadata.get('error_response')):
                            # This is an error response, handle it specially
                            logger.info(f"🔍 AGENT: Detected error response chunk")
                            # The content should already be JSON formatted
                            yield f"data: {chunk.content}\n\n"
                            yield f"data: {json.dumps({'done': True})}\n\n"
                            return
                        
                        content = chunk.content() if callable(chunk.content) else chunk.content
                        content_str = str(content) if content else ""
                    else:
                        content_str = str(chunk)

                    # Skip empty chunks
                    if not content_str: 
                        continue

                    # Check if this content is actually an error response that should be handled specially
                    if content_str.strip().startswith('{"error":') and '"validation_error"' in content_str:
                        logger.info("🔍 AGENT: Detected validation error in model response, converting to proper error handling")
                        try:
                            error_data = json.loads(content_str.strip().replace('[DONE]', ''))
                            # Don't stream this as content, instead raise an exception to be handled by middleware
                            from app.utils.custom_exceptions import ValidationError
                            raise ValidationError(error_data.get('detail', 'Validation error occurred'))
                        except (json.JSONDecodeError, ValueError):
                            logger.warning("Failed to parse error JSON, treating as regular content")
                            # Fall through to normal processing

                    # Always accumulate content in current_response for tool detection
                    current_response += content_str
 
                    # Check if we should suppress this content from streaming
                    should_suppress = (
                        TOOL_SENTINEL_OPEN in current_response or 
                        TOOL_SENTINEL_CLOSE in current_response or
                        content_str.strip().startswith('<TOOL') or
                        content_str.strip().endswith('_call') or
                        TOOL_SENTINEL_CLOSE.lstrip('<') in content_str or
                        # Only suppress internal tool sentinels, not frontend tool blocks
                        '<TOOL_' in content_str or
                        any(marker in content_str for marker in ['<name>', '</name>', '<arguments>', '</arguments>'])
                        # Note: We preserve ```tool: blocks as they are the expected frontend format
                    )
                    
                    if not should_suppress:

                        ops = [{"op": "add", "path": "/streamed_output_str/-", "value": content_str}]
                        yield f"data: {json.dumps({'ops': ops})}\n\n"
                    else:
                        logger.debug(f"🔍 AGENT: Suppressed tool call content from frontend")

                    # Check for complete tool call - must have both opening and closing tags
                    if TOOL_SENTINEL_OPEN in current_response and TOOL_SENTINEL_CLOSE in current_response:
                        logger.info("🔍 AGENT: Complete tool call detected, stopping generation")
                        tool_executed = True
                        break

                logger.info(f"🔍 AGENT: Finished streaming loop for iteration {iteration}")

                # If this is the first iteration and no tool was executed, 
                logger.info(f"🔍 AGENT: Iteration {iteration} complete. current_response length: {len(current_response)}, tool_executed: {tool_executed}")

                # Always update full_response with current_response content
                if current_response and not full_response:
                    full_response = current_response
                    logger.info(f"🔍 AGENT: Updated full_response from current_response: {len(full_response)} chars")

                # the model has completed its response normally
                if iteration == 1 and not tool_executed:
                    logger.info("🔍 AGENT: First iteration complete, no tool calls - normal response")
                    break
                
                # If no tool was executed in this iteration, we're done
                if not tool_executed:
                    logger.info("🔍 AGENT: No tool call detected in iteration {iteration}, ending iterations")
                    break
                
                # Execute the tool
                logger.info("🔍 AGENT: Executing tool call")
                processed_response = await detect_and_execute_mcp_tools(current_response, processed_tool_calls)
                
                if processed_response != current_response:
                    # The processed_response contains the formatted result, extract it properly
                    # Look for the "**Tool Result:**" section
                    # Extract tool name from the current response
                    import re
                    tool_match = re.search(r'<name>([^<]+)</name>', current_response)
                    tool_name = tool_match.group(1) if tool_match else "unknown"
                    
                    if "**Tool Result:**" in processed_response:
                        tool_result_start = processed_response.find("**Tool Result:**")
                        tool_result = processed_response[tool_result_start:]
                    else:
                        # Fallback: use the difference between responses
                        tool_result = processed_response.replace(current_response.split(TOOL_SENTINEL_OPEN)[0], '').strip()

                    logger.info(f"🔍 AGENT: Tool executed")
                    logger.info(f"🔍 AGENT: current_response length: {len(current_response)}")
                    logger.info(f"🔍 AGENT: processed_response length: {len(processed_response)}")
                    logger.info(f"🔍 AGENT: tool_result length: {len(tool_result)}")
                    logger.info(f"🔍 AGENT: processed_response content: {processed_response}")
                    logger.info(f"🔍 AGENT: tool_result content: {tool_result}")
                   
                    # Format the tool result properly for frontend consumption
                    if tool_result.startswith("**Tool Result:**"):
                        clean_result = tool_result.replace("", "").strip()
                    else:
                        clean_result = tool_result.strip()
                    
                    # Remove only internal tool sentinels, preserve other content
                    import re
                    clean_result = re.sub(r'<TOOL_[^>]*?>', '', clean_result)
                    clean_result = re.sub(r'</?(?:name|arguments)>[^<]*', '', clean_result)
                    # Format with proper leading newline for frontend processing
                    formatted_result = f"\n```tool:{tool_name}\n{clean_result.strip()}\n```"

                    # Add tool call and result to messages for next iteration
                    from langchain_core.messages import AIMessage
                    messages.append(AIMessage(content=current_response.strip()))  # The tool call
                    messages.append(AIMessage(content=formatted_result.strip()))  # The tool result
                    logger.info("🔍 AGENT: Added tool call and result to messages for next iteration")
                    # Format shell command results for terminal display
                    display_result = formatted_result

                    # Update full response and continue to next iteration
                    full_response = processed_response
                    
                    # Store this tool result for the final summary
                    all_tool_results.append(formatted_result)

                    # Stream each tool result immediately as it's executed with flush
                    ops = [{"op": "add", "path": "/streamed_output_str/-", "value": formatted_result}]
                    sse_data = f"data: {json.dumps({'ops': ops})}\n\n"
                    yield sse_data
                    logger.info(f"🔍 AGENT: Streamed tool result to frontend: {formatted_result[:100]}...")

                    # Add a small delay to ensure the data is flushed
                    await asyncio.sleep(0.01)
                    
                    # Signal that we're about to submit tool results back to the model
                    processing_signal = {"op": "add", "path": "/processing_state", "value": "awaiting_model_response"}
                    yield f"data: {json.dumps({'ops': [processing_signal]})}\n\n"
                    logger.info("🔍 AGENT: Signaled frontend that we're awaiting model response")
                    await asyncio.sleep(0.01)  # Small delay to ensure signal is processed
                    
                    logger.info("🔍 AGENT: Added tool result to context, breaking stream, should submit results back to model next")
                else:
                    logger.warning("🔍 AGENT: Tool execution failed or no change")
                    # Tool execution failed or no change - still update full_response
                    if current_response and len(current_response) > len(full_response):
                        full_response = current_response
                        logger.info(f"🔍 AGENT: Updated full_response after failed tool execution: {len(full_response)} chars")

                    break

            except Exception as e:
                logger.error(f"Error in agent iteration {iteration}: {str(e)}", exc_info=True)
                
                # Preserve any accumulated response content before handling the error
                if current_response and len(current_response.strip()) > 0:
                    logger.info(f"Preserving {len(current_response)} characters of partial response before error")
                    print(f"PARTIAL RESPONSE PRESERVED (AGENT ERROR):\n{current_response}")
                    
                    # Send the partial content to the frontend
                    ops = [{"op": "add", "path": "/streamed_output_str/-", "value": current_response}]
                    yield f"data: {json.dumps({'ops': ops})}\n\n"
                    
                    # Send warning about partial response
                    warning_signal = {"op": "add", "path": "/warning", "value": f"Server encountered an error after generating {len(current_response)} characters. The partial response has been preserved."}
                    yield f"data: {json.dumps({'ops': [warning_signal]})}\n\n"
                    
                    full_response = current_response  # Ensure it's preserved in full_response
                
                # Handle ValidationError specifically by sending proper SSE error
                if isinstance(e, ValidationError):
                    logger.info("🔍 AGENT: Handling ValidationError in streaming context, sending SSE error")
                    error_data = {
                        "error": "validation_error",
                        "detail": str(e),
                        "status_code": 413
                    }
                    
                    # Send error as direct SSE data
                    yield f"data: {json.dumps(error_data)}\n\n"
                    yield f"data: [DONE]\n\n"
                    
                    # Clean up and return
                    await cleanup_stream(conversation_id)
                    return
                
                break

        # Log why the iteration loop ended
        logger.info(f"🔍 AGENT: Iteration loop ended after {iteration} iterations")
        
        # Signal that processing is complete
        completion_signal = {"op": "add", "path": "/processing_state", "value": "complete"}
        yield f"data: {json.dumps({'ops': [completion_signal]})}\n\n"
        logger.info(f"🔍 AGENT: Final iteration < max_iterations: {iteration < max_iterations}")
        
        # Log final response
        logger.info("=== FULL SERVER RESPONSE ===")
        logger.info(f"Response length: {len(full_response)}")
        logger.info("=== CONTEXT PROCESSING SUMMARY ===")
        logger.info(f"Conversation ID: {conversation_id}")
        logger.info(f"Total iterations: {iteration}")
        logger.info(f"Final response length: {len(full_response)} characters")
        logger.info(f"Tool calls processed: {len(processed_tool_calls)}")
        logger.info(f"Messages sent to model: {len(messages)}")
        logger.info(f"Files in context: {len(files)}")
        logger.info(f"Chat history items: {len(chat_history)}")
        logger.info("=" * 50)
        
        logger.info(f"Response content:\n{full_response}")
        logger.info("=== END SERVER RESPONSE ===")

        # Send DONE marker and cleanup
        # Initialize data_sent flag
        # Ensure we always send a DONE marker to complete the stream properly
        logger.info("🔍 AGENT: Sending final DONE marker")
        data_sent = len(full_response) > 0
        if not done_marker_sent:
            yield f"data: {json.dumps({'done': True})}\n\n"
            done_marker_sent = True
        else:
            # Send another DONE marker to ensure stream completion
            yield f"data: {json.dumps({'stream_complete': True})}\n\n"
        
        await cleanup_stream(conversation_id)
        return

    except ConnectionError as e:
        logger.info(f"Connection error in stream_chunks: {e}")
        connection_active = False
        await cleanup_stream(conversation_id)
        # Don't re-raise connection errors as they're expected when clients disconnect
        
    except Exception as e:
        logger.error(f"Unhandled exception in stream_chunks: {str(e)}", exc_info=True)
        if conversation_id: # Ensure cleanup if conversation_id was set
            await cleanup_stream(conversation_id)

# Override the stream endpoint with our error handling
@app.post("/ziya/stream")
async def stream_endpoint(request: Request, body: dict):
    """Stream endpoint with centralized error handling."""
    logger.info(f"🔍 STREAM_ENDPOINT: Direct /ziya/stream called - this should be using stream_chunks")
    logger.info(f"🔍 STREAM_ENDPOINT: Request body keys: {body.keys()}")
    try:
        # Debug logging
        logger.info("[INSTRUMENTATION] /ziya/stream received request")
        logger.info(f"[INSTRUMENTATION] /ziya/stream question: '{body.get('question', 'EMPTY')[:50]}...' (truncated)")
        logger.info(f"[INSTRUMENTATION] /ziya/stream chat_history length: {len(body.get('chat_history', []))}")
        logger.info(f"[INSTRUMENTATION] /ziya/stream files count: {len(body.get('config', {}).get('files', []))}")
        
        # Log body structure
        logger.info(f"[INSTRUMENTATION] /ziya/stream body keys: {body.keys() if isinstance(body, dict) else type(body)}")
        
        # Log chat history structure if present
        chat_history = body.get('chat_history', [])
        if chat_history and len(chat_history) > 0:
            logger.info(f"[INSTRUMENTATION] /ziya/stream first history item type: {type(chat_history[0])}")
            if isinstance(chat_history[0], list) and len(chat_history[0]) >= 2:
                logger.info(f"[INSTRUMENTATION] /ziya/stream first history format: ['{chat_history[0][0][:20]}...', '{chat_history[0][1][:20]}...'] (truncated)")
            elif isinstance(chat_history[0], dict):
                logger.info(f"[INSTRUMENTATION] /ziya/stream first history keys: {chat_history[0].keys()}")

        # Check if the question is empty or missing
        if not body.get("question") or not body.get("question").strip():
            logger.warning("[INSTRUMENTATION] /ziya/stream empty question detected")
            raise ValidationError("Please provide a question to continue.")
            
        # Clean chat history if present
        if "chat_history" in body:
            logger.info(f"[INSTRUMENTATION] /ziya/stream cleaning chat history of length {len(chat_history)}")
            cleaned_history = []
            for pair in body["chat_history"]:
                try:
                    # Handle both tuple format [role, content] and dict format {"type": role, "content": content}
                    if isinstance(pair, dict) and 'type' in pair and 'content' in pair:
                        role, content = pair['type'], pair['content']
                    elif isinstance(pair, (list, tuple)) and len(pair) == 2:
                        role, content = pair[0], pair[1]
                    else:
                        logger.warning(f"[INSTRUMENTATION] /ziya/stream invalid chat history pair format: {type(pair)}")
                        continue
                    
                    if not isinstance(role, str) or not isinstance(content, str):
                        logger.warning(f"[INSTRUMENTATION] /ziya/stream non-string message: role={type(role)}, content={type(content)}")
                        continue
                    
                    if role.strip() and content.strip():
                        cleaned_history.append((role.strip(), content.strip()))
                        logger.info(f"[INSTRUMENTATION] /ziya/stream added valid message: role='{role}', content='{content[:20]}...' (truncated)")
                    else:
                        logger.warning(f"[INSTRUMENTATION] /ziya/stream empty message content")
                except Exception as e:
                    logger.error(f"[INSTRUMENTATION] /ziya/stream error processing chat history item: {str(e)}")
            
            logger.info(f"[INSTRUMENTATION] /ziya/stream cleaned chat history from {len(body['chat_history'])} to {len(cleaned_history)} pairs")
            body["chat_history"] = cleaned_history
            
        logger.info("[INSTRUMENTATION] /ziya/stream starting stream endpoint with body size: %d", len(str(body)))
        
        # Convert to ChatPromptValue if needed
        if isinstance(body, dict) and "messages" in body:
            logger.info(f"[INSTRUMENTATION] /ziya/stream converting {len(body['messages'])} messages to ChatPromptValue")
            from langchain_core.prompt_values import ChatPromptValue
            from langchain_core.messages import HumanMessage
            messages = [HumanMessage(content=msg) for msg in body["messages"]]
            prompt_value = ChatPromptValue(messages=messages)
            # Keep body as dict but store the prompt value for later use if needed
            logger.info(f"[INSTRUMENTATION] /ziya/stream created ChatPromptValue with {len(messages)} messages")
        
        # Return the streaming response
        logger.info("[INSTRUMENTATION] /ziya/stream calling stream_chunks()")
        return StreamingResponse(
            stream_chunks(body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type"
            }
        )
    except Exception as e:
        # Handle any exceptions using the centralized error handler
        logger.error(f"Exception in stream_endpoint: {str(e)}")
        return handle_request_exception(request, e)

async def stream_agent_response(body, request):
    """Stream the agent's response with centralized error handling."""
    try:
        first_chunk = True
        # Stream the response
        async for chunk in agent_executor.astream_log(body):
            # Process the chunk
            try:
                # Parse and clean the chunk before sending
                parsed_chunk = parse_output(chunk)
                if parsed_chunk and parsed_chunk.return_values:
                    cleaned_output = parsed_chunk.return_values.get("output", "")
                    if cleaned_output:
                        first_chunk = False
                        # Use proper JSON format for SSE
                        yield f"data: {json.dumps({'text': cleaned_output})}\n\n"
                        continue
                
                # Fall back to original chunk if parsing fails
                chunk_content = chunk.content if hasattr(chunk, 'content') else str(chunk)
                first_chunk = False
                # Use proper JSON format for SSE
                yield f"data: {json.dumps({'text': chunk_content})}\n\n"
            except Exception as e:
                logger.error(f"Error processing chunk: {e}")
                continue
        
        # Send the [DONE] marker
        yield f"data: {json.dumps({'done': True})}\n\n"
        
    except Exception as e:
        # Use the centralized error handler for streaming errors
        logger.error(f"Exception during streaming: {str(e)}")
        
        # Don't try to handle the error here, let the middleware handle it
        # Just re-raise the exception so the middleware can catch it
        raise

@app.get("/")
async def root(request: Request):
    try:
        # Log detailed information about templates
        logger.info(f"Rendering index.html using custom template loader")
        
        # Create the context for the template
        context = {
            "request": request,
            "diff_view_type": os.environ.get("ZIYA_DIFF_VIEW_TYPE", "unified"),
            "api_poth": "/ziya"
        }
        
        # Try to render the template
        return templates.TemplateResponse("index.html", context)
    except Exception as e:
        logger.error(f"Error rendering index.html: {str(e)}")
        # Return a simple HTML response as fallback
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Ziya</title>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }
                h1 { color: #333; }
                .container { max-width: 800px; margin: 0 auto; }
                .error { color: #721c24; background-color: #f8d7da; padding: 10px; border-radius: 5px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Ziya</h1>
                <div class="error">
                    <p>Error loading template. Please check server logs.</p>
                    <p>Error details: """ + str(e) + """</p>
                </div>
                <p>Please ensure that the templates directory is properly included in the package.</p>
            </div>
        </body>
        </html>
        """
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html_content)


@app.get("/debug")
async def debug(request: Request):
   return templates.TemplateResponse("index.html", {"request": request})

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # Look for favicon in the templates directory
    try:
        favicon_path = os.path.join(templates_dir, "favicon.ico")
        if os.path.exists(favicon_path):
            logger.info(f"Serving favicon from: {favicon_path}")
            return FileResponse(favicon_path)
    except Exception as e:
        logger.warning(f"Error finding favicon: {e}")
    
    logger.warning("Favicon not found in any location")
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Favicon not found")



# Cache for folder structure with timestamp
_folder_cache = {'timestamp': 0, 'data': None}





@app.post("/folder")
async def get_folder(request: FolderRequest):
    """Get the folder structure of a directory with improved error handling."""
    start_time = time.time()
    logger.info(f"Starting folder scan for: {request.directory}")
    logger.info(f"Max depth: {request.max_depth}")
    
    try:
        # Special handling for home directory
        if request.directory == os.path.expanduser("~"):
            logger.warning("Home directory scan requested - this may be slow or fail")
            return {
                "error": "Home directory scans are not recommended",
                "suggestion": "Please use a specific project directory instead of your home directory"
            }
            
        # Validate the directory exists and is accessible
        if not os.path.exists(request.directory):
            logger.error(f"Directory does not exist: {request.directory}")
            return {"error": f"Directory does not exist: {request.directory}"}
            
        if not os.path.isdir(request.directory):
            logger.error(f"Path is not a directory: {request.directory}")
            return {"error": f"Path is not a directory: {request.directory}"}
            
        # Test basic access
        try:
            os.listdir(request.directory)
        except PermissionError:
            logger.error(f"Permission denied accessing: {request.directory}")
            return {"error": "Permission denied accessing directory"}
        except OSError as e:
            logger.error(f"OS error accessing {request.directory}: {e}")
            return {"error": f"Cannot access directory: {str(e)}"}
        
        # Get the ignored patterns
        ignored_patterns = get_ignored_patterns(request.directory)
        logger.info(f"Ignore patterns loaded: {len(ignored_patterns)} patterns")
        
        # Use the max_depth from the request, but ensure it's at least 15 if not specified
        max_depth = request.max_depth if request.max_depth > 0 else int(os.environ.get("ZIYA_MAX_DEPTH", 15))
        logger.info(f"Using max depth for folder structure: {max_depth}")
        
        # Use our enhanced cached folder structure function
        result = get_cached_folder_structure(request.directory, ignored_patterns, max_depth)
        
        # Check if we got an error result
        if isinstance(result, dict) and "error" in result:
            logger.warning(f"Folder scan returned error: {result['error']}")
            # Add helpful context for home directory scans
            if "home" in request.directory.lower() or request.directory.endswith(os.path.expanduser("~")):
                result["suggestion"] = "Home directory scans can be very slow. Consider using a specific project directory instead."
            return result
            
        logger.info(f"Folder scan completed successfully in {time.time() - start_time:.2f}s")
        return result
    except Exception as e:
        logger.error(f"Error in get_folder: {e}")
        return {"error": str(e)}

@app.post("/file")
async def get_file(request: FileRequest):
    """Get the content of a file."""
    try:
        with open(request.file_path, 'r') as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        logger.error(f"Error in get_file: {e}")
        return {"error": str(e)}

@app.post("/save")
async def save_file(request: FileContentRequest):
    """Save content to a file."""
    try:
        with open(request.file_path, 'w') as f:
            f.write(request.content)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error in save_file: {e}")
        return {"error": str(e)}

@app.post("/apply_patch")
async def apply_patch(request: PatchRequest):
    """Apply a git diff to a file."""
    try:
        # If file_path is not provided, try to extract it from the diff
        target_file = request.file_path
        if not target_file:
            logger.info("No file_path provided, attempting to extract from diff")
            target_file = extract_target_file_from_diff(request.diff)
            
        if not target_file:
            return {"error": "Could not determine target file from diff"}
            
        # Apply the patch
        try:
            # Use the request ID if provided, otherise generate one
            learned_id = getattr(request, 'requestId', None)
            if learned_id:
                request_id = getattr(request, 'requestId', None) or str(uuid.uuid4()) 
                logger.info(f"Using request ID from frontend for patch application: {request_id}")
            else:
                request_id = str(uuid.uuid4()) 
                logger.warning(f"Generated request ID for patch application: {request_id}")
            result = request_id

            # Check if result contains error information
            if isinstance(result, dict) and result.get('status') == 'error':
                return JSONResponse(
                    status_code=422,
                    content={
                        "status": "error",
                        "request_id": request_id,
                        "type": result.get("type", "patch_error"), 
                        "message": result.get("message", "Failed to apply patch"),
                        "details": result.get("details", {})
                    }
                )

            # Check for partial success with failed hunks
            if isinstance(result, dict) and result.get('hunk_statuses'):
                failed_hunks = [
                    hunk_num for hunk_num, status in result['hunk_statuses'].items()
                    if status.get('status') == 'failed'
                ]
                
                # Get the list of successful hunks
                successful_hunks = [
                    hunk_num for hunk_num, status in result['hunk_statuses'].items()
                    if status.get('status') == 'succeeded'
                ]
                
                if failed_hunks:
                    if successful_hunks:
                        # Some hunks succeeded, some failed - partial success
                        return JSONResponse(
                            status_code=207,
                            content={
                                "status": "partial",
                                "message": "Some hunks failed to apply",
                                "request_id": request_id,
                                "details": {
                                    "failed": failed_hunks,
                                    "succeeded": successful_hunks,
                                    "hunk_statuses": result['hunk_statuses']
                                }
                            }
                        )
                    else:
                        # All hunks failed - complete failure
                        logger.info("All hunks failed, returning error status with 422 code")
                        return JSONResponse(
                            status_code=422,
                            content={
                                "status": "error",
                                "message": "All hunks failed to apply",
                                "request_id": request_id,
                                "details": {
                                    "failed": failed_hunks,
                                    "succeeded": [],
                                    "hunk_statuses": result['hunk_statuses']
                                }
                            }
                        )

            # All hunks succeeded
            return JSONResponse(
                content={
                    "status": "success",
                    "message": "Changes applied successfully",
                    "request_id": request_id,
                    "details": {
                        "succeeded": list(result['hunk_statuses'].keys()) if isinstance(result, dict) and result.get('hunk_statuses') else [],
                        "failed": [],
                        "hunk_statuses": result['hunk_statuses'] if isinstance(result, dict) and result.get('hunk_statuses') else {}
                    }
                }
            )
        except Exception as e: 
            logger.error("Error in apply_path: {e}")

    except PatchApplicationError as e:
        logger.error(f"Error applying patch: {e}")
        return {"error": str(e), "type": "patch_error"}
    except Exception as e:
        logger.error(f"Error in apply_patch: {e}")
        return {"error": str(e)}

@app.get('/api/available-models')
def get_available_models():
    """Get list of available models for the current endpoint."""
    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")

    try:
        models = []
        for name, config in ModelManager.MODEL_CONFIGS[endpoint].items():
            model_id = config.get("model_id", name)
            
            # For region-specific model IDs, use a simplified representation
            if isinstance(model_id, dict):
                # Use the first region's model ID as a representative
                representative_id = next(iter(model_id.values()))
                display_name = f"{name} ({representative_id})"
                
                # Add region information if available
                if "region" in config:
                    preferred_region = config["region"]
                    display_name = f"{name} ({representative_id}, {preferred_region})"
            else:
                display_name = f"{name} ({model_id})"
                
            # Always include all models regardless of region
            models.append({
                "id": name,  # Use the alias as the ID for consistency
                "name": name,
                "alias": name,
                "display_name": display_name,
                "preferred_region": config.get("region", None)  # Include preferred region if available
            })
            
        # Log the models being returned
        logger.debug(f"Available models: {json.dumps(models)}")
        return models
    except Exception as e:
        logger.error(f"Error getting available models: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))



if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run the Ziya server")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT, help="Port to run the server on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to run the server on")
    parser.add_argument("--model", type=str, default=None, help="Model to use")
    parser.add_argument("--profile", type=str, default=None, help="AWS profile to use")
    parser.add_argument("--region", type=str, default=None, help="AWS region to use")
    
    args = parser.parse_args()
    
    # Set the AWS profile if provided
    if args.profile:
        os.environ["AWS_PROFILE"] = args.profile
        logger.info(f"Using AWS profile: {args.profile}")
        
    # Set the AWS region if provided
    if args.region:
        os.environ["AWS_REGION"] = args.region
        logger.info(f"Using AWS region: {args.region}")
        
    # Initialize the model if provided
    if args.model:
        try:
            ModelManager.initialize_model(args.model)
        except Exception as e:
            logger.error(f"Error initializing model: {e}")
            
    # Run the server
    uvicorn.run(app, host=args.host, port=args.port)

@app.get('/api/default-included-folders')
async def get_default_included_folders():
    """Get the default included folders."""
    return []

@app.post('/api/chat')
async def chat_endpoint(request: Request):
    """Handle chat requests from the frontend.
    
    This is a thin wrapper around the /ziya/stream endpoint that formats
    the request data appropriately and forwards it to the stream endpoint.
    """
    try:
        body = await request.json()
        
        # Extract data from the request
        messages = body.get('messages', [])
        question = body.get('question', '')
        files = body.get('files', [])
        conversation_id = body.get('conversation_id')
        logger.info(f"Chat API received conversation_id: {conversation_id}")
        logger.info(f"🔍 CHAT_API: Received conversation_id from frontend: {conversation_id}")

        # Debug: Log what we received from frontend
        logger.info(f"🔍 CHAT_API: Received messages count: {len(messages)}")
        logger.info(f"🔍 CHAT_API: Messages structure: {messages[:2] if messages else 'No messages'}")
        
        logger.info("=== File Processing Debug ===")
        logger.info(f"Files received: {files}")
        
        # Log message structure for debugging
        if messages and len(messages) > 0:
            logger.info(f"[INSTRUMENTATION] First message structure: {type(messages[0])}")
            if isinstance(messages[0], list) and len(messages[0]) >= 2:
                logger.info(f"[INSTRUMENTATION] First message format: ['{messages[0][0][:20]}...', '{messages[0][1][:20]}...'] (truncated)")
            elif isinstance(messages[0], dict):
                logger.info(f"[INSTRUMENTATION] First message keys: {messages[0].keys()}")
        
        # Convert frontend message tuples to proper chat history format
        formatted_chat_history = []
        for msg in messages:
            if isinstance(msg, list) and len(msg) >= 2:
                role, content = msg[0], msg[1]
                # Convert role names to match expected format
                if role in ['human', 'user']:
                    formatted_chat_history.append({'type': 'human', 'content': content})
                elif role in ['assistant', 'ai']:
                    formatted_chat_history.append({'type': 'ai', 'content': content})
            elif isinstance(msg, dict):
                # Already in correct format
                formatted_chat_history.append(msg)
        
        # Debug: Log the converted chat history
        logger.info(f"🔍 CHAT_API: Converted chat history count: {len(formatted_chat_history)}")
        
        # Format the data for the stream endpoint
        formatted_body = {
            'question': question,
            'conversation_id': conversation_id,
            'chat_history': formatted_chat_history,
            'config': {
                'conversation_id': conversation_id,  # Also include in config for backward compatibility
                'files': files
            }
        }
        
        logger.info(f"[INSTRUMENTATION] /api/chat formatted body structure: {formatted_body.keys()}")
        logger.info(f"[INSTRUMENTATION] /api/chat forwarding to /ziya/stream")

        
        # Forward the request to the /ziya/stream endpoint
        # This ensures all validation and normalization logic is applied
        stream_request = Request(scope=request.scope)
        stream_request._body = json.dumps(formatted_body).encode()
        
        # Call the stream endpoint directly
        return await stream_endpoint(stream_request, formatted_body)
    except Exception as e:
        logger.error(f"Error in chat_endpoint: {str(e)}")
        # Return error as streaming response
        error_json = json.dumps({"error": str(e)})
        return StreamingResponse(
            (f"data: {error_json}\n\ndata: {json.dumps({'done': True})}\n\n" for _ in range(1)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                "Content-Type": "text/event-stream"
            }
        )

@app.get('/api/current-model')
def get_current_model():
    """Get detailed information about the currently active model."""
    try:
        logger.info("Current model info request received")
        
        # Get model alias (name) from ModelManager
        model_alias = ModelManager.get_model_alias()
        
        # Get model ID and endpoint
        model_id = ModelManager.get_model_id(model)
        endpoint = os.environ.get("ZIYA_ENDPOINT", config.DEFAULT_ENDPOINT)
        
        # Get model settings through ModelManager
        model_settings = ModelManager.get_model_settings(model)

        # Get model config for token limits
        model_config = ModelManager.get_model_config(endpoint, model_alias)
        
        # Ensure model_settings has the correct token limits
        if "max_output_tokens" not in model_settings:
            model_settings["max_output_tokens"] = model_config.get("max_output_tokens", 4096)
        
        if "max_input_tokens" not in model_settings:
            model_settings["max_input_tokens"] = model_config.get("token_limit", 4096)
            
        # Ensure temperature and top_k have default values if not present
        model_settings["temperature"] = model_settings.get("temperature", 0.3)
        model_settings["top_k"] = model_settings.get("top_k", 15)
        
        # Get region information
        region = os.environ.get("AWS_REGION", ModelManager._state.get('aws_region', 'us-west-2'))
        
        # Format the actual model ID for display
        display_model_id = model_id
        if isinstance(model_id, dict):
            # If we're using a region-specific model ID, use the one for the current region
            if region.startswith('eu-') and 'eu' in model_id:
                display_model_id = model_id['eu']
            elif region.startswith('us-') and 'us' in model_id:
                display_model_id = model_id['us']
            else:
                # Use the first available region's model ID
                display_model_id = next(iter(model_id.values()))

        # Log the response we're sending
        logger.info(f"Sending current model info: model_id={model_alias}, display_model_id={display_model_id}, settings={json.dumps(model_settings)}")
        
        logger.info("Sending current model configuration:")
        logger.info(f"  Model ID: {model_id}")
        logger.info(f"  Display Model ID: {display_model_id}")
        logger.info(f"  Model Alias: {model_alias}")
        logger.info(f"  Endpoint: {endpoint}")
        logger.info(f"  Region: {region}")
        logger.info(f"  Settings: {model_settings}")

        # Return complete model information
        return {
            'model_id': model_alias,  # Use the alias (like "sonnet3.7") for model selection
            'model_alias': model_alias,  # Explicit alias field
            'actual_model_id': model_id,  # Full model ID object or string
            'display_model_id': display_model_id,  # Region-specific model ID for display
            'endpoint': endpoint,
            'region': region,
            'settings': model_settings,
            'token_limit': model_config.get("token_limit", 4096)
        }
    except Exception as e:
        logger.error(f"Error getting current model: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get current model: {str(e)}")

@app.get('/api/model-id')
def get_model_id():
    """Get the model ID in a simplified format for the frontend."""
    # Always return the model alias (name) rather than the full model ID
    return {'model_id': ModelManager.get_model_alias()}


def get_cached_folder_structure(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int) -> Dict[str, Any]:
    """
    Get folder structure with caching and timeout protection.
    
    This function will:
    1. Return cached results if they're fresh (less than 10 seconds old)
    2. Implement timeout protection for large directories
    3. Cache results for future requests
    4. Handle errors gracefully
    
    Args:
        directory: The directory to scan
        ignored_patterns: Patterns to ignore
        max_depth: Maximum depth to traverse
        
    Returns:
        Dict with folder structure or error message
    """
    current_time = time.time()
    cache_age = current_time - _folder_cache['timestamp']

    # Return cached results if they're fresh (less than 10 seconds old)
    if _folder_cache['data'] is not None and cache_age < 10:
        logger.debug(f"Returning cached folder structure (age: {cache_age:.1f}s)")
        return _folder_cache['data']
    
    try:
        # Set a maximum time limit for scanning (30 seconds)
        max_scan_time = 30
        start_time = time.time()
        
        # Special handling for home directory
        if directory == os.path.expanduser("~"):
            logger.warning("Home directory scan requested - this may be slow or fail")
            # Return a helpful error for home directory
            return {
                "error": "Home directory scans are not recommended",
                "suggestion": "Please use a specific project directory instead of your home directory"
            }
        
        # Import the folder structure function from directory_util
        from app.utils.directory_util import get_folder_structure
        
        # Get the folder structure with timeout protection
        result = get_folder_structure(directory, ignored_patterns, max_depth)
        
        # Check if scan took too long
        scan_time = time.time() - start_time
        if scan_time > max_scan_time:
            logger.warning(f"Folder scan took too long: {scan_time:.1f}s")
            return {
                "error": f"Scan took too long ({scan_time:.1f}s)",
                "suggestion": "Try scanning a smaller directory or increasing the timeout"
            }
        
        # Cache the successful result
        _folder_cache['data'] = result
        _folder_cache['timestamp'] = current_time
        logger.info(f"Refreshed folder structure cache in {scan_time:.2f}s")
        
        return result
    except Exception as e:
        logger.error(f"Error during folder scan: {str(e)}")
        # Return error but don't cache it
        return {"error": f"Scan failed: {str(e)}"}

@app.get('/api/folders')
async def api_get_folders():
    """Get the folder structure for API compatibility with improved error handling."""
    try:
        # Get the user's codebase directory
        user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        if not user_codebase_dir:
            logger.error("ZIYA_USER_CODEBASE_DIR environment variable not set")
            return {"error": "Server configuration error: codebase directory not set"}
            
        # Validate the directory exists and is accessible
        if not os.path.exists(user_codebase_dir):
            logger.error(f"Codebase directory does not exist: {user_codebase_dir}")
            return {"error": f"Directory does not exist: {user_codebase_dir}"}
            
        if not os.path.isdir(user_codebase_dir):
            logger.error(f"Codebase path is not a directory: {user_codebase_dir}")
            return {"error": f"Path is not a directory: {user_codebase_dir}"}
            
        # Test basic access
        try:
            os.listdir(user_codebase_dir)
        except PermissionError:
            logger.error(f"Permission denied accessing: {user_codebase_dir}")
            return {"error": "Permission denied accessing directory"}
        except OSError as e:
            logger.error(f"OS error accessing {user_codebase_dir}: {e}")
            return {"error": f"Cannot access directory: {str(e)}"}
        
        # Get max depth from environment or use default
        try:
            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
        except ValueError:
            logger.warning("Invalid ZIYA_MAX_DEPTH value, using default of 15")
            max_depth = 15
            
        # Get ignored patterns
        ignored_patterns = get_ignored_patterns(user_codebase_dir)
        logger.info(f"Loaded {len(ignored_patterns)} ignore patterns")
        
        # Use our enhanced cached folder structure function
        result = get_cached_folder_structure(user_codebase_dir, ignored_patterns, max_depth)
        
        # Check if we got an error result
        if isinstance(result, dict) and "error" in result:
            logger.warning(f"Folder scan returned error: {result['error']}")
            return result
            
        # Log a sample of the result to see if token counts are included
        sample_files = []
        def collect_sample(data, path=""):
            if isinstance(data, dict):
                for key, value in data.items():
                    current_path = f"{path}/{key}" if path else key
                    if isinstance(value, dict) and 'token_count' in value:
                        sample_files.append(f"{current_path}: {value['token_count']} tokens")
                        if len(sample_files) >= 5:  # Only collect first 5 for logging
                            return
                    elif isinstance(value, dict) and 'children' in value:
                        collect_sample(value['children'], current_path)
        
        collect_sample(result)
        if sample_files:
            logger.info(f"Sample files with token counts: {sample_files}")
        else:
            logger.debug("No files with token counts found in folder structure")
        
        return result
    except Exception as e:
        logger.error(f"Error in api_get_folders: {e}")
        return {"error": f"Unexpected error: {str(e)}"}

@app.post('/api/set-model')
async def set_model(request: SetModelRequest):
    """Set the active model for the current endpoint."""
    import gc
    
    try:
        # Force garbage collection at the start
        gc.collect()
        
        model_id = request.model_id
        logger.info(f"Received model change request: {model_id}")

        if not model_id:
            logger.error("Empty model ID provided")
            raise HTTPException(status_code=400, detail="Model ID is required")

        # Get current endpoint
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        current_model = os.environ.get("ZIYA_MODEL")
        current_region = os.environ.get("AWS_REGION") or ModelManager._state.get('aws_region', 'us-west-1')

        logger.info(f"Current state - Endpoint: {endpoint}, Model: {current_model}")

        # Handle both string and dictionary model IDs
        found_alias = None
        
        # First, try direct match by alias
        if isinstance(model_id, str) and model_id in ModelManager.MODEL_CONFIGS[endpoint]:
            found_alias = model_id
        else:
            # Search through all model configurations
            for alias, model_config_item in ModelManager.MODEL_CONFIGS[endpoint].items():
                config_model_id = model_config_item.get('model_id')
                
                # Case 1: Both are dictionaries - check if they match
                if isinstance(model_id, dict) and isinstance(config_model_id, dict):
                    # Check if dictionaries have the same structure and values
                    if model_id == config_model_id:
                        found_alias = alias
                        break
                    
                    # Check if any region-specific IDs match
                    # This handles partial matches where only some regions are specified
                    matching_regions = 0
                    for region in model_id:
                        if region in config_model_id and model_id[region] == config_model_id[region]:
                            matching_regions += 1
                    
                    # If we have at least one matching region and no mismatches
                    if matching_regions > 0 and all(
                        region not in config_model_id or model_id[region] == config_model_id[region]
                        for region in model_id
                    ):
                        found_alias = alias
                        break
                
                # Case 2: Direct string comparison
                elif model_id == config_model_id:
                    found_alias = alias
                    break
                
                # Case 3: String model_id matches one of the values in a dictionary config_model_id
                elif isinstance(model_id, str) and isinstance(config_model_id, dict):
                    if any(val == model_id for val in config_model_id.values()):
                        found_alias = alias
                        break
                
                # Case 4: Dictionary model_id contains a value that matches string config_model_id
                elif isinstance(model_id, dict) and isinstance(config_model_id, str):
                    if any(val == config_model_id for val in model_id.values()):
                        found_alias = alias
                        break

        if not found_alias:
            logger.error(f"Invalid model identifier: {model_id}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid model identifier: {model_id}. Valid models are: "
                       f"{', '.join(ModelManager.MODEL_CONFIGS[endpoint].keys())}"
            )

        # If model hasn't actually changed, return early
        if found_alias == current_model:
            logger.info(f"Model {found_alias} is already active, no change needed")
            return {"status": "success", "model": found_alias, "changed": False}

        # Check if we need to adjust the region based on the model
        model_config = ModelManager.get_model_config(endpoint, found_alias)
        model_id = model_config.get("model_id")
        
        # If the model has region-specific IDs, ensure we're using the right region
        if isinstance(model_id, dict):
            # Check if we're in an EU region
            is_eu_region = current_region.startswith("eu-")
            
            # If we're in an EU region but the model has EU-specific ID, make sure we use it
            if is_eu_region and "eu" in model_id:
                logger.info(f"Using EU-specific model ID for {found_alias} in region {current_region}")
                # No need to change region as it's already set correctly
            elif not is_eu_region and "us" in model_id:
                logger.info(f"Using US-specific model ID for {found_alias} in region {current_region}")

        # Update environment variable
        logger.info(f"Setting model to: {found_alias}")

        # Reinitialize all model related state
        old_state = {
            'model_id': os.environ.get("ZIYA_MODEL"),
            'model': ModelManager._state.get('model'),
            'current_model_id': ModelManager._state.get('current_model_id')
        }
        logger.info(f"Saved old state: {old_state}")

        try:
            logger.info(f"Reinitializing model with alias: {found_alias}")
            ModelManager._reset_state()
            logger.info(f"State after reset: {ModelManager._state}")

            # Set the new model in environment
            os.environ["ZIYA_MODEL"] = found_alias
            logger.info(f"Set ZIYA_MODEL environment variable to: {found_alias}")

            # Reinitialize with agent
            try:
                new_model = ModelManager.initialize_model(force_reinit=True)
                logger.info(f"Model initialization successful: {type(new_model)}")
            except Exception as model_init_error:
                logger.error(f"Model initialization failed: {str(model_init_error)}", exc_info=True)
                raise model_init_error

            # Verify the model was actually changed by checking the model ID and updating global references
            expected_model_id = ModelManager.MODEL_CONFIGS[endpoint][found_alias]['model_id']
            actual_model_id = ModelManager.get_model_id(new_model)
            logger.info(f"Model ID verification - Expected: {expected_model_id}, Actual: {actual_model_id}")
            
            if actual_model_id != expected_model_id:
                logger.error(f"Model initialization failed - expected ID: {expected_model_id}, got: {actual_model_id}")
                # Restore previous state
                os.environ["ZIYA_MODEL"] = old_state['model_id'] if old_state['model_id'] else ModelManager.DEFAULT_MODELS["bedrock"]
                ModelManager._state.update(old_state)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to change model - expected {expected_model_id}, got {actual_model_id}"
                )
            logger.info(f"Successfully changed model to {found_alias} ({actual_model_id})")
            # update the global model reference
            global model
            model = new_model

            global agent
            global agent_executor
            global llm_with_stop  # Add global reference to llm_with_stop

            # Recreate agent chain and executor with new model
            try:
                agent = create_agent_chain(new_model)
                agent_executor = create_agent_executor(agent)
                # Get the updated llm_with_stop from ModelManager
                llm_with_stop = ModelManager._state.get('llm_with_stop')
                logger.info("Created new agent chain and executor")
            except Exception as agent_error:
                logger.error(f"Failed to create agent: {str(agent_error)}", exc_info=True)
                raise agent_error

            # Reinitialize langserve routes with new agent_executor
            try:
                initialize_langserve(app, agent_executor)
                logger.info("Reinitialized langserve routes")
            except Exception as langserve_error:
                logger.error(f"Failed to initialize langserve: {str(langserve_error)}", exc_info=True)
                raise langserve_error

            # Force garbage collection after successful model change
            import gc
            gc.collect()

            # Return success response
            return {
                "status": "success",
                "model": found_alias, 
                "previous_model": old_state['model_id'],
                "model_display_name": ModelManager.MODEL_CONFIGS[endpoint][found_alias].get("display_name", found_alias),
                "changed": True,
                "message": "Model and routes successfully updated"
            }

        except ValueError as e:
            logger.error(f"Model initialization error: {str(e)}", exc_info=True)
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Failed to initialize model {found_alias}: {str(e)}", exc_info=True)
            # Restore previous state
            logger.info(f"Restoring previous state: {old_state}")

            os.environ["ZIYA_MODEL"] = old_state['model_id'] if old_state['model_id'] else ModelManager.DEFAULT_MODELS["bedrock"]
            if old_state['model']:
                ModelManager._state.update(old_state)
            else:
                logger.warning("No previous model state to restore")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to initialize model {found_alias}: {str(e)}"
            )

    except Exception as e:
        logger.error(f"Error in set_model: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to change model: {str(e)}")

@app.get('/api/model-capabilities')
def get_model_capabilities(model: str = None):
    """Get the capabilities of the current model."""

    import json

    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
    # If model parameter is provided, get capabilities for that model
    # Otherwise use current model
    model_alias = None

    if model:
        try:
            # Try to parse as JSON if it's a dictionary
            import json
            try:
                model_dict = json.loads(model)
                if isinstance(model_dict, dict):
                    # Handle dictionary model ID
                    for alias, config in ModelManager.MODEL_CONFIGS[endpoint].items():
                        config_model_id = config.get('model_id')
                        
                        # Case 1: Both are dictionaries - check if they match
                        if isinstance(config_model_id, dict):
                            # Check if dictionaries have the same structure and values
                            if model_dict == config_model_id:
                                model_alias = alias
                                break
                            
                            # Check if any region-specific IDs match
                            matching_regions = 0
                            for region in model_dict:
                                if region in config_model_id and model_dict[region] == config_model_id[region]:
                                    matching_regions += 1
                            
                            # If we have at least one matching region and no mismatches
                            if matching_regions > 0 and all(
                                region not in config_model_id or model_dict[region] == config_model_id[region]
                                for region in model_dict
                            ):
                                model_alias = alias
                                break
                    
                    if not model_alias:
                        return {"error": f"Unknown model ID: {model}"}
            except json.JSONDecodeError:
                # Not JSON, treat as string
                pass
        except Exception as e:
            logger.error(f"Error parsing model parameter: {str(e)}")
            
        # If we didn't find a match with JSON parsing or it wasn't JSON, try string matching
        if not model_alias:
            # Check if it's a direct model alias
            if model in ModelManager.MODEL_CONFIGS[endpoint]:
                model_alias = model
            else:
                # Check if it's a model ID that matches any config
                for alias, config in ModelManager.MODEL_CONFIGS[endpoint].items():
                    config_model_id = config.get('model_id')
                    
                    # Direct string comparison
                    if config_model_id == model:
                        model_alias = alias
                        break
                    
                    # Check if it's a value in a dictionary model ID
                    if isinstance(config_model_id, dict) and any(val == model for val in config_model_id.values()):
                        model_alias = alias
                        break
                
                if not model_alias:
                    return {"error": f"Unknown model ID: {model}"}
    else:
        model_alias = os.environ.get("ZIYA_MODEL")

    try:
        base_model_config = ModelManager.get_model_config(endpoint, model_alias)
        logger.debug(f"base_model_config: {json.dumps(base_model_config)}")

        # Get the *current effective settings* which include env overrides
        effective_settings = ModelManager.get_model_settings()
        logger.debug(f"effective_settings: {json.dumps(effective_settings)}")

        capabilities = {
            "supports_thinking": effective_settings.get("thinking_mode", base_model_config.get("supports_thinking", False)),
        }

        # Get CURRENT effective token limits
        effective_max_output_tokens = effective_settings.get("max_output_tokens", base_model_config.get("max_output_tokens", 4096))
        # Use max_input_tokens from effective settings, fallback to token_limit from base config
        max_input_tokens = effective_settings.get("max_input_tokens", base_model_config.get("token_limit", 4096))

        # Add token limits to capabilities
        effective_max_input_tokens = effective_settings.get("max_input_tokens", base_model_config.get("token_limit", 4096))
 
        # Get ABSOLUTE maximums from base config for ranges
        absolute_max_output_tokens = base_model_config.get("max_output_tokens", 4096)

        logger.debug(f"absolute_max_output_tokens from base_model_config: {absolute_max_output_tokens}") # DEBUG
        logger.debug(f"effective_max_output_tokens from effective_settings: {effective_max_output_tokens}") # DEBUG

        # Get absolute max input tokens from base config (usually under 'token_limit')
        absolute_max_input_tokens = base_model_config.get("token_limit", 4096)
        logger.debug(f"absolute_max_input_tokens from base_model_config: {absolute_max_input_tokens}") # DEBUG

 
        # Add token limits to capabilities
        capabilities["max_output_tokens"] = effective_max_output_tokens # Current value
        capabilities["max_input_tokens"] = effective_max_input_tokens # Current value
        capabilities["token_limit"] = effective_max_input_tokens # Use max_input_tokens for consistency
        
        # Add parameter ranges
        capabilities["temperature_range"] = {"min": 0, "max": 1, "default": effective_settings.get("temperature", base_model_config.get("temperature", 0.3))}
        # Use base_model_config for top_k range as it's static capability, but default from effective settings
        base_top_k_range = base_model_config.get("top_k_range", {"min": 0, "max": 500, "default": 15}) if endpoint == "bedrock" else None
        if base_top_k_range:
             base_top_k_range["default"] = effective_settings.get("top_k", base_top_k_range.get("default", 15))
        capabilities["top_k_range"] = base_top_k_range
        # Add range for max_output_tokens using the absolute max
        capabilities["max_output_tokens_range"] = {"min": 1, "max": absolute_max_output_tokens, "default": effective_max_output_tokens}
        logger.debug(f"max_output_tokens_range being set: {capabilities['max_output_tokens_range']}") # DEBUG         # Add range for max_input_tokens using the absolute max

        # Add range for max_input_tokens using the absolute max
        capabilities["max_input_tokens_range"] = {"min": 1, "max": absolute_max_input_tokens, "default": effective_max_input_tokens}
        
        # Log the capabilities we're sending
        logger.info(f"Sending model capabilities for {model_alias}: {capabilities}")
        return capabilities
    except Exception as e:
        logger.error(f"Error getting model capabilities: {str(e)}")
        return {"error": str(e)}

class ApplyChangesRequest(BaseModel):
    diff: str
    filePath: str = Field(..., description="Path to the file being modified")
    requestId: Optional[str] = Field(None, description="Unique ID to track this specific diff application")

    class Config:
        json_schema_extra = {
            "example": {
                "diff": "diff --git a/file.txt b/file.txt\n...",
                "filePath": "file.txt"
            }
        }
        max_str_length = 1000000  # Allow larger diffs

class ModelSettingsRequest(BaseModel):
    temperature: float = Field(default=0.3, ge=0, le=1)
    top_k: int = Field(default=15, ge=0, le=500)
    max_output_tokens: int = Field(default=4096, ge=1)
    thinking_mode: bool = Field(default=False)


class TokenCountRequest(BaseModel):
    text: str

def count_tokens_fallback(text: str) -> int:
    """Fallback methods for counting tokens when primary method fails."""
    try:
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        # First try using tiktoken directly with cl100k_base (used by Claude)
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception as e:
        logger.warning(f"Tiktoken fallback failed: {str(e)}")
        try:
            # Simple approximation based on whitespace-split words
            # Multiply by 1.3 as tokens are typically fewer than words
            return int(len(text.split()) * 1.3)
        except Exception as e:
            logger.error(f"All token counting methods failed: {str(e)}")
            # Return character count divided by 4 as very rough approximation
            return int(len(text) / 4)

@app.post('/api/token-count')
async def count_tokens(request: TokenCountRequest) -> Dict[str, int]:
    try:
        token_count = 0
        method_used = "unknown"

        try:
            # Try primary method first
            token_count = model.get_num_tokens(request.text)
            method_used = "primary"
        except AttributeError:
            # If primary method fails, use fallback
            logger.debug("Primary token counting method unavailable, using fallback")
            token_count = count_tokens_fallback(request.text)
            method_used = "fallback"
        except Exception as e:
            logger.error(f"Unexpected error in primary token counting: {str(e)}")
            token_count = count_tokens_fallback(request.text)
            method_used = "fallback"

        logger.info(f"Counted {token_count} tokens using {method_used} method for text length {len(request.text)}")
        return {"token_count": token_count}
    except Exception as e:
        logger.error(f"Error counting tokens: {str(e)}", exc_info=True)
        # Return 0 in case of error to avoid breaking the frontend
        return {"token_count": 0}

@app.get('/api/cache-stats')
async def get_cache_stats():
    """Get context caching statistics and effectiveness metrics."""
    try:
        from app.utils.context_cache import get_context_cache_manager
        cache_manager = get_context_cache_manager()
        
        stats = cache_manager.get_cache_stats()
        
        # Calculate effectiveness metrics
        total_operations = stats["hits"] + stats["misses"]
        hit_rate = (stats["hits"] / total_operations * 100) if total_operations > 0 else 0
        
        return {
            "cache_enabled": True,
            "statistics": {
                "cache_hits": stats["hits"],
                "cache_misses": stats["misses"],
                "context_splits": stats["splits"],
                "hit_rate_percent": round(hit_rate, 1),
                "active_cache_entries": stats["cache_entries"],
                "estimated_tokens_cached": stats["estimated_token_savings"]
            }
        }
    except Exception as e:
        logger.error(f"Error getting cache stats: {str(e)}")
        return {"cache_enabled": False, "error": str(e)}

@app.get('/api/cache-test')
async def test_cache_functionality():
    """Test if context caching is properly configured and working."""
    try:
        from app.utils.context_cache import get_context_cache_manager
        from app.agents.models import ModelManager
        
        # Check model configuration
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        model_name = os.environ.get("ZIYA_MODEL", ModelManager.DEFAULT_MODELS.get(endpoint))
        model_config = ModelManager.get_model_config(endpoint, model_name)
        
        cache_manager = get_context_cache_manager()
        
        # Create a large test content
        test_content = "Test file content. " * 1000  # ~20,000 chars
        
        return {
            "model_supports_caching": model_config.get("supports_context_caching", False),
            "current_model": model_name,
            "endpoint": endpoint,
            "test_content_size": len(test_content),
            "should_cache": cache_manager.should_cache_context(test_content, model_config),
            "min_cache_size": cache_manager.min_cache_size,
            "cache_manager_initialized": cache_manager is not None
        }
    except Exception as e:
        logger.error(f"Error testing cache functionality: {str(e)}")
        return {"error": str(e)}


@app.post('/api/model-settings')
async def update_model_settings(settings: ModelSettingsRequest):
    global model
    import gc
    original_settings = settings.dict()
    try:
        # Log the requested settings

        # Get current model configuration
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        model_name = os.environ.get("ZIYA_MODEL")
        model_config = ModelManager.get_model_config(endpoint, model_name)
        
        # Store original model config values for reference
        original_config_values = model_config.copy()
        
        # Check if we need to switch regions based on model-specific region preference
        new_model = getattr(settings, "model", None)
        if new_model and new_model != model_name:
            # Get the new model's configuration
            new_model_config = ModelManager.get_model_config(endpoint, new_model)
            
            # Check if the new model has a preferred region
            if "region" in new_model_config:
                preferred_region = new_model_config["region"]
                logger.info(f"Model {new_model} has preferred region: {preferred_region}")
                
                # Set the AWS_REGION environment variable to the preferred region
                os.environ["AWS_REGION"] = preferred_region
                logger.info(f"Switched region to {preferred_region} for model {new_model}")

        # Store all settings in environment variables with ZIYA_ prefix
        for key, value in settings.dict().items():
            if value is not None:  # Only set if value is provided
                env_key = f"ZIYA_{key.upper()}"
                logger.info(f"  Set {env_key}={value}")

            # Special handling for boolean values
                if isinstance(value, bool):
                    os.environ[env_key] = "1" if value else "0"
                else:
                    os.environ[env_key] = str(value)

        # Create a kwargs dictionary with all settings
        model_kwargs = {}
        # Map settings to model parameter names
        param_mapping = {
            'temperature': 'temperature',
            'top_k': 'top_k',
            'max_output_tokens': 'max_tokens',
            # Only include max_input_tokens if the model supports it
            # This will be filtered by filter_model_kwargs if not supported
        }

        for setting_name, param_name in param_mapping.items():
            value = getattr(settings, setting_name, None)
            if value is not None:
                model_kwargs[param_name] = value
                
        # Filter kwargs to only include supported parameters
        logger.info(f"Model kwargs before filtering: {model_kwargs}")
        filtered_kwargs = ModelManager.filter_model_kwargs(model_kwargs, model_config)
        logger.info(f"Filtered model kwargs: {filtered_kwargs}")

        # Extract conversation_id from config if available for caching
        if config and isinstance(config, dict) and config.get('conversation_id'):
            filtered_kwargs["conversation_id"] = config.get('conversation_id')
            logger.info(f"Added conversation_id to astream kwargs for caching: {config.get('conversation_id')}")
        elif hasattr(input, 'get') and input.get('conversation_id'):
            filtered_kwargs["conversation_id"] = input.get('conversation_id')
            logger.info(f"Added conversation_id from input to astream kwargs for caching")

        # Update the model's kwargs directly
        if hasattr(model, 'model'):
            # For wrapped models (e.g., RetryingChatBedrock)
            if hasattr(model.model, 'model_kwargs'):
                # Replace the entire model_kwargs dict
                model.model.model_kwargs = filtered_kwargs
                logger.info(f"Updated model.model.model_kwargs: {model.model.model_kwargs}")
                model.model.max_tokens = int(os.environ["ZIYA_MAX_OUTPUT_TOKENS"])
        elif hasattr(model, 'model_kwargs'):
            # For direct model instances
            model.model_kwargs = filtered_kwargs
            # Don't try to set max_tokens directly on NovaBedrock models
            if not isinstance(model, NovaBedrock):
                try:
                    model.max_tokens = int(os.environ["ZIYA_MAX_OUTPUT_TOKENS"])  # Use the environment variable value
                except (ValueError, AttributeError) as e:
                    logger.warning(f"Could not set max_tokens directly on model: {e}")
                    # The max_tokens is already in model_kwargs, so this is just a warning

        # Force model reinitialization to apply new settings
        model = ModelManager.initialize_model(force_reinit=True, settings_override=original_settings)

        # Get the model's current settings for verification
        current_kwargs = {}
        if hasattr(model, 'model') and hasattr(model.model, 'model_kwargs'):
            current_kwargs = model.model.model_kwargs
        elif hasattr(model, 'model_kwargs'):
            current_kwargs = model.model_kwargs

        logger.info("Current model settings after update:")
        for key, value in current_kwargs.items():
            logger.info(f"  {key}: {value}")

        # Also check the model's max_tokens attribute directly
        if hasattr(model, 'max_tokens'):
            logger.info(f"  Direct max_tokens: {model.max_tokens}")
        if hasattr(model, 'model') and hasattr(model.model, 'max_tokens'):
            logger.info(f"  model.model.max_tokens: {model.model.max_tokens}")

        # Return the original requested settings to ensure the frontend knows what was requested

        return {
            'status': 'success',
            'message': 'Model settings updated',
            'settings': original_settings,
            'applied_settings': current_kwargs
        }

    except Exception as e:
        logger.error(f"Error updating model settings: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error updating model settings: {str(e)}"
        )

@app.post('/api/abort-stream')
async def abort_stream(request: Request):
    """Explicitly abort a streaming response from the client side."""
    try:
        body = await request.json()
        conversation_id = body.get("conversation_id") or body.get("conversationId")
        
        if not conversation_id:
            return JSONResponse(
                status_code=400,
                content={"error": "conversation_id is required"}
            )
            
        if conversation_id in active_streams:
            logger.info(f"Explicitly aborting stream for conversation: {conversation_id}")
            # Remove from active streams to signal to any ongoing processing that it should stop
            await cleanup_stream(conversation_id)
            return JSONResponse(content={"status": "success", "message": "Stream aborted"})
        else:
            return JSONResponse(content={"status": "not_found", "message": "No active stream found for this conversation"})
    except Exception as e:
        logger.error(f"Error aborting stream: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post('/api/apply-changes')
async def apply_changes(request: ApplyChangesRequest):
    try:
        logger.info(f"TRACE_ID: Received apply-changes request with ID: {request.requestId}")
        # Validate diff size
        if len(request.diff) < 100:  # Arbitrary minimum for a valid git diff
            logger.warning(f"Suspiciously small diff received: {len(request.diff)} bytes")
            logger.warning(f"Diff content: {request.diff}")

        logger.info(f"Received request to apply changes to file: {request.filePath}")
        logger.info(f"Raw request diff length: {len(request.diff)} bytes")
        logger.info(f"First 100 chars of raw diff for request {request.requestId}:")
        
        # Always use the client-provided request ID if available
        if request.requestId:
            request_id = request.requestId
            logger.info(f"Using client-provided request ID: {request_id}")
        else:
            # Only generate a server-side ID if absolutely necessary
            request_id = str(uuid.uuid4())
            logger.warning(f"Using server-side generated request ID: {request_id}")

        logger.info(request.diff[:100])
        logger.info(f"Full diff content: \n{request.diff}")

        # --- SUGGESTION: Add secure path validation ---
        user_codebase_dir = os.path.abspath(os.environ.get("ZIYA_USER_CODEBASE_DIR"))
        if not user_codebase_dir:
            raise ValueError("ZIYA_USER_CODEBASE_DIR environment variable is not set")
        
        user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        
        # Prioritize extracting the file path from the diff content itself
        extracted_path = extract_target_file_from_diff(request.diff)

        if extracted_path:
            file_path = os.path.join(user_codebase_dir, extracted_path)
            logger.info(f"Extracted target file from diff: {extracted_path}")
        elif request.filePath:
            # Fallback to using the provided filePath if extraction fails
            file_path = os.path.join(user_codebase_dir, request.filePath)
            logger.info(f"Using provided file path: {request.filePath}")

            # Resolve the absolute path and check if it's within the codebase dir
            resolved_path = os.path.abspath(file_path)
            if not resolved_path.startswith(user_codebase_dir):
                logger.error(f"Attempt to access file outside codebase directory: {resolved_path}")
                raise ValueError("Invalid file path specified")
        else:
            raise ValueError("Could not determine target file path from diff or request")

        # Extract individual diffs if multiple are present
        individual_diffs = split_combined_diff(request.diff)
        if len(individual_diffs) > 1:
            logger.info(f"Received combined diff with {len(individual_diffs)} files")
            # Find the diff for our target file
            logger.debug("Individual diffs:")
            logger.debug('\n'.join(individual_diffs))
            target_diff = None
            for diff in individual_diffs:
                target_file = extract_target_file_from_diff(diff)
                if target_file and os.path.normpath(target_file) == os.path.normpath(extracted_path or request.filePath):
                    target_diff = diff
                    break

            if not target_diff:
                raise HTTPException(
                    status_code=400,
                    detail={
                        'status': 'error',
                        'type': 'file_not_found',
                        'message': f'No diff found for requested file {request.filePath} in combined diff'
                    }
                )
        else:
            logger.info("Single diff found")
            target_diff = individual_diffs[0]

        result = apply_diff_pipeline(request.diff, file_path, request_id)
        
        # Check the result status and return appropriate response
        status_code = 200 # Default to OK
        if result.get('status') == 'error':
            # Determine appropriate error code
            error_message = result.get('message', '').lower()
            if "file does not exist" in error_message:
                status_code = 404 # Not Found
            elif "malformed" in error_message or "failed to apply" in error_message:
                status_code = 422 # Unprocessable Entity
            else:
                status_code = 500 # Internal Server Error
        elif result.get('status') == 'partial':
            status_code = 207 # Multi-Status
 
        return JSONResponse(content=result, status_code=status_code)

    except Exception as e:
        error_msg = str(e)
        if isinstance(e, PatchApplicationError):
            details = e.details
            logger.error(f"Patch application failed:")
            status = details.get('status', 'error')
            if status == 'success':
                return JSONResponse(status_code=200, content={
                    'status': 'success',
                    'message': 'Changes applied successfully',
                    'request_id' : request_id,
                    'details': details
                })
            elif status == 'partial':
                return JSONResponse(status_code=207, content={
                    'status': 'partial',
                    'message': str(e),
                    'request_id' : request_id,
                    'details': details
                })
            elif status == 'error':
                error_type = details.get('type', 'unknown')
                if error_type == 'no_hunks':
                    status_code = 400  # Bad Request
                elif error_type == 'invalid_count':
                    status_code = 500  # Internal Server Error
                elif error_type == 'missing_file':
                    status_code = 404 # Not Found
                else:
                    status_code = 422  # Unprocessable Entity

                # Format error response based on whether we have multiple failures
                error_content = {
                    'status': 'error',
                    'message': str(e),
                    'request_id': request_id
                }
                if 'failures' in details:
                    error_content['failures'] = details['failures']
                else:
                    error_content['details'] = details

                raise HTTPException(status_code=status_code, detail={
                    'status': 'error',
                    'request_id': request_id,
                    **error_content
                })
        logger.error(f"Error applying changes: {error_msg}")
        if isinstance(e, FileNotFoundError):
             status_code = 404
        elif isinstance(e, ValueError): # e.g., invalid path
             status_code = 400 # Bad Request
        else:
            status_code = 500 # Default Internal Server Error
        raise HTTPException(
            # Determine status code based on exception type if possible
            status_code = status_code,
            detail={
                'status': 'error',
                'request_id': request_id,
                'message': f"Unexpected error: {error_msg}"
            }
        )
