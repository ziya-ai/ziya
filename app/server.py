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

# Lazy import tiktoken - it's optional with fallbacks
try:
    from app.utils.tiktoken_compat import tiktoken
except ImportError:
    tiktoken = None  # Will be handled at usage time

from fastapi import FastAPI, Request, HTTPException, APIRouter, routing, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langserve import add_routes
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from app.agents.agent import model, RetryingChatBedrock, initialize_langserve
from app.agents.agent import get_or_create_agent, get_or_create_agent_executor, create_agent_chain, create_agent_executor
from app.agents.agent import update_conversation_state, update_and_return, parse_output
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
from app.utils.diff_utils.pipeline.reverse_pipeline import apply_reverse_diff_pipeline
from app.utils.custom_exceptions import ThrottlingException, ExpiredTokenException
from app.utils.custom_exceptions import ValidationError
from app.utils.file_utils import read_file_content
from app.middleware import RequestSizeMiddleware, ModelSettingsMiddleware, ErrorHandlingMiddleware, HunkStatusMiddleware, StreamingMiddleware
from app.middleware.project_context import ProjectContextMiddleware
from app.utils.context_enhancer import initialize_ast_if_enabled
from fastapi.websockets import WebSocketState
from app.middleware.continuation import ContinuationMiddleware

# PCAP analysis utilities
from app.utils.pcap_analyzer import analyze_pcap_file, is_pcap_supported
from app.utils.conversation_exporter import export_conversation_for_paste

# Session management API routers
from app.api import projects, contexts, skills, chats, tokens
from app.utils.paths import get_ziya_home
from app.utils.logging_utils import logger as app_logger

active_feedback_connections: dict[str, list[dict]] = {}  # conversation_id → list of connection dicts
from fastapi.websockets import WebSocket, WebSocketDisconnect
 
# Track active WebSocket connections for feedback

# Global security stats tracker
_security_stats = {
    'total_verifications': 0,
    'successful_verifications': 0,
    'failed_verifications': 0,
    'hallucination_attempts': [],
    'last_reset': time.time()
}
_security_stats_lock = threading.Lock()

# Track active WebSocket connections for file tree updates
active_file_tree_connections = set()

def record_verification_result(tool_name: str, is_valid: bool, error_message: str = None):
    """Record a verification result for monitoring."""
    with _security_stats_lock:
        _security_stats['total_verifications'] += 1
        if is_valid:
            _security_stats['successful_verifications'] += 1
        else:
            _security_stats['failed_verifications'] += 1
            _security_stats['hallucination_attempts'].append({
                'tool_name': tool_name,
                'error': error_message,
                'timestamp': time.time()
            })
            # Keep only last 100 attempts
            _security_stats['hallucination_attempts'] = _security_stats['hallucination_attempts'][-100:]

def build_messages_for_streaming(question: str, chat_history: List, files: List, conversation_id: str, use_langchain_format: bool = False) -> List:
    """
    Build messages for streaming using the extended prompt template.
    This centralizes message construction to avoid duplication.
    """
    logger.debug(f"🔍 FUNCTION_START: build_messages_for_streaming called with {len(files)} files")
    
    def format_content_with_images(text_content: str, images: List[dict] = None):
        """
        Format message content with images for Claude API.
        Returns either a string (text-only) or a list of content blocks (with images).
        """
        if not images:
            return text_content
        
        # Multi-modal format: list of content blocks
        content_blocks = []
        
        # Add images first (Claude processes images before text typically)
        for img in images:
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.get('mediaType', 'image/jpeg'),
                    "data": img.get('data')
                }
            })
            logger.debug(f"🖼️ Added image: {img.get('filename', 'unnamed')} ({img.get('mediaType')})")
        
        # Add text content after images
        if text_content and text_content.strip():
            content_blocks.append({
                "type": "text",
                "text": text_content
            })
        
        logger.info(f"🖼️ Formatted content with {len(images)} images and text: {len(text_content)} chars")
        return content_blocks

    # Always use precision prompt system
    from app.utils.precision_prompt_system import precision_system
    from app.agents.prompts_manager import get_model_info_from_config

    model_info = get_model_info_from_config()
    request_path = "/streaming_tools"  # Default for streaming
    
    # Process chat history to format images properly
    processed_chat_history = []
    for msg in chat_history:
        if isinstance(msg, dict):
            content = msg.get('content', '')
            images = msg.get('images')
            formatted_content = format_content_with_images(content, images)
            
            processed_chat_history.append({
                'type': msg.get('type', 'human'),
                'content': formatted_content
            })
        elif isinstance(msg, (list, tuple)):
            # Handle tuple format: [role, content] or [role, content, images_json]
            if len(msg) == 2:
                role, content = msg
                processed_chat_history.append({
                    'type': role,
                    'content': content
                })
            elif len(msg) == 3:
                # 3-element tuple with images: [role, content, images_json]
                role, content, images_json = msg
                
                try:
                    # Parse the images JSON
                    images = json.loads(images_json) if isinstance(images_json, str) else images_json
                    
                    # Format content with images
                    formatted_content = format_content_with_images(content, images)
                    
                    processed_chat_history.append({
                        'type': role,
                        'content': formatted_content
                    })
                    logger.info(f"🖼️ Processed message with {len(images)} images from tuple format")
                except Exception as e:
                    logger.error(f"Error processing images from tuple: {e}")
                    # Fallback: add without images
                    processed_chat_history.append({'type': role, 'content': content})
        else:
            processed_chat_history.append(msg)

    # Use precision system for 100% equivalence
    messages = precision_system.build_messages(
        request_path=request_path,
        model_info=model_info,
        files=files,
        question=question,
        chat_history=processed_chat_history
    )

    logger.debug(f"🎯 PRECISION_SYSTEM: Built {len(messages)} messages with {len(files)} files preserved")
    
    # Log if any messages contain images
    image_message_count = sum(1 for msg in messages if isinstance(msg.get('content'), list))
    if image_message_count > 0:
        logger.info(f"🖼️ MULTI_MODAL: {image_message_count} messages contain images")

    # Convert to LangChain format if needed
    if use_langchain_format:
        from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
        langchain_messages = []
        for msg in messages:
            if isinstance(msg, dict) and "role" in msg:
                content = msg["content"]
                
                # Handle both string and list content (multi-modal)
                if msg["role"] == "system":
                    langchain_messages.append(SystemMessage(content=content))
                elif msg["role"] == "user":
                    langchain_messages.append(HumanMessage(content=content))
                elif msg["role"] == "assistant":
                    langchain_messages.append(AIMessage(content=content))
        return langchain_messages

    return messages


# Dictionary to track active streaming tasks
active_streams = {}
active_streams_lock = threading.Lock()

# Event loop reference for cross-thread async scheduling (set during lifespan startup)
_main_event_loop = None

# Use configuration from config module
# For model configurations, see app/config.py

class SetModelRequest(BaseModel):
    model_config = {"extra": "allow"}
    model_id: str

class PatchRequest(BaseModel):
    model_config = {"extra": "allow"}
    diff: str
    file_path: Optional[str] = None
    
class FolderRequest(BaseModel):
    model_config = {"extra": "allow"}
    directory: str
    max_depth: int = 3
    
class FileRequest(BaseModel):
    model_config = {"extra": "allow"}
    file_path: str
    
class FileContentRequest(BaseModel):
    model_config = {"extra": "allow"}
    file_path: str
    content: str

class PcapAnalyzeRequest(BaseModel):
    model_config = {"extra": "allow"}
    file_path: str
    operation: str = "summary"
    src_ip: Optional[str] = None
    dst_ip: Optional[str] = None
    protocol: Optional[str] = None
    port: Optional[int] = None
    tcp_flags: Optional[str] = None
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    icmp_type: Optional[int] = None
    pattern: Optional[str] = None
    packet_index: Optional[int] = None
    limit: Optional[int] = None

class AddExplicitPathsRequest(BaseModel):
    model_config = {"extra": "allow"}
    paths: List[str]
    add_to_context: bool = False

# Define lifespan context manager before app creation
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events.
    
    CRITICAL: This must return quickly to allow the server to start accepting requests.
    All heavy initialization is deferred to background tasks.
    """
    # Capture the main event loop so background threads (file watcher, etc.)
    # can schedule async coroutines via run_coroutine_threadsafe.
    global _main_event_loop
    _main_event_loop = asyncio.get_running_loop()

    # Initialize Ziya home directory
    ziya_home = get_ziya_home()
    app_logger.info(f"Ziya home directory initialized at {ziya_home}")
    
    # Startup - spawn background tasks for heavy initialization
    
    # MCP initialization - run in background to not block server startup
    if os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
        # Start MCP initialization in background - don't await
        asyncio.create_task(_initialize_mcp_background())
    else:
        logger.info("MCP integration is disabled.")
    
    # Start folder cache warming in background - don't block server startup
    asyncio.create_task(_warm_folder_cache_background())
    
    # Print clear banner that server is ready
    logger.info("=" * 80)
    logger.info("🚀 SERVER READY - Accepting connections now")
    logger.info("=" * 80)
    logger.info("📋 Background tasks running:")
    if os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
        logger.info("   🔧 MCP server initialization")
    logger.info("   📂 Folder structure scanning")
    logger.info("=" * 80)
    
    yield
    
    # Shutdown - cleanup
    # Cancel any ongoing folder scans
    try:
        from app.utils.directory_util import cancel_scan
        cancel_scan()
        logger.debug("Cancelled any ongoing folder scans during shutdown")
    except Exception as e:
        logger.warning(f"Error cancelling folder scan: {e}")
    
    # MCP shutdown
    if os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
        try:
            from app.mcp.manager import get_mcp_manager
            mcp_manager = get_mcp_manager()
            await mcp_manager.shutdown()
            logger.info("MCP manager shutdown completed")
        except Exception as e:
            logger.warning(f"MCP shutdown failed: {str(e)}")


async def _initialize_mcp_background():
    """Initialize MCP in the background without blocking server startup."""
    # Track completion for final banner
    global _mcp_ready, _folder_ready, _background_tasks_lock
    _mcp_ready = False
    
    # Small delay to let the server finish starting
    await asyncio.sleep(0.1)
    
    try:
        logger.info("🔧 Starting background MCP initialization...")
        
        # Initialize signing secret for security
        from app.mcp.signing import get_session_secret
        get_session_secret()  # Generate secret at startup
        
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        await mcp_manager.initialize()
        
        # Log MCP initialization status
        if mcp_manager.is_initialized:
            status = mcp_manager.get_server_status()
            connected_servers = sum(1 for s in status.values() if s["connected"])
            total_tools = sum(s["tools"] for s in status.values())
            logger.info(f"🔧 MCP initialized: {connected_servers} servers connected, {total_tools} tools available")
            
            # Initialize secure MCP tools
            from app.mcp.connection_pool import get_connection_pool as get_secure_pool
            secure_pool = get_secure_pool()
            secure_pool.set_server_configs(mcp_manager.server_configs)
            logger.debug("Initialized secure MCP connection pool")
            
            # Force garbage collection to ensure clean state
            import gc; gc.collect()
            from app.agents.agent import create_agent_chain, create_agent_executor, model
            agent = create_agent_chain(model.get_model())
            agent_executor = create_agent_executor(agent)
            
            _mcp_ready = True
            _check_and_print_completion_banner()
        else:
            logger.warning("MCP initialization failed or no servers configured")
    except Exception as e:
        logger.warning(f"Background MCP initialization failed: {str(e)}")
        _mcp_ready = True  # Mark as complete even on failure
        _check_and_print_completion_banner()


async def _warm_folder_cache_background():
    """Warm the folder cache in the background without blocking server startup.
    
    This allows the server to start accepting requests immediately while
    the folder structure is being scanned.
    """
    global _folder_ready
    _folder_ready = False
    
    # Small delay to let the server finish starting up
    await asyncio.sleep(0.5)
    
    try:
        logger.info("📂 Starting background folder cache warming...")
        
        user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
        if not user_codebase_dir or not os.path.exists(user_codebase_dir):
            logger.warning(f"Cannot warm folder cache: directory does not exist: {user_codebase_dir}")
            return
        
        # Get max depth from environment
        try:
            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
        except ValueError:
            max_depth = 15
        
        # Get ignored patterns
        ignored_patterns = get_ignored_patterns(user_codebase_dir)
        
        # Trigger the cached folder structure - this will start background scanning
        # The function returns immediately with {"_scanning": True} if no cache exists
        result = get_cached_folder_structure(user_codebase_dir, ignored_patterns, max_depth)
        
        if isinstance(result, dict) and result.get("_scanning"):
            logger.debug("📂 Folder cache warming initiated (scanning in background)")
        else:
            logger.debug("📂 Folder cache already available")
            _folder_ready = True
            _check_and_print_completion_banner()
            
    except Exception as e:
        logger.warning(f"Background folder cache warming failed: {e}")
        _folder_ready = True  # Mark as complete even on failure
        _check_and_print_completion_banner()


def _mark_folder_scan_complete():
    """Called by directory_util when folder scan completes."""
    global _folder_ready
    _folder_ready = True
    _check_and_print_completion_banner()


# Global state for tracking background task completion
_mcp_ready = True  # Default to true if MCP is disabled
_folder_ready = False
_background_tasks_lock = threading.Lock()
_completion_banner_shown = False

def _check_and_print_completion_banner():
    """Print completion banner when all background tasks are done."""
    global _mcp_ready, _folder_ready, _completion_banner_shown, _background_tasks_lock
    
    with _background_tasks_lock:
        # Check if we should print the banner
        mcp_enabled = os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes")
        mcp_done = _mcp_ready or not mcp_enabled
        
        if mcp_done and _folder_ready and not _completion_banner_shown:
            _completion_banner_shown = True
            # Print prominent completion banner
            print("\n" + "=" * 80)
            print("✅ INITIALIZATION COMPLETE - All systems ready")
            print("=" * 80 + "\n")


async def broadcast_file_tree_update(event_type: str, rel_path: str, token_count: int = 0):
    """Broadcast file tree updates to all connected clients."""
    if not active_file_tree_connections:
        # Log this so we know if the frontend isn't connected
        logger.warning(f"📢 No active WebSocket connections for file tree updates - {event_type}: {rel_path}")
        return
    
    logger.info(f"📢 Broadcasting {event_type} for {rel_path} to {len(active_file_tree_connections)} client(s)")
    
    message = {
        'type': event_type,  # 'file_added', 'file_modified', 'file_deleted'
        'path': rel_path,
        'token_count': token_count,
        'timestamp': int(time.time() * 1000)
    }
    
    # Send to all connected clients
    disconnected = set()
    for ws in active_file_tree_connections:
        try:
            await ws.send_json(message)
        except Exception as e:
            logger.debug(f"Failed to send to client: {e}")
            disconnected.add(ws)
    
    # Clean up disconnected clients
    for ws in disconnected:
        active_file_tree_connections.discard(ws)
    
    logger.debug(f"📢 Broadcast {event_type} for {rel_path} to {len(active_file_tree_connections)} client(s)")

app = FastAPI(
    title="Ziya API",
    description="API for Ziya, a code assistant powered by LLMs",
    version="0.1.0",
    lifespan=lifespan,
)

# Suppress noisy access logs for high-frequency polling endpoints.
# These are routine background sync requests that clutter the console.
import logging as _logging

class _PollingAccessFilter(_logging.Filter):
    """Filter routine polling GETs from uvicorn access log."""
    _quiet = {'/chats?', '/chat-groups', '/skills', '/contexts', '/api/config'}
    def filter(self, record: _logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(q in msg for q in self._quiet)

_logging.getLogger("uvicorn.access").addFilter(_PollingAccessFilter())

@app.websocket("/ws/feedback/{conversation_id}")
async def feedback_websocket(websocket: WebSocket, conversation_id: str):
    """WebSocket endpoint for real-time streaming feedback."""
    logger.info(f"🔄 FEEDBACK: WebSocket connection attempt for conversation {conversation_id}")
    await websocket.accept()
    logger.info(f"🔄 FEEDBACK: WebSocket connected for conversation {conversation_id}")
    
    # Register this connection
    conn_entry = {
        'websocket': websocket,
        'connected_at': time.time(),
        'feedback_queue': asyncio.Queue()
    }
    if conversation_id not in active_feedback_connections:
        active_feedback_connections[conversation_id] = []
    active_feedback_connections[conversation_id].append(conn_entry)
    
    try:
        while True:
            try:
                # Listen for feedback messages
                data = await websocket.receive_json()
                feedback_type = data.get('type')
                
                if feedback_type == 'tool_feedback':
                    logger.info(f"🔄 FEEDBACK: Received tool feedback for {conversation_id}: {data.get('message', '')}")
                    
                    # Add to feedback queue of ALL connections for this conversation
                    if conversation_id in active_feedback_connections:
                        for conn in active_feedback_connections[conversation_id]:
                            await conn['feedback_queue'].put(data)
                elif feedback_type == 'interrupt':
                    logger.info(f"🔄 FEEDBACK: Received interrupt request for {conversation_id}")
                    if conversation_id in active_feedback_connections:
                        for conn in active_feedback_connections[conversation_id]:
                            await conn['feedback_queue'].put({'type': 'interrupt'})
                
            except WebSocketDisconnect:
                logger.info(f"🔄 FEEDBACK: WebSocket disconnected for {conversation_id}")
                break
    finally:
        # Clean up connection
        if conversation_id in active_feedback_connections:
            active_feedback_connections[conversation_id] = [
                c for c in active_feedback_connections[conversation_id]
                if c['websocket'] is not websocket
            ]
            # Remove the key entirely if no connections remain
            if not active_feedback_connections[conversation_id]:
                del active_feedback_connections[conversation_id]

# Create the FastAPI app
@app.websocket("/ws/file-tree")
async def file_tree_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time file tree update notifications."""
    logger.info("🔄 FILE_TREE: WebSocket connection attempt")
    await websocket.accept()
    logger.info("🔄 FILE_TREE: WebSocket connected")
    
    # Register this connection
    active_file_tree_connections.add(websocket)
    
    try:
        # Send initial connection confirmation
        await websocket.send_json({
            'type': 'connected',
            'message': 'File tree watcher connected'
        })
        
        # Keep connection alive - just listen for pings/close
        while True:
            try:
                # Wait for any message (pings, etc.)
                await websocket.receive_text()
            except WebSocketDisconnect:
                logger.info("🔄 FILE_TREE: WebSocket disconnected")
                break
    finally:
        # Clean up connection
        if websocket in active_file_tree_connections:
            active_file_tree_connections.remove(websocket)
            logger.info(f"🔄 FILE_TREE: Connection removed, {len(active_file_tree_connections)} remaining")
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
        system_prompt_addition = body.get('systemPromptAddition', '')
        project_root = body.get('project_root')
        logger.info(f"🔍 CHAT_ENDPOINT: project_root from request = '{project_root}', body keys = {list(body.keys())}")
        
        # Log if we received any messages with images
        messages_with_images = sum(1 for msg in messages if isinstance(msg, (list, tuple)) and len(msg) >= 3)
        if messages_with_images > 0:
            logger.info(f"🖼️ CHAT_ENDPOINT: Received {messages_with_images} messages with images")
        
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
                has_images = False
                
                if isinstance(last_msg, list) and len(last_msg) >= 2:
                    last_content = last_msg[1]
                    # Check if this is a 3-element tuple with images
                    has_images = len(last_msg) >= 3 and last_msg[2]
                elif isinstance(last_msg, dict):
                    last_content = last_msg.get('content', '')
                    has_images = bool(last_msg.get('images'))
                else:
                    last_content = ''
                
                # If the last message content matches the question, exclude it
                # UNLESS it has images - then keep it because question doesn't have image data
                if last_content.strip() == question.strip() and not has_images:
                    messages_to_process = messages[:-1]
                    logger.debug(f"Excluded duplicate last message (no images)")
                elif has_images:
                    logger.info(f"🖼️ Keeping last message despite matching question - has images")
            
            for msg in messages_to_process:
                if isinstance(msg, list) and len(msg) >= 2:
                    # Frontend tuple format: ["human", "content"]
                    # or ["human", "content", json_encoded_images]
                    role, content = msg[0], msg[1]
                    images = None
                    if len(msg) >= 3:
                        try:
                            import json
                            images = json.loads(msg[2])
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(f"Failed to parse images from message: {msg[2][:100] if len(msg[2]) > 100 else msg[2]}")
                    
                    if role in ['human', 'user']:
                        chat_history.append({'type': 'human', 'content': content, 'images': images})
                    elif role in ['assistant', 'ai']:
                        chat_history.append({'type': 'ai', 'content': content, 'images': images})
                elif isinstance(msg, dict):
                    # Already in dict format
                    role = msg.get('role', msg.get('type', 'user'))
                    content = msg.get('content', '')
                    images = msg.get('images')
                    if role and content:
                        if role in ['human', 'user']:
                            chat_history.append({'type': 'human', 'content': content, 'images': images})
                        elif role in ['assistant', 'ai']:
                            chat_history.append({'type': 'ai', 'content': content, 'images': images})
            
            logger.info(f"🔍 CHAT_ENDPOINT: Built chat_history with {len(chat_history)} entries")

            # Format the data for stream_chunks - LangChain expects files at top level
            formatted_body = {
                'question': question,
                'conversation_id': conversation_id,
                'chat_history': chat_history,
                'files': files,  # LangChain expects files at top level
                'config': {
                    'conversation_id': conversation_id,
                    'files': files,
                    'systemPromptAddition': system_prompt_addition,
                    'project_root': project_root  # Pass project root for MCP tools
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

# Project context middleware — outermost so all handlers see the correct project root.
# Reads X-Project-Root header and sets request-scoped ContextVar.
app.add_middleware(ProjectContextMiddleware)

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

# Import and include export routes
from app.routes.export_routes import router as export_router
app.include_router(export_router)

# Include session management API routes
app.include_router(projects.router)
app.include_router(contexts.router)
app.include_router(skills.router)
app.include_router(chats.router)
app.include_router(tokens.router)
app_logger.info("Session management API routes loaded")

# Import and include model routes
# Disabled duplicate routers - server.py already defines all these routes
# The route modules were attempting to forward to @app decorated functions which doesn't work
# from app.routes.model_routes import router as model_router
# app.include_router(model_router)

# from app.routes.folder_routes import router as folder_router
# app.include_router(folder_router)

# from app.routes.token_routes import router as token_router
# app.include_router(token_router)

# from app.routes.diff_routes import router as diff_router
# app.include_router(diff_router)

# from app.routes.static_routes import router as static_router
# app.include_router(static_router)

# Initialize Ziya home directory
@app.on_event("startup")
async def init_ziya_home():
    ziya_home = get_ziya_home()
    app_logger.info(f"Ziya home directory initialized at {ziya_home}")

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
        logger.debug(f"Found templates in app package: {app_templates_dir}")
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
    logger.debug(f"Mounted static files from {static_dir}")

# Global flag to prevent multiple LangServe initializations
_langserve_initialized = False

# SELECTIVELY REMOVE ONLY CONFLICTING LANGSERVE ROUTES
logger.debug("=== REMOVING CONFLICTING LANGSERVE ROUTES ===")
routes_to_remove = []
for route in app.routes:
    if hasattr(route, 'path'):
        # Only remove routes that conflict with our custom streaming endpoints
        if (route.path == '/ziya/stream' and hasattr(route, 'endpoint') and 
            'langserve' in str(type(route.endpoint))):
            routes_to_remove.append(route)
            logger.debug(f"Removing conflicting LangServe route: {route.path}")

for route in routes_to_remove:
    app.routes.remove(route)

logger.debug(f"Removed {len(routes_to_remove)} conflicting LangServe routes")

# Log remaining /ziya routes
logger.debug("=== REMAINING /ziya ROUTES ===")
for route in app.routes:
    if hasattr(route, 'path') and route.path.startswith('/ziya'):
        logger.debug(f"Route: {route.methods if hasattr(route, 'methods') else 'N/A'} {route.path}")
logger.debug("=== END /ziya ROUTES ===")

# DISABLED: LangServe routes bypass custom streaming and extended context handling
# add_routes(app, agent_executor, disabled_endpoints=["playground", "stream_log", "stream", "invoke"], path="/ziya")

# DISABLED: Manual /ziya endpoints conflict with /api/chat
# @app.post("/ziya/stream_log")
# async def stream_log_endpoint(request: Request, body: dict):
async def cleanup_stream(conversation_id: str):
    """Clean up resources when a stream ends or is aborted."""
    with active_streams_lock:
        if conversation_id in active_streams:
            logger.info(f"Cleaning up stream for conversation: {conversation_id}")
            # Remove only this specific stream from active streams
            del active_streams[conversation_id]
            logger.info(f"Stream cleanup complete for conversation: {conversation_id}")
        else:
            logger.debug(f"Stream {conversation_id} already cleaned up")

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
            
            # Import verification function
            from app.mcp.signing import verify_tool_result
            
            if not mcp_manager.is_initialized:
                logger.error("🔍 MCP: Manager not initialized")
                continue
            
            # Execute the tool (remove mcp_ prefix if present for internal lookup)
            internal_tool_name = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
            
            # Inject workspace path from environment if available
            # This allows workspace-scoped MCP servers to route to correct instance
            workspace_path = os.environ.get("ZIYA_USER_CODEBASE_DIR")
            if workspace_path and os.path.isdir(workspace_path):
                # Add as internal parameter for workspace routing
                arguments["_workspace_path"] = workspace_path
                logger.debug(f"Injected workspace path for {internal_tool_name}: {workspace_path}")
            
            result = await mcp_manager.call_tool(internal_tool_name, arguments)
            
            logger.debug(f"🔍 MCP TOOL RESULT: tool_name='{internal_tool_name}', result_type={type(result)}, result={result}")
            logger.info(f"🔧 MCP EXECUTION: {internal_tool_name}({arguments}) -> {str(result)[:300]}{'...' if len(str(result)) > 300 else ''}")
            
            if result is None:
                logger.error(f"🔍 MCP: Tool {internal_tool_name} returned None")
                continue
            
            # SECURITY: Verify the result signature
            is_valid, error_message = verify_tool_result(result, internal_tool_name, arguments)
            if not is_valid:
                logger.error(f"🔐 SECURITY: Tool result verification failed for {internal_tool_name}: {error_message}")
                # CRITICAL: Replace with corrective error that tells model it was rejected
                corrective_error = f"\n\n🚨 **TOOL CALL REJECTED**: Result verification failed.\n\n{error_message}\n\nDO NOT proceed as if this tool executed. Please try again or use a different approach.\n\n"
                modified_response = modified_response.replace(tool_call_block, corrective_error)
                continue
            
            # Signature valid - strip metadata before using
            from app.mcp.signing import strip_signature_metadata
            result = strip_signature_metadata(result)
            
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
            # Helper function to detect markdown block state at position
            def get_markdown_state_at_position(text: str, position: int) -> dict:
                """Analyze markdown structure at a specific position."""
                lines_before = text[:position].split('\n')
                
                # Track code block state using a stack
                code_fences = []
                for i, line in enumerate(lines_before):
                    stripped = line.lstrip()
                    # Match code fences at line start
                    fence_match = re.match(r'^(`{3,}|~{3,})(\w*)', stripped)
                    if fence_match:
                        fence_chars = fence_match.group(1)
                        language = fence_match.group(2) or ''
                        fence_type = fence_chars[0]  # '`' or '~'
                        
                        # Check if this closes an existing fence
                        if code_fences and code_fences[-1]['type'] == fence_type:
                            code_fences.pop()
                        else:
                            # Open a new block
                            code_fences.append({
                                'type': fence_type,
                                'language': language,
                                'line': i
                            })
                
                return {
                    'in_code_block': len(code_fences) > 0,
                    'code_fence_language': code_fences[-1]['language'] if code_fences else None,
                    'code_fence_type': code_fences[-1]['type'] if code_fences else None
                }
            
            # Find the last complete line before continuation point
            lines = current_response[:continuation_point].split('\n')
            complete_lines = lines[:-1]  # All but the potentially partial last line
            partial_last_line = lines[-1] if lines else ""
            
            completed_part = '\n'.join(complete_lines)
            
            # Analyze markdown state at the continuation point
            markdown_state = get_markdown_state_at_position(current_response, len(completed_part))
            
            # Build rewind marker with code block state information
            state_info = ""
            if markdown_state['in_code_block']:
                fence_type = markdown_state['code_fence_type']
                fence_language = markdown_state['code_fence_language'] or ''
                state_info = f"|FENCE:{fence_type}{fence_language}"
                logger.info(f"🔄 CONTEXT: Rewind point is inside code block: {fence_type * 3}{fence_language}")
            
            # Add rewind marker that identifies exactly where to splice
            rewind_marker = f"\n\n<!-- REWIND_MARKER: {len(complete_lines)}{state_info} -->\n**🔄 Response continues...**\n"
            completed_part += rewind_marker
            
            # Prepare continuation state
            continuation_state = {
                "rewind_line_number": len(complete_lines),
                "partial_last_line": partial_last_line,
                "rewind_marker": f"<!-- REWIND_MARKER: {len(complete_lines)}{state_info} -->",
                "markdown_state": markdown_state,
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
    
    # Helper to check if we're inside a code block at a given position
    def is_inside_code_block(text: str, position: int) -> bool:
        """Check if a position is inside a code block."""
        lines_before = text[:position].split('\n')
        code_block_stack = []
        
        for line in lines_before:
            stripped = line.lstrip()
            fence_match = re.match(r'^(`{3,}|~{3,})', stripped)
            if fence_match:
                fence_type = fence_match.group(1)[0]
                # Toggle stack
                if code_block_stack and code_block_stack[-1] == fence_type:
                    code_block_stack.pop()
                else:
                    code_block_stack.append(fence_type)
        
        return len(code_block_stack) > 0
    
    # Helper to find safe paragraph breaks (not inside code blocks)
    def find_safe_breaks(breaks: list[int]) -> list[int]:
        """Filter breaks to only those outside code blocks."""
        safe_breaks = []
        for break_point in breaks:
            if not is_inside_code_block(text, break_point):
                safe_breaks.append(break_point)
        return safe_breaks
    
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
    safe_paragraph_breaks = find_safe_breaks(paragraph_breaks)
    if safe_paragraph_breaks:
        # Find the last paragraph break that's not too close to the end
        for break_point in reversed(safe_paragraph_breaks):
            if break_point < len(text) * 0.8:  # Not in last 20% of text
                logger.debug(f"🔄 CONTINUATION: Found safe paragraph break at {break_point}")
                return break_point
    
    # Look for sentence endings
    sentence_endings = [m.end() for m in re.finditer(r'[.!?]\s+', text)]
    safe_sentence_endings = find_safe_breaks(sentence_endings)
    if safe_sentence_endings:
        for break_point in reversed(safe_sentence_endings):
            if break_point < len(text) * 0.8:
                logger.debug(f"🔄 CONTINUATION: Found safe sentence ending at {break_point}")
                return break_point
    
    # If no safe breaks found, log warning
    if paragraph_breaks or sentence_endings:
        logger.warning(f"🔄 CONTINUATION: No safe breaks found outside code blocks. Total paragraph breaks: {len(paragraph_breaks)}, safe: {len(safe_paragraph_breaks)}")
    
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
    
    # Initialize diff validation hook
    from app.utils.diff_validation_hook import DiffValidationHook
    from app.config.app_config import ENABLE_DIFF_VALIDATION, AUTO_REGENERATE_INVALID_DIFFS, AUTO_ENHANCE_CONTEXT_ON_VALIDATION_FAILURE
    
    files = body.get("config", {}).get("files", [])
    conversation_id = body.get("conversation_id")
    project_root = body.get("config", {}).get("project_root") or body.get("project_root")
    
    # Use request-scoped context (set by ProjectContextMiddleware) if no explicit body param.
    # Fall back to body param for backwards compatibility with older frontends.
    if not project_root:
        from app.context import get_project_root_or_none
        project_root = get_project_root_or_none()

    validation_hook = DiffValidationHook(
        enabled=ENABLE_DIFF_VALIDATION,
        auto_regenerate=AUTO_REGENERATE_INVALID_DIFFS,
        current_context=files
    )
    accumulated_content = ""
    
    # Project root is now handled by ProjectContextMiddleware via X-Project-Root header.
    # Also set the ContextVar from the body param for backwards compatibility.
    if project_root and os.path.isdir(project_root):
        from app.context import set_project_root
        set_project_root(project_root)
        logger.info(f"🔄 PROJECT: Request-scoped project root = {project_root}")

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
        project_root = body.get("config", {}).get("project_root") or body.get("project_root")
        
        logger.debug(f"🔍 DIRECT_STREAMING_DEBUG: question='{question}', chat_history={len(chat_history)}, files={len(files)}")
        
        if question:
            # Check for common connectivity-related errors early
            try:
                # Quick connectivity check before expensive operations
                from app.agents.models import ModelManager
                state = ModelManager.get_state()
                if state.get('last_auth_error') and 'i/o timeout' in str(state.get('last_auth_error')):
                    yield f"data: {json.dumps({'error': 'Network connectivity issue detected. Please check your internet connection and try again.', 'error_type': 'connectivity'})}\n\n"
                    # Clean up stream before returning
                    if conversation_id:
                        await cleanup_stream(conversation_id)
                    return
            except Exception as conn_check_error:
                logger.debug(f"Connectivity pre-check failed: {conn_check_error}")
            
            try:
                from app.streaming_tool_executor import StreamingToolExecutor
                from app.agents.models import ModelManager
                
                chunk_count = 0
                last_diff_start_line = -1
                diff_counter = 0
                
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
                
                # Get MCP tools to pass to executor
                mcp_tools = []
                try:
                    from app.mcp.enhanced_tools import create_secure_mcp_tools
                    mcp_tools = create_secure_mcp_tools()
                except Exception as e:
                    logger.warning(f"Failed to get MCP tools: {e}")
                
                async for chunk in executor.stream_with_tools(messages, tools=mcp_tools, conversation_id=conversation_id, project_root=project_root):
                    chunk_count += 1
                    
                    # Centralized REWIND_MARKER stripping: remove from ALL text content
                    # before any downstream processing (diff-fence, accumulator, yield).
                    # Rewind metadata is carried by chunk['rewind'] / chunk['to_marker'],
                    # not by the HTML comment in content.
                    if chunk.get('type') == 'text' and chunk.get('content') and not chunk.get('rewind'):
                        import re as _re_strip
                        chunk['content'] = _re_strip.sub(r'<!-- REWIND_MARKER: [^>]+ -->\n*', '', chunk['content'])

                    if chunk.get('type') == 'text':
                        content = chunk.get('content', '')

                        # Track if we handled this chunk specially (to skip normal processing)
                        chunk_was_handled = False
                        
                        # If it does, we need to split the content and insert the marker
                        if '```diff' in content or '````diff' in content:
                            # Calculate current line count for rewind marker
                            current_lines = accumulated_content.count('\n')
                            
                            # Only insert marker if this is a NEW diff position
                            if last_diff_start_line != current_lines:
                                last_diff_start_line = current_lines
                                
                                # Split the content at the diff fence
                                fence_pos = content.find('```diff') if '```diff' in content else content.find('````diff')
                                before_fence = content[:fence_pos]
                                fence_and_after = content[fence_pos:]
                                
                                # Yield content before the fence first
                                if before_fence:
                                    yield f"data: {json.dumps({'content': before_fence})}\n\n"
                                    accumulated_content += before_fence
                                last_diff_start_line = current_lines
                            
                                # Yield the fence and remaining content
                                yield f"data: {json.dumps({'content': fence_and_after})}\n\n"
                                accumulated_content += fence_and_after
                                
                                # Skip the normal content yielding below since we handled it
                                continue
                        
                    # Accumulate text content for validation
                    if chunk.get('type') == 'text':
                        accumulated_content += chunk.get('content', '')
                    
                    # Log all chunks for debugging
                    chunk_type = chunk.get('type', 'unknown')
                    logger.debug(f"🔍 CHUNK_RECEIVED: type={chunk_type}, chunk_count={chunk_count}")
                    
                    # Convert to expected format and yield all chunk types
                    if chunk.get('type') == 'text':
                        content = chunk.get('content', '')
                        # Forward rewind chunks with metadata so frontend can splice properly
                        if chunk.get('rewind') and chunk.get('to_marker'):
                            yield f"data: {json.dumps({'type': 'rewind', 'to_marker': chunk['to_marker'], 'content': content})}\n\n"
                        else:
                            # Safety net: strip REWIND_MARKER HTML comments that leaked into content
                            import re as _re
                            cleaned = _re.sub(r'<!-- REWIND_MARKER: [^>]+ -->\n*', '', content)
                            if cleaned != content:
                                logger.warning(f"🔄 REWIND_LEAK: Stripped leaked REWIND_MARKER from content chunk")
                            if cleaned:
                                yield f"data: {json.dumps({'content': cleaned})}\n\n"
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
                        logger.info(f"🔐 ERROR_CHUNK: Received error chunk: {chunk}")
                        yield f"data: {json.dumps({'error': chunk.get('content', 'Unknown error'), 'error_type': chunk.get('error', 'unknown'), 'can_retry': chunk.get('can_retry', False), 'retry_message': chunk.get('retry_message', '')})}\n\n"
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
                
                # After streaming completes, validate any diffs
                if accumulated_content and ENABLE_DIFF_VALIDATION:
                    # Track events to yield after validation
                    validation_events = []
                    
                    def send_sse_event(event_type: str, data: dict):
                        """Collect SSE events during validation to yield later."""
                        validation_events.append({"type": event_type, **data})
                    
                    validation_feedback = validation_hook.validate_and_enhance(
                        content=accumulated_content,
                        model_messages=messages,
                        send_event=send_sse_event
                    )
                    
                    # Yield any events that were collected during validation
                    for event in validation_events:
                        yield f"data: {json.dumps(event)}\n\n"
                    
                    # If validation failed
                    if validation_feedback:
                        # Get validation summary to log specifics
                        summary = validation_hook.get_validation_summary()
                        
                        # Log with clear breakdown
                        logger.error("=" * 80)
                        logger.error("🚨 DIFF VALIDATION FAILED 🚨")
                        logger.error("=" * 80)
                        logger.error(f"Total diffs: {summary['total_validated']}")
                        logger.error(f"  ✅ Passed: {summary['successful']} diffs")
                        
                        if summary['successful_files']:
                            for file in summary['successful_files']:
                                logger.error(f"     • {file}")
                        
                        logger.error(f"  ❌ Failed: {summary['failed']} diff(s)")
                        
                        if summary['failed_details']:
                            for detail in summary['failed_details']:
                                logger.error(f"     • Diff #{detail['diff_number']}: {detail['file_path']}")
                                logger.error(f"       Reason: {detail['reason'][:100]}...")
                        
                        logger.error("")
                        logger.error("Model feedback (targeted to failed diff only):")
                        logger.error(validation_feedback[:500] + "..." if len(validation_feedback) > 500 else validation_feedback)
                        logger.error("=" * 80)
                        
                        # Notify frontend about context enhancement
                        if validation_hook.added_files:
                            yield f"data: {json.dumps({'type': 'context_sync', 'added_files': validation_hook.added_files, 'reason': 'diff_validation'})}\n\n"
                            logger.info(f"📂 Context enhanced with: {validation_hook.added_files}")
                            validation_hook.added_files = []
                        
                        # No rewind - model will naturally acknowledge and continue
                        logger.info(f"📝 Requesting corrected diff for {summary['failed']} file(s)")
                        
                        # Add feedback to messages and restart generation
                        from langchain_core.messages import HumanMessage
                        
                        # Simple feedback - model will acknowledge naturally
                        enhanced_feedback = validation_feedback
                        messages.append(HumanMessage(content=enhanced_feedback))
                        
                        # Send a transition marker so the frontend can show a separator
                        yield f"data: {json.dumps({'type': 'validation_retry', 'content': '\\n\\n---\\n\\n**Correcting failed diff(s):**\\n\\n'})}\\n\\n"
                        
                        # Generate again with the feedback
                        logger.info("🔄 Restarting stream with validation feedback")
                        async for retry_chunk in executor.stream_with_tools(messages, tools=mcp_tools, conversation_id=conversation_id, project_root=project_root):
                            if retry_chunk.get('type') == 'text':
                                yield f"data: {json.dumps({'content': retry_chunk.get('content', '')})}\n\n"
                            elif retry_chunk.get('type') == 'tool_start':
                                yield f"data: {json.dumps({'tool_start': retry_chunk})}\n\n"
                            elif retry_chunk.get('type') == 'tool_execution':
                                yield f"data: {json.dumps({'tool_execution': retry_chunk})}\n\n"
                            elif retry_chunk.get('type') == 'tool_display':
                                yield f"data: {json.dumps({'tool_result': retry_chunk})}\n\n"
                            elif retry_chunk.get('type') == 'throttling_error':
                                yield f"data: {json.dumps(retry_chunk)}\n\n"
                            elif retry_chunk.get('type') == 'stream_end':
                                break
                    else:
                        # Only log success banner if diffs were actually validated
                        summary = validation_hook.get_validation_summary()
                        if summary['total_validated'] > 0:
                            logger.info("=" * 80)
                            logger.info("✅ POST-STREAM DIFF VALIDATION PASSED ✅")
                            logger.info("=" * 80)
                            logger.info(f"All {summary['total_validated']} diff(s) validated successfully")
                            logger.info("=" * 80)
                
                # Always send done message at the end
                # Log complete response at INFO level before sending done marker
                if accumulated_content and accumulated_content.strip():
                    logger.info("=" * 80)
                    logger.info(f"🤖 COMPLETE MODEL RESPONSE ({len(accumulated_content)} characters):")
                    logger.info(accumulated_content)
                    logger.info("=" * 80)
                
                yield f"data: {json.dumps({'done': True})}\n\n"
                
                # Clean up stream before returning
                await cleanup_stream(conversation_id)
                
                logger.debug(f"🚀 DIRECT_STREAMING: Completed streaming with {chunk_count} chunks")
                return
                
            except ValueError as ve:
                # Expected error for non-Bedrock endpoints - fall through to LangChain silently
                logger.debug(f"🚀 DIRECT_STREAMING: {ve} - falling back to LangChain")
            except Exception as e:
                import traceback
                error_str = str(e)
                error_details = traceback.format_exc()
                logger.error(f"🚀 DIRECT_STREAMING: Error in StreamingToolExecutor: {e}")
                logger.error(f"🚀 DIRECT_STREAMING: Full traceback:\n{error_details}")
                
                # Check for auth/credential errors
                from app.plugins import get_active_auth_provider
                auth_provider = get_active_auth_provider()
                from app.utils.custom_exceptions import KnownCredentialException
                is_auth_error = (
                    isinstance(e, KnownCredentialException) or
                    (auth_provider and auth_provider.is_auth_error(error_str))
                )
                
                if is_auth_error:
                    error_message = auth_provider.get_credential_help_message() if auth_provider else "AWS credentials have expired."
                    yield f"data: {json.dumps({'error': error_message, 'error_type': 'authentication_error', 'can_retry': True})}\n\n"
                    # Clean up stream before returning
                    if conversation_id:
                        await cleanup_stream(conversation_id)
                    return
                
                # Check for connectivity errors
                if any(indicator in error_str.lower() for indicator in ['i/o timeout', 'dial tcp', 'lookup', 'network', 'connection']):
                    yield f"data: {json.dumps({'error': 'Network connectivity issue. Please check your internet connection and try again.', 'error_type': 'connectivity'})}\n\n"
                    return
                
                # Generic error - always send to frontend
                yield f"data: {json.dumps({'error': f'Error: {str(e)[:200]}', 'error_type': type(e).__name__})}\n\n"
                # Clean up stream before returning
                if conversation_id:
                    await cleanup_stream(conversation_id)
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
            # Check if it's nested in config
            logger.debug("🔍 STREAM: No conversation_id in body, checking config...")
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
        with active_streams_lock:
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
            from app.plugins import get_active_auth_provider
            auth_provider = get_active_auth_provider()
            if auth_provider and auth_provider.is_auth_error(error_str):
                # Preserve conversation context in error response
                conversation_id = body.get("conversation_id")
                if conversation_id:
                    logger.info(f"Adding conversation_id to credential error: {conversation_id}")
                else:
                    logger.warning("No conversation_id available for credential error")
                logger.error(f"Credential error during model binding: {e}")
                credential_error = {
                    "error": "auth_error",
                    "detail": auth_provider.get_credential_help_message(),
                    "error_type": "authentication_error",
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
            with active_streams_lock:
                stream_interrupted = conversation_id not in active_streams
            if stream_interrupted:
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
                    # CRITICAL: Only use LangChain path for non-Bedrock endpoints
                    endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                    if endpoint == "bedrock":
                        logger.error("🚨 ARCHITECTURE BUG: LangChain path reached for Bedrock model - this should never happen")
                        return
                    
                    # Log the actual messages being sent to model on first chunk only
                    if chunk_count == 0:
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

                # CRITICAL FIX: Only do ONE iteration unless tools were executed
                if iteration == 1 and not tool_executed:
                    logger.debug("🔍 AGENT: First iteration complete with no tools - STOPPING HERE")
                    break
                
                # If tools were executed, we need iteration 2 for the response
                if iteration == 1 and tool_executed:
                    logger.debug("🔍 AGENT: Tools executed, continuing to iteration 2 for response")
                    # Continue to next iteration without rewind marker
                    # Rewind markers should only be used for context overflow, not tool execution
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
            logger.info("=" * 80)
            logger.info(f"🤖 COMPLETE MODEL RESPONSE ({len(full_response)} characters):")
            logger.info(full_response)
            logger.info("=" * 80)
        
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
        
    except (ClientError, ThrottlingException, ExpiredTokenException) as e:
        logger.error(f"AWS error in stream_chunks: {str(e)}", exc_info=True)
        error_type = 'aws_error'
        if isinstance(e, ThrottlingException):
            error_type = 'throttling_error'
        elif isinstance(e, ExpiredTokenException):
            error_type = 'authentication_error'
        
        yield f"data: {json.dumps({'error': str(e), 'error_type': error_type, 'can_retry': True})}\n\n"
        if conversation_id:
            await cleanup_stream(conversation_id)
    
    except (AttributeError, NameError) as e:
        logger.error(f"Code error in stream_chunks: {str(e)}", exc_info=True)
        yield f"data: {json.dumps({'error': 'Service configuration issue. Please contact support.', 'error_type': 'configuration', 'technical_details': str(e)})}\n\n"
        if conversation_id:
            await cleanup_stream(conversation_id)
    
    except asyncio.CancelledError:
        logger.info(f"Stream cancelled for conversation: {conversation_id}")
        if conversation_id:
            await cleanup_stream(conversation_id)
        raise  # Re-raise to propagate cancellation
            
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

@app.get("/")
async def root(request: Request):
    try:
        # Get formatter scripts from plugins
        formatter_scripts = []
        from app.plugins import get_active_config_providers
        for provider in get_active_config_providers():
            config = provider.get_defaults()
            if 'frontend' in config and 'formatters' in config['frontend']:
                formatter_scripts.extend(config['frontend']['formatters'])
        
        # Log detailed information about templates
        logger.info(f"Rendering index.html using custom template loader")
        
        # Create the context for the template
        context = {
            "request": request,
            "diff_view_type": os.environ.get("ZIYA_DIFF_VIEW_TYPE", "unified"),
            "api_path": "/ziya",
            "formatter_scripts": formatter_scripts or []  # Ensure always a list, never None
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

@app.get("/info")
async def info_page(request: Request):
    """Render the info page as part of the React app."""
    try:
        # Check if this is a request for the telemetry dashboard
        
        # Get formatter scripts from plugins
        formatter_scripts = []
        from app.plugins import get_active_config_providers
        for provider in get_active_config_providers():
            config = provider.get_defaults()
            if 'frontend' in config and 'formatters' in config['frontend']:
                formatter_scripts.extend(config['frontend']['formatters'])
        
        context = {"request": request, "formatter_scripts": formatter_scripts, "info_page": True}
        return templates.TemplateResponse("index.html", context)
    except Exception as e:
        logger.error(f"Error rendering info page: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/debug2")
async def debug_page_old(request: Request):
    """Legacy route - renders full HTML info page."""
    try:
        import platform
        import sys
        from app.utils.version_util import get_current_version, get_build_info
        
        # Get all the system information
        edition = "Community Edition"
        try:
            from app.plugins import _auth_providers, _config_providers, _registry_providers, get_active_auth_provider, _initialized
            if _initialized:
                for provider in _config_providers:
                    if hasattr(provider, 'get_defaults'):
                        config = provider.get_defaults()
                        if 'branding' in config and 'edition' in config['branding']:
                            edition = config['branding']['edition']
                            break
        except Exception as e:
            logger.warning(f"Could not get edition info: {e}")
        
        # Build the HTML content
        html_parts = [
            '<!DOCTYPE html>',
            '<html>',
            '<head>',
            '    <title>Ziya System Information</title>',
            '    <meta charset="UTF-8">',
            '    <meta name="viewport" content="width=device-width, initial-scale=1.0">',
            '            <style>',
            '        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; line-height: 1.6; }',
            '        body { overflow: auto !important; position: static !important; height: auto !important; }',
            '        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }',
            '        h1 { color: #333; border-bottom: 3px solid #4a90e2; padding-bottom: 10px; margin-top: 0; }',
            '        h2 { color: #4a90e2; margin-top: 30px; border-bottom: 2px solid #e0e0e0; padding-bottom: 8px; }',
            '        .info-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin: 20px 0; }',
            '        .info-card { background: #f9f9f9; padding: 15px; border-radius: 5px; border-left: 4px solid #4a90e2; }',
            '        .info-card h3 { margin-top: 0; color: #333; font-size: 16px; }',
            '        .info-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #e0e0e0; }',
            '        .info-row:last-child { border-bottom: none; }',
            '        .info-label { font-weight: 600; color: #666; }',
            '        .info-value { color: #333; text-align: right; word-break: break-all; max-width: 60%; }',
            '        .status-badge { display: inline-block; padding: 3px 8px; border-radius: 3px; font-size: 12px; font-weight: 600; }',
            '        .status-valid { background: #d4edda; color: #155724; }',
            '        .status-error { background: #f8d7da; color: #721c24; }',
            '        .status-warning { background: #fff3cd; color: #856404; }',
            '        .plugin-list { list-style: none; padding: 0; }',
            '        .plugin-item { padding: 8px; margin: 5px 0; background: white; border-radius: 3px; display: flex; justify-content: space-between; align-items: center; }',
            '        .plugin-active { border-left: 3px solid #28a745; }',
            '        .env-vars { font-family: "Courier New", monospace; font-size: 14px; background: #2d2d2d; color: #f8f8f2; padding: 15px; border-radius: 5px; overflow-x: auto; }',
            '        .env-var { margin: 5px 0; }',
            '        .env-key { color: #66d9ef; }',
            '        .env-value { color: #a6e22e; }',
            '        code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-family: "Courier New", monospace; }',
            '    </style>',
            '</head>',
            '<body>',
            '    <div class="container">',
            f'        <h1>🔧 Ziya System Information</h1>',
            f'        <p><strong>Edition:</strong> {edition} • <strong>Version:</strong> {get_current_version()}</p>',
        ]
        
        # Version Information
        html_parts.extend([
            '        <h2>📦 Version Information</h2>',
            '        <div class="info-grid">',
            '            <div class="info-card">',
            '                <h3>Runtime</h3>',
            f'                <div class="info-row"><span class="info-label">Python Version:</span><span class="info-value">{sys.version.split()[0]}</span></div>',
            f'                <div class="info-row"><span class="info-label">Python Executable:</span><span class="info-value"><code>{sys.executable}</code></span></div>',
            f'                <div class="info-row"><span class="info-label">Platform:</span><span class="info-value">{platform.platform()}</span></div>',
            '            </div>',
        ])
        
        # Directories
        html_parts.extend([
            '            <div class="info-card">',
            '                <h3>Directories</h3>',
            f'                <div class="info-row"><span class="info-label">Root Directory:</span><span class="info-value"><code>{os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())}</code></span></div>',
            f'                <div class="info-row"><span class="info-label">Working Directory:</span><span class="info-value"><code>{os.getcwd()}</code></span></div>',
            '            </div>',
            '        </div>',
        ])
        
        # Client Information
        html_parts.extend([
            '        <h2>💻 Client Information</h2>',
            '        <div class="info-card">',
            f'            <div class="info-row"><span class="info-label">User Agent:</span><span class="info-value">{request.headers.get("user-agent", "Unknown")}</span></div>',
            f'            <div class="info-row"><span class="info-label">Remote Address:</span><span class="info-value">{request.client.host if request.client else "Unknown"}</span></div>',
            '        </div>',
        ])
        
        # Model Configuration
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        
        # Get current model from ModelManager instead of just env var
        try:
            current_model = ModelManager.get_model_alias()
            model_id = ModelManager.get_model_id()
            if isinstance(model_id, dict):
                # Format multi-region model IDs nicely
                model_id_display = ', '.join(f"{k}: {v}" for k, v in model_id.items())
            else:
                model_id_display = str(model_id)
        except Exception as e:
            logger.warning(f"Could not get current model from ModelManager: {e}")
            current_model = os.environ.get("ZIYA_MODEL", "Not set")
            model_id_display = "Unknown"
        
        html_parts.extend([
            '        <h2>🤖 Model Configuration</h2>',
            '        <div class="info-card">',
            f'            <div class="info-row"><span class="info-label">Endpoint:</span><span class="info-value"><strong>{endpoint}</strong></span></div>',
            f'            <div class="info-row"><span class="info-label">Model:</span><span class="info-value"><strong>{current_model}</strong></span></div>',
            f'            <div class="info-row"><span class="info-label">Model ID:</span><span class="info-value"><code>{model_id_display}</code></span></div>',
        ])
        
        # AWS/Google Configuration
        if endpoint == "bedrock":
            import boto3
            profile = os.environ.get('ZIYA_AWS_PROFILE') or os.environ.get('AWS_PROFILE', 'default')
            region = os.environ.get('AWS_REGION', 'us-west-2')
            html_parts.extend([
                f'            <div class="info-row"><span class="info-label">AWS Profile:</span><span class="info-value">{profile}</span></div>',
                f'            <div class="info-row"><span class="info-label">AWS Region:</span><span class="info-value">{region}</span></div>',
            ])
            
            try:
                session = boto3.Session(profile_name=profile, region_name=region)
                credentials = session.get_credentials()
                if credentials:
                    try:
                        sts = session.client('sts', region_name=region)
                        identity = sts.get_caller_identity()
                        html_parts.append(f'            <div class="info-row"><span class="info-label">AWS Account:</span><span class="info-value">{identity["Account"]}</span></div>')
                        html_parts.append(f'            <div class="info-row"><span class="info-label">Credentials:</span><span class="info-value"><span class="status-badge status-valid">✓ Valid</span></span></div>')
                    except Exception as e:
                        error_msg = str(e)
                        if 'ExpiredToken' in error_msg:
                            html_parts.append(f'            <div class="info-row"><span class="info-label">Credentials:</span><span class="info-value"><span class="status-badge status-error">✗ Expired</span></span></div>')
                        else:
                            html_parts.append(f'            <div class="info-row"><span class="info-label">Credentials:</span><span class="info-value"><span class="status-badge status-error">✗ Error</span></span></div>')
                else:
                    html_parts.append(f'            <div class="info-row"><span class="info-label">Credentials:</span><span class="info-value"><span class="status-badge status-error">✗ Not found</span></span></div>')
            except Exception:
                html_parts.append(f'            <div class="info-row"><span class="info-label">Credentials:</span><span class="info-value"><span class="status-badge status-error">✗ Error</span></span></div>')
        elif endpoint == "google":
            api_key = os.environ.get('GOOGLE_API_KEY')
            status = '✓ Set' if api_key else '✗ Not set'
            badge_class = 'status-valid' if api_key else 'status-error'
            html_parts.append(f'            <div class="info-row"><span class="info-label">API Key:</span><span class="info-value"><span class="status-badge {badge_class}">{status}</span></span></div>')
        
        html_parts.append('        </div>')
        
        # MCP Information
        mcp_enabled = os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes")
        html_parts.extend([
            '        <h2>🔧 MCP Servers and Tools</h2>',
            '        <div class="info-card">',
            f'            <div class="info-row"><span class="info-label">MCP Enabled:</span><span class="info-value">{"✓ Yes" if mcp_enabled else "✗ No"}</span></div>',
        ])
        
        if mcp_enabled:
            try:
                from app.mcp.manager import get_mcp_manager
                mcp_manager = get_mcp_manager()
                
                if mcp_manager.is_initialized:
                    status = mcp_manager.get_server_status()
                    connected_servers = sum(1 for s in status.values() if s["connected"])
                    total_tools = sum(s["tools"] for s in status.values())
                    
                    html_parts.extend([
                        f'            <div class="info-row"><span class="info-label">Initialized:</span><span class="info-value"><span class="status-badge status-valid">✓ Yes</span></span></div>',
                        f'            <div class="info-row"><span class="info-label">Connected Servers:</span><span class="info-value"><strong>{connected_servers}</strong> / {len(status)}</span></div>',
                        f'            <div class="info-row"><span class="info-label">Total Tools:</span><span class="info-value"><strong>{total_tools}</strong></span></div>',
                    ])
                    
                    # List each server with its tools
                    html_parts.append('        </div>')
                    for server_name, server_info in status.items():
                        is_connected = server_info["connected"]
                        tool_count = server_info["tools"]
                        status_class = 'status-valid' if is_connected else 'status-error'
                        status_text = '✓ Connected' if is_connected else '✗ Disconnected'
                        
                        html_parts.extend([
                            '        <div class="info-card">',
                            f'            <h3>{server_name}</h3>',
                            f'            <div class="info-row"><span class="info-label">Status:</span><span class="info-value"><span class="status-badge {status_class}">{status_text}</span></span></div>',
                            f'            <div class="info-row"><span class="info-label">Tools:</span><span class="info-value">{tool_count}</span></div>',
                            '        </div>',
                        ])
                else:
                    html_parts.append(f'            <div class="info-row"><span class="info-label">Status:</span><span class="info-value"><span class="status-badge status-warning">Not initialized</span></span></div>')
                    html_parts.append('        </div>')
            except Exception as e:
                html_parts.append(f'            <div class="info-row"><span class="info-label">Status:</span><span class="info-value"><span class="status-badge status-error">Error: {str(e)}</span></span></div>')
                html_parts.append('        </div>')
        else:
            html_parts.append(f'            <div class="info-row"><span class="info-label">Status:</span><span class="info-value"><span class="status-badge status-warning">Disabled</span></span></div>')
            html_parts.append('        </div>')
        
        # Feature Flags
        ast_enabled = os.environ.get("ZIYA_ENABLE_AST", "false").lower() in ("true", "1", "yes")
        mcp_enabled = os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes")
        ephemeral = os.environ.get("ZIYA_EPHEMERAL_MODE", "false").lower() in ("true", "1", "yes")
        
        html_parts.extend([
            '        <h2>⚙️ Feature Flags</h2>',
            '        <div class="info-card">',
            f'            <div class="info-row"><span class="info-label">AST Analysis:</span><span class="info-value">{"✓ Enabled" if ast_enabled else "✗ Disabled"}</span></div>',
            f'            <div class="info-row"><span class="info-label">MCP Tools:</span><span class="info-value">{"✓ Enabled" if mcp_enabled else "✗ Disabled"}</span></div>',
            f'            <div class="info-row"><span class="info-label">Ephemeral Mode:</span><span class="info-value">{"✓ Enabled" if ephemeral else "✗ Disabled"}</span></div>',
            '        </div>',
        ])
        
        # Plugins
        html_parts.extend([
            '        <h2>🔌 Plugins</h2>',
        ])
        
        try:
            from app.plugins import _auth_providers, _config_providers, _registry_providers, get_active_auth_provider, _initialized
            
            if _initialized:
                active_auth = get_active_auth_provider()
                
                # Auth Providers
                html_parts.extend([
                    '        <div class="info-card">',
                    f'            <h3>Authentication Providers ({len(_auth_providers)})</h3>',
                    '            <ul class="plugin-list">',
                ])
                for p in _auth_providers:
                    provider_id = getattr(p, 'provider_id', 'unknown')
                    is_active = p == active_auth
                    active_class = ' plugin-active' if is_active else ''
                    active_badge = '<span class="status-badge status-valid">Active</span>' if is_active else ''
                    html_parts.append(f'                <li class="plugin-item{active_class}">{provider_id} {active_badge}</li>')
                html_parts.extend([
                    '            </ul>',
                    '        </div>',
                ])
                
                # Config Providers
                html_parts.extend([
                    '        <div class="info-card">',
                    f'            <h3>Configuration Providers ({len(_config_providers)})</h3>',
                    '            <ul class="plugin-list">',
                ])
                for p in _config_providers:
                    provider_id = getattr(p, 'provider_id', 'unknown')
                    html_parts.append(f'                <li class="plugin-item">{provider_id}</li>')
                html_parts.extend([
                    '            </ul>',
                    '        </div>',
                ])
                
                # Registry Providers
                html_parts.extend([
                    '        <div class="info-card">',
                    f'            <h3>Registry Providers ({len(_registry_providers)})</h3>',
                    '            <ul class="plugin-list">',
                ])
                for p in _registry_providers:
                    provider_id = getattr(p, 'identifier', 'unknown')
                    html_parts.append(f'                <li class="plugin-item">{provider_id}</li>')
                html_parts.extend([
                    '            </ul>',
                    '        </div>',
                ])
                
                # Formatter Providers (populated by JavaScript)
                html_parts.extend([
                    '        <div class="info-card">',
                    '            <h3>Formatter Providers <span id="formatter-count" style="opacity: 0.7;"></span></h3>',
                    '            <ul class="plugin-list" id="formatter-list">',
                    '                <li style="opacity: 0.6;">Loading...</li>',
                    '            </ul>',
                    '        </div>',
                ])
        except Exception as e:
            logger.warning(f"Could not get plugin info: {e}")
            info['plugins']['error'] = str(e)
        # Environment Variables
        ziya_vars = {k: v for k, v in os.environ.items() if k.startswith('ZIYA_')}
        html_parts.extend([
            '        <h2>🌍 Environment Variables</h2>',
            '        <div class="env-vars">',
        ])
        for key, value in sorted(ziya_vars.items()):
            # Mask sensitive values
            if 'KEY' in key or 'SECRET' in key or 'TOKEN' in key:
                display_value = value[:8] + '...' if len(value) > 8 else '***'
            else:
                display_value = value
            html_parts.append(f'            <div class="env-var"><span class="env-key">{key}</span>=<span class="env-value">{display_value}</span></div>')
        
        html_parts.extend([
            '        </div>',
            '    <script>',
            '        // Populate formatter info from frontend registry',
            '        window.addEventListener("load", function() {',
            '            setTimeout(function() {',
            '                if (window.FormatterRegistry) {',
            '                    const formatters = window.FormatterRegistry.getAllFormatters();',
            '                    const countSpan = document.getElementById("formatter-count");',
            '                    const listEl = document.getElementById("formatter-list");',
            '                    ',
            '                    if (countSpan) countSpan.textContent = "(" + formatters.length + ")";',
            '                    ',
            '                    if (listEl) {',
            '                        listEl.innerHTML = "";',
            '                        formatters.forEach(function(f) {',
            '                            var li = document.createElement("li");',
            '                            li.className = "plugin-item";',
            '                            li.innerHTML = f.formatterId + " <span style=\\"opacity: 0.7; font-size: 11px;\\">(priority: " + f.priority + ")</span>";',
            '                            listEl.appendChild(li);',
            '                        });',
            '                        if (formatters.length === 0) {',
            '                            listEl.innerHTML = "<li style=\\"opacity: 0.6;\\">No formatters registered</li>";',
            '                        }',
            '                    }',
            '                } else {',
            '                    document.getElementById("formatter-list").innerHTML = "<li style=\\"opacity: 0.6; color: #ff4d4f;\\">FormatterRegistry not available</li>";',
            '                }',
            '            }, 100);',
            '        });',
            '    </script>',
            '    </div>',
            '</body>',
            '</html>',
        ])
        
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content='\n'.join(html_parts))
        
    except Exception as e:
        logger.error(f"Error rendering info page: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/debug1")

async def debug(request: Request):
    # Return the same app but with a query parameter to show debug mode
    return templates.TemplateResponse("index.html", {
        "request": request,
        "debug_mode": True
    })

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
_folder_cache: dict[str, dict] = {}  # keyed by absolute directory path
_cache_lock = threading.Lock()
_background_scan_thread = None
_last_cache_invalidation = 0
_cache_invalidation_debounce = 2.0  # seconds


def invalidate_folder_cache():
    """Invalidate the folder structure cache with debouncing."""
    global _folder_cache, _last_cache_invalidation, _cache_lock
    current_time = time.time()
    
    # Debounce: only invalidate if enough time has passed
    if current_time - _last_cache_invalidation < _cache_invalidation_debounce:
        return
    
    with _cache_lock:
        for dir_key in list(_folder_cache.keys()):
            entry = _folder_cache[dir_key]
            external_paths = None
            if entry.get('data') and '[external]' in entry['data']:
                external_paths = entry['data']['[external]']
            entry['data'] = {'[external]': external_paths} if external_paths else None
            entry['timestamp'] = 0
        logger.debug(f"📂 Cache invalidated for {len(_folder_cache)} project(s)")
    _last_cache_invalidation = current_time

def add_file_to_folder_cache(rel_path: str) -> bool:
    """
    Add a newly created file to the folder cache without full rescan.
    
    Args:
        rel_path: Relative path from user_codebase_dir
        
    Returns:
        True if successfully added, False otherwise
    """
    global _folder_cache, _cache_lock
    
    from app.context import get_project_root
    project_root = get_project_root()
    entry = _folder_cache.get(project_root)
    if not entry or entry.get('data') is None:
        return False
    
    try:
        user_codebase_dir = project_root
        full_path = os.path.join(user_codebase_dir, rel_path)
        
        # Calculate token count for new file
        from app.utils.directory_util import estimate_tokens_fast
        token_count = estimate_tokens_fast(full_path)
        
        # Navigate to correct position in cache structure
        path_parts = rel_path.split(os.sep)
        
        with _cache_lock:
            current_level = entry['data']
            
            # Navigate/create parent directories
            for part in path_parts[:-1]:
                if part not in current_level:
                    current_level[part] = {'children': {}, 'token_count': 0}
                current_level = current_level[part].get('children', {})
            
            # Add the file
            filename = path_parts[-1]
            current_level[filename] = {'token_count': token_count}
            
            logger.info(f"✅ Added file to cache: {rel_path} ({token_count} tokens)")
            
            # Notify all connected clients - handle case where no event loop is running
            _schedule_broadcast('file_added', rel_path, token_count)
            return True
            
    except Exception as e:
        logger.error(f"Failed to add file to cache: {rel_path}, error: {e}")
        return False

def _schedule_broadcast(event_type: str, rel_path: str, token_count: int = 0):
    """Schedule a broadcast, handling the case where no event loop is running."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast_file_tree_update(event_type, rel_path, token_count))
    except RuntimeError:
        # Called from a background thread (file watcher, threadpool worker).
        # Use the main event loop captured at startup.
        if _main_event_loop is not None and _main_event_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                broadcast_file_tree_update(event_type, rel_path, token_count),
                _main_event_loop
            )
        else:
            logger.debug(f"Skipping broadcast for {event_type}: {rel_path} (no main event loop)")

def update_file_in_folder_cache(rel_path: str) -> bool:
    """Update token count for modified file in cache."""
    global _folder_cache, _cache_lock
    
    from app.context import get_project_root
    project_root = get_project_root()
    entry = _folder_cache.get(project_root)
    if not entry or entry.get('data') is None:
        return False
    
    try:
        user_codebase_dir = project_root
        full_path = os.path.join(user_codebase_dir, rel_path)
        
        from app.utils.directory_util import estimate_tokens_fast
        token_count = estimate_tokens_fast(full_path)
        
        path_parts = rel_path.split(os.sep)
        
        with _cache_lock:
            current_level = entry['data']
            
            # Navigate to file location
            for part in path_parts[:-1]:
                if part not in current_level:
                    return False  # Path doesn't exist in cache
                current_level = current_level[part].get('children', {})
            
            filename = path_parts[-1]
            if filename in current_level:
                current_level[filename]['token_count'] = token_count
                logger.debug(f"✅ Updated file in cache: {rel_path} ({token_count} tokens)")
                
                _schedule_broadcast('file_modified', rel_path, token_count)
                return True
    except Exception as e:
        logger.error(f"Failed to update file in cache: {rel_path}, error: {e}")
    
    return False

def remove_file_from_folder_cache(rel_path: str) -> bool:
    """Remove deleted file from cache."""
    global _folder_cache, _cache_lock
    
    from app.context import get_project_root
    project_root = get_project_root()
    entry = _folder_cache.get(project_root)
    if not entry or entry.get('data') is None:
        return False
    
    try:
        path_parts = rel_path.split(os.sep)
        
        with _cache_lock:
            current_level = entry['data']
            
            # Navigate to parent directory
            for part in path_parts[:-1]:
                if part not in current_level:
                    return False
                current_level = current_level[part].get('children', {})
            
            filename = path_parts[-1]
            if filename in current_level:
                del current_level[filename]
                logger.info(f"✅ Removed file from cache: {rel_path}")
                
                _schedule_broadcast('file_deleted', rel_path, 0)
                return True
    except Exception as e:
        logger.error(f"Failed to remove file from cache: {rel_path}, error: {e}")
    
    return False




@app.post("/folder")
async def get_folder(request: FolderRequest):
    """Get the folder structure of a directory with improved error handling."""
    # Add timeout configuration
    timeout = int(os.environ.get("ZIYA_SCAN_TIMEOUT", "45"))
    logger.info(f"Starting folder scan with {timeout}s timeout for: {request.directory}")
    logger.info(f"Max depth: {request.max_depth}")
    
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
            
            # For timeout errors, provide more helpful response
            if result.get("timeout"):
                result["suggestion"] = f"Scan timed out after {timeout}s. Try:\n" + \
                                     "1. Increase timeout with ZIYA_SCAN_TIMEOUT environment variable\n" + \
                                     "2. Reduce max depth\n" + \
                                     "3. Add more patterns to .gitignore to exclude large directories"
                result["timeout_seconds"] = timeout
            # Add helpful context for home directory scans
            if "home" in request.directory.lower() or request.directory.endswith(os.path.expanduser("~")):
                result["suggestion"] = "Home directory scans can be very slow. Consider using a specific project directory instead."
            return result
            
        logger.info(f"Folder scan completed successfully in {time.time() - start_time:.2f}s")
        
        # Add metadata about the scan
        if isinstance(result, dict):
            result["_scan_time"] = time.time() - start_time
            result["_timeout_used"] = timeout
            
        return result
    except Exception as e:
        logger.error(f"Error in get_folder: {e}")
        return {"error": str(e)}

# Import scan progress from directory_util
# from app.utils.directory_util import get_scan_progress, cancel_scan, _scan_progress

@app.get("/folder-progress")
async def get_folder_progress():
    """Get current folder scanning progress."""
    from app.utils.directory_util import get_scan_progress
    progress = get_scan_progress()
    
    # Only return active=True if there's actual progress to report
    if progress["active"] and not progress["progress"]:
        # No actual progress data, don't report as active
        progress["active"] = False
        progress["progress"] = {}
    
    # Add percentage if we have estimated total
    if progress.get("estimated_total", 0) > 0 and progress.get("progress", {}).get("directories", 0) > 0:
        progress["progress"]["percentage"] = min(100, int(
            (progress["progress"]["directories"] / progress["estimated_total"]) * 100
        ))
    
    return progress

@app.post("/api/cancel-scan")
async def cancel_folder_scan():
    """Cancel current folder scanning operation."""
    from app.utils.directory_util import cancel_scan
    was_active = cancel_scan()
    if was_active:
        logger.info("Folder scan cancellation requested")
    return {"cancelled": was_active}

@app.post("/api/dynamic-tools/update")
async def update_dynamic_tools(request: Request):
    """
    Update dynamically loaded tools based on file selection.
    Called by frontend when file selection changes.
    """
    try:
        body = await request.json()
        files = body.get('files', [])

        logger.info(f"Dynamic tools update requested for {len(files)} files")

        from app.mcp.dynamic_tools import get_dynamic_loader
        from app.mcp.manager import get_mcp_manager

        # Get the dynamic loader
        loader = get_dynamic_loader()

        # Load appropriate tools based on files
        newly_loaded = loader.load_tools_for_files(files)

        # Invalidate MCP manager's tools cache so new tools appear
        mcp_manager = get_mcp_manager()
        if mcp_manager.is_initialized:
            mcp_manager.invalidate_tools_cache()

        # Get currently active dynamic tools
        active_tools = loader.get_active_tools()

        return JSONResponse({
            "success": True,
            "newly_loaded": list(newly_loaded.keys()),
            "active_tools": list(active_tools.keys()),
            "message": f"Loaded {len(newly_loaded)} new tools, {len(active_tools)} total active"
        })

    except Exception as e:
        logger.error(f"Error updating dynamic tools: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/tools/pcap/analyze")
async def analyze_pcap(request: PcapAnalyzeRequest):
    """
    Analyze a pcap file with various operations
    
    Operations:
    - summary: Get overall statistics
    - conversations: Extract TCP/UDP conversations
    - dns_queries: Extract DNS queries
    - dns_responses: Extract DNS responses
    - filter: Filter packets by IP/protocol/port
    - search: Search for pattern in payloads
    - tcp_health: Analyze TCP health metrics (retransmissions, resets, errors)
    - flow_stats: Get detailed flow-level statistics with timing
    - connectivity_map: Get connectivity map for visualization
    - flow_health: Combined flow statistics with health analysis
    - search_advanced: Advanced filtering with TCP flags, size, etc.
    - http: Extract HTTP requests
    - packet_details: Get details for specific packet
    - tunneling: Get tunneling protocol information
    - ipv6_extensions: Get IPv6 extension header details
    - tls: Get TLS/SSL connection information
    - icmp: Get ICMP/ICMPv6 packet information
    """
    if not is_pcap_supported():
        return JSONResponse(
            status_code=501,
            content={
                "error": "pcap_not_supported",
                "message": "Scapy is not installed. Install with: pip install scapy"
            }
        )
    
    try:
        result = analyze_pcap_file(
            file_path=request.file_path,
            operation=request.operation,
            src_ip=request.src_ip,
            dst_ip=request.dst_ip,
            protocol=request.protocol,
            port=request.port,
            pattern=request.pattern,
            packet_index=request.packet_index,
            limit=request.limit
        )
        
        # Check if result contains an error
        if isinstance(result, dict) and "error" in result:
            status_code = 400
            if result["error"] == "file_not_found":
                status_code = 404
            elif result["error"] == "import_error":
                status_code = 501
            
            return JSONResponse(
                status_code=status_code,
                content=result
            )
        
        return result
        
    except Exception as e:
        logger.error(f"Error in pcap analysis endpoint: {e}")
        return JSONResponse(
            status_code=500,
            content={"error": "analysis_failed", "message": str(e)}
        )

@app.get("/api/tools/pcap/status")
async def pcap_status():
    """Check if pcap analysis is available"""
    return {
        "available": is_pcap_supported(),
        "message": "Scapy is installed and ready" if is_pcap_supported() else "Scapy is not installed. Install with: pip install scapy"
    }

@app.post("/api/clear-folder-cache")
async def clear_folder_cache():
    """Clear the folder structure cache."""
    global _folder_cache
    _folder_cache = {}
    logger.info("Folder cache cleared")
    return {"cleared": True}

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
    
    # Try to import setproctitle for persistent process naming
    try:
        from setproctitle import setproctitle
        has_setproctitle = True
    except ImportError:
        has_setproctitle = False
        logger.debug("setproctitle not available - process title will use default")
    
    parser = argparse.ArgumentParser(description="Run the Ziya server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to run the server on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to run the server on")
    parser.add_argument("--model", type=str, default=None, help="Model to use")
    parser.add_argument("--profile", type=str, default=None, help="AWS profile to use")
    parser.add_argument("--region", type=str, default=None, help="AWS region to use")
    
    # Set terminal window title for iTerm/xterm
    def set_terminal_title(title):
        """Set the terminal window title for iTerm2 and xterm-compatible terminals."""
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
    # Set process title using setproctitle if available - this persists through library calls
    port = args.port
    if has_setproctitle:
        setproctitle(f"Ziya : {port}")
        logger.info(f"Set process title to: Ziya : {port}")
    
    uvicorn.run(app, host=args.host, port=args.port)

@app.get('/api/default-included-folders')
async def get_default_included_folders():
    """Get the default included folders."""
    return []

@app.get('/api/browse-directory')
async def api_browse_directory(path: str = '~'):
    """
    Browse a directory on the server filesystem.
    Returns list of files and directories for the file browser dialog.
    """
    try:
        # Expand ~ to home directory
        if path.startswith('~'):
            path = os.path.expanduser(path)
        
        # Resolve to absolute path
        path = os.path.abspath(path)
        
        # Security: Validate the path exists and is a directory
        if not os.path.exists(path):
            return JSONResponse(
                status_code=404,
                content={"detail": f"Path does not exist: {path}"}
            )
        
        if not os.path.isdir(path):
            # If it's a file, return the parent directory
            path = os.path.dirname(path)
        
        # List directory contents
        entries = []
        try:
            for entry_name in sorted(os.listdir(path)):
                # Skip hidden files (starting with .)
                if entry_name.startswith('.'):
                    continue
                    
                entry_path = os.path.join(path, entry_name)
                try:
                    is_dir = os.path.isdir(entry_path)
                    size = None
                    if not is_dir:
                        try:
                            size = os.path.getsize(entry_path)
                        except OSError:
                            size = None
                    
                    entries.append({
                        "name": entry_name,
                        "path": entry_path,
                        "is_dir": is_dir,
                        "size": size
                    })
                except (PermissionError, OSError):
                    # Skip entries we can't access
                    continue
                    
        except PermissionError:
            return JSONResponse(
                status_code=403,
                content={"detail": f"Permission denied: {path}"}
            )
        
        # Sort: directories first, then files, both alphabetically
        entries.sort(key=lambda e: (not e['is_dir'], e['name'].lower()))
        
        return {
            "current_path": path,
            "entries": entries
        }
        
    except Exception as e:
        logger.error(f"Error browsing directory {path}: {e}")
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error browsing directory: {str(e)}"}
        )

@app.post('/api/add-explicit-paths')
async def api_add_explicit_paths(request: AddExplicitPathsRequest):
    """
    Add explicit file/directory paths to the folder browser tree.
    Paths outside the workspace root will be shown with their full path prefix.
    
    If add_to_context is True, the paths are also added to the current context selection.
    """
    global _folder_cache, _cache_lock
    
    user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    added_paths = []
    errors = []
    
    for path in request.paths:
        try:
            # Expand ~ and resolve to absolute path
            if path.startswith('~'):
                path = os.path.expanduser(path)
            path = os.path.abspath(path)
            
            # Validate path exists
            if not os.path.exists(path):
                errors.append(f"Path does not exist: {path}")
                continue
            
            # Determine if this is inside or outside the workspace
            is_inside_workspace = path.startswith(user_codebase_dir + os.sep) or path == user_codebase_dir
            
            if is_inside_workspace:
                # For paths inside workspace, use relative path
                rel_path = os.path.relpath(path, user_codebase_dir)
            else:
                # For paths outside workspace, use full path as the key
                rel_path = path
            
            # Add to folder cache
            if os.path.isfile(path):
                success = add_file_to_folder_cache(rel_path) if is_inside_workspace else add_external_path_to_cache(path)
                if success:
                    added_paths.append(path)
            elif os.path.isdir(path):
                success = add_directory_to_folder_cache(rel_path, path, is_inside_workspace)
                if success:
                    added_paths.append(path)
                    
        except Exception as e:
            logger.error(f"Error adding path {path}: {e}")
            errors.append(f"Error adding {path}: {str(e)}")
    
    return {
        "added_count": len(added_paths),
        "added_paths": added_paths,
        "errors": errors if errors else None,
        "add_to_context": request.add_to_context
    }


def add_external_path_to_cache(full_path: str) -> bool:
    """
    Add an external file or directory (outside workspace) to the folder cache.
    External files are stored under a special '[external]' root node.
    """
    global _folder_cache, _cache_lock
    
    # Get the current workspace directory to determine which cache entry to use
    user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
    user_codebase_dir = os.path.abspath(user_codebase_dir)
    
    # Ensure cache entry exists for this workspace
    with _cache_lock:
        if user_codebase_dir not in _folder_cache:
            _folder_cache[user_codebase_dir] = {'timestamp': 0, 'data': None}
        
        # Initialize data dict if None
        if _folder_cache[user_codebase_dir]['data'] is None:
            _folder_cache[user_codebase_dir]['data'] = {}
            _folder_cache[user_codebase_dir]['timestamp'] = time.time()
            logger.info("🔧 Initialized empty folder cache for external path addition")
    
    try:
        from app.utils.directory_util import estimate_tokens_fast
        
        # For directories, scan the contents recursively
        if os.path.isdir(full_path):
            def scan_directory(dir_path: str, max_depth: int = 10, current_depth: int = 0) -> dict:
                if current_depth >= max_depth:
                    return {'children': {}, 'token_count': 0}
                
                result = {'children': {}, 'token_count': 0}
                total_tokens = 0
                
                try:
                    for entry_name in os.listdir(dir_path):
                        if entry_name.startswith('.'):
                            continue
                        
                        entry_path = os.path.join(dir_path, entry_name)
                        
                        try:
                            if os.path.isfile(entry_path):
                                token_count = estimate_tokens_fast(entry_path)
                                result['children'][entry_name] = {'token_count': token_count}
                                total_tokens += token_count
                            elif os.path.isdir(entry_path):
                                sub_dir = scan_directory(entry_path, max_depth, current_depth + 1)
                                result['children'][entry_name] = sub_dir
                                total_tokens += sub_dir.get('token_count', 0)
                        except (PermissionError, OSError):
                            continue
                except (PermissionError, OSError):
                    pass
                
                result['token_count'] = total_tokens
                return result
            
            dir_structure = scan_directory(full_path)
            token_count = dir_structure.get('token_count', 0)
        else:
            # For files, just get token count
            token_count = estimate_tokens_fast(full_path)
            dir_structure = None
        
        with _cache_lock:
            # Ensure [external] root exists
            if '[external]' not in _folder_cache[user_codebase_dir]['data']:
                _folder_cache[user_codebase_dir]['data']['[external]'] = {'children': {}, 'token_count': 0}
            
            # Parse the path and create nested structure
            # e.g., /home/user/file.txt -> [external] / home / user / file.txt
            path_parts = full_path.strip('/').split('/')
            current_level = _folder_cache[user_codebase_dir]['data']['[external]']['children']
            
            # Navigate/create parent directories
            for part in path_parts[:-1]:
                if part not in current_level:
                    current_level[part] = {'children': {}, 'token_count': 0}
                if 'children' not in current_level[part]:
                    current_level[part]['children'] = {}
                current_level = current_level[part]['children']
            
            # Add the file/directory
            filename = path_parts[-1]
            if dir_structure:
                # Directory with scanned contents
                current_level[filename] = dir_structure
            else:
                # File
                current_level[filename] = {'token_count': token_count}
            
            logger.info(f"✅ Added external path to cache: {full_path} ({token_count} tokens)")
            
            # Notify connected clients about the new external path
            _schedule_broadcast('file_added', f"[external]{full_path}", token_count)
            return True
            
    except Exception as e:
        logger.error(f"Failed to add external path to cache: {full_path}, error: {e}")
        return False


def add_directory_to_folder_cache(rel_path: str, full_path: str, is_inside_workspace: bool) -> bool:
    """
    Add a directory and its contents to the folder cache.
    """
    global _folder_cache, _cache_lock
    
    from app.context import get_project_root
    project_root = get_project_root()
    entry = _folder_cache.get(project_root)
    if not entry or entry.get('data') is None:
        return False
    
    try:
        from app.utils.directory_util import estimate_tokens_fast
        
        # For external paths, delegate to external handler
        if not is_inside_workspace:
            return add_external_path_to_cache(full_path)
        
        # Build the directory structure recursively
        def scan_directory(dir_path: str, max_depth: int = 10, current_depth: int = 0) -> dict:
            if current_depth >= max_depth:
                return {'children': {}, 'token_count': 0}
            
            result = {'children': {}, 'token_count': 0}
            total_tokens = 0
            
            try:
                for entry_name in os.listdir(dir_path):
                    if entry_name.startswith('.'):
                        continue
                    
                    entry_path = os.path.join(dir_path, entry_name)
                    
                    try:
                        if os.path.isfile(entry_path):
                            token_count = estimate_tokens_fast(entry_path)
                            result['children'][entry_name] = {'token_count': token_count}
                            total_tokens += token_count
                        elif os.path.isdir(entry_path):
                            sub_dir = scan_directory(entry_path, max_depth, current_depth + 1)
                            result['children'][entry_name] = sub_dir
                            total_tokens += sub_dir.get('token_count', 0)
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                pass
            
            result['token_count'] = total_tokens
            return result
        
        dir_structure = scan_directory(full_path)
        
        with _cache_lock:
            path_parts = rel_path.split(os.sep)
            current_level = entry['data']
            
            # Navigate/create parent directories
            for part in path_parts[:-1]:
                if part not in current_level:
                    current_level[part] = {'children': {}, 'token_count': 0}
                if 'children' not in current_level[part]:
                    current_level[part]['children'] = {}
                current_level = current_level[part]['children']
            
            # Add the directory with its scanned contents
            dirname = path_parts[-1] if path_parts else os.path.basename(full_path)
            # Ensure the directory structure has 'children' key (even if empty) to mark it as a directory
            if 'children' not in dir_structure:
                dir_structure['children'] = {}
            current_level[dirname] = dir_structure
            
            logger.info(f"✅ Added directory to cache: {rel_path} ({dir_structure.get('token_count', 0)} tokens)")
            
            # Notify connected clients
            _schedule_broadcast('file_added', rel_path, dir_structure.get('token_count', 0))
            return True
            
    except Exception as e:
        logger.error(f"Failed to add directory to cache: {rel_path}, error: {e}")
        return False

# Cache for ignored patterns - build once at startup, reuse for all scans
_ignored_patterns_cache = None
_ignored_patterns_cache_time = 0
_ignored_patterns_cache_dir = None
IGNORED_PATTERNS_CACHE_TTL = 300  # 5 minutes

@app.get('/api/folders-cached')
async def get_folders_cached():
    """Get folder structure from cache only - returns instantly without scanning."""
    try:
        # Get the user's codebase directory
        user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
        if not user_codebase_dir:
            user_codebase_dir = os.getcwd()
            
        # Get max depth from environment or use default
        try:
            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
        except ValueError:
            max_depth = 15
            
        # Get ignored patterns
        ignored_patterns = get_ignored_patterns(user_codebase_dir)
        
        # Import here to avoid circular imports
        from app.utils.directory_util import _folder_cache, _token_cache, _cache_lock
        
        # First check if we have any cached data at all
        with _cache_lock:
            cache_key = f"{user_codebase_dir}:{max_depth}:{hash(str(ignored_patterns))}"
            
            # Check for token cache first (most complete)
            if cache_key in _token_cache:
                logger.info("🚀 Returning cached folder structure with tokens (instant)")
                result = _token_cache[cache_key]
                if "_accurate_tokens" in result:
                    result["_accurate_token_counts"] = result["_accurate_tokens"]
                return result
                
            # Fall back to folder cache if available
            # _folder_cache is now a per-directory dict
            dir_entry = _folder_cache.get(user_codebase_dir)
            if dir_entry and dir_entry.get('data') is not None:
                logger.info("🚀 Returning basic folder cache (instant)")
                return dir_entry['data']
                
        # No cache available
        return {"error": "No cached data available"}
    except Exception as e:
        logger.error(f"Error in get_folders_cached: {e}")
        return {"error": f"Cache error: {str(e)}"}

@app.get('/api/folders-with-accurate-tokens')
async def get_folders_with_accurate_tokens():
    """Get folder structure with pre-calculated accurate token counts."""
    try:
        # Get the user's codebase directory
        user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
        if not user_codebase_dir:
            logger.warning("ZIYA_USER_CODEBASE_DIR environment variable not set")
            user_codebase_dir = os.getcwd()
            
        # Get max depth from environment or use default
        try:
            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
        except ValueError:
            logger.warning("Invalid ZIYA_MAX_DEPTH value, using default of 15")
            max_depth = 15
            
        # Get ignored patterns
        ignored_patterns = get_ignored_patterns(user_codebase_dir)
        logger.info(f"Loaded {len(ignored_patterns)} ignore patterns")
        
        # Check if we have cached accurate token counts
        from app.utils.directory_util import get_cached_folder_structure_with_tokens
        result = get_cached_folder_structure_with_tokens(user_codebase_dir, ignored_patterns, max_depth)
        
        if result:
            result["_has_accurate_tokens"] = True
            # Include accurate token counts if available
            if "_accurate_tokens" in result:
                result["_accurate_token_counts"] = result["_accurate_tokens"]
                logger.info(f"Returning folder structure with {len(result['_accurate_tokens'])} accurate token counts")
            return result
            
        # Get regular folder structure
        regular_result = await api_get_folders()
        return regular_result
        # Fall back to regular folder structure and start background calculation
        return await api_get_folders()
    except Exception as e:
        logger.error(f"Error in get_folders_with_accurate_tokens: {e}")
        return {"error": f"Unexpected error: {str(e)}"}
        return JSONResponse({"error": str(e)}, status_code=500)
        files = body.get('files', [])
        conversation_id = body.get('conversation_id')
        logger.info(f"Chat API received conversation_id: {conversation_id}")
        logger.debug(f"🔍 CHAT_API: Received conversation_id from frontend: {conversation_id}")

        # Debug: Log what we received from frontend
        logger.debug(f"🔍 CHAT_API: Received messages count: {len(messages)}")
        logger.debug(f"🔍 CHAT_API: Messages structure: {messages[:2] if messages else 'No messages'}")
        
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
        logger.debug(f"🔍 CHAT_API: Converted chat history count: {len(formatted_chat_history)}")
        
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

        
        # Call stream_chunks directly to force direct streaming path
        logger.info("[INSTRUMENTATION] /api/chat calling stream_chunks directly for direct streaming")
        
        # DEBUGGING: Wrap the stream_chunks generator to monitor transmission
        async def debug_stream_wrapper():
            total_bytes_sent = 0
            chunk_count = 0
            async for chunk in stream_chunks(formatted_body):
                chunk_count += 1
                chunk_size = len(chunk.encode('utf-8'))
                total_bytes_sent += chunk_size
                
                if chunk_count % 50 == 0:  # Log every 50th chunk
                    logger.debug(f"🔍 STREAM_PROGRESS: chunk #{chunk_count}, total_bytes={total_bytes_sent}")
                    
                yield chunk
        
        return StreamingResponse(
            debug_stream_wrapper(),
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

@app.get('/api/config')
def get_config():
    """Get application configuration for frontend."""
    # Base config from environment
    # Cache the merged config to avoid re-reading plugin files on every poll.
    # Invalidated only on model change or explicit refresh.
    if not hasattr(get_config, '_cache'):
        get_config._cache = None

    if get_config._cache is not None:
        return get_config._cache

    config = {
        'theme': os.environ.get('ZIYA_THEME', 'light'),
        'defaultModel': os.environ.get('ZIYA_MODEL'),
        'endpoint': os.environ.get('ZIYA_ENDPOINT', 'bedrock'),
        'port': int(os.environ.get('ZIYA_PORT', DEFAULT_PORT)),
        'mcpEnabled': os.environ.get('ZIYA_ENABLE_MCP', 'true').lower() in ('true', '1', 'yes'),
        'version': os.environ.get('ZIYA_VERSION', 'development'),
        'ephemeralMode': os.environ.get('ZIYA_EPHEMERAL_MODE', 'false').lower() in ('true', '1', 'yes'),
        'projectRoot': os.environ.get('ZIYA_USER_CODEBASE_DIR', os.getcwd()),
    }
    
    # Merge frontend config from active config providers
    try:
        from app.plugins import get_all_config_providers
        for provider in get_all_config_providers():
            logger.debug(f"Checking provider: {provider.provider_id}")
            if hasattr(provider, 'get_defaults'):
                defaults = provider.get_defaults()
                logger.debug(f"Provider {provider.provider_id} defaults keys: {defaults.keys()}")
                if 'frontend' in defaults:
                    logger.debug(f"Found frontend config in {provider.provider_id}: {defaults['frontend']}")
                    config['frontend'] = defaults['frontend']
    except Exception as e:
        logger.warning(f"Error loading frontend config from providers: {e}")
    
    get_config._cache = config
    return config

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
            'token_limit': model_config.get("extended_context_limit" if model_config.get("supports_extended_context") else "token_limit", 4096),
            'ephemeral': os.environ.get("ZIYA_EPHEMERAL_MODE", "false").lower() in ("true", "1", "yes")
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
    """Get folder structure with caching and background scanning."""
    from app.utils.directory_util import get_folder_structure, get_scan_progress
    from app.utils.directory_util import get_basic_folder_structure
    
    # Normalize directory path for consistent cache keys
    directory = os.path.abspath(directory)
    
    # Get or create cache entry for this directory
    if directory not in _folder_cache:
        _folder_cache[directory] = {'timestamp': 0, 'data': None}
    
    cache_entry = _folder_cache[directory]
    current_time = time.time()
    cache_age = current_time - cache_entry['timestamp']

    # Check if scan is already in progress
    scan_status = get_scan_progress()
    is_scanning = scan_status.get("active", False)
    
    # If scan is active and healthy, return scanning indicator immediately (non-blocking)
    if is_scanning:
        logger.debug("Scan in progress, returning scanning indicator (non-blocking)")
        return {"_scanning": True, "children": {}}
    
    # Return cached results immediately if available
    if cache_entry['data'] is not None:
        # Add staleness indicator if cache is very old (> 1 hour)
        if cache_age > 3600:
            logger.debug(f"Returning stale cached folder structure (age: {cache_age:.1f}s)")
            return {**cache_entry['data'], "_stale": True}
        logger.debug(f"Returning cached folder structure (age: {cache_age:.1f}s)")
        return cache_entry['data']
    
    # No cache available - start background scan and return immediately
    global _background_scan_thread
    if _background_scan_thread is None or not _background_scan_thread.is_alive():
        def background_scan():
            scan_start = time.time()
            logger.info(f"📂 Background folder scan starting for {directory}")
            
            # Update scan progress to indicate start
            from app.utils.directory_util import _scan_progress
            _scan_progress["active"] = True
            _scan_progress["start_time"] = scan_start
            _scan_progress["last_update"] = scan_start
            _scan_progress["progress"] = {"directories": 0, "files": 0, "elapsed": 0}
            
            try:
                result = get_folder_structure(directory, ignored_patterns, max_depth)
                _scan_progress["last_update"] = time.time()  # Mark progress update
                cache_entry['data'] = result
                cache_entry['timestamp'] = time.time()
                scan_duration = time.time() - scan_start
                logger.info(f"📂 Background folder scan completed in {scan_duration:.1f}s")
            except Exception as e:
                logger.error(f"📂 Background folder scan error: {e}", exc_info=True)
            finally:
                _scan_progress["active"] = False
        
        # Clean up any stuck previous thread
        if _background_scan_thread and _background_scan_thread.is_alive():
            logger.warning("Abandoning stuck background scan thread")
            _background_scan_thread = None
        
        _background_scan_thread = threading.Thread(target=background_scan, daemon=True)
        _background_scan_thread.start()
        logger.info("📂 Started background folder scan")
    
    # Return scanning indicator immediately (non-blocking)
    return {"_scanning": True, "children": {}}

@app.get('/api/folders')
async def api_get_folders(refresh: bool = False, project_path: str = Query(None)):
    """Get folder structure for API compatibility with improved error handling."""
    
    # DIAGNOSTIC: Log what we're about to return
    def log_folder_contents(data, path="", max_depth=3, current_depth=0):
        if current_depth >= max_depth:
            return
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            if key.startswith('_'):
                continue
            current_path = f"{path}/{key}" if path else key
            if isinstance(value, dict):
                if 'children' in value:
                    logger.info(f"📁 {current_path}/ ({len(value.get('children', {}))} children)")
                    log_folder_contents(value.get('children', {}), current_path, max_depth, current_depth + 1)
                else:
                    token_count = value.get('token_count', 0)
                    logger.info(f"📄 {current_path} ({token_count} tokens)")
    
    # Add cache headers to help frontend avoid unnecessary requests
    if refresh:
        # If refresh requested, invalidate caches BEFORE any processing
        logger.info("🔄 Refresh requested - invalidating caches")
        invalidate_folder_cache()
        
        # Also invalidate the gitignore patterns cache to pick up new files
        import app.utils.directory_util as dir_util
        dir_util._ignored_patterns_cache = None
        dir_util._ignored_patterns_cache_dir = None
        dir_util._ignored_patterns_cache_time = 0
        logger.info("🔄 Invalidated gitignore patterns cache")
    
    from fastapi import Response
    response = Response()
    response.headers["Cache-Control"] = "public, max-age=30"
    
    try:
        # Get the user's codebase directory
        if project_path:
            user_codebase_dir = os.path.abspath(project_path)
            logger.info(f"Using provided project_path: {user_codebase_dir}")
        else:
            user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
        if not user_codebase_dir:
            logger.warning("ZIYA_USER_CODEBASE_DIR environment variable not set")
            user_codebase_dir = os.getcwd()
            
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
            logger.error(f"Permission denied accessing: {user_codebase_dir}")
            return {"error": "Permission denied accessing directory"}
        
        # Get ignored patterns (will use cache if available)
        ignored_patterns = get_ignored_patterns(user_codebase_dir)
        
        # Get max depth from environment or use default
        try:
            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
        except ValueError:
            logger.warning("Invalid ZIYA_MAX_DEPTH value, using default of 15")
            max_depth = 15
            
        # Get ignored patterns
        try:
            ignored_patterns = get_ignored_patterns(user_codebase_dir)
            logger.info(f"Loaded {len(ignored_patterns)} ignore patterns")
        except re.error as e:
            logger.error(f"Invalid gitignore pattern detected: {e}")
            # Use minimal default patterns if gitignore parsing fails
            ignored_patterns = [
                (".git", user_codebase_dir),
                ("node_modules", user_codebase_dir),
                ("__pycache__", user_codebase_dir)
            ]
        
        # Check if a scan is in progress BEFORE we call get_cached_folder_structure
        from app.utils.directory_util import get_scan_progress
        scan_status_before = get_scan_progress()
        
        # Use our enhanced cached folder structure function
        result = get_cached_folder_structure(user_codebase_dir, ignored_patterns, max_depth)
        
        # Log the structure we're returning
        logger.info("=== FOLDER STRUCTURE BEING RETURNED ===")
        log_folder_contents(result, max_depth=2)
        logger.info("=== END FOLDER STRUCTURE ===")
        
        # Background calculation is automatically ensured by get_cached_folder_structure_with_tokens
        # Check if we got an error result
        if isinstance(result, dict) and "error" in result:
            logger.warning(f"Folder scan returned error: {result['error']}")
            
            # If the result is completely empty, try to return at least some basic structure
            if not result.get('children') and not result.get('token_count'):
                logger.warning("Empty result returned, creating minimal folder structure")
                result = {"_error": result['error'], "app": {"token_count": 0, "children": {}}}
                
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
        
        # The _scanning flag is already set by get_cached_folder_structure when appropriate
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

        found_alias = None
        found_endpoint = None
        
        # Search through all endpoints and models to find the matching alias and its endpoint
        for ep, models in ModelManager.MODEL_CONFIGS.items():
            # Direct match by alias
            if model_id in models:
                found_alias = model_id
                found_endpoint = ep
                break
            # Search by model_id value
            for alias, model_config_item in models.items():
                config_model_id = model_config_item.get('model_id')
                
                # Case 1: Both are dictionaries - check if they match
                if isinstance(model_id, dict) and isinstance(config_model_id, dict):
                    # Check if dictionaries have the same structure and values
                    if model_id == config_model_id:
                        found_alias = alias
                        found_endpoint = ep
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
                        found_endpoint = ep
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
            os.environ["ZIYA_ENDPOINT"] = found_endpoint
            logger.info(f"Set ZIYA_ENDPOINT environment variable to: {found_endpoint}")
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
                # Check if this is a Google model with native function calling
                endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                model_name = os.environ.get("ZIYA_MODEL")
                
                # For Google models with native function calling, skip agent creation
                if endpoint == "google" and model_name:
                    model_config = ModelManager.get_model_config(endpoint, model_name)
                    uses_native_calling = model_config.get("native_function_calling", False)
                    
                    if uses_native_calling:
                        logger.info(f"Model {model_name} uses native function calling, skipping XML agent creation")
                        # Store the model directly without wrapping in XML agent
                        agent = None  # No XML agent needed
                        agent_executor = None  # No executor needed
                    else:
                        # Create XML agent for models that need it
                        agent = create_agent_chain(new_model)
                        agent_executor = create_agent_executor(agent)
                else:
                    # For Bedrock and other models, create XML agent normally
                    agent = create_agent_chain(new_model)
                    agent_executor = create_agent_executor(agent)
                
                # Get the updated llm_with_stop from ModelManager
                llm_with_stop = ModelManager._state.get('llm_with_stop')
                logger.info("Created new agent chain and executor")
            except Exception as agent_error:
                logger.error(f"Failed to create agent: {str(agent_error)}", exc_info=True)
                raise agent_error

            # COMPLETELY DISABLED: LangServe routes cause duplicate execution with /api/chat
            # initialize_langserve(app, agent_executor)
            # _langserve_initialized = True
            logger.info("LangServe completely disabled to prevent duplicate execution - using /api/chat only")

            # Invalidate config cache so next /api/config poll picks up new model
            if hasattr(get_config, '_cache'):
                get_config._cache = None

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
            "supports_vision": base_model_config.get("supports_vision", False),
        }
        
        # Add thinking level support for Gemini 3 models
        if base_model_config.get("family") == "gemini-3":
            capabilities["supports_thinking_level"] = True
            capabilities["thinking_level_default"] = base_model_config.get("thinking_level", "high")
            capabilities["thinking_level"] = effective_settings.get("thinking_level", capabilities["thinking_level_default"])

        # Add adaptive thinking support for Claude 4.6+ models
        if base_model_config.get("supports_adaptive_thinking"):
            capabilities["supports_adaptive_thinking"] = True
            capabilities["thinking_effort_default"] = base_model_config.get("thinking_effort_default", "high")
            capabilities["thinking_effort"] = effective_settings.get(
                "thinking_effort",
                os.environ.get("ZIYA_THINKING_EFFORT", capabilities["thinking_effort_default"])
            )
            capabilities["is_advanced_model"] = base_model_config.get("is_advanced_model", False)

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
    projectRoot: Optional[str] = Field(None, description="Root directory for the project (client-specific)")
    elementId: Optional[str] = None
    buttonInstanceId: Optional[str] = None

    model_config = {
        "extra": "allow",
        "json_schema_extra": {
            "example": {
                "diff": "diff --git a/file.txt b/file.txt\n...",
                "filePath": "file.txt"
            }
        },
        "str_max_length": 1000000  # Allow larger diffs
    }

class ModelSettingsRequest(BaseModel):
    model_config = {"extra": "allow"}
    temperature: float = Field(default=0.3, ge=0, le=1)
    top_k: int = Field(default=15, ge=0, le=500)
    max_output_tokens: int = Field(default=4096, ge=1)
    thinking_mode: bool = Field(default=False)
    thinking_level: Optional[str] = Field(default=None, pattern='^(low|medium|high)$')
    thinking_effort: Optional[str] = Field(default=None, pattern='^(low|medium|high|max)$')


class TokenCountRequest(BaseModel):
    model_config = {"extra": "allow"}
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
        # Use estimate_token_count which tries calibrator first, then tiktoken, then fallback
        # This gives us calibrated estimates when available, with graceful degradation
        from app.agents.agent import estimate_token_count
        
        token_count = estimate_token_count(text=request.text)
        
        # Log which method was actually used (calibrator logs this internally)
        method_used = "estimate_token_count"

        logger.info(f"Counted {token_count} tokens using {method_used} method for text length {len(request.text)}")
        return {"token_count": token_count}
    except Exception as e:
        logger.error(f"Error counting tokens: {str(e)}", exc_info=True)
        # Return 0 in case of error to avoid breaking the frontend
        return {"token_count": 0}

class AccurateTokenCountRequest(BaseModel):
    model_config = {"extra": "allow"}
    file_paths: List[str]
    
@app.post('/api/accurate-token-count')
async def get_accurate_token_counts(request: AccurateTokenCountRequest) -> Dict[str, Any]:
    """Get accurate token counts for specific files."""
    try:
        from app.utils.directory_util import get_accurate_token_count

        # Check if we have pre-calculated accurate counts
        from app.utils.directory_util import _accurate_token_cache
        if _accurate_token_cache:
            logger.info(f"API request for accurate tokens: {len(request.file_paths)} files requested")
            results = {}
            for file_path in request.file_paths:
                if file_path in _accurate_token_cache:
                    results[file_path] = {
                        "accurate_count": _accurate_token_cache[file_path],
                        "timestamp": int(time.time())
                    }
            if results:
                cached_count = sum(1 for path in request.file_paths if path in _accurate_token_cache)
                calculated_count = len(results) - cached_count
                logger.info(f"Returning {len(results)} token counts: {cached_count} from cache (accurate), {calculated_count} calculated on-demand")
                return {"results": results, "debug_info": {"source": "precalculated_cache"}}

        import os
        
        user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        logger.info(f"Accurate token count requested for {len(request.file_paths)} files")
        if not user_codebase_dir:
            raise ValueError("ZIYA_USER_CODEBASE_DIR not set")
        
        results = {}
        for file_path in request.file_paths:
            full_path = os.path.join(user_codebase_dir, file_path)
            if os.path.exists(full_path) and os.path.isfile(full_path):
                accurate_count = get_accurate_token_count(full_path)
                # Get the estimated count for comparison
                from app.utils.directory_util import estimate_tokens_fast
                estimated_count = estimate_tokens_fast(full_path)
                logger.debug(f"File: {file_path} - ACCURATE: {accurate_count} vs ESTIMATED: {estimated_count} (diff: {accurate_count - estimated_count})")
                results[file_path] = {
                    "accurate_count": accurate_count,
                    "timestamp": int(time.time())
                }
            else:
                results[file_path] = {"accurate_count": 0, "error": "File not found"}
                
        return {"results": results, "debug_info": {"files_processed": len(results)}}
    except Exception as e:
        logger.error(f"Error getting accurate token counts: {str(e)}")
        return {"error": str(e), "results": {}}

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
    original_settings = settings.model_dump()
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
        for key, value in settings.model_dump().items():
            if value is not None:  # Only set if value is provided
                env_key = f"ZIYA_{key.upper()}"
                logger.info(f"  Set {env_key}={value}")
                
                # Special handling for thinking_level - preserve string value
                if key == 'thinking_level':
                    os.environ[env_key] = value

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

@app.get('/api/info')
async def get_system_info(request: Request):
    """Get comprehensive system information and configuration for debugging."""
    try:
        import platform
        import sys
        from app.utils.version_util import get_current_version
        from app.utils.version_util import get_build_info
        
        info = {}
        
        # Edition from plugins
        edition = "Community Edition"
        try:
            from app.plugins import _auth_providers, _config_providers, _registry_providers, get_active_auth_provider, _initialized
            if _initialized:
                for provider in _config_providers:
                    if hasattr(provider, 'get_defaults'):
                        config = provider.get_defaults()
                        if 'branding' in config and 'edition' in config['branding']:
                            edition = config['branding']['edition']
                            break
        except Exception as e:
            logger.warning(f"Could not get edition info: {e}")
        
        # Version and platform information
        info['version'] = {
            'edition': edition,
            'ziya_version': get_current_version(),
            'build_info': get_build_info(),
            'python_version': sys.version.split()[0],
            'python_executable': sys.executable,
            'platform': platform.platform()
        }
        
        # Root directory information
        info['directories'] = {
            'root': os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd()),
            'templates': os.environ.get("ZIYA_TEMPLATES_DIR", "Not set"),
            'current_working_directory': os.getcwd()
        }
        
        # User agent from request headers
        info['client'] = {
            'user_agent': request.headers.get('user-agent', 'Unknown'),
            'remote_addr': request.client.host if request.client else 'Unknown'
        }
        
        # Plugin information
        info['plugins'] = {}
        try:
            from app.plugins import _auth_providers, _config_providers, _registry_providers, get_active_auth_provider, _initialized
            
            if _initialized:
                # Auth providers
                info['plugins']['auth_providers'] = {
                    'count': len(_auth_providers),
                    'providers': []
                }
                active_auth = get_active_auth_provider()
                for p in _auth_providers:
                    provider_id = getattr(p, 'provider_id', 'unknown')
                    is_active = p == active_auth
                    info['plugins']['auth_providers']['providers'].append({
                        'id': provider_id,
                        'active': is_active
                    })
                
                # Config providers
                info['plugins']['config_providers'] = {
                    'count': len(_config_providers),
                    'providers': [getattr(p, 'provider_id', 'unknown') for p in _config_providers]
                }
                
                # Registry providers
                info['plugins']['registry_providers'] = {
                    'count': len(_registry_providers),
                    'providers': [getattr(p, 'identifier', 'unknown') for p in _registry_providers]
                }
                
                # Check for enterprise formatter files
                import glob
                static_dir = os.path.join(os.path.dirname(__file__), 'templates', 'static', 'js')
                formatter_files = glob.glob(os.path.join(static_dir, '*[Ff]ormatter*.js')) if os.path.exists(static_dir) else []
                info['plugins']['enterprise_formatters'] = {
                    'count': len(formatter_files),
                    'files': [os.path.basename(f) for f in formatter_files]
                }
        except Exception as e:
            logger.warning(f"Could not get plugin info: {e}")
            info['plugins']['error'] = str(e)
        
        # Frontend Formatter Registry (from plugins)
        info['formatters'] = {}
        try:
            from app.plugins import get_formatter_providers
            
            formatter_providers = get_formatter_providers()
            info['formatters'] = {
                'count': len(formatter_providers),
                'providers': [{'id': p.formatter_id, 'priority': p.priority} for p in formatter_providers]
            }
        except Exception as e:
            logger.warning(f"Could not get formatter info: {e}")
            info['formatters']['error'] = str(e)
        
        # Endpoint and model configuration
        info['model'] = {
            'endpoint': os.environ.get("ZIYA_ENDPOINT", "bedrock"),
            'model': os.environ.get("ZIYA_MODEL", "Not set"),
            'model_id_override': os.environ.get("ZIYA_MODEL_ID_OVERRIDE")
        }
        
        # Get current model details
        try:
            model_alias = ModelManager.get_model_alias()
            model_id = ModelManager.get_model_id()
            info['model']['current_alias'] = model_alias
            info['model']['current_id'] = model_id
        except Exception as e:
            logger.warning(f"Could not get current model details: {e}")
        
        # AWS configuration (if using Bedrock)
        if info['model']['endpoint'] == "bedrock":
            import boto3
            info['aws'] = {
                'profile': os.environ.get('ZIYA_AWS_PROFILE') or os.environ.get('AWS_PROFILE', 'default'),
                'region': os.environ.get('AWS_REGION', 'us-west-2')
            }
            
            try:
                session = boto3.Session(
                    profile_name=info['aws']['profile'],
                    region_name=info['aws']['region']
                )
                credentials = session.get_credentials()
                if credentials:
                    try:
                        sts = session.client('sts', region_name=info['aws']['region'])
                        identity = sts.get_caller_identity()
                        info['aws']['account_id'] = identity['Account']
                        info['aws']['access_key'] = credentials.access_key[:8] + '...'
                        info['aws']['status'] = 'Valid'
                    except Exception as sts_error:
                        error_msg = str(sts_error)
                        if 'ExpiredToken' in error_msg:
                            info['aws']['access_key'] = credentials.access_key[:8] + '...'
                            info['aws']['status'] = 'Expired'
                        elif 'InvalidClientTokenId' in error_msg:
                            info['aws']['status'] = 'Invalid credentials'
                        else:
                            info['aws']['status'] = f'Error: {error_msg[:80]}'
                else:
                    info['aws']['status'] = 'No credentials found'
            except Exception as e:
                info['aws']['status'] = f'Error: {str(e)[:80]}'
        
        # Google configuration (if using Google)
        elif info['model']['endpoint'] == "google":
            api_key = os.environ.get('GOOGLE_API_KEY')
            info['google'] = {
                'api_key_set': bool(api_key),
                'api_key_masked': api_key[:8] + '...' if api_key else None
            }
        
        # Feature flags
        info['features'] = {
            'ast_enabled': os.environ.get("ZIYA_ENABLE_AST", "false").lower() in ("true", "1", "yes"),
            'ast_resolution': os.environ.get("ZIYA_AST_RESOLUTION", "medium"),
            'mcp_enabled': os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"),
            'ephemeral_mode': os.environ.get("ZIYA_EPHEMERAL_MODE", "false").lower() in ("true", "1", "yes")
        }
        
        # MCP Registry status
        if info['features']['mcp_enabled']:
            try:
                import subprocess
                result = subprocess.run(['which', 'mcp-registry'], capture_output=True, text=True)
                info['features']['mcp_registry_installed'] = result.returncode == 0
            except Exception:
                info['features']['mcp_registry_installed'] = False
        
        # ZIYA environment variables
        ziya_vars = {k: v for k, v in os.environ.items() if k.startswith('ZIYA_')}
        info['environment_variables'] = {}
        for key, value in sorted(ziya_vars.items()):
            # Mask sensitive values
            if 'KEY' in key or 'SECRET' in key or 'TOKEN' in key:
                info['environment_variables'][key] = value[:8] + '...' if len(value) > 8 else '***'
            else:
                info['environment_variables'][key] = value
        
        return info
        
    except Exception as e:
        logger.error(f"Error getting system info: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

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

@app.get('/api/telemetry/cache-health')
async def get_cache_health_telemetry():
    """Get real-time cache health and efficiency metrics."""
    try:
        from app.streaming_tool_executor import get_global_usage_tracker
        
        tracker = get_global_usage_tracker()
        
        # Get current conversation metrics
        current_conversation = None
        from app.agents.models import ModelManager
        
        # Get all tracked conversations
        all_conversations = tracker.get_all_conversations()
        
        # Calculate global statistics
        global_stats = {
            'total_conversations': len(all_conversations),
            'total_fresh_tokens': 0,
            'total_cached_tokens': 0,
            'total_output_tokens': 0,
            'total_throttle_events': 0,
            'conversations_with_cache_issues': 0
        }
        
        conversation_details = []
        
        for conv_id, usages in all_conversations.items():
            if not usages:
                continue
            
            # Aggregate metrics for this conversation
            conv_metrics = {
                'conversation_id': conv_id,
                'iteration_count': len(usages),
                'fresh_tokens': sum(u.input_tokens for u in usages),
                'cached_tokens': sum(u.cache_read_tokens for u in usages),
                'output_tokens': sum(u.output_tokens for u in usages),
                'cache_created': sum(u.cache_write_tokens for u in usages),
                'throttle_count': sum(1 for u in usages if getattr(u, 'was_throttled', False)),
                'timestamp': max(getattr(u, 'timestamp', 0) for u in usages) if usages else 0
            }
            
            # Calculate efficiency
            total_input = conv_metrics['fresh_tokens'] + conv_metrics['cached_tokens']
            conv_metrics['cache_efficiency'] = (
                (conv_metrics['cached_tokens'] / total_input * 100) if total_input > 0 else 0
            )
            
            # Detect issues
            cache_issue = (
                len(usages) > 1 and  # Multi-iteration conversation
                conv_metrics['cached_tokens'] == 0 and  # No cache reads
                conv_metrics['fresh_tokens'] > 50000  # Significant token usage
            )
            
            conv_metrics['has_cache_issue'] = cache_issue
            
            # Update global stats
            global_stats['total_fresh_tokens'] += conv_metrics['fresh_tokens']
            global_stats['total_cached_tokens'] += conv_metrics['cached_tokens']
            global_stats['total_output_tokens'] += conv_metrics['output_tokens']
            global_stats['total_throttle_events'] += conv_metrics['throttle_count']
            if cache_issue:
                global_stats['conversations_with_cache_issues'] += 1
            
            conversation_details.append(conv_metrics)
        
        # Calculate global cache efficiency
        total_global_input = global_stats['total_fresh_tokens'] + global_stats['total_cached_tokens']
        global_stats['overall_cache_efficiency'] = (
            (global_stats['total_cached_tokens'] / total_global_input * 100) 
            if total_global_input > 0 else 0
        )
        
        # Calculate cost savings
        global_stats['estimated_cost_savings_pct'] = global_stats['overall_cache_efficiency']
        
        # Sort conversations by most recent
        conversation_details.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return {
            'status': 'success',
            'timestamp': int(time.time() * 1000),
            'global_stats': global_stats,
            'conversations': conversation_details[:20],  # Most recent 20
            'health_summary': {
                'cache_working': global_stats['conversations_with_cache_issues'] == 0,
                'issues_detected': global_stats['conversations_with_cache_issues'],
                'throttle_pressure': 'high' if global_stats['total_throttle_events'] > 5 else 
                                    'medium' if global_stats['total_throttle_events'] > 0 else 'low'
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting cache health telemetry: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get('/api/telemetry/current-conversation')
async def get_current_conversation_telemetry(conversation_id: str):
    """Get detailed telemetry for a specific conversation."""
    try:
        from app.streaming_tool_executor import get_global_usage_tracker
        
        tracker = get_global_usage_tracker()
        usages = tracker.get_conversation_usages(conversation_id)
        
        if not usages:
            return {'status': 'no_data', 'conversation_id': conversation_id}
        
        # Build detailed iteration breakdown
        iterations = []
        for i, usage in enumerate(usages):
            iterations.append({
                'iteration': i,
                'fresh_tokens': usage.input_tokens,
                'cached_tokens': usage.cache_read_tokens,
                'output_tokens': usage.output_tokens,
                'cache_efficiency': f"{usage.cache_hit_rate * 100:.1f}%",
                'was_throttled': getattr(usage, 'was_throttled', False),
                'timestamp': getattr(usage, 'timestamp', 0)
            })
        
        # Calculate trends
        cache_trend = []
        for usage in usages:
            if usage.cache_read_tokens + usage.input_tokens > 0:
                cache_trend.append(usage.cache_hit_rate)
        
        return {
            'status': 'success',
            'conversation_id': conversation_id,
            'iterations': iterations,
            'cache_trend': cache_trend,
            'summary': {
                'total_iterations': len(usages),
                'average_cache_efficiency': sum(cache_trend) / len(cache_trend) if cache_trend else 0
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting conversation telemetry: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post('/api/files/validate')
async def validate_files(request: Request):
    """Validate which files from a list actually exist on disk."""
    try:
        body = await request.json()
        files = body.get('files', [])
        project_root = body.get('projectRoot')
        
        # Use provided project root if available, otherwise fall back to env var
        if project_root:
            user_codebase_dir = os.path.abspath(project_root)
            logger.info(f"🔍 VALIDATE: Using provided project root: {user_codebase_dir}")
        else:
            user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        
        if not user_codebase_dir or not os.path.isdir(user_codebase_dir):
            return JSONResponse(status_code=500, content={"error": "ZIYA_USER_CODEBASE_DIR not set"})
        
        existing_files = []
        for file_path in files:
            full_path = os.path.join(user_codebase_dir, file_path)
            if os.path.exists(full_path) and os.path.isfile(full_path):
                existing_files.append(file_path)
        
        return {"existingFiles": existing_files}
    except Exception as e:
        logger.error(f"Error validating files: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post('/api/check-files-in-context')
async def check_files_in_context(request: Request):
    """Check which files from a list are currently available in the selected context."""
    try:
        body = await request.json()
        file_paths = body.get('filePaths', [])
        current_files = body.get('currentFiles', [])
        
        if not file_paths:
            return {"missingFiles": [], "availableFiles": []}
        
        user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        if not user_codebase_dir:
            return JSONResponse(status_code=500, content={"error": "ZIYA_USER_CODEBASE_DIR not set"})
        
        logger.info(f"🔄 CONTEXT_CHECK: Checking {len(file_paths)} files against {len(current_files)} current context files")
        logger.info(f"🔄 CONTEXT_CHECK: Files to check: {file_paths}")
        logger.info(f"🔄 CONTEXT_CHECK: Current context: {current_files[:10]}...")
        
        missing_files = []
        available_files = []
        
        for file_path in file_paths:
            # Clean up the file path (remove a/ or b/ prefixes from git diffs)
            clean_path = file_path.strip()
            if clean_path.startswith('a/') or clean_path.startswith('b/'):
                clean_path = clean_path[2:]
            
            # Check if the file is in the current selected context
            is_in_context = False
            
            # Direct match
            if clean_path in current_files:
                is_in_context = True
            # Check if any selected folder contains this file
            elif any(clean_path.startswith(f + '/') or f.endswith('/') and clean_path.startswith(f) 
                    for f in current_files):
                is_in_context = True
            
            logger.info(f"🔄 CONTEXT_CHECK: File '{clean_path}' in context: {is_in_context}")
            
            if is_in_context:
                available_files.append(clean_path)
            else:
                # File is not in current context - check if it exists on disk (can be added)
                full_path = os.path.join(user_codebase_dir, clean_path)
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    missing_files.append(clean_path)  # Exists but not in context
                else:
                    missing_files.append(clean_path)  # Doesn't exist at all
        
        logger.info(f"🔄 CONTEXT_CHECK: Result - Available: {available_files}, Missing: {missing_files}")
        return {
            "missingFiles": missing_files,
            "availableFiles": available_files
        }
        
    except Exception as e:
        logger.error(f"Error checking files in context: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post('/api/export-conversation')
async def export_conversation(request: Request):
    """Export a conversation in a format suitable for paste services."""
    try:
        body = await request.json()
        conversation_id = body.get('conversation_id')
        format_type = body.get('format', 'markdown')  # 'markdown' or 'html'
        target = body.get('target', 'public')  # 'public' or 'internal'
        captured_diagrams = body.get('captured_diagrams', [])
        
        if not conversation_id:
            return JSONResponse(
                status_code=400, 
                content={"error": "conversation_id is required"}
            )
        
        # Get conversation messages
        # In a real implementation, you'd fetch from the conversation store
        messages = body.get('messages', [])
        
        # Get current model info
        from app.agents.models import ModelManager
        model_alias = ModelManager.get_model_alias()
        endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
        
        # Get version
        from app.utils.version_util import get_current_version
        version = get_current_version()
        
        logger.info(f"Exporting conversation with {len(captured_diagrams)} captured diagrams")
        
        # Export the conversation
        exported = export_conversation_for_paste(
            messages=messages,
            format_type=format_type,
            target=target,
            captured_diagrams=captured_diagrams,
            version=version,
            model=model_alias,
            provider=endpoint
        )
        
        return JSONResponse(content=exported)
        
    except Exception as e:
        logger.error(f"Error exporting conversation: {e}")
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

@app.post('/api/apply-changes')
async def apply_changes(request: Request):
    try:
        # Parse body manually to debug
        body = await request.json()
        logger.info(f"Raw apply-changes body: {body}")
        
        # Validate manually
        try:
            validated = ApplyChangesRequest(**body)
        except Exception as e:
            logger.error(f"Validation error: {e}")
            return JSONResponse(status_code=422, content={"detail": str(e)})
        
        logger.info(f"TRACE_ID: Received apply-changes request with ID: {validated.requestId}")
        # Validate diff size
        if len(validated.diff) < 100:  # Arbitrary minimum for a valid git diff
            logger.warning(f"Suspiciously small diff received: {len(validated.diff)} bytes")
            logger.warning(f"Diff content: {validated.diff}")

        logger.info(f"Received request to apply changes to file: {validated.filePath}")
        logger.info(f"Raw request diff length: {len(validated.diff)} bytes")
        logger.info(f"First 100 chars of raw diff for request {validated.requestId}:")
        
        # Always use the client-provided request ID if available
        if validated.requestId:
            request_id = validated.requestId
            logger.info(f"Using client-provided request ID: {request_id}")
        else:
            # Only generate a server-side ID if absolutely necessary
            request_id = str(uuid.uuid4())
            logger.warning(f"Using server-side generated request ID: {request_id}")

        logger.info(validated.diff[:100])
        logger.info(f"Full diff content: \n{validated.diff}")

        # Use client-provided projectRoot if available, otherwise fall back to environment variable
        if validated.projectRoot:
            user_codebase_dir = os.path.abspath(validated.projectRoot)
            logger.info(f"Using client-provided project root: {user_codebase_dir}")
        else:
            env_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
            if not env_codebase_dir:
                raise ValueError("ZIYA_USER_CODEBASE_DIR environment variable is not set and no projectRoot provided")
            user_codebase_dir = os.path.abspath(env_codebase_dir)
            logger.info(f"Using environment variable project root: {user_codebase_dir}")
        
        if not os.path.isdir(user_codebase_dir):
            raise ValueError(f"Project root directory does not exist: {user_codebase_dir}")
        
        # Prioritize extracting the file path from the diff content itself
        extracted_path = extract_target_file_from_diff(validated.diff)

        if extracted_path:
            file_path = os.path.join(user_codebase_dir, extracted_path)
            logger.info(f"Extracted target file from diff: {extracted_path}")
        elif validated.filePath:
            # Fallback to using the provided filePath if extraction fails
            file_path = os.path.join(user_codebase_dir, validated.filePath)
            logger.info(f"Using provided file path: {validated.filePath}")

            # Resolve the absolute path and check if it's within the codebase dir
            resolved_path = os.path.abspath(file_path)
            if not resolved_path.startswith(user_codebase_dir):
                logger.error(f"Attempt to access file outside codebase directory: {resolved_path}")
                raise ValueError("Invalid file path specified")
        else:
            raise ValueError("Could not determine target file path from diff or request")

        # Extract individual diffs if multiple are present
        individual_diffs = split_combined_diff(validated.diff)
        if len(individual_diffs) > 1:
            logger.info(f"Received combined diff with {len(individual_diffs)} files")
            # Find the diff for our target file
            logger.debug("Individual diffs:")
            logger.debug('\n'.join(individual_diffs))
            target_diff = None
            for diff in individual_diffs:
                target_file = extract_target_file_from_diff(diff)
                if target_file and os.path.normpath(target_file) == os.path.normpath(extracted_path or validated.filePath):
                    target_diff = diff
                    break

            if not target_diff:
                raise HTTPException(
                    status_code=400,
                    detail={
                        'status': 'error',
                        'type': 'file_not_found',
                        'message': f'No diff found for requested file {validated.filePath} in combined diff'
                    }
                )
        else:
            logger.info("Single diff found")
            target_diff = individual_diffs[0]

        # Run in thread pool to avoid blocking the event loop and allow parallel processing
        result = await run_in_threadpool(apply_diff_pipeline, validated.diff, file_path, request_id)
        
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

@app.post('/api/unapply-changes')
async def unapply_changes(request: Request):
    """Reverse/unapply a previously applied diff."""
    try:
        body = await request.json()
        diff = body.get('diff', '')
        file_path_from_request = body.get('filePath', '')
        project_root_from_request = body.get('projectRoot', '')
        request_id = body.get('requestId', str(uuid.uuid4()))
        
        logger.info(f"Received unapply-changes request with ID: {request_id}")
        
        # Use client-provided projectRoot if available, otherwise fall back to environment variable
        if project_root_from_request:
            user_codebase_dir = os.path.abspath(project_root_from_request)
            logger.info(f"Using client-provided project root for unapply: {user_codebase_dir}")
        else:
            user_codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR")
            if not user_codebase_dir:
                raise ValueError("ZIYA_USER_CODEBASE_DIR environment variable is not set and no projectRoot provided")
            user_codebase_dir = os.path.abspath(user_codebase_dir)
            logger.info(f"Using environment variable project root for unapply: {user_codebase_dir}")
        
        # Extract file path from diff or use provided path
        extracted_path = extract_target_file_from_diff(diff)
        if extracted_path:
            file_path = os.path.join(user_codebase_dir, extracted_path)
        elif file_path_from_request:
            file_path = os.path.join(user_codebase_dir, file_path_from_request)
        else:
            raise ValueError("Could not determine target file path")
        
        # Validate path is within codebase
        resolved_path = os.path.abspath(file_path)
        if not resolved_path.startswith(os.path.abspath(user_codebase_dir)):
            raise ValueError("Invalid file path specified")
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File does not exist: {file_path}")
        
        # Apply the reverse diff
        result = await run_in_threadpool(apply_reverse_diff_pipeline, diff, file_path)
        
        if result.get('status') == 'success':
            return JSONResponse(content={
                'status': 'success',
                'message': 'Changes successfully reversed',
                'request_id': request_id,
                'stage': result.get('stage')
            }, status_code=200)
        else:
            return JSONResponse(content={
                'status': 'error',
                'message': result.get('error', 'Failed to reverse changes'),
                'request_id': request_id
            }, status_code=422)
            
    except FileNotFoundError as e:
        return JSONResponse(content={
            'status': 'error',
            'message': str(e)
        }, status_code=404)
    except ValueError as e:
        return JSONResponse(content={
            'status': 'error', 
            'message': str(e)
        }, status_code=400)
    except Exception as e:
        logger.error(f"Error unapplying changes: {e}")
        return JSONResponse(content={
            'status': 'error',
            'message': f"Unexpected error: {str(e)}"
        }, status_code=500)
