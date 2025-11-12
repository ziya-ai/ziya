# Context overflow management
from typing import Optional, Dict, Any, List, Tuple, Union
import asyncio
from threading import Lock
from contextlib import asynccontextmanager

# Global state for managing context overflow
_continuation_lock = Lock()
_active_continuations = {}

import os
import os.path
import re
import signal
import time
import threading
import json
import hashlib
import uuid
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from starlette.background import BackgroundTask
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
from app.agents.agent import get_or_create_agent, get_or_create_agent_executor, create_agent_chain, create_agent_executor
from app.agents.agent import update_conversation_state, update_and_return, parse_output
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError 
from fastapi.responses import FileResponse
from starlette.responses import StreamingResponse
from pydantic import BaseModel, Field

# Direct streaming imports
from app.config.app_config import USE_DIRECT_STREAMING
from app.agents.direct_streaming import get_direct_streaming_agent, get_shell_tool_schema

# Import configuration
import app.config.models_config as config
from app.config.app_config import DEFAULT_PORT
from app.agents.models import ModelManager
from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE
from app.agents.wrappers.nova_wrapper import NovaBedrock  # Import NovaBedrock for isinstance check
from botocore.exceptions import ClientError, BotoCoreError, CredentialRetrievalError
from botocore.exceptions import EventStreamError
import botocore.errorfactory
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
from app.middleware import RequestSizeMiddleware, ModelSettingsMiddleware, ErrorHandlingMiddleware, HunkStatusMiddleware, StreamingMiddleware
from app.utils.context_enhancer import initialize_ast_if_enabled
from fastapi.websockets import WebSocketState
from app.middleware.continuation import ContinuationMiddleware

# WebSocket support for real-time feedback
from fastapi.websockets import WebSocket, WebSocketDisconnect
 
# Track active WebSocket connections for feedback
active_feedback_connections = {}

def build_messages_for_streaming(question: str, chat_history: List, files: List, conversation_id: str, use_langchain_format: bool = False) -> List:
    """
    Build messages for streaming using the extended prompt template.
    This centralizes message construction to avoid duplication.
    """
    logger.debug(f"🔍 FUNCTION_START: build_messages_for_streaming called with {len(files)} files")

    # Always use precision prompt system
    from app.utils.precision_prompt_system import precision_system
    from app.agents.prompts_manager import get_model_info_from_config

    model_info = get_model_info_from_config()
    request_path = "/streaming_tools"  # Default for streaming

    # Use precision system for 100% equivalence
    messages = precision_system.build_messages(
        request_path=request_path,
        model_info=model_info,
        files=files,
        question=question,
        chat_history=chat_history
    )

    logger.debug(f"🎯 PRECISION_SYSTEM: Built {len(messages)} messages with {len(files)} files preserved")

    # Convert to LangChain format if needed
    if use_langchain_format:
        from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
        langchain_messages = []
        for msg in messages:
            if isinstance(msg, dict) and "role" in msg:
                if msg["role"] == "system":
                    langchain_messages.append(SystemMessage(content=msg["content"]))
                elif msg["role"] == "user":
                    langchain_messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    langchain_messages.append(AIMessage(content=msg["content"]))
        return langchain_messages

    return messages


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

# Define lifespan context manager before app creation
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    # Check if MCP is enabled
    if os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
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
                
                # Initialize secure MCP tools
                from app.mcp.connection_pool import get_connection_pool as get_secure_pool
                secure_pool = get_secure_pool()
                secure_pool.set_server_configs(mcp_manager.server_configs)
                logger.info("Initialized secure MCP connection pool")
                
                # Force garbage collection to ensure clean state
                import gc; gc.collect()
                from app.agents.agent import create_agent_chain, create_agent_executor, model
                agent = create_agent_chain(model.get_model())
                agent_executor = create_agent_executor(agent)
                
                logger.info("LangServe completely disabled to prevent duplicate execution - using /api/chat only")
            else:
                logger.warning("MCP initialization failed or no servers configured")
            logger.info("MCP manager initialized successfully during startup")
        except Exception as e:
            logger.warning(f"MCP initialization failed during startup: {str(e)}")
    else:
        logger.info("MCP integration is disabled. Use --mcp flag to enable.")
    
    yield
    
    # Shutdown
    if os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
        try:
            from app.mcp.manager import get_mcp_manager
            mcp_manager = get_mcp_manager()
            await mcp_manager.shutdown()
            logger.info("MCP manager shutdown completed")
        except Exception as e:
            logger.warning(f"MCP shutdown failed: {str(e)}")

# Create the FastAPI app
app = FastAPI(
    title="Ziya API",
    description="API for Ziya, a code assistant powered by LLMs",
    version="0.1.0",
    lifespan=lifespan,
)

@app.websocket("/ws/feedback/{conversation_id}")
async def feedback_websocket(websocket: WebSocket, conversation_id: str):
    """WebSocket endpoint for real-time streaming feedback."""
    logger.info(f"🔄 FEEDBACK: WebSocket connection attempt for conversation {conversation_id}")
    await websocket.accept()
    logger.info(f"🔄 FEEDBACK: WebSocket connected for conversation {conversation_id}")
    
    # Register this connection
    active_feedback_connections[conversation_id] = {
        'websocket': websocket,
        'connected_at': time.time(),
        'feedback_queue': asyncio.Queue()
    }
    
    try:
        while True:
            try:
                # Listen for feedback messages
                data = await websocket.receive_json()
                feedback_type = data.get('type')
                
                if feedback_type == 'tool_feedback':
                    logger.info(f"🔄 FEEDBACK: Received tool feedback for {conversation_id}: {data.get('message', '')}")
                    
                    # Add to feedback queue for tool execution to consume
                    if conversation_id in active_feedback_connections:
                        await active_feedback_connections[conversation_id]['feedback_queue'].put(data)
                elif feedback_type == 'interrupt':
                    logger.info(f"🔄 FEEDBACK: Received interrupt request for {conversation_id}")
                    # Signal tool execution to pause/stop
                    await active_feedback_connections[conversation_id]['feedback_queue'].put({'type': 'interrupt'})
                
            except WebSocketDisconnect:
                logger.info(f"🔄 FEEDBACK: WebSocket disconnected for {conversation_id}")
                break
    finally:
        # Clean up connection
        if conversation_id in active_feedback_connections:
            del active_feedback_connections[conversation_id]

# PRIORITY ROUTE: /api/chat - MUST BE FIRST TO TAKE PRECEDENCE
@app.post('/api/chat')
async def chat_endpoint(request: Request):
    """Handle chat requests from the frontend with model-specific routing."""
    logger.debug("🔍 CHAT_ENDPOINT: /api/chat endpoint called - PRIORITY ROUTE")
    
    try:
        body = await request.json()
        logger.debug(f"🔍 CHAT_ENDPOINT: Request body keys: {list(body.keys())}")
        
        # Extract data from the request
        messages = body.get('messages', [])
        question = body.get('question', '') or body.get('message', '')  # Check both question and message
        files = body.get('files', [])
        conversation_id = body.get('conversation_id')
        
        logger.debug(f"🔍 CHAT_ENDPOINT: question='{question[:50]}...', messages={len(messages)}, files={len(files)}")
        
        # Check current model to determine routing
        from app.agents.models import ModelManager
        current_model = ModelManager.get_model_alias()
        logger.debug(f"🔍 CHAT_ENDPOINT: current_model={current_model}")
        is_bedrock_claude = current_model and ('claude' in current_model.lower() or 'sonnet' in current_model.lower() or 'opus' in current_model.lower() or 'haiku' in current_model.lower())
        is_bedrock_nova = current_model and 'nova' in current_model.lower()
        is_bedrock_deepseek = current_model and 'deepseek' in current_model.lower()
        is_bedrock_openai = current_model and 'openai' in current_model.lower()
        is_google_model = current_model and ('gemini' in current_model.lower() or 'google' in current_model.lower())
        # Check if direct streaming is enabled globally - use direct streaming by default for Bedrock models like 0.3.1
        use_direct_streaming = is_bedrock_claude or is_bedrock_nova or is_bedrock_deepseek or is_bedrock_openai or is_google_model
        
        logger.debug(f"🔍 CHAT_ENDPOINT: Current model = {current_model}, is_bedrock_claude = {is_bedrock_claude}")
        
        if use_direct_streaming:
            # Use direct streaming for Bedrock Claude and Nova models
            logger.debug("🔍 CHAT_ENDPOINT: Using DIRECT STREAMING for Bedrock models")
            
            # Format chat history - handle both tuple and dict formats
            chat_history = []
            
            # Check if the question is already the last message to avoid duplication
            messages_to_process = messages
            if messages and question:
                last_msg = messages[-1]
                if isinstance(last_msg, list) and len(last_msg) >= 2:
                    last_content = last_msg[1]
                elif isinstance(last_msg, dict):
                    last_content = last_msg.get('content', '')
                else:
                    last_content = ''
                
                # If the last message content matches the question, exclude it
                if last_content.strip() == question.strip():
                    messages_to_process = messages[:-1]
            
            for msg in messages_to_process:
                if isinstance(msg, list) and len(msg) >= 2:
                    # Frontend tuple format: ["human", "content"]
                    role, content = msg[0], msg[1]
                    if role in ['human', 'user']:
                        chat_history.append({'type': 'human', 'content': content})
                    elif role in ['assistant', 'ai']:
                        chat_history.append({'type': 'ai', 'content': content})
                elif isinstance(msg, dict):
                    # Already in dict format
                    role = msg.get('role', msg.get('type', 'user'))
                    content = msg.get('content', '')
                    if role and content:
                        if role in ['human', 'user']:
                            chat_history.append({'type': 'human', 'content': content})
                        elif role in ['assistant', 'ai']:
                            chat_history.append({'type': 'ai', 'content': content})
            
            # Format the data for stream_chunks - LangChain expects files at top level
            formatted_body = {
                'question': question,
                'conversation_id': conversation_id,
                'chat_history': chat_history,
                'files': files,  # LangChain expects files at top level
                'config': {
                    'conversation_id': conversation_id,
                    'files': files  # Also include in config for compatibility
                }
            }
            
            logger.info("[CHAT_ENDPOINT] Using StreamingToolExecutor via stream_chunks for unified execution")
            
            return StreamingResponse(
                stream_chunks(formatted_body),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    "X-Content-Type-Options": "nosniff",
                    "Transfer-Encoding": "chunked",
                    "X-Nginx-Buffering": "no",
                    "Proxy-Buffering": "off",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type"
                }
            )
        else:
            # Use LangChain for other models (Gemini, Nova, etc.)
            logger.debug("🔍 CHAT_ENDPOINT: Using LANGCHAIN for non-Bedrock models")
            
            # Format chat history for LangChain
            formatted_chat_history = []
            
            # Check if the question is already the last message to avoid duplication
            messages_to_process = messages
            if messages and question:
                last_msg = messages[-1]
                if isinstance(last_msg, list) and len(last_msg) >= 2:
                    last_content = last_msg[1]
                elif isinstance(last_msg, dict):
                    last_content = last_msg.get('content', '')
                else:
                    last_content = ''
                
                # If the last message content matches the question, exclude it
                if last_content.strip() == question.strip():
                    messages_to_process = messages[:-1]
            
            for msg in messages_to_process:
                if isinstance(msg, list) and len(msg) >= 2:
                    # Frontend tuple format: ["human", "content"]
                    role, content = msg[0], msg[1]
                    if role in ['human', 'user']:
                        formatted_chat_history.append(('human', content))
                    elif role in ['assistant', 'ai']:
                        formatted_chat_history.append(('assistant', content))
                elif isinstance(msg, dict):
                    # Already in dict format
                    role = msg.get('role', msg.get('type', 'user'))
                    content = msg.get('content', '')
                    if role and content:
                        if role in ['human', 'user']:
                            formatted_chat_history.append(('human', content))
                        elif role in ['assistant', 'ai']:
                            formatted_chat_history.append(('assistant', content))
            
            # Format the data for LangChain endpoint
            formatted_body = {
                'question': question,
                'conversation_id': conversation_id,
                'chat_history': formatted_chat_history,
                'config': {
                    'conversation_id': conversation_id,
                    'files': files
                }
            }
            
            # Forward to /ziya/stream endpoint for LangChain processing
            stream_request = Request(scope=request.scope)
            stream_request._body = json.dumps(formatted_body).encode()
            
            return await stream_endpoint(stream_request, formatted_body)
            
    except Exception as e:
        logger.error(f"Error in chat_endpoint: {str(e)}")
        return JSONResponse({"error": str(e)}, status_code=500)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add streaming middleware
app.add_middleware(StreamingMiddleware)

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

# Add continuation middleware
app.add_middleware(ContinuationMiddleware)

# Import and include AST routes
from app.routes.ast_routes import router as ast_router
app.include_router(ast_router)

# Add connection state tracking middleware
@app.middleware("http")
async def connection_state_middleware(request: Request, call_next):
    """Track connection state to handle disconnections gracefully."""
    # Only log API requests, not static assets
    if not request.url.path.startswith('/static/'):
        logger.debug(f"🔍 MIDDLEWARE: Request {request.method} {request.url.path}")
    
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

# Import and include conversation management routes
from app.routes.conversation_routes import router as conversation_router
app.include_router(conversation_router)

# Import and include MCP registry routes
from app.routes.mcp_registry_routes import router as mcp_registry_router
app.include_router(mcp_registry_router)

# Import and include model routes
from app.routes.model_routes import router as model_router
app.include_router(model_router)

# Import and include folder routes
from app.routes.folder_routes import router as folder_router
app.include_router(folder_router)

# Import and include token routes
from app.routes.token_routes import router as token_router
app.include_router(token_router)

# Import and include diff routes
from app.routes.diff_routes import router as diff_router
app.include_router(diff_router)

# Import and include static routes
from app.routes.static_routes import router as static_router
app.include_router(static_router)

# Import and include AST routes
# AST routes already imported and included above
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

# Global flag to prevent multiple LangServe initializations
_langserve_initialized = False

# SELECTIVELY REMOVE ONLY CONFLICTING LANGSERVE ROUTES
logger.info("=== REMOVING CONFLICTING LANGSERVE ROUTES ===")
routes_to_remove = []
for route in app.routes:
    if hasattr(route, 'path'):
        # Only remove routes that conflict with our custom streaming endpoints
        if (route.path == '/ziya/stream' and hasattr(route, 'endpoint') and 
            'langserve' in str(type(route.endpoint))):
            routes_to_remove.append(route)
            logger.info(f"Removing conflicting LangServe route: {route.path}")

for route in routes_to_remove:
    app.routes.remove(route)

logger.info(f"Removed {len(routes_to_remove)} conflicting LangServe routes")

# Log remaining /ziya routes
logger.info("=== REMAINING /ziya ROUTES ===")
for route in app.routes:
    if hasattr(route, 'path') and route.path.startswith('/ziya'):
        logger.info(f"Route: {route.methods if hasattr(route, 'methods') else 'N/A'} {route.path}")
logger.info("=== END /ziya ROUTES ===")

# DISABLED: LangServe routes bypass custom streaming and extended context handling
# add_routes(app, agent_executor, disabled_endpoints=["playground", "stream_log", "stream", "invoke"], path="/ziya")

# DISABLED: Manual /ziya endpoints conflict with /api/chat
# @app.post("/ziya/stream_log")
# async def stream_log_endpoint(request: Request, body: dict):
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

async def execute_tools_and_update_conversation(
    current_response: str, 
    processed_tool_calls: set, 
    messages: list
) -> Tuple[str, List[Dict[str, Any]]]:
    """Single consolidated function for tool execution and conversation updates."""
    processed_response = await detect_and_execute_mcp_tools(current_response, processed_tool_calls)
    tool_results = []
    
    if processed_response != current_response:
        # Extract tool results
        tool_blocks = []
        start_pos = 0
        while True:
            tool_start = processed_response.find("```tool:", start_pos)
            if tool_start == -1:
                break
            tool_end = processed_response.find("```", tool_start + 8)
            if tool_end == -1:
                break
            tool_block = processed_response[tool_start:tool_end + 3]
            tool_blocks.append(tool_block)
            start_pos = tool_end + 3
        
        # Update conversation with tool results
        from langchain_core.messages import AIMessage, HumanMessage
        
        messages.append(AIMessage(content=current_response))
        
        combined_results = "Tool execution results:\n\n"
        for i, tool_block in enumerate(tool_blocks, 1):
            clean_result = tool_block.split("```tool:")[1].split("```")[0].strip()
            combined_results += f"Result {i}:\n{clean_result}\n\n"
            tool_results.append({"block": tool_block, "result": clean_result})
        
        messages.append(AIMessage(content=combined_results))
        messages.append(HumanMessage(content="Continue your response based on the tool results above."))
        
        logger.debug(f"🔍 CONSOLIDATED_TOOLS: Executed {len(tool_blocks)} tools and updated conversation")
    else:
        logger.debug("🔍 CONSOLIDATED_TOOLS: No tool execution changes detected")
    
    return processed_response, tool_results


async def detect_and_execute_mcp_tools(full_response: str, processed_calls: Optional[set] = None) -> str:
    def clean_internal_sentinels(text: str) -> str:
        """Remove any tool sentinel fragments that might have leaked into the response."""
        import re
        # Remove complete tool sentinels
        text = text.replace(TOOL_SENTINEL_OPEN, "")
        text = text.replace(TOOL_SENTINEL_CLOSE, "")
        # Remove partial fragments that are clearly tool-related
        text = re.sub(r'<TOOL_[^>]*>', '', text)
        # Only remove <n> and <name> tags if they appear to be tool-related
        text = re.sub(r'<n>[^<]*</n>(?=\s*<arguments>)', '', text)  # Only if followed by arguments
        text = re.sub(r'<name>[^<]*</name>(?=\s*<arguments>)', '', text)  # Only if followed by arguments
        text = re.sub(r'<arguments>\s*\{[^}]*\}\s*</arguments>', '', text)  # Only if contains JSON-like content
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
    
    # Check if response contains tool calls (XML format or markdown format)
    has_xml_tools = TOOL_SENTINEL_OPEN in full_response
    has_markdown_tools = '```tool:' in full_response
    
    if not has_xml_tools and not has_markdown_tools:
        return full_response
    
    # Find all tool call blocks
    tool_call_pattern = re.escape(TOOL_SENTINEL_OPEN) + r'.*?' + re.escape(TOOL_SENTINEL_CLOSE)
    tool_calls = re.findall(tool_call_pattern, full_response, re.DOTALL)
    
    if not tool_calls:
        return full_response
    
    # CRITICAL: Only process the FIRST tool call to prevent multiple executions
    # This prevents the model from executing multiple tool calls that may depend on each other
    if len(tool_calls) > 1:
        logger.debug(f"🔍 MCP: Found {len(tool_calls)} tool calls, limiting to first one only")
        tool_calls = tool_calls[:1]
    
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
        
        logger.debug(f"🔍 MCP TOOL CALL: tool_name='{tool_name}', arguments={arguments}")
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
            
            logger.debug(f"🔍 MCP TOOL RESULT: tool_name='{internal_tool_name}', result_type={type(result)}, result={result}")
            logger.info(f"🔧 MCP EXECUTION: {internal_tool_name}({arguments}) -> {str(result)[:300]}{'...' if len(str(result)) > 300 else ''}")
            
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
            
            # Skip empty results to prevent empty blocks in frontend
            if not tool_output or tool_output.strip() == "":
                logger.debug(f"🔍 MCP: Skipping empty result for tool {tool_name}")
                # Remove the tool call entirely instead of replacing with empty block
                modified_response = modified_response.replace(tool_call_block, "")
                continue
            
            logger.debug(f"🔍 MCP: Tool executed successfully, output: {tool_output[:100]}...")
            
            # Replace the tool call with properly formatted tool block
            clean_output = clean_internal_sentinels(tool_output)
            
            # For sequential thinking, include the query in the tool name for better UI display
            display_tool_name = tool_name
            if tool_name == "mcp_sequentialthinking" and arguments.get("query"):
                query = arguments["query"][:80]  # Limit length
                display_tool_name = f"{tool_name}|{query}"
            
            replacement = f"\n```tool:{display_tool_name}\n{clean_output.strip()}\n```\n"
            modified_response = modified_response.replace(tool_call_block, replacement)
            
        except Exception as e:
            logger.error(f"🔍 MCP: Error executing tool {tool_name}: {str(e)}")
            # Replace tool call with error message
            error_msg = f"\n\n**Tool Error:** {str(e)}\n\n"
            modified_response = modified_response.replace(tool_call_block, error_msg)
    
    # Final cleanup to ensure no fragments remain
    return clean_internal_sentinels(modified_response)

def get_response_continuation_threshold() -> int:
    """
    Get the response continuation threshold based on current model's max_output_tokens.
    
    Returns:
        Token threshold at which continuation should be considered
    """
    try:
        from app.agents.models import ModelManager
        
        # Get current model settings
        model_settings = ModelManager.get_model_settings()
        max_output_tokens = model_settings.get("max_output_tokens", 4096)
        
        # Use 85% of the configured max_output_tokens as threshold
        # This leaves room for the model to complete its thought naturally
        threshold = int(max_output_tokens * 0.85)
        
        logger.debug(f"🔄 THRESHOLD: Using {threshold} tokens (85% of {max_output_tokens} max_output_tokens)")
        return threshold
        
    except Exception as e:
        logger.warning(f"Failed to get model token limit, using default: {e}")
        # Fallback to reasonable default
        return 3400  # 85% of 4096

async def check_context_overflow(
    current_response: str, 
    conversation_id: str,
    messages: List,
    full_context: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Check if we're approaching context limits and need to continue in a new session.
    
    Returns:
        None if no continuation needed
        Dict with continuation info if overflow detected
    """
    # Get current model's token threshold
    token_threshold = get_response_continuation_threshold()
    
    # Estimate current response tokens
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        response_tokens = len(encoding.encode(current_response))
    except Exception:
        # Fallback: rough character-based estimation
        response_tokens = len(current_response) // 4
    
    # Check if we're approaching the model's output token limit
    if response_tokens > token_threshold:
        logger.info(f"🔄 CONTEXT: Response tokens ({response_tokens}) approaching limit ({token_threshold}), preparing continuation")
        
        # Find a good breaking point (end of sentence or paragraph)
        continuation_point = find_continuation_point(current_response)
        
        if continuation_point:
            # Find the last complete line before continuation point
            lines = current_response[:continuation_point].split('\n')
            complete_lines = lines[:-1]  # All but the potentially partial last line
            partial_last_line = lines[-1] if lines else ""
            
            completed_part = '\n'.join(complete_lines)
            
            # Add rewind marker that identifies exactly where to splice
            rewind_marker = f"\n\n<!-- REWIND_MARKER: {len(complete_lines)} -->\n**🔄 Response continues...**\n"
            completed_part += rewind_marker
            
            # Prepare continuation state
            continuation_state = {
                "rewind_line_number": len(complete_lines),
                "partial_last_line": partial_last_line,
                "rewind_marker": f"<!-- REWIND_MARKER: {len(complete_lines)} -->",
                "conversation_id": conversation_id,
                "completed_response": completed_part,
                "messages": messages,
                "context": full_context,
                "continuation_id": f"{conversation_id}_cont_{int(time.time())}"
            }
            
            return continuation_state
    
    return None
 
def find_continuation_point(text: str) -> Optional[int]:
    """
    Find an appropriate point to break the response for continuation.
    
    Prioritizes:
    1. End of paragraphs (double newlines)
    2. End of sentences
    3. End of code blocks
    4. End of complete lines (for code/structured content)
    3. End of code blocks
    4. Natural word boundaries
    """
    
    # For code/structured content, prioritize complete lines
    lines = text.split('\n')
    if len(lines) > 10:
        # Find a line that ends with punctuation or closing brace
        for i in range(len(lines) - 3, max(0, len(lines) - 10), -1):
            line = lines[i].strip()
            if line and line[-1] in ';})]':
                line_end_pos = sum(len(l) + 1 for l in lines[:i+1]) - 1
                if line_end_pos < len(text) * 0.9:  # Not too close to end
                    logger.info(f"🔄 CONTINUATION: Found code line boundary at line {i+1}: '{line[-20:]}'")
                    return line_end_pos
    
    # Look for paragraph breaks first
    paragraph_breaks = [m.end() for m in re.finditer(r'\n\n+', text)]
    if paragraph_breaks:
        # Find the last paragraph break that's not too close to the end
        for break_point in reversed(paragraph_breaks):
            if break_point < len(text) * 0.8:  # Not in last 20% of text
                return break_point
    
    # Look for sentence endings
    sentence_endings = [m.end() for m in re.finditer(r'[.!?]\s+', text)]
    if sentence_endings:
        for break_point in reversed(sentence_endings):
            if break_point < len(text) * 0.8:
                return break_point
    
    # Look for code block endings
    code_block_endings = [m.end() for m in re.finditer(r'```\n+', text)]
    if code_block_endings:
        for break_point in reversed(code_block_endings):
            if break_point < len(text) * 0.8:
                return break_point
    
    # Fall back to word boundary
    words = text.split()
    if len(words) > 10:
        # Take about 80% of the words
        word_count = int(len(words) * 0.8)
        return len(' '.join(words[:word_count])) + 1
    
    return None
 
async def handle_continuation(continuation_state: Dict[str, Any]):
    """
    Handle continuation in a new query with clean output buffer.
    
    This creates a new streaming session that appears seamless to the user.
    """
    continuation_id = continuation_state["continuation_id"]
    
    with _continuation_lock:
        _active_continuations[continuation_id] = continuation_state
    
    try:
        logger.info(f"🔄 CONTINUATION: Starting continuation session {continuation_id}")
        
        # Create a continuation prompt that maintains context
        continuation_prompt = create_continuation_prompt(continuation_state)
        
        # Update messages for continuation
        updated_messages = continuation_state["messages"].copy()
        # Update messages for continuation
        updated_messages = continuation_state["messages"].copy()
        
        # Create continuation prompt that instructs exact pickup point
        # Much simpler approach - just tell model to continue with zero context about what came before
        continuation_prompt = "Continue your previous response from where it was cut off. Do not repeat any previous content."
        updated_messages.append(HumanMessage(content=continuation_prompt))
        
        # Stream continuation and handle rewind markers in frontend
        async for chunk in stream_continuation(updated_messages, continuation_state):
            # Add rewind marker to first chunk so frontend knows how to splice
            if hasattr(chunk, 'content') and chunk.content and not continuation_state.get('marker_sent'):
                chunk.content = f"<!-- REWIND_MARKER: {continuation_state['rewind_line_number']} -->" + chunk.content
                continuation_state['marker_sent'] = True
            yield chunk
            
    except Exception as e:
        logger.error(f"🔄 CONTINUATION: Error in continuation {continuation_id}: {e}")
        # Yield error and complete the stream
        yield f"data: {json.dumps({'error': f'Continuation error: {str(e)}'})}\n\n"
            
        # Clean up continuation state
        with _continuation_lock:
            _active_continuations.pop(continuation_id, None)

def splice_continuation_response(original_response: str, continuation_response: str, rewind_marker: str) -> str:
    """
    Splice continuation response into original response at the exact rewind marker.
    This ensures zero duplication around the boundary.
    """
    # Find the rewind marker in the original response
    marker_pos = original_response.find(rewind_marker)
    if marker_pos == -1:
        logger.warning("Rewind marker not found, appending continuation")
        return original_response + continuation_response
    
    # Split at the marker
    before_marker = original_response[:marker_pos]
    
    # Find where the continuation marker ends
    marker_end = original_response.find("**\n", marker_pos)
    if marker_end == -1:
        marker_end = marker_pos + len(rewind_marker)
    else:
        marker_end += 3  # Include the "**\n"
    
    # Splice: everything before marker + continuation response
    spliced = before_marker + continuation_response
    
    logger.info(f"🔄 SPLICE: Spliced continuation at marker, "
               f"original: {len(original_response)}, continuation: {len(continuation_response)}, "
               f"result: {len(spliced)}")
    
    return spliced
def create_continuation_prompt(continuation_state: Dict[str, Any]) -> str:
    """Create a prompt for seamless continuation."""
    remaining = continuation_state.get("remaining_response", "")
    
    if remaining:
        return f"Continue from where you left off. You were in the middle of: {remaining[:200]}..."
    else:
        return "Continue your response from where you left off. Maintain the same context and tone."

async def stream_continuation(messages: List, continuation_state: Dict[str, Any]):
    """Stream the continuation part with clean output buffer."""
    try:
        # Use the same streaming logic but with updated messages
        from app.agents.agent import model
        model_instance = model.get_model()
        
        # Stream the continuation
        async for chunk in model_instance.astream(messages):
            # Extract content from chunk - handle different chunk types
            if hasattr(chunk, 'content'):
                if callable(chunk.content):
                    content = chunk.content()
                else:
                    content = chunk.content
            else:
                content = str(chunk) if chunk else ""
            
            # Convert to string
            if isinstance(content, str):
                content_str = content
            else:
                content_str = str(content) if content else ""
                
            if content_str:
                yield f"data: {json.dumps({'content': content_str})}\n\n"
        
        yield f"data: {json.dumps({'done': True})}\n\n"
        
    except Exception as e:
        logger.error(f"Error in stream_continuation: {e}")
        raise

async def stream_chunks(body):
    """Stream chunks from the agent executor."""
    logger.debug("🔍 STREAM_CHUNKS: FUNCTION CALLED - ENTRY POINT")
    logger.error("🔍 EXECUTION_TRACE: stream_chunks() called - ENTRY POINT")
    logger.debug("🔍 STREAM_CHUNKS: Function called")
    
    # Temporarily reduce context to test tool execution
    if body.get("question") and "distribution by file type" in body.get("question", "").lower():
        logger.debug("🔍 TEMP: Reducing context for tool execution test")
        if "config" in body and "files" in body["config"]:
            body["config"]["files"] = []  # Skip file context to avoid throttling
    
    # Restore 0.3.0 direct streaming behavior
    use_direct_streaming = True
    
    logger.debug(f"🔍 STREAM_CHUNKS: use_direct_streaming = {use_direct_streaming}")
    
    logger.debug(f"🚀 DIRECT_STREAMING: Environment check = {use_direct_streaming}")
    logger.debug(f"🚀 DIRECT_STREAMING: ZIYA_USE_DIRECT_STREAMING env var = '{os.getenv('ZIYA_USE_DIRECT_STREAMING', 'NOT_SET')}'")
    
    # Check if we should use direct streaming
    if use_direct_streaming:
        logger.info("🚀 DIRECT_STREAMING: Using StreamingToolExecutor for direct streaming")
        
        # Extract data from body for StreamingToolExecutor
        question = body.get("question", "")
        chat_history = body.get("chat_history", [])
        files = body.get("config", {}).get("files", [])
        conversation_id = body.get("conversation_id")
        
        logger.debug(f"🔍 DIRECT_STREAMING_DEBUG: question='{question}', chat_history={len(chat_history)}, files={len(files)}")
        
        if question:
            # Check for common connectivity-related errors early
            try:
                # Quick connectivity check before expensive operations
                from app.agents.models import ModelManager
                state = ModelManager.get_state()
                if state.get('last_auth_error') and 'i/o timeout' in str(state.get('last_auth_error')):
                    yield f"data: {json.dumps({'error': 'Network connectivity issue detected. Please check your internet connection and try again.', 'error_type': 'connectivity'})}\n\n"
                    return
            except Exception as conn_check_error:
                logger.debug(f"Connectivity pre-check failed: {conn_check_error}")
            
            try:
                from app.streaming_tool_executor import StreamingToolExecutor
                from app.agents.models import ModelManager
                
                # Get current model state
                state = ModelManager.get_state()
                current_region = state.get('aws_region', 'us-east-1')
                aws_profile = state.get('aws_profile', 'default')
                endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                
                # Only use StreamingToolExecutor for Bedrock models
                if endpoint != 'bedrock':
                    logger.debug(f"🚀 DIRECT_STREAMING: Endpoint {endpoint} not supported by StreamingToolExecutor, falling back to LangChain")
                    raise ValueError(f"StreamingToolExecutor only supports bedrock endpoint, got {endpoint}")
                
                logger.debug(f"🔍 DIRECT_STREAMING_DEBUG: About to call build_messages_for_streaming with {len(files)} files")
                # Build messages with full context using the same function as LangChain path - use langchain format like 0.3.0
                logger.debug(f"🔍 CALLING_BUILD_MESSAGES: About to call build_messages_for_streaming")
                messages = build_messages_for_streaming(question, chat_history, files, conversation_id, use_langchain_format=True)
                logger.debug(f"🔍 DIRECT_STREAMING_PATH: Built {len(messages)} messages with full context")
                
                # Debug the system message content
                if messages and hasattr(messages[0], 'content'):
                    system_content_length = len(messages[0].content)
                    logger.debug(f"🔍 DIRECT_STREAMING_DEBUG: System message length = {system_content_length}")
                    logger.debug(f"🔍 DIRECT_STREAMING_DEBUG: System message preview = {messages[0].content[:200]}...")
                
                executor = StreamingToolExecutor(profile_name=aws_profile, region=current_region)
                logger.debug(f"🚀 DIRECT_STREAMING: Created StreamingToolExecutor with profile={aws_profile}, region={current_region}")
                
                # Send initial heartbeat
                yield f"data: {json.dumps({'heartbeat': True, 'type': 'heartbeat'})}\n\n"
                
                chunk_count = 0
                async for chunk in executor.stream_with_tools(messages, conversation_id=conversation_id):
                    chunk_count += 1
                    
                    # Convert to expected format and yield all chunk types
                    if chunk.get('type') == 'text':
                        content = chunk.get('content', '')
                        yield f"data: {json.dumps({'content': content})}\n\n"
                    elif chunk.get('type') == 'tool_start':
                        # Stream tool start notification
                        yield f"data: {json.dumps({'tool_start': chunk})}\n\n"
                    elif chunk.get('type') == 'tool_display':
                        logger.debug(f"🔍 TOOL_DISPLAY: {chunk.get('tool_name')} completed")
                        # Stream tool result
                        yield f"data: {json.dumps({'tool_result': chunk})}\n\n"
                    elif chunk.get('type') == 'tool_execution':  # Legacy support
                        logger.debug(f"🔍 TOOL_EXECUTION (legacy): {chunk.get('tool_name')} completed")
                    elif chunk.get('type') == 'stream_end':
                        break
                    elif chunk.get('type') == 'error':
                        # Send error with all available details
                        error_data = {
                            'error': chunk.get('error', 'error'),
                            'content': chunk.get('content', 'Unknown error'),
                            'detail': chunk.get('detail'),
                            'can_retry': chunk.get('can_retry', False),
                            'retry_message': chunk.get('retry_message')
                        }
                        # Remove None values
                        error_data = {k: v for k, v in error_data.items() if v is not None}
                        yield f"data: {json.dumps(error_data)}\n\n"
                    elif chunk.get('type') == 'heartbeat':
                        # Pass through heartbeat messages
                        yield f"data: {json.dumps({'heartbeat': True, 'type': 'heartbeat'})}\\n\\n"
                    elif chunk.get('type') == 'tool_result_for_model':
                        # Don't stream to frontend - this is for model conversation only
                        logger.debug(f"Tool result for model conversation: {chunk.get('tool_use_id')}")
                    elif chunk.get('type') == 'throttling_error':
                        # Pass through throttling errors to frontend for inline display
                        yield f"data: {json.dumps(chunk)}\n\n"
                    elif chunk.get('type') == 'iteration_continue':
                        # Send heartbeat to flush stream before next iteration
                        yield f"data: {json.dumps({'heartbeat': True, 'type': 'heartbeat'})}\n\n"
                    else:
                        logger.debug(f"Unknown chunk type: {chunk.get('type')}")
                
                # Always send done message at the end
                yield f"data: {json.dumps({'done': True})}\n\n"
                
                logger.debug(f"🚀 DIRECT_STREAMING: Completed streaming with {chunk_count} chunks")
                return
                
            except ValueError as ve:
                # Expected error for non-Bedrock endpoints - fall through to LangChain silently
                logger.debug(f"🚀 DIRECT_STREAMING: {ve} - falling back to LangChain")
            except Exception as e:
                # Check if this is a connectivity-related error
                error_str = str(e)
                if any(indicator in error_str.lower() for indicator in ['i/o timeout', 'dial tcp', 'lookup', 'network', 'connection']):
                    yield f"data: {json.dumps({'error': 'Network connectivity issue. Please check your internet connection and try again.', 'error_type': 'connectivity', 'technical_details': str(e)[:200]})}\n\n"
                    return
                    
            except Exception as e:
                import traceback
                error_details = traceback.format_exc()
                logger.error(f"🚀 DIRECT_STREAMING: Error in StreamingToolExecutor: {e}")
                logger.error(f"🚀 DIRECT_STREAMING: Full traceback:\n{error_details}")
                # Fall through to LangChain path
                yield f"data: {json.dumps({'error': f'Service initialization failed: {str(e)[:100]}...', 'error_type': 'initialization'})}\n\n"
                return
        
        logger.info("🚀 DIRECT_STREAMING: No question found or error occurred, falling back to LangChain")
    
    # Build messages properly for non-Bedrock models
    question = body.get("question", "")
    chat_history = body.get("chat_history", [])
    files = body.get("config", {}).get("files", [])
    conversation_id = body.get("conversation_id")
    
    if question:
        messages = build_messages_for_streaming(question, chat_history, files, conversation_id, use_langchain_format=True)
        logger.debug(f"🔍 LANGCHAIN_PATH: Built {len(messages)} messages for non-Bedrock model")
    else:
        
        # Extract messages from body
        messages = []
        logger.debug(f"Request body keys: {list(body.keys())}")
        logger.debug(f"chat_history present: {'chat_history' in body}")
        logger.debug(f"question present: {'question' in body}")
        logger.debug(f"config contents: {body.get('config', 'No config key')}")
        if 'question' in body:
            logger.debug(f"question value: '{body['question']}'")
        
        # Add system prompt with full capabilities (including visualization)
        from app.agents.prompts_manager import get_extended_prompt, get_model_info_from_config
        
        # Get model info for prompt extensions
        model_info = get_model_info_from_config()
        
        # Get MCP context, including endpoint and model_id for extensions
        from app.agents.models import ModelManager
        mcp_context = {
            "model_id": ModelManager.get_model_id(),
            "endpoint": model_info["endpoint"]
        }
        try:
            from app.mcp.manager import get_mcp_manager
            mcp_manager = get_mcp_manager()
            if mcp_manager.is_initialized:
                available_tools = [tool.name for tool in mcp_manager.get_all_tools()]
                mcp_context["mcp_tools_available"] = len(available_tools) > 0
                mcp_context["available_mcp_tools"] = available_tools
        except Exception as e:
            logger.warning(f"Could not get MCP tools for stream_chunks: {e}")

        extended_prompt = get_extended_prompt(
            model_name=model_info["model_name"],
            model_family=model_info["model_family"],
            endpoint=model_info["endpoint"],
            context=mcp_context
        )
        
        # Extract system content from the extended prompt
        system_message_template = extended_prompt.messages[0]
        if hasattr(system_message_template, 'prompt') and hasattr(system_message_template.prompt, 'template'):
            system_content = system_message_template.prompt.template
            
            # Add codebase context
            codebase_content = ""
            logger.debug(f"Files in body: {'files' in body}")
            logger.debug(f"Files in config: {'files' in body.get('config', {})}")
            
            files_list = None
            if 'files' in body:
                files_list = body['files']
            elif 'config' in body and 'files' in body['config']:
                files_list = body['config']['files']
                
            if files_list:
                logger.debug(f"Files count: {len(files_list)}")
                from app.agents.agent import get_combined_docs_from_files
                codebase_content = get_combined_docs_from_files(files_list)
                logger.debug(f"Codebase content length: {len(codebase_content)}")
            else:
                logger.debug("No files provided, codebase will be empty")
            
            # Format the system message
            formatted_system_content = system_content.replace('{codebase}', codebase_content)
            
            # Check if MCP is actually enabled and has tools
            mcp_tools_text = "No tools available"
            # Check if MCP is enabled before loading tools
            if os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
                try:
                    mcp_manager = get_mcp_manager()
                    if mcp_manager.is_initialized:
                        available_tools = mcp_manager.get_all_tools()
                        if available_tools:
                            mcp_tools_text = f"MCP tools available: {', '.join([tool.name for tool in available_tools])}"
                        else:
                            mcp_tools_text = "MCP initialized but no tools available"
                    else:
                        mcp_tools_text = "MCP tools disabled"
                except Exception as e:
                    mcp_tools_text = "MCP tools unavailable"
            else:
                mcp_tools_text = "MCP tools disabled"
                
            formatted_system_content = formatted_system_content.replace('{tools}', mcp_tools_text)
            
            messages.append({'type': 'system', 'content': formatted_system_content})
        
        # Handle both 'chat_history' and 'messages' formats
        chat_messages = body.get('chat_history') or body.get('messages', [])
        logger.debug(f"Found {len(chat_messages)} chat messages")
        for i, msg in enumerate(chat_messages):
            logger.debug(f"Message {i}: {msg}")
            # Convert list format [role, content] to dict format
            if isinstance(msg, list) and len(msg) == 2:
                role, content = msg
                if role == 'human':
                    messages.append({'type': 'human', 'content': content})
                elif role == 'assistant':
                    messages.append({'type': 'ai', 'content': content})
                elif role == 'system':
                    messages.append({'type': 'system', 'content': content})
            else:
                messages.append(msg)
        
        # Add the current question as a user message
        if 'question' in body and body['question']:
            messages.append({'type': 'human', 'content': body['question']})
        
        logger.debug(f"Final messages count: {len(messages)}")
        if messages:
            logger.debug(f"First message type: {messages[0].get('type', 'unknown')}")
            logger.debug(f"System message length: {len(messages[0].get('content', '')) if messages[0].get('type') == 'system' else 'N/A'}")
        # Create DirectStreamingAgent and stream
        # try:
        #     agent = DirectStreamingAgent()
        #     
        #     chunk_count = 0
        #     tool_results_attempted = 0
        #     total_data_sent = 0
        #     
        #     # Get available tools to pass to the agent
        #     from app.mcp.enhanced_tools import create_secure_mcp_tools
        #     mcp_tools = create_secure_mcp_tools()
        #     logger.debug(f"🚀 DIRECT_STREAMING: Passing {len(mcp_tools)} tools to DirectStreamingAgent")
        #     
        #     async for chunk in agent.stream_with_tools(messages, tools=mcp_tools, conversation_id=body.get('conversation_id')):
        #         chunk_count += 1
        #         
        #         if chunk.get('type') == 'tool_execution':
        #             tool_results_attempted += 1
        #             logger.debug(f"🔍 ATTEMPTING_TOOL_TRANSMISSION: #{tool_results_attempted} - {chunk.get('tool_name')}")
        #             
        #             # DEBUGGING: Test JSON serialization before transmission
        #             try:
        #                 test_json = json.dumps(chunk)
        #                 json_size = len(test_json)
        #                 logger.debug(f"🔍 JSON_SERIALIZATION: {chunk.get('tool_name')} serialized to {json_size} chars")
        #                 
        #                 if json_size > 100000:  # 100KB
        #                     logger.warning(f"🔍 LARGE_JSON_PAYLOAD: {chunk.get('tool_name')} JSON is {json_size} chars")
        #                     if json_size > 1000000:  # 1MB
        #                         logger.error(f"🔍 JSON_TOO_LARGE: {chunk.get('tool_name')} JSON is {json_size} chars - may break transmission")
        #                         
        #             except Exception as json_error:
        #                 logger.error(f"🔍 JSON_SERIALIZATION_FAILED: {chunk.get('tool_name')} failed to serialize: {json_error}")
        #                 continue  # Skip this chunk
        #         
        #         sse_data = f"data: {json.dumps(chunk)}\n\n"
        #         chunk_size = len(sse_data)
        #         total_data_sent += chunk_size
        #         
        #         # Log large chunks or tool results
        #         if chunk.get('type') == 'tool_execution' or chunk_size > 1000:
        #             logger.debug(f"🔍 CHUNK_TRANSMISSION: chunk #{chunk_count}, type={chunk.get('type')}, size={chunk_size}, total_sent={total_data_sent}")
        #             if chunk.get('type') == 'tool_execution':
        #                 logger.debug(f"🔍 TOOL_CHUNK: tool_name={chunk.get('tool_name')}, result_size={len(chunk.get('result', ''))}")
        #         
        #         yield sse_data
        #         
        #         # Force immediate delivery for tool results
        #         if chunk.get('type') == 'tool_execution':
        #             import sys
        #             sys.stdout.flush()
        #     
        #     yield "data: [DONE]\n\n"
        #     return
        # except CredentialRetrievalError as e:
        #     # Handle credential errors (including mwinit failures) with proper SSE error response
        #     from app.utils.error_handlers import handle_streaming_error
        #     async for error_chunk in handle_streaming_error(None, e):
        #         yield error_chunk
        #     return
        # except ValueError as e:
        #     if "OpenAI models should use LangChain path" in str(e):
        #         logger.info("🚀 DIRECT_STREAMING: OpenAI model detected, falling back to LangChain path")
        #         # Fall through to LangChain path below
        #     else:
        #         raise
        pass  # DirectStreamingAgent disabled
        
        # Check if model should use LangChain path instead of StreamingToolExecutor
        from app.agents.models import ModelManager
        try:
            model_id_result = ModelManager.get_model_id()
            if isinstance(model_id_result, dict):
                current_model_id = list(model_id_result.values())[0]
            else:
                current_model_id = model_id_result
            
            # OpenAI models should use same message construction as other Bedrock models
            if current_model_id and 'openai' in current_model_id.lower():
                logger.debug(f"{current_model_id} detected, using same message construction as other Bedrock models")
                
                # Extract variables from request body
                question = body.get("question", "")
                chat_history = body.get("chat_history", [])
                config_data = body.get("config", {})
                files = config_data.get("files", [])
                conversation_id = body.get("conversation_id") or config_data.get("conversation_id")
                
                # Handle frontend messages format - same as other models
                if "messages" in body:
                    frontend_messages = body.get("messages", [])
                    # Convert frontend format to chat_history
                    for msg in frontend_messages:
                        if isinstance(msg, list) and len(msg) >= 2:
                            role, content = msg[0], msg[1]
                            if role in ['human', 'user']:
                                chat_history.append({'type': 'human', 'content': content})
                            elif role in ['assistant', 'ai']:
                                chat_history.append({'type': 'ai', 'content': content})
                
                # Use StreamingToolExecutor path for OpenAI models to get same context
                try:
                    from app.agents.direct_streaming import StreamingToolExecutor
                    executor = StreamingToolExecutor()
                    
                    # Build messages using same method as other Bedrock models
                    messages = executor.build_messages(question, chat_history, files, conversation_id)
                    
                    # Convert to LangChain format for OpenAI wrapper
                    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
                    langchain_messages = []
                    for msg in messages:
                        if msg["role"] == "system":
                            langchain_messages.append(SystemMessage(content=msg["content"]))
                        elif msg["role"] == "user":
                            langchain_messages.append(HumanMessage(content=msg["content"]))
                        elif msg["role"] == "assistant":
                            langchain_messages.append(AIMessage(content=msg["content"]))
                    
                    # Skip to LangChain execution with proper messages
                    messages = langchain_messages
                    logger.debug(f"Built {len(messages)} LangChain messages for OpenAI model")
                    
                    # Jump directly to LangChain execution
                    model_instance = model.get_model()
                    
                    # Stream the response
                    async for chunk in model_instance.astream(messages):
                        if hasattr(chunk, 'content') and chunk.content:
                            content_str = chunk.content
                            if content_str:
                                yield f"data: {json.dumps({'content': content_str})}\n\n"
                    
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    return
                    
                except Exception as e:
                    logger.error(f"🚀 DIRECT_STREAMING: Error in OpenAI message construction: {e}")
                    # Fall through to regular LangChain path
            else:
                # DISABLED: Redundant StreamingToolExecutor path - causes duplicate execution
                logger.info("🚀 DIRECT_STREAMING: Skipping redundant StreamingToolExecutor path - using primary path only")
                pass
                
                # Debug: Log what we received
                logger.debug(f"Received question: '{question}'")
                logger.debug(f"Received chat_history with {len(chat_history)} items")
                for i, item in enumerate(chat_history):
                    logger.debug(f"Chat history item {i}: {item}")
                logger.debug(f"Received {len(files)} files")
                
                # Build messages using existing function - use langchain format for proper message handling
                messages = build_messages_for_streaming(question, chat_history, files, 
                                                       body.get("conversation_id", f"direct_{int(time.time())}"),
                                                       use_langchain_format=True)
                
                # Debug: Log the messages being sent
                logger.debug(f"Built {len(messages)} messages for StreamingToolExecutor")
                for i, msg in enumerate(messages):
                    role = msg.get('role', 'unknown')
                    content_preview = msg.get('content', '')[:100] + '...' if len(msg.get('content', '')) > 100 else msg.get('content', '')
                    logger.debug(f"Message {i}: role={role}, content_preview='{content_preview}'")
                
                # Use StreamingToolExecutor for proper tool execution (Bedrock only)
                endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                logger.debug(f"Current endpoint: {endpoint}")
                
                if endpoint == "bedrock":
                    try:
                        logger.debug("ENTERING StreamingToolExecutor section")
                        from app.streaming_tool_executor import StreamingToolExecutor
                        
                        # Create StreamingToolExecutor instance with correct region
                        state = ModelManager.get_state()
                        current_region = state.get('aws_region', 'us-east-1')
                        aws_profile = state.get('aws_profile')
                        executor = StreamingToolExecutor(profile_name=aws_profile, region=current_region)
                        
                        logger.info("🚀 DIRECT_STREAMING: Using StreamingToolExecutor")
                        logger.debug("About to load MCP tools")
                        
                        # Get available tools including MCP tools
                        tools = []
                        
                        # Check if MCP is enabled before loading tools
                        if not os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
                            logger.debug("MCP is disabled, no tools will be loaded")
                        else:
                            try:
                                from app.mcp.manager import get_mcp_manager
                                mcp_manager = get_mcp_manager()
                                logger.debug(f"MCP manager initialized: {mcp_manager.is_initialized}")
                                if mcp_manager.is_initialized:
                                    # Convert MCP tools to Bedrock format
                                    mcp_tools = mcp_manager.get_all_tools()
                                    logger.debug(f"Found {len(mcp_tools)} MCP tools")
                                    for tool in mcp_tools:
                                        logger.debug(f"MCP tool: {tool.name}")
                                        tools.append({
                                            'name': tool.name,
                                        'description': tool.description,
                                        'input_schema': getattr(tool, 'inputSchema', getattr(tool, 'input_schema', {}))
                                    })
                            except Exception as e:
                                logger.debug(f"MCP tool loading error: {e}")
                                logger.warning(f"Could not get MCP tools: {e}")
                        
                        # Add shell tool if no MCP tools available
                        if not tools:
                            logger.debug("No MCP tools found, using shell tool")
                            # from app.agents.direct_streaming import get_shell_tool_schema
                            # tools = [get_shell_tool_schema()]
                            logger.debug("Shell tool functionality not available")
                        else:
                            logger.debug(f"Using {len(tools)} tools: {[t['name'] for t in tools]}")
                        
                        # DISABLED: Redundant StreamingToolExecutor call - causes duplicate execution
                        # async for chunk in executor.stream_with_tools(messages, tools):
                        logger.info("🚀 DIRECT_STREAMING: Skipping redundant StreamingToolExecutor call")
                        return
                        
                    except Exception as e:
                        logger.error(f"🚀 DIRECT_STREAMING: Error in StreamingToolExecutor: {e}")
                        yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
                        return
                else:
                    # For non-Bedrock endpoints, skip StreamingToolExecutor and use LangChain path
                    logger.debug(f"🚀 DIRECT_STREAMING: Skipping StreamingToolExecutor for endpoint '{endpoint}' - using LangChain path")
        except Exception as e:
            logger.error(f"🚀 DIRECT_STREAMING: Error checking model ID: {e}")
            # For Nova models, still try to use StreamingToolExecutor even if model ID check fails
            from app.agents.models import ModelManager
            try:
                current_model = ModelManager.get_model_alias()
                if current_model and any(nova_model in current_model.lower() for nova_model in ['nova-micro', 'nova-lite', 'nova-pro', 'nova-premier']):
                    logger.debug(f"🚀 DIRECT_STREAMING: Nova model detected ({current_model}), forcing StreamingToolExecutor path")
                    # Force Nova to use StreamingToolExecutor
                    endpoint = "bedrock"
                    # Continue to StreamingToolExecutor section below
                else:
                    # Fall through to LangChain path for non-Nova models
                    pass
            except:
                # Fall through to LangChain path
                pass
    
    # Check if this is a Nova model before falling back to LangChain
    try:
        from app.agents.models import ModelManager
        current_model = ModelManager.get_model_alias()
        if current_model and any(nova_model in current_model.lower() for nova_model in ['nova-micro', 'nova-lite', 'nova-pro', 'nova-premier']):
            logger.debug(f"🚀 DIRECT_STREAMING: Nova model ({current_model}) should not use LangChain path - redirecting to StreamingToolExecutor")
            # Force Nova models to use StreamingToolExecutor by setting endpoint to bedrock
            endpoint = "bedrock"
            # Jump to StreamingToolExecutor section
            if endpoint == "bedrock":
                try:
                    logger.debug("ENTERING StreamingToolExecutor section for Nova")
                    from app.streaming_tool_executor import StreamingToolExecutor
                    
                    # Create StreamingToolExecutor instance with correct region
                    state = ModelManager.get_state()
                    current_region = state.get('aws_region', 'us-east-1')
                    aws_profile = state.get('aws_profile')
                    executor = StreamingToolExecutor(profile_name=aws_profile, region=current_region)
                    
                    logger.info("🚀 DIRECT_STREAMING: Using StreamingToolExecutor for Nova")
                    
                    # Build messages for Nova
                    question = body.get("question", "")
                    chat_history = body.get("chat_history", [])
                    config_data = body.get("config", {})
                    files = config_data.get("files", [])
                    conversation_id = body.get("conversation_id") or config_data.get("conversation_id")
                    
                    # Handle frontend messages format conversion
                    if (not chat_history or len(chat_history) == 0) and "messages" in body:
                        messages = body.get("messages", [])
                        if len(messages) > 1:
                            raw_history = messages[:-1]
                            for msg in raw_history:
                                if isinstance(msg, list) and len(msg) >= 2:
                                    role, content = msg[0], msg[1]
                                    if role in ['human', 'user']:
                                        chat_history.append({'type': 'human', 'content': content})
                                    elif role in ['assistant', 'ai']:
                                        chat_history.append({'type': 'ai', 'content': content})
                    
                    # Build messages using same method as other Bedrock models
                    messages = build_messages_for_streaming(
                        question=question,
                        chat_history=chat_history,
                        files=files,
                        conversation_id=conversation_id
                    )
                    
                    logger.debug(f"Built {len(messages)} messages for Nova StreamingToolExecutor")
                    
                    # DISABLED: Redundant Nova StreamingToolExecutor call - causes duplicate execution  
                    # async for chunk in executor.stream_with_tools(messages):
                    logger.info("🚀 DIRECT_STREAMING: Skipping redundant Nova StreamingToolExecutor call")
                    return
                    
                except Exception as e:
                    logger.error(f"🚀 DIRECT_STREAMING: Error in Nova StreamingToolExecutor: {e}")
                    # Fall through to LangChain as last resort
                    pass
    except:
        pass
    
    # Fallback to LangChain for non-direct streaming
    logger.debug("🔍 STREAM_CHUNKS: Using LangChain mode")
    yield f"data: {json.dumps({'heartbeat': True, 'type': 'heartbeat'})}\n\n"
    
    # Track if we've successfully sent any data
    data_sent = False

    # Prepare messages for the model
    from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

    # Initialize variables that are always needed
    conversation_id = body.get("conversation_id")
    if not conversation_id:
        config_data = body.get("config", {})
        conversation_id = config_data.get("conversation_id")
    if not conversation_id:
        import uuid
        conversation_id = f"stream_{uuid.uuid4().hex[:8]}"

    # Check if messages were already built for OpenAI models in direct streaming section
    if 'messages' not in locals():
        # Extract all needed variables from request body
        question = body.get("question", "")
        chat_history = body.get("chat_history", [])
        
        # Log the user's question at INFO level
        if question.strip():
            logger.info(f"👤 USER QUERY: {question}")
        
        # Handle frontend messages format conversion
        if (not chat_history or len(chat_history) == 0) and "messages" in body:
            messages = body.get("messages", [])
            logger.debug(f"🔍 FRONTEND_MESSAGES: Raw messages from frontend: {messages}")
            # Convert [["human", "content"], ["assistant", "content"]] to chat_history format
            # Skip the last message as it's the current question
            if len(messages) > 1:
                raw_history = messages[:-1]  # All but the last message
                # Convert to proper format
                chat_history = []
                for msg in raw_history:
                    if isinstance(msg, list) and len(msg) >= 2:
                        role, content = msg[0], msg[1]
                        if role in ['human', 'user']:
                            chat_history.append({'type': 'human', 'content': content})
                        elif role in ['assistant', 'ai']:
                            chat_history.append({'type': 'ai', 'content': content})
            logger.debug(f"🔍 FRONTEND_MESSAGES: Converted {len(messages)} frontend messages to {len(chat_history)} chat history items")
            logger.debug(f"🔍 FRONTEND_MESSAGES: Chat history: {chat_history}")
        
        
        config_data = body.get("config", {})
        files = config_data.get("files", [])
        
        logger.debug(f"🔍 STREAM_CHUNKS: Using conversation_id: {conversation_id}")

        # Use centralized message construction to eliminate all duplication
        messages = build_messages_for_streaming(question, chat_history, files, conversation_id, use_langchain_format=True)
    
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
        msg_type = getattr(message, 'type', getattr(message, 'role', 'unknown') if hasattr(message, 'role') else 'unknown')
        logger.info(f"MESSAGE {i+1}/{len(messages)} - TYPE: {msg_type}")
        logger.info(f"ROLE: {getattr(message, 'role', 'N/A')}")
        content = getattr(message, 'content', '')
        logger.info(f"CONTENT LENGTH: {len(content) if content else 0} characters")
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
            logger.debug("🔍 STREAM: No conversation_id in body, checking config...")
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
            logger.debug(f"🔍 STREAM: Final conversation_id: {conversation_id}")
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
        
        # Check if we have a Google function calling agent available
        from app.agents.models import ModelManager
        agent_chain_cache = ModelManager._state.get('agent_chain_cache', {})
        agent_chain = None
        for cache_key, cached_agent in agent_chain_cache.items():
            if cached_agent and hasattr(cached_agent, 'func') and 'google_agent_call' in str(cached_agent.func):
                agent_chain = cached_agent
                break
        
        if agent_chain:
            logger.debug("🔍 STREAM_CHUNKS: Using agent chain with file context")
            # Use agent chain with proper file context
            try:
                input_data = {
                    "question": question,
                    "conversation_id": conversation_id,
                    "chat_history": chat_history,
                    "config": {
                        "conversation_id": conversation_id,
                        "files": files  # Include the actual files
                    }
                }
                
                result = agent_chain.invoke(input_data)
                response_content = result.get("output", "")
                
                # Stream the response
                yield f"data: {json.dumps({'type': 'text', 'content': response_content})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return
                
            except Exception as e:
                logger.error(f"Agent chain failed: {e}")
                # Fall back to direct model approach
        
        # Use the messages that were already built correctly above with build_messages_for_streaming()
        # Don't rebuild them here - this was causing the context history loss for OpenAI models
        logger.debug(f"🔍 STREAM_CHUNKS: Using {len(messages)} messages built by build_messages_for_streaming()")
        
        # Stream directly from the model
        
        # Enhance context with AST if available
        from app.utils.context_enhancer import enhance_context_with_ast
        enhanced_context = enhance_context_with_ast(question, {"codebase": "current"})
        if enhanced_context.get("ast_context"):
            logger.info(f"Enhanced context with AST: {len(enhanced_context['ast_context'])} chars")
        
        chunk_count = 0
        full_response = ""

        full_context = {"body": body, "files": files, "chat_history": chat_history}
        # Initialize variables for agent iteration loop
        processed_tool_calls = set()
        max_iterations = 20
        iteration = 0
        messages_for_model = messages  # Use the correctly built messages from build_messages_for_streaming()
        all_tool_results = []  # Track all tool results across iterations

        logger.debug(f"🔍 STREAM_CHUNKS: Using model instance type: {type(model.get_model())}")
        logger.debug(f"🔍 STREAM_CHUNKS: Model has tools: {hasattr(model.get_model(), 'tools') if hasattr(model.get_model(), 'tools') else 'No tools attribute'}")
        logger.debug("🔍 STREAM_CHUNKS: About to start model streaming")

        done_marker_sent = False
        
        processed_tool_calls = set()  # Track which tool calls we've already processed
        # Create a background task for cleanup when the stream ends

        token_throttling_retries = 0
        max_token_throttling_retries = 2  # Allow 2 fresh connection attempts
        within_stream_retries = 0
        max_within_stream_retries = 3  # Quick retries within same stream first

        # Context overflow detection state
        overflow_checked = False
        continuation_triggered = False
        
        # Get MCP tools for the iteration
        mcp_tools = []

        # Get MCP tools for the iteration
        mcp_tools = []
        try:
            from app.mcp.enhanced_tools import create_secure_mcp_tools
            mcp_tools = create_secure_mcp_tools()
            logger.debug(f"🔍 STREAM_CHUNKS: Created {len(mcp_tools)} MCP tools for iteration")
        except Exception as e:
            logger.warning(f"Failed to get MCP tools for iteration: {e}")
        
        # Allow tool calls to complete - only stop at the END of tool calls
        try:
            model_with_stop = model_instance.bind(stop=["</TOOL_SENTINEL>"])
        except Exception as e:
            # Handle credential errors specifically
            error_str = str(e)
            if "mwinit" in error_str.lower() or "authentication" in error_str.lower() or "credential" in error_str.lower():
                # Preserve conversation context in error response
                conversation_id = body.get("conversation_id")
                if conversation_id:
                    logger.info(f"Adding conversation_id to credential error: {conversation_id}")
                else:
                    logger.warning("No conversation_id available for credential error")
                logger.error(f"Credential error during model binding: {e}")
                credential_error = {
                    "error": "auth_error",
                    "detail": "AWS credentials have expired. Please run 'mwinit' to authenticate and try again.",
                    "status_code": 401,
                    "technical_details": error_str
                }
                yield f"data: {json.dumps(credential_error)}\n\n"
                yield f"data: [DONE]\n\n"
                return
            raise  # Re-raise other errors
        
        logger.debug(f"🔍 STREAM_CHUNKS: model_with_stop type: {type(model_with_stop)}")

        # Agent iteration loop for tool execution
        while iteration < max_iterations:
            iteration += 1
            logger.debug(f"🔍 AGENT ITERATION {iteration}: Starting iteration")

            # Check for stream interruption requests
            if conversation_id not in active_streams:
                logger.info(f"🔄 Stream for {conversation_id} was interrupted, stopping gracefully")
                interruption_notice = {"op": "add", "path": "/processing_state", "value": "interrupted"}
                yield f"data: {json.dumps({'ops': [interruption_notice]})}\n\n"
                yield f"data: {json.dumps({'stream_interrupted': True})}\n\n"
                await cleanup_stream(conversation_id)
                return

            current_response = ""
            tool_executed = False
            tool_execution_completed = False  # Initialize the variable
        
            try:                
                # Use model instance for tool detection
                model_to_use = model_instance
                logger.debug(f"🔍 AGENT ITERATION {iteration}: Available tools: {[tool.name for tool in mcp_tools] if mcp_tools else 'No tools'}")

                # Track if we're currently inside a tool call across chunks
                inside_tool_call = False
                tool_call_buffer = ""
                tool_call_detected = False  # Flag to suppress ALL output after tool detection
                pending_tool_execution = False  # Flag to indicate we need to execute tools
                
                # DISABLED for Bedrock: LangChain streaming path - causes duplicate execution with StreamingToolExecutor
                # But ENABLED for non-Bedrock endpoints like Google
                endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                if endpoint == "bedrock":
                    logger.info("🚀 DIRECT_STREAMING: LangChain path disabled for Bedrock - using StreamingToolExecutor only")
                    return
                
                # Stream from model for non-Bedrock endpoints (use simple streaming like 0.3.0)
                async for chunk in model_instance.astream(messages):
                    # Log the actual messages being sent to model on first iteration
                    if iteration == 1 and not hasattr(stream_chunks, '_logged_model_input'):
                        stream_chunks._logged_model_input = True
                        logger.debug("🔥" * 50)
                        logger.debug("FINAL MODEL INPUT - ACTUAL MESSAGES SENT TO MODEL")
                        logger.debug("🔥" * 50)
                        for idx, msg in enumerate(messages):
                            logger.debug(f"FINAL MESSAGE {idx+1}: {type(msg).__name__}")
                            if hasattr(msg, 'content'):
                                logger.debug(f"CONTENT: {msg.content}")
                            elif isinstance(msg, dict) and 'content' in msg:
                                logger.debug(f"CONTENT: {msg['content']}")
                            else:
                                logger.debug(f"CONTENT: {msg}")
                            if hasattr(msg, 'additional_kwargs') and msg.additional_kwargs:
                                logger.debug(f"ADDITIONAL_KWARGS: {msg.additional_kwargs}")
                            logger.debug("-" * 30)
                        logger.debug("🔥" * 50)

                    # Check connection status
                    if not connection_active:
                        logger.info("Connection lost during agent iteration")
                        break
                    
                    # Handle dict chunks from DirectGoogleModel
                    if isinstance(chunk, dict):
                        if chunk.get('type') == 'text':
                            content_str = chunk.get('content', '')
                            if content_str:
                                current_response += content_str
                                ops = [{"op": "add", "path": "/streamed_output_str/-", "value": content_str}]
                                yield f"data: {json.dumps({'ops': ops})}\n\n"
                                chunk_count += 1
                        elif chunk.get('type') == 'error':
                            error_msg = chunk.get('content', 'Unknown error')
                            yield f"data: {json.dumps({'error': error_msg})}\n\n"
                            yield f"data: {json.dumps({'done': True})}\n\n"
                            return
                        continue
                    
                    # Process chunk content - always process chunks, don't check for 'content' attribute first
                    
                    # Check if this is an error response chunk
                    if (hasattr(chunk, 'response_metadata') and 
                        chunk.response_metadata and 
                        chunk.response_metadata.get('error_response')):
                        # This is an error response, handle it specially
                        logger.debug(f"🔍 AGENT: Detected error response chunk")
                        # The content should already be JSON formatted
                        yield f"data: {chunk.content}\n\n"
                        yield f"data: {json.dumps({'done': True})}\n\n"
                        return
                    
                    # Extract content from different chunk types
                    if hasattr(chunk, 'message') and hasattr(chunk.message, 'content'):
                        content = chunk.message.content
                    elif hasattr(chunk, 'content'):
                        if callable(chunk.content):
                            content = chunk.content()
                        else:
                            content = chunk.content
                    else:
                        content = ""
                    
                    # Ensure we get the actual text content, not the object representation
                    if hasattr(content, 'text'):
                        content_str = content.text
                    elif isinstance(content, str):
                        content_str = content
                    else:
                        content_str = str(content) if content else ""
                    
                    # Always accumulate content in current_response for tool detection
                    current_response += content_str

                    # More frequent overflow checking - check every 20 chunks or when approaching token limits
                    if not overflow_checked:
                        # Estimate tokens more frequently
                        estimated_tokens = len(current_response) // 4
                        token_threshold = get_response_continuation_threshold()
                        
                        # Check if we're approaching 90% of the limit OR every 20 chunks
                        should_check = (
                            estimated_tokens > token_threshold * 0.9 or
                            chunk_count % 20 == 0 or
                            len(current_response) > 15000  # Lower character threshold
                        )
                        
                        if should_check:
                            logger.info(f"🔄 OVERFLOW_CHECK: tokens={estimated_tokens}, threshold={token_threshold}, chunk_count={chunk_count}")

                        overflow_info = await check_context_overflow(
                            current_response,
                            conversation_id, 
                            messages_for_model, 
                            full_context
                        )
                        
                        if overflow_info:
                            logger.info("🔄 CONTEXT: Triggering continuation due to overflow")
                            # Stream the completed part
                            
                            # Add visual marker that continuation is happening
                            marker_msg = "\n\n---\\n**⏳ Response is long, preparing continuation...**\\n---\n\n"
                            yield f"data: {json.dumps({'content': marker_msg})}\n\n"
                            
                            yield f"data: {json.dumps({'content': overflow_info['completed_response']})}\n\n"
                            
                            # Start continuation
                            async for continuation_chunk in handle_continuation(overflow_info):
                                yield continuation_chunk
                            return
                        overflow_checked = True
                    # Check for reasoning content in OpenAI format (ops structure)
                    if content_str and '<reasoning>' in content_str and '</reasoning>' in content_str:
                        import re
                        reasoning_matches = re.findall(r'<reasoning>(.*?)</reasoning>', content_str, re.DOTALL)
                        for reasoning in reasoning_matches:
                            ops = [{"op": "add", "path": "/reasoning_content/-", "value": reasoning}]
                            yield f"data: {json.dumps({'ops': ops})}\n\n"
                        # Remove reasoning tags from main content
                        content_str = re.sub(r'<reasoning>.*?</reasoning>', '', content_str, flags=re.DOTALL)
                        
                    # Check for reasoning content in additional_kwargs (alternative format)
                    if hasattr(chunk, 'message') and hasattr(chunk.message, 'additional_kwargs'):
                        reasoning = chunk.message.additional_kwargs.get('reasoning')
                        if reasoning:
                            ops = [{"op": "add", "path": "/reasoning_content/-", "value": reasoning}]
                            yield f"data: {json.dumps({'ops': ops})}\n\n"

                    # Check for complete tool calls - need both opening and closing sentinels
                    # and proper structure with name and arguments
                    if ("<TOOL_SENTINEL>" in current_response and 
                        "</TOOL_SENTINEL>" in current_response and
                        "<name>" in current_response and 
                        "</name>" in current_response and
                        "<arguments>" in current_response and
                        "</arguments>" in current_response):
                        tool_call_detected = True
                        logger.debug(f"🔍 STREAM: Complete tool call detected, stopping stream")
                        break
                    
                    # If we've just executed tools, the model should now be generating the response
                    if tool_execution_completed:
                        # Reset flag and allow normal streaming
                        tool_execution_completed = False
                        
                        # If we've detected a tool call, buffer everything instead of streaming
                        if tool_call_detected:
                            buffered_content += content_str
                        
                        # STATEFUL TOOL CALL DETECTION - Track across chunks
                        # Check if we're entering a tool call
                        if TOOL_SENTINEL_OPEN in content_str:
                            inside_tool_call = True
                            tool_call_buffer = ""
                            # Stream any content before the tool call
                            before_tool = content_str[:content_str.find(TOOL_SENTINEL_OPEN)]
                            if before_tool:
                                text_msg = {
                                    'type': 'text',
                                    'content': before_tool
                                }
                                yield f"data: {json.dumps(text_msg)}\n\n"
                                import asyncio
                                await asyncio.sleep(0.01)  # Longer delay to prevent batching
                            
                            # Send tool_start message
                            tool_start_msg = {
                                'type': 'tool_start',
                                'message': 'Tool execution starting...'
                            }
                            yield f"data: {json.dumps(tool_start_msg)}\n\n"
                            logger.debug("🔍 STREAM: Sent tool_start message to frontend")
                            await asyncio.sleep(0.01)  # Delay after tool_start
                            
                            tool_call_detected = True  # Set flag to suppress all further output
                            buffered_content = ""  # Start buffering from tool call
                            logger.debug("🔍 STREAM: Entering tool call - suppressing all output")
                        
                        # If we're inside a tool call, buffer the content instead of streaming
                        if inside_tool_call:
                            tool_call_buffer += content_str
                            logger.debug(f"🔍 STREAM: Buffering tool call content: {content_str[:50]}...")
                            # Check if we're exiting the tool call
                            if TOOL_SENTINEL_CLOSE in content_str:
                                inside_tool_call = False
                                logger.debug(f"🔍 STREAM: Exiting tool call - buffered {len(tool_call_buffer)} chars")
                                pending_tool_execution = True  # Mark that we need to execute tools
                                # Don't stream anything after tool call closes
                            # Skip streaming this chunk - it's part of a tool call
                            continue
                        
                    else:
                        # Extract content properly from LangChain chunks
                        if hasattr(chunk, 'content'):
                            content_str = chunk.content
                        elif hasattr(chunk, 'text'):
                            content_str = chunk.text
                        else:
                            content_str = ""
                    if not content_str: 
                        continue

                    # Check if this content is actually an error response that should be handled specially
                    if content_str.strip().startswith('{"error":') and '"validation_error"' in content_str:
                        logger.debug("🔍 AGENT: Detected validation error in model response, converting to proper error handling")
                        try:
                            error_data = json.loads(content_str.strip().replace('[DONE]', ''))
                            # Don't stream this as content, instead raise an exception to be handled by middleware
                            from app.utils.custom_exceptions import ValidationError
                            raise ValidationError(error_data.get('detail', 'Validation error occurred'))
                        except (json.JSONDecodeError, ValueError):
                            logger.warning("Failed to parse error JSON, treating as regular content")
                            # Fall through to normal processing
                    
                    # If tool has been detected, don't stream anything
                    if tool_call_detected:
                        # Keep accumulating in current_response for tool detection
                        # but don't stream anything to the user
                        if content_str:
                            buffered_content += content_str
                        logger.debug(f"🔍 STREAM: Buffering post-tool content: {content_str[:50]}...")
                        continue  # Skip all streaming after tool detection
 
                    # Check if we should suppress this content from streaming
                    # LAYER 1: Stateful detection above should catch most tool calls
                    # LAYER 2: Pattern-based suppression below as fallback for edge cases
                    # Check if we should suppress this content from streaming
                    # LAYER 1: Stateful detection above should catch most tool calls
                    # LAYER 2: Pattern-based suppression below as fallback for edge cases
                    # First check if we have tool calls in the response but haven't executed them yet
                    has_pending_tools = (TOOL_SENTINEL_OPEN in current_response and 
                                       TOOL_SENTINEL_CLOSE in current_response and 
                                       not tool_executed)
                    
                    # Check if we're currently inside a diff code block
                    # Count backticks to determine if we're in a code block
                    backtick_count = current_response.count('```')
                    in_code_block = (backtick_count % 2) == 1
                    
                    # Check if the current code block is a diff block
                    is_in_diff_block = in_code_block and '```diff' in current_response
                    
                    # Ultra-aggressive tool suppression - catch any fragment that could be part of a tool call
                    should_suppress = (
                        not is_in_diff_block and (
                        inside_tool_call or
                        TOOL_SENTINEL_OPEN in current_response or  # If we've seen the start of a tool call anywhere
                        '<TOOL' in content_str or  # Catch partial tool sentinels
                        'TOOL_' in content_str or  # Catch fragments like "_modules.\n\n<TOOL_"
                        '</TOOL' in content_str or
                        'SENTINEL' in content_str or
                        '<name>' in content_str or
                        '</name>' in content_str or
                        '<arguments>' in content_str or
                        '</arguments>' in content_str or
                        'mcp_run_shell_command' in content_str or
                        'mcp_get_current_time' in content_str or
                        ('"command"' in content_str and TOOL_SENTINEL_OPEN in current_response) or
                        ('"timeout"' in content_str and TOOL_SENTINEL_OPEN in current_response) or
                        ('find .' in content_str and TOOL_SENTINEL_OPEN in current_response) or
                        # Catch split fragments
                        content_str.strip().endswith('<TOOL') or
                        content_str.strip().endswith('_modules.\n\n<TOOL') or
                        content_str.strip().startswith('_') and TOOL_SENTINEL_OPEN in current_response
                        )
                    )

                    if not should_suppress:
                        text_msg = {
                            'type': 'text',
                            'content': content_str
                        }
                        yield f"data: {json.dumps(text_msg)}\n\n"
                        # Force task scheduling to ensure individual processing
                        import asyncio
                        await asyncio.sleep(0)
                    else:
                        logger.debug(f"🔍 AGENT: Suppressed tool call content from frontend")
                    # Check for tool calls and execute when model has finished generating them
                    if pending_tool_execution or (TOOL_SENTINEL_OPEN in current_response and 
                                                  TOOL_SENTINEL_CLOSE in current_response) and not tool_executed:
                        
                        # Count complete tool calls
                        complete_tool_calls = current_response.count(TOOL_SENTINEL_CLOSE)
                        
                        # Only execute if we haven't already executed these tools
                        if complete_tool_calls > 0 and not tool_executed:
                            logger.debug(f"🔍 STREAM: Executing {complete_tool_calls} tool call(s) inline")
                            tool_executed = True  # Mark as executed to prevent re-execution
                        
                            # Limit tool calls per round if needed
                            # Make this configurable via environment variable
                            max_tools_per_round = int(os.environ.get("ZIYA_MAX_TOOLS_PER_ROUND", "5"))
                            if complete_tool_calls > max_tools_per_round:
                                logger.debug(f"🔍 STREAM: Limiting to {max_tools_per_round} tools per round")
                                # Truncate to first N tool calls

                            # Find the position after the Nth tool call
                            tool_closes = []
                            start_pos = 0
                            for i in range(max_tools_per_round):
                                pos = current_response.find(TOOL_SENTINEL_CLOSE, start_pos)
                                if pos != -1:
                                    tool_closes.append(pos + len(TOOL_SENTINEL_CLOSE))
                                    start_pos = pos + 1

                            if tool_closes:
                                # Truncate current_response to only include first N tool calls
                                truncated_response = current_response[:tool_closes[-1]]
                                # Add a note about continuing exploration
                                truncated_response += "\n\nLet me start with these commands and then continue based on the results."
                                current_response = truncated_response
                                complete_tool_calls = max_tools_per_round
                        
                            # Execute tools immediately
                            try:
                                # Mark that we're handling tool execution here to prevent later duplicate calls
                                tools_handled_inline = True
                                processed_response, tool_results = await execute_tools_and_update_conversation(
                                    current_response, processed_tool_calls, messages
                                )
                                
                                if tool_results:
                                    tool_executed = True
                                    # Reset state for continued streaming
                                    current_response = ""
                                    continue  # Continue streaming for model response
                                
                            except Exception as tool_error:
                                logger.error(f"🔍 STREAM: Tool execution error: {tool_error}")
                                error_msg = f"**Tool Error:** {str(tool_error)}"
                                yield f"data: {json.dumps({'content': error_msg})}\n\n"
                                tool_executed = True
                                tool_call_detected = False
                                pending_tool_execution = False

                            logger.debug(f"🔍 AGENT: Finished streaming loop for iteration {iteration}")
                                
                            # Only execute if tools weren't already handled inline
                            if not locals().get('tools_handled_inline', False):
                                try:
                                    processed_response, tool_results = await execute_tools_and_update_conversation(
                                        current_response, processed_tool_calls, messages
                                    )
                                    if tool_results:
                                        tool_executed = True
                                except Exception as tool_error:
                                    logger.error(f"🔍 STREAM: Tool execution error: {tool_error}")

                logger.debug(f"🔍 AGENT: Finished streaming loop for iteration {iteration}")

                logger.debug(f"🔍 AGENT: Finished streaming loop for iteration {iteration}")

                # Check if we have tool calls to execute after stream ended
                # CRITICAL: Only process the FIRST tool call, discard others
                if TOOL_SENTINEL_OPEN in current_response and TOOL_SENTINEL_CLOSE in current_response:
                    # Extract only the first complete tool call
                    first_close = current_response.find(TOOL_SENTINEL_CLOSE) + len(TOOL_SENTINEL_CLOSE)
                    current_response = current_response[:first_close]
                    logger.debug(f"🔍 STREAM: Truncated to first tool call only, discarding subsequent calls")

                # Check if we have tool calls to execute after stream ended
                if (TOOL_SENTINEL_OPEN in current_response and 
                    TOOL_SENTINEL_CLOSE in current_response and not tool_executed):
                    logger.debug(f"🔍 STREAM: Post-stream check: tool calls detected but not yet executed")
                    
                    try:
                        processed_response, tool_results = await execute_tools_and_update_conversation(
                            current_response, processed_tool_calls, messages
                        )
                        if tool_results:
                            tool_executed = True
                            current_response = ""
                            continue
                    except Exception as tool_exec_error:
                        logger.error(f"🔍 STREAM: Final tool execution error: {tool_exec_error}")

                # If this is the first iteration and no tool was executed,
                logger.debug(f"🔍 AGENT: Iteration {iteration} complete. current_response length: {len(current_response)}, tool_executed: {tool_executed}")

                # Always update full_response with current_response content
                if current_response and not full_response:
                    full_response = current_response
                    logger.debug(f"🔍 AGENT: Updated full_response from current_response: {len(full_response)} chars")

                # Only do ONE iteration unless tools were executed
                if iteration == 1 and not tool_executed:
                    logger.debug("🔍 AGENT: First iteration complete with no tools - STOPPING HERE")
                    break
                
                # If tools were executed, we need iteration 2 for the response
                if iteration == 1 and tool_executed:
                    logger.debug("🔍 AGENT: Tools executed, continuing to iteration 2 for response")
                    # Mark rewind boundary before continuing to next iteration
                    if current_response:
                        lines = current_response.split('\n')
                        rewind_marker = f"<!-- REWIND_MARKER: {len(lines)} -->"
                        rewind_content = f"\n\n{rewind_marker}\n**🔄 Response continues...**\n"
                        # Send as atomic unit with continuation flag
                        yield f"data: {json.dumps({'content': rewind_content, 'continuation_boundary': True})}\n\n"
                        logger.info(f"🔄 ITERATION_REWIND: Marked boundary at line {len(lines)} before iteration continue")
                    
                    continue
                
                # After iteration 2, we're done
                if iteration >= 2:
                    logger.debug(f"🔍 AGENT: Iteration {iteration} complete - STOPPING")
                    break
                
                # If no tool was executed in this iteration, we're done
                if not tool_executed:
                    logger.debug(f"🔍 AGENT: No tool call detected in iteration {iteration}, ending iterations")
                    break
                
                # Continue to next iteration if tools were executed
                logger.debug("🔍 STREAM: Tools executed, continuing to next iteration for more tool calls...")
                
                # OLD SYSTEM DISABLED - Using new stream breaking system instead
                if len(current_response) > 0:
                    logger.debug("🔍 AGENT: Old tool result system disabled - using stream breaking system")
                    # Just update the full_response to keep the processed content
                    full_response = current_response
                else:
                    logger.warning("🔍 AGENT: Tool execution failed or no change")
                    # Tool execution failed or no change - still update full_response
                    if current_response and len(current_response) > len(full_response):
                        full_response = current_response
                        logger.debug(f"🔍 AGENT: Updated full_response after failed tool execution: {len(full_response)} chars")

                    break

            except Exception as e:
                logger.error(f"Error in agent iteration {iteration}: {str(e)}", exc_info=True)
                processed_response = current_response  # Initialize before use

                # Handle timeout errors with retry logic
                error_str = str(e)
                is_timeout_error = ("Read timeout" in error_str or 
                                  "ReadTimeoutError" in error_str or
                                  "timeout" in error_str.lower())
                
                # Check for token-based throttling specifically
                is_token_throttling = ("Too many tokens" in error_str and 
                                     "ThrottlingException" in error_str and
                                     "reached max retries" in error_str)
                
                # Preserve conversation context for throttling errors
                conversation_id = body.get("conversation_id")
                logger.info(f"Throttling error for conversation: {conversation_id}")
                
                # Use two-tier retry: first within stream, then new stream
                if (is_timeout_error or is_token_throttling):
                    # Tier 1: Quick retries within same stream
                    if within_stream_retries < max_within_stream_retries:
                        within_stream_retries += 1
                        wait_time = min(2 ** within_stream_retries, 8)  # 2s, 4s, 8s
                        error_type = "timeout" if is_timeout_error else "token throttling"
                        
                        logger.info(f"🔄 WITHIN-STREAM: {error_type} retry {within_stream_retries}/{max_within_stream_retries} in {wait_time}s")
                        
                        retry_msg = f"\\n🔄 {error_type.title()} detected, retrying in {wait_time}s...\\n"
                        yield f"data: {json.dumps({'content': retry_msg})}\n\n"
                        
                        await asyncio.sleep(wait_time)
                        
                        # Retry same iteration within stream
                        iteration -= 1
                        if iteration < 1:
                            iteration = 1
                        continue
                    
                    # Tier 2: Fresh connection/new stream
                    elif token_throttling_retries < max_token_throttling_retries:
                        token_throttling_retries += 1
                        within_stream_retries = 0  # Reset within-stream counter
                        wait_time = min(10 * (2 ** (token_throttling_retries - 1)), 30)  # 10s, 20s, 30s
                        error_type = "timeout" if is_timeout_error else "token throttling"
                        
                        logger.info(f"🔄 NEW-STREAM: {error_type} retry {token_throttling_retries}/{max_token_throttling_retries} with fresh connection in {wait_time}s")
                        
                        fresh_conn_msg = f"\\n🔄 Starting fresh connection... (attempt {token_throttling_retries}/{max_token_throttling_retries})\\n"
                        yield f"data: {json.dumps({'content': fresh_conn_msg})}\n\n"
                        
                        await asyncio.sleep(wait_time)
                        
                        # Mark rewind boundary before recursive continuation
                        if current_response:
                            lines = current_response.split('\n')
                            rewind_marker = f"<!-- REWIND_MARKER: {len(lines)} -->"
                            content = f'{rewind_marker}\n**🔄 Response continues...**\n'
                            # Send as atomic unit with continuation flag
                            yield f"data: {json.dumps({'content': content, 'continuation_boundary': True})}\n\n"
                            logger.info(f"🔄 RETRY_REWIND: Marked boundary at line {len(lines)} before recursive call")
                        
                        # End current stream and trigger new one via recursive call
                        yield f"data: {json.dumps({'retry_with_fresh_stream': True})}\n\n"
                        
                        # Start completely new stream
                        async for chunk in stream_chunks(body):
                            # For the first chunk of continuation, prepend the rewind marker
                            if current_response and 'data: {' in chunk and '"content":' in chunk:
                                try:
                                    chunk_data = json.loads(chunk.split('data: ')[1].split('\n\n')[0])
                                    if chunk_data.get('content') and not chunk_data['content'].startswith('<!-- REWIND_MARKER:'):
                                        line_count = len(current_response.split('\n'))
                                        chunk_data['content'] = f"<!-- REWIND_MARKER: {line_count} -->" + chunk_data['content']
                                        chunk = f"data: {json.dumps(chunk_data)}\n\n"
                                except:
                                    pass  # If parsing fails, just yield original chunk
                            yield chunk
                        return
                
                # Gracefully close stream with error message
                if is_timeout_error:
                    error_msg = "⚠️ Request timed out. The response may be incomplete."
                elif is_token_throttling:
                    error_msg = "⚠️ Rate limit exceeded. Please try again in a moment."
                else:
                    error_msg = f"⚠️ An error occurred: {str(e)}"
                
                # Send error to client
                error_content = f"\n\n{error_msg}\\n"
                yield f"data: {json.dumps({'content': error_content})}\n\n"
                
                # Send completion signal
                yield f"data: {json.dumps({'done': True})}\n\n"
                
                # Clean up and exit gracefully
                await cleanup_stream(conversation_id)
                return
                
                if is_token_throttling and token_throttling_retries < max_token_throttling_retries:
                    token_throttling_retries += 1
                    logger.info(f"🔄 TOKEN_THROTTLING: Detected token throttling in multi-round session, attempt {token_throttling_retries}/{max_token_throttling_retries}")
                    
                    # Send status update to frontend
                    status_update = {
                        "type": "token_throttling_retry",
                        "message": f"Token limit reached, retrying with fresh connection in 20s (attempt {token_throttling_retries}/{max_token_throttling_retries})",
                        "retry_attempt": token_throttling_retries,
                        "wait_time": 20
                    }
                    final_retry_msg = f"\\n🔄 Retrying with fresh connection... (attempt {token_throttling_retries}/{max_token_throttling_retries})\\n"
                    yield f"data: {json.dumps({'content': final_retry_msg})}\n\n"
                    
                    # Wait 20 seconds and retry with fresh connection
                    await asyncio.sleep(20)
                    
                    # Reset iteration counter to retry the current iteration
                    iteration -= 1
                    if iteration < 1:
                        iteration = 1
                    continue
                
                # Preserve any accumulated response content before handling the error
                if current_response and len(current_response.strip()) > 0:
                    logger.info(f"Preserving {len(current_response)} characters of partial response before error")
                    logger.debug(f"PARTIAL RESPONSE PRESERVED (AGENT ERROR):\n{current_response}")
                    
                    # Send the partial content to the frontend
                    yield f"data: {json.dumps({'content': current_response})}\n\n"
                    
                    # Send warning about partial response
                    warning_msg = f"Server encountered an error after generating {len(current_response)} characters. The partial response has been preserved."
                    yield f"data: {json.dumps({'warning': warning_msg})}\n\n"
                    
                    full_response = current_response  # Ensure it's preserved in full_response
                
                # Handle ValidationError specifically by sending proper SSE error
                from app.utils.custom_exceptions import ValidationError
                if isinstance(e, ValidationError):
                    logger.debug("🔍 AGENT: Handling ValidationError in streaming context, sending SSE error")
                    error_data = {
                        "error": "validation_error",
                        "conversation_id": body.get("conversation_id"),
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
        logger.debug(f"🔍 AGENT: Iteration loop ended after {iteration} iterations")
        
        # Signal that processing is complete
        completion_signal = {"op": "add", "path": "/processing_state", "value": "complete"}
        yield f"data: {json.dumps({'ops': [completion_signal]})}\n\n"
        logger.debug(f"🔍 AGENT: Final iteration < max_iterations: {iteration < max_iterations}")
        
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
        
        # Log the final server response at INFO level
        if full_response.strip():
            logger.info(f"🤖 FINAL SERVER RESPONSE: {full_response[:500]}{'...' if len(full_response) > 500 else ''}")
        
        logger.info("=" * 50)
        
        logger.info("=== END SERVER RESPONSE ===")

        # Send DONE marker and cleanup
        # Initialize data_sent flag
        # Ensure we always send a DONE marker to complete the stream properly
        logger.debug("🔍 AGENT: Sending final DONE marker")
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
        
    except (AttributeError, NameError) as e:
        logger.error(f"Code error in stream_chunks: {str(e)}", exc_info=True)
        # This indicates missing code/methods - provide helpful error to user
        yield f"data: {json.dumps({'error': 'Service configuration issue. Please contact support.', 'error_type': 'configuration', 'technical_details': str(e)})}\n\n"
        if conversation_id:
            await cleanup_stream(conversation_id)
            
    except Exception as e:
        logger.error(f"Unhandled exception in stream_chunks: {str(e)}", exc_info=True)
        # Check if this is a connectivity issue
        if any(indicator in str(e).lower() for indicator in ['i/o timeout', 'dial tcp', 'lookup', 'network', 'connection']):
            yield f"data: {json.dumps({'error': 'Network connectivity issue. Please check your internet connection and try again.', 'error_type': 'connectivity'})}\n\n"
        else:
            yield f"data: {json.dumps({'error': f'An unexpected error occurred: {str(e)[:100]}...', 'error_type': 'unexpected'})}\n\n"
        if conversation_id: # Ensure cleanup if conversation_id was set
            await cleanup_stream(conversation_id)

# Override the stream endpoint with our error handling
# DISABLED: Manual /ziya/stream endpoint conflicts with /api/chat
async def stream_endpoint(request: Request, body: dict = None):
    """Stream the agent's response with centralized error handling."""
    if body is None:
        body = await request.json()
        
    try:
        # Get agent executor from ModelManager
        from app.agents.agent import get_or_create_agent_executor
        agent_executor = get_or_create_agent_executor()
        
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

def invalidate_folder_cache():
    """Invalidate the folder structure cache with debouncing."""
    global _folder_cache, _last_cache_invalidation
    current_time = time.time()
    
    # Debounce: only invalidate if enough time has passed
    if current_time - _last_cache_invalidation < _cache_invalidation_debounce:
        return
    
    _folder_cache['data'] = None
    _folder_cache['timestamp'] = 0
    _last_cache_invalidation = current_time





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
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to run the server on")
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
        
        # Use extended context limit if supported, otherwise use standard token_limit
        base_token_limit = model_config.get("token_limit", 4096)
        if model_config.get("supports_extended_context"):
            base_token_limit = model_config.get("extended_context_limit", base_token_limit)
        
        if "max_input_tokens" not in model_settings:
            model_settings["max_input_tokens"] = base_token_limit
            
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
        logger.debug(f"Sending current model info: model_id={model_alias}, display_model_id={display_model_id}, settings={json.dumps(model_settings)}")
        
        logger.debug("Sending current model configuration:")
        logger.debug(f"  Model ID: {model_id}")
        logger.debug(f"  Display Model ID: {display_model_id}")
        logger.debug(f"  Model Alias: {model_alias}")
        logger.debug(f"  Endpoint: {endpoint}")
        logger.debug(f"  Region: {region}")
        logger.debug(f"  Settings: {model_settings}")

        # Return complete model information
        return {
            'model_id': model_alias,  # Use the alias (like "sonnet3.7") for model selection
            'model_alias': model_alias,  # Explicit alias field
            'actual_model_id': model_id,  # Full model ID object or string
            'display_model_id': display_model_id,  # Region-specific model ID for display
            'endpoint': endpoint,
            'region': region,
            'settings': model_settings,
            'token_limit': model_config.get("extended_context_limit" if model_config.get("supports_extended_context") else "token_limit", 4096)
        }
    except Exception as e:
        logger.error(f"Error getting current model: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get current model: {str(e)}")

def get_model_id():
    """Get the model ID in a simplified format for the frontend."""
    # Always return the model alias (name) rather than the full model ID
    return {'model_id': ModelManager.get_model_alias()}


def get_cached_folder_structure(directory: str, ignored_patterns: List[Tuple[str, str]], max_depth: int) -> Dict[str, Any]:
    """Get folder structure with caching and background scanning."""
    from app.utils.directory_util import get_folder_structure, get_scan_progress
    from app.utils.directory_util import is_scan_healthy, get_basic_folder_structure
    import threading
    
    current_time = time.time()
    cache_age = current_time - _folder_cache['timestamp']

    # Check if scan is already in progress
    scan_status = get_scan_progress()
    is_scanning = scan_status.get("active", False)
    scan_start_time = scan_status.get("start_time", 0)
    
    # Check for stuck or unhealthy scans
    if is_scanning:
        scan_duration = current_time - scan_start_time if scan_start_time > 0 else 0
        if scan_duration > 300:  # 5 minutes timeout
            logger.warning(f"Folder scan has been running for {scan_duration:.1f}s, considering it stuck")
            from app.utils.directory_util import cancel_scan
            cancel_scan()
            is_scanning = False
        elif not is_scan_healthy():
            logger.warning("Folder scan appears unhealthy, cancelling")
            from app.utils.directory_util import cancel_scan  
            cancel_scan()
            is_scanning = False
    # Check if scan is already in progress
    scan_status = get_scan_progress()
    is_scanning = scan_status.get("active", False)
    scan_start_time = scan_status.get("start_time", 0)
    
    # Check for stuck or unhealthy scans
    if is_scanning:
        scan_duration = time.time() - scan_start_time if scan_start_time > 0 else 0
        if scan_duration > 300:  # 5 minutes timeout
            logger.warning(f"Folder scan has been running for {scan_duration:.1f}s, considering it stuck")
            from app.utils.directory_util import cancel_scan
            cancel_scan()
            is_scanning = False
        elif not is_scan_healthy():
            logger.warning("Folder scan appears unhealthy, cancelling")
            from app.utils.directory_util import cancel_scan  
            cancel_scan()
            is_scanning = False
    
    # If scan is active and healthy, return scanning indicator
    if is_scanning:
        logger.info("Scan in progress, returning scanning indicator")
        return {"_scanning": True, "children": {}}
    
    # Return cached results if available (even if old - cache is persistent)
    if _folder_cache['data'] is not None:
        # Add staleness indicator if cache is very old (> 1 hour)
        if cache_age > 3600:
            return {**_folder_cache['data'], "_stale": True}
        logger.info(f"Returning cached folder structure (age: {cache_age:.1f}s)")
        return _folder_cache['data']
    
    # No cache available - start background scan
    global _background_scan_thread
    if _background_scan_thread is None or not _background_scan_thread.is_alive():
        def background_scan():
            scan_start = time.time()
            logger.info(f"Background scan starting for {directory}")
            
            # Update scan progress to indicate start
            from app.utils.directory_util import _scan_progress
            with _scan_progress_lock if '_scan_progress_lock' in globals() else threading.Lock():
                _scan_progress["active"] = True
                _scan_progress["start_time"] = scan_start
                _scan_progress["last_update"] = scan_start
            try:
                from app.utils.directory_util import _scan_progress
                _scan_progress["active"] = True
                _scan_progress["progress"] = {"directories": 0, "files": 0, "elapsed": 0}
                
                logger.debug(f"🔥 Background scan starting for {directory}")
                result = get_folder_structure(directory, ignored_patterns, max_depth)
                _scan_progress["last_update"] = time.time()  # Mark progress update
                logger.debug(f"🔥 Scan complete: {len(result)} entries")
                _folder_cache['data'] = result
                _folder_cache['timestamp'] = time.time()
            except Exception as e:
                logger.error(f"🔥 Scan error: {e}")
            finally:
                _scan_progress["active"] = False
                scan_end = time.time()
                logger.info(f"Background scan completed in {scan_end - scan_start:.1f}s")
        
        # Clean up any stuck previous thread
        if _background_scan_thread and _background_scan_thread.is_alive():
            logger.warning("Abandoning stuck background scan thread")
            # Don't join() stuck threads - just abandon them
            _background_scan_thread = None
        
        _background_scan_thread = threading.Thread(target=background_scan, daemon=True)
        _background_scan_thread.start()
        logger.info("🔥 Started background scan")
        time.sleep(0.1)  # Let thread start and initialize progress
    
    # Return scanning indicator
    return {"_scanning": True, "children": {}}

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

        # Get base token limit, using extended context if supported
        base_token_limit = base_model_config.get("token_limit", 4096)
        if base_model_config.get("supports_extended_context"):
            base_token_limit = base_model_config.get("extended_context_limit", base_token_limit)
            logger.debug(f"Using extended context limit: {base_token_limit}")
        else:
            logger.debug(f"Using standard token limit: {base_token_limit}")

        # Get CURRENT effective token limits
        effective_max_output_tokens = effective_settings.get("max_output_tokens", base_model_config.get("max_output_tokens", 4096))
        # Use max_input_tokens from effective settings, fallback to extended token_limit from base config
        max_input_tokens = effective_settings.get("max_input_tokens", base_token_limit)

        # Add token limits to capabilities
        effective_max_input_tokens = effective_settings.get("max_input_tokens", base_token_limit)
 
        # Get ABSOLUTE maximums from base config for ranges
        absolute_max_output_tokens = base_model_config.get("max_output_tokens", 4096)

        logger.debug(f"absolute_max_output_tokens from base_model_config: {absolute_max_output_tokens}") # DEBUG
        logger.debug(f"effective_max_output_tokens from effective_settings: {effective_max_output_tokens}") # DEBUG

        # Get absolute max input tokens from base config (using extended context if supported)
        absolute_max_input_tokens = base_token_limit
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
        logger.debug(f"Sending model capabilities for {model_alias}: {capabilities}")
        return capabilities
    except Exception as e:
        logger.error(f"Error getting model capabilities: {str(e)}")
        return {"error": str(e)}

class ApplyChangesRequest(BaseModel):
    diff: str
    filePath: str = Field(..., description="Path to the file being modified")
    requestId: Optional[str] = Field(None, description="Unique ID to track this specific diff application")

    model_config = {
        "json_schema_extra": {
            "example": {
                "diff": "diff --git a/file.txt b/file.txt\n...",
                "filePath": "file.txt"
            }
        },
        "str_max_length": 1000000  # Allow larger diffs
    }

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

@app.post('/api/retry-throttled-request')
async def retry_throttled_request(request: Request):
    """Retry a request that was throttled, with fresh retry attempts."""
    try:
        body = await request.json()
        
        if not body.get("conversation_id"):
            return JSONResponse(status_code=400, content={"error": "conversation_id is required"})
            
        logger.info(f"User retry requested for conversation: {body.get('conversation_id')}")
        
        # Forward to the main streaming endpoint with fresh retry attempts
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
        logger.error(f"Error retrying throttled request: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get('/api/debug/mcp-state')
async def debug_mcp_state():
    """Debug endpoint to check MCP connection and tool execution state."""
    try:
        from app.mcp.manager import get_mcp_manager
        from app.mcp.tools import _tool_execution_counter, _consecutive_timeouts, _conversation_tool_states
        
        mcp_manager = get_mcp_manager()
        
        # Check manager state
        manager_state = {
            "is_initialized": mcp_manager.is_initialized,
            "client_count": len(mcp_manager.clients),
            "clients": {}
        }
        
        # Check each client's state
        for server_name, client in mcp_manager.clients.items():
            try:
                # Check process health
                process_healthy = client._is_process_healthy() if hasattr(client, '_is_process_healthy') else True
                
                manager_state["clients"][server_name] = {
                    "is_connected": client.is_connected,
                    "process_healthy": process_healthy,
                    "process_running": client.process and client.process.poll() is None,
                    "tools_count": len(client.tools),
                    "last_successful_call": getattr(client, '_last_successful_call', 0)
                }
            except Exception as e:
                manager_state["clients"][server_name] = {"error": str(e)}
        
        return {
            "manager": manager_state,
            "global_tool_counter": _tool_execution_counter,
            "consecutive_timeouts": _consecutive_timeouts,
            "conversation_states": _conversation_tool_states
        }
    except Exception as e:
        logger.error(f"Error getting MCP debug state: {e}")
        return {"error": str(e)}

@app.post('/api/debug/reset-mcp')
async def reset_mcp_state(request: Request):
    """Reset MCP state to recover from stuck tool execution."""
    try:
        body = await request.json()
        conversation_id = body.get("conversation_id")
        
        from app.mcp.manager import get_mcp_manager
        from app.mcp.tools import _tool_execution_counter, _consecutive_timeouts, _conversation_tool_states, _reset_counter_async
        
        mcp_manager = get_mcp_manager()
        
        # Reset global state
        await _reset_counter_async()
        
        # Reset conversation-specific state if provided
        if conversation_id and conversation_id in _conversation_tool_states:
            del _conversation_tool_states[conversation_id]
            logger.info(f"Reset tool state for conversation: {conversation_id}")
        
        # Force reconnection to all MCP servers
        for server_name, client in mcp_manager.clients.items():
            if not client._is_process_healthy():
                logger.info(f"Reconnecting unhealthy MCP server: {server_name}")
                asyncio.create_task(mcp_manager._ensure_client_healthy(client))
        
        return {"status": "success", "message": "MCP state reset initiated"}
        
    except Exception as e:
        logger.error(f"Error resetting MCP state: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post('/api/restart-stream-with-context')
async def restart_stream_with_context(request: Request):
    """Restart stream with enhanced context including additional files."""
    try:
        body = await request.json()
        conversation_id = body.get('conversation_id')
        added_files = body.get('added_files', [])
        current_files = body.get('current_files', [])
        
        if not conversation_id:
            return JSONResponse(status_code=400, content={"error": "conversation_id is required"})
        
        logger.info(f"🔄 CONTEXT_ENHANCEMENT: Restarting stream for {conversation_id} with {len(added_files)} additional files")
        
        # First, cleanly abort the current stream if it exists
        if conversation_id in active_streams:
            logger.info(f"🔄 CONTEXT_ENHANCEMENT: Aborting existing stream for {conversation_id}")
            await cleanup_stream(conversation_id)
            # Give it a moment to clean up
            await asyncio.sleep(0.1)
        
        # Combine current files with newly added files
        all_files = list(set(current_files + added_files))
        logger.info(f"🔄 CONTEXT_ENHANCEMENT: Using combined files: current={len(current_files)}, added={len(added_files)}, total={len(all_files)}")
        
        # Build enhanced context body
        enhanced_body = {
            'question': "The referenced files have been added to your context.",
            'conversation_id': conversation_id,
            'config': {
                'files': all_files,  # Use all files including newly added ones
                'conversation_id': conversation_id
            },
            '_context_enhancement': True,  # Flag to indicate this is a context enhancement
            '_added_files': added_files
        }
        
        logger.info(f"🔄 CONTEXT_ENHANCEMENT: Starting enhanced stream with {len(added_files)} files")
        
        # Stream the enhanced response
        return StreamingResponse(
            stream_chunks(enhanced_body),
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
        logger.error(f"Error restarting stream with enhanced context: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

