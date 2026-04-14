# Context overflow management
from typing import Optional, Dict, Any, List, Tuple, Union
import asyncio
from contextlib import asynccontextmanager

import os
import os.path
import re
import signal
import sys
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
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from app.agents.agent import model, create_agent_chain, create_agent_executor
from fastapi.responses import FileResponse
from starlette.responses import StreamingResponse
from pydantic import BaseModel, Field

# Import configuration
import app.config.models_config as config
from app.config.app_config import DEFAULT_PORT
from app.agents.models import ModelManager
from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE, DEFAULT_MAX_OUTPUT_TOKENS
from app.agents.wrappers.nova_wrapper import NovaBedrock  # Import NovaBedrock for isinstance check
from botocore.exceptions import ClientError, BotoCoreError, CredentialRetrievalError
from botocore.exceptions import EventStreamError
import botocore.errorfactory
from starlette.concurrency import run_in_threadpool

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
from app.middleware.security_headers import SecurityHeadersMiddleware
from fastapi.websockets import WebSocketState
from app.middleware.continuation import ContinuationMiddleware

# PCAP analysis utilities
from app.utils.pcap_analyzer import analyze_pcap_file, is_pcap_supported
from app.utils.conversation_exporter import export_conversation_for_paste

# Session management API routers
from app.api import projects, contexts, skills, chats, tokens
from app.api import delegates as delegates_api
from app.api import memory as memory_api
from app.utils.paths import get_ziya_home
from app.utils.logging_utils import logger as app_logger

active_feedback_connections: dict[str, list[dict]] = {}  # conversation_id → list of connection dicts
from fastapi.websockets import WebSocket, WebSocketDisconnect
 
# Track active WebSocket connections for feedback
# (Delegate streaming connections are managed by app.agents.delegate_stream_relay)

# Global security stats tracker
_security_stats = {
    'total_verifications': 0,
    'successful_verifications': 0,
    'failed_verifications': 0,
    'hallucination_attempts': [],
    'last_reset': time.time()
}
_security_stats_lock = threading.Lock()

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

def build_messages_for_streaming(question: str, chat_history: List, files: List, conversation_id: str, use_langchain_format: bool = False, system_prompt_addition: str = "") -> List:
    """
    Build messages for streaming using the extended prompt template.
    This centralizes message construction to avoid duplication.
    """
    logger.debug(f"🔍 FUNCTION_START: build_messages_for_streaming called with {len(files)} files")
    
    # SDO-183: Strip hidden characters from user input before it reaches the model.
    # This prevents Unicode tag smuggling attacks where invisible instructions
    # are embedded in user prompts.
    from app.mcp.response_validator import sanitize_text
    question = sanitize_text(question)

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
                except (ValueError, TypeError, KeyError) as e:
                    logger.error(f"Error processing images from tuple: {e}")
                    # Fallback: add without images
                    processed_chat_history.append({'type': role, 'content': content})
        else:
            processed_chat_history.append(msg)

    # Extract conversation start timestamp from first message
    conv_start_ts = None
    for msg in processed_chat_history:
        if isinstance(msg, dict):
            ts = msg.get('_timestamp')
            if ts:
                conv_start_ts = ts
                break

    # Use precision system for 100% equivalence
    messages = precision_system.build_messages(
        request_path=request_path,
        model_info=model_info,
        files=files,
        question=question,
        chat_history=processed_chat_history,
        system_prompt_addition=system_prompt_addition,
        conv_start_ts=conv_start_ts,
        conversation_id=conversation_id
    )

    logger.debug(f"🎯 PRECISION_SYSTEM: Built {len(messages)} messages with {len(files)} files preserved")
    
    # Log if any messages contain images
    image_message_count = sum(1 for msg in messages if isinstance(msg.get('content'), list))
    if image_message_count > 0:
        logger.info(f"🖼️ MULTI_MODAL: {image_message_count} messages contain images")

    # Convert to LangChain format if needed
    if use_langchain_format:
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

# MCP tool objects (LangChain StructuredTool wrappers) are stateless config — cache them.
# Invalidated automatically after 30 s so tool additions/removals are picked up quickly.
_mcp_tools_cache: list = []
_mcp_tools_cache_ts: float = 0.0
_MCP_TOOLS_CACHE_TTL: float = 30.0

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
    _set_folder_service_event_loop(_main_event_loop)

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
    
    # Start encryption key rotation monitor in background
    asyncio.create_task(_monitor_key_rotation())
    
    # Start folder cache warming in background - don't block server startup
    # REMOVED: The startup scan was wasted work — the frontend triggers a
    # project-specific scan when it connects, and the two caches were
    # split-brained so the startup result was often ignored anyway.
    global _folder_ready
    _folder_ready = True
    
    # Register deferred plugin routes (plugins may have loaded before server)
    if os.environ.get('ZIYA_LOAD_INTERNAL_PLUGINS') == '1':
        try:
            for module_name in ['plugins', 'internal.plugins']:
                if module_name in sys.modules:
                    mod = sys.modules[module_name]
                    if hasattr(mod, 'register_deferred_routes'):
                        mod.register_deferred_routes(app)
                    break
        except (ImportError, AttributeError, RuntimeError) as e:
            logger.warning(f"Could not register deferred plugin routes: {e}")

    # Print clear banner that server is ready
    logger.info("=" * 80)
    logger.info("🚀 SERVER READY - Accepting connections now")
    logger.info("=" * 80)
    logger.info("📋 Background tasks running:")
    if os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
        logger.info("   🔧 MCP server initialization")
    logger.info("   📂 Folder structure scanned on first client connection")
    logger.info("=" * 80)
    
    yield
    
    # Shutdown: run swarm scratch GC for all known project roots
    try:
        from app.agents.swarm_scratch import _instances as scratch_instances
        for scratch_mgr in scratch_instances.values():
            cleaned = scratch_mgr.gc_stale_tasks(max_age_hours=48)
            if cleaned:
                logger.info(f"🗑️ Shutdown GC: cleaned {len(cleaned)} stale task dir(s)")
    except (ImportError, OSError, RuntimeError) as e:
        logger.warning(f"Swarm scratch GC during shutdown: {e}")

    # Shutdown - cleanup
    logger.info("🛑 Shutting down gracefully...")

    # Persist delegate orchestration state so plans survive restart
    try:
        from app.agents.delegate_manager import get_delegate_manager
        project_id = os.environ.get("ZIYA_PROJECT_ID")
        if project_id:
            mgr = get_delegate_manager(project_id)
            for plan_id in list(mgr._plans.keys()):
                mgr._persist_plan(plan_id)
            active_count = sum(1 for s in mgr._running.values() for _ in s)
            logger.info(f"💾 Persisted {len(mgr._plans)} plan(s), {active_count} running delegate(s)")
    except (ImportError, OSError, RuntimeError, KeyError) as e:
        logger.warning(f"Delegate state persistence during shutdown: {e}")

    # Cancel any ongoing folder scans
    try:
        from app.utils.directory_util import cancel_scan
        cancel_scan()
        logger.debug("Cancelled any ongoing folder scans during shutdown")
    except (OSError, asyncio.CancelledError, RuntimeError) as e:
        logger.warning(f"Error cancelling folder scan: {e}")
    
    # MCP shutdown
    if os.environ.get("ZIYA_ENABLE_MCP", "true").lower() in ("true", "1", "yes"):
        try:
            # Shut down headless diagram renderer if running
            from app.services.diagram_renderer import shutdown_diagram_renderer
            await shutdown_diagram_renderer()
        except (ImportError, OSError, RuntimeError) as e:
            logger.debug(f"Diagram renderer shutdown: {e}")

        try:
            from app.mcp.manager import get_mcp_manager
            mcp_manager = get_mcp_manager()
            await mcp_manager.shutdown()
            logger.info("MCP manager shutdown completed")
        except (OSError, RuntimeError, asyncio.TimeoutError) as e:
            logger.warning(f"MCP shutdown failed: {str(e)}")

async def _initialize_mcp_background():
    """Initialize MCP in the background without blocking server startup."""
    global _mcp_ready, _folder_ready, _background_tasks_lock
    _mcp_ready = False
    
    # Small delay to let the server finish starting
    await asyncio.sleep(0.1)
    
    try:
        logger.debug("🔧 Starting background MCP initialization...")
        
        from app.mcp.signing import get_session_secret
        get_session_secret()
        
        from app.mcp.manager import get_mcp_manager
        mcp_manager = get_mcp_manager()
        await mcp_manager.initialize()
        
        if mcp_manager.is_initialized:
            status = mcp_manager.get_server_status()
            connected_servers = sum(1 for s in status.values() if s["connected"])
            total_tools = sum(s["tools"] for s in status.values())
            
            from app.mcp.connection_pool import get_connection_pool as get_secure_pool
            secure_pool = get_secure_pool()
            secure_pool.set_server_configs(mcp_manager.server_configs)
            
            import gc; gc.collect()
            agent = create_agent_chain(model.get_model())
            agent_executor = create_agent_executor(agent)
            
            _mcp_ready = True
            logger.info(f"🔧 MCP ready: {connected_servers} servers, {total_tools} tools")
            _check_and_print_completion_banner()
        else:
            logger.warning("MCP initialization failed or no servers configured")
    except (ImportError, OSError, RuntimeError, asyncio.TimeoutError) as e:
        logger.warning(f"Background MCP initialization failed: {str(e)}")
        _mcp_ready = True
        _check_and_print_completion_banner()

    except (OSError, RuntimeError, ImportError) as e:
        logger.warning(f"Background folder cache warming failed: {e}")
        _folder_ready = True  # Mark as complete even on failure
        _check_and_print_completion_banner()

async def _monitor_key_rotation():
    """Background task that checks for DEK rotation on a schedule."""
    # Wait for startup to complete
    await asyncio.sleep(5)

    while True:
        try:
            from app.utils.encryption import get_encryptor
            encryptor = get_encryptor()

            if encryptor.is_enabled():
                # The encryptor checks rotation needs during _initialize(),
                # but we also check periodically for long-running servers.
                if (encryptor._policy and encryptor._policy.dek_rotation_interval
                        and encryptor._keyring):
                    active = encryptor._keyring.get_active_dek()
                    if active:
                        import time as _time
                        age = _time.time() - active.created_at
                        max_age = encryptor._policy.dek_rotation_interval.total_seconds()
                        if age > max_age:
                            logger.info(f"🔑 KEY_ROTATION: DEK expired (age: {age/86400:.1f}d, max: {max_age/86400:.1f}d), rotating")
                            encryptor._generate_dek()
                            logger.info("🔑 KEY_ROTATION: DEK rotation completed")
        except (OSError, RuntimeError, ValueError) as e:
            logger.debug(f"Key rotation check failed (non-fatal): {e}")

        # Check every hour
        await asyncio.sleep(3600)

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
    _quiet = {'/chats?', '/chat-groups', '/skills', '/contexts', '/api/config', '/ws/',
              '/folder-progress', '/model-capabilities', '/current-model', '/static/',
              '/delegate-status', '/bulk-sync', '/api/ast/status',}
    # UUID pattern for individual chat GETs: /chats/<uuid>
    _chat_get_re = __import__('re').compile(r'/chats/[0-9a-f]{8}-[0-9a-f]{4}-.*" [23]')
    def filter(self, record: _logging.LogRecord) -> bool:
        msg = record.getMessage()
        if any(q in msg for q in self._quiet):
            return False
        if 'GET' in msg and self._chat_get_re.search(msg):
            return False
        return True

_logging.getLogger("uvicorn.access").addFilter(_PollingAccessFilter())

class _WebSocketLifecycleFilter(_logging.Filter):
    """Filter noisy WebSocket connection open/close messages from uvicorn."""
    _noise = {'connection open', 'connection closed'}
    def filter(self, record: _logging.LogRecord) -> bool:
        return record.getMessage().strip() not in self._noise

_logging.getLogger("uvicorn.error").addFilter(_WebSocketLifecycleFilter())

# Suppress noisy urllib3 connection pool warnings (expected under delegate concurrency)
_logging.getLogger("urllib3.connectionpool").setLevel(_logging.ERROR)

@app.websocket("/ws/feedback/{conversation_id}")
async def feedback_websocket(websocket: WebSocket, conversation_id: str):
    await websocket.accept()
    logger.debug(f"🔄 FEEDBACK: WebSocket connected for conversation {conversation_id}")
    
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
                logger.debug(f"🔄 FEEDBACK: WebSocket disconnected for {conversation_id}")
                break
    except WebSocketDisconnect:
        # Client disconnected before entering the receive loop (page reload race)
        logger.debug(f"🔄 FEEDBACK: Client disconnected early for {conversation_id}")
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
    logger.debug("🔄 FILE_TREE: WebSocket connection attempt")
    await websocket.accept()
    active_file_tree_connections.add(websocket)
    logger.debug("🔄 FILE_TREE: WebSocket connected")
    
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
                logger.debug("🔄 FILE_TREE: WebSocket disconnected")
                break
    except WebSocketDisconnect:
        # Client disconnected before or during the initial send — this is
        # normal during page reloads and browser navigation.
        logger.debug("🔄 FILE_TREE: Client disconnected early (race during page load)")
    finally:
        # Clean up connection
        active_file_tree_connections.discard(websocket)
        logger.debug(f"🔄 FILE_TREE: Connection removed, {len(active_file_tree_connections)} remaining")

@app.websocket("/ws/delegate-stream/{conversation_id}")
async def delegate_stream_websocket(websocket: WebSocket, conversation_id: str):
    """WebSocket endpoint for live delegate conversation streaming.

    When a user views a delegate conversation, the frontend connects here.
    DelegateManager pushes chunks via delegate_stream_relay.push(), which
    this endpoint relays to the connected client in real time.
    """
    await websocket.accept()
    logger.debug(f"📡 DELEGATE_STREAM: WebSocket connected for {conversation_id[:8]}")

    from app.agents.delegate_stream_relay import connect, disconnect
    await connect(conversation_id, websocket)

    try:
        # Keep alive — wait for client disconnect
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.debug(f"📡 DELEGATE_STREAM: WebSocket disconnected for {conversation_id[:8]}")
    except Exception:  # Intentionally broad: WebSocket protocol errors vary by client
        # Connection errors (e.g. client vanished without close frame) are expected
        logger.debug(f"📡 DELEGATE_STREAM: Connection lost for {conversation_id[:8]}")
    finally:
        await disconnect(conversation_id, websocket)

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
        current_model = ModelManager.get_model_alias()
        logger.debug(f"🔍 CHAT_ENDPOINT: current_model={current_model}")
        is_bedrock_claude = current_model and ('claude' in current_model.lower() or 'sonnet' in current_model.lower() or 'opus' in current_model.lower() or 'haiku' in current_model.lower())
        is_bedrock_nova = current_model and 'nova' in current_model.lower()
        is_bedrock_deepseek = current_model and 'deepseek' in current_model.lower()
        is_bedrock_openai = current_model and 'openai' in current_model.lower()
        is_google_model = current_model and ('gemini' in current_model.lower() or 'google' in current_model.lower())
        is_openai_direct = os.environ.get("ZIYA_ENDPOINT") == "openai"
        is_anthropic_direct = os.environ.get("ZIYA_ENDPOINT") == "anthropic"
        # Check if direct streaming is enabled globally - use direct streaming by default for Bedrock models like 0.3.1
        use_direct_streaming = is_bedrock_claude or is_bedrock_nova or is_bedrock_deepseek or is_bedrock_openai or is_google_model or is_openai_direct or is_anthropic_direct
        
        logger.debug(f"🔍 CHAT_ENDPOINT: Current model = {current_model}, is_bedrock_claude = {is_bedrock_claude}, is_openai_direct = {is_openai_direct}")
        
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
                    has_images = False
                
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
                            images = json.loads(msg[2])
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(f"Failed to parse images from message: {msg[2][:100] if len(msg[2]) > 100 else msg[2]}")
                    
                    if role in ['human', 'user']:
                        chat_history.append({'type': 'human', 'content': content, 'images': images,
                                             '_timestamp': None})
                    elif role in ['assistant', 'ai']:
                        chat_history.append({'type': 'ai', 'content': content, 'images': images,
                                             '_timestamp': None})
                elif isinstance(msg, dict):
                    # Already in dict format
                    role = msg.get('role', msg.get('type', 'user'))
                    content = msg.get('content', '')
                    images = msg.get('images')
                    msg_ts = msg.get('_timestamp') or msg.get('timestamp')
                    if role and content:
                        if role in ['human', 'user']:
                            chat_history.append({'type': 'human', 'content': content, 'images': images,
                                                 '_timestamp': msg_ts})
                        elif role in ['assistant', 'ai']:
                            chat_history.append({'type': 'ai', 'content': content, 'images': images,
                                                 '_timestamp': msg_ts})
            
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
                    'project_root': project_root,
                    'modelOverrides': body.get('modelOverrides', {}),
                    'preferredToolIds': body.get('preferredToolIds', []),
                }
            }
            
            logger.info("[CHAT_ENDPOINT] Using StreamingToolExecutor via stream_chunks for unified execution")
            
            return StreamingResponse(
                _keepalive_wrapper(stream_chunks(formatted_body)),
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
    except Exception as e:  # Intentionally broad: top-level HTTP error handler
        # Ensures clients always get a JSON error response, never a bare 500
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

# Security headers — XSS protection, CSP, clickjacking prevention.
app.add_middleware(SecurityHeadersMiddleware)

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
    except Exception as e:  # Intentionally broad: SSE stream must not propagate
        # Connection errors are checked and logged at appropriate severity below
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
app.include_router(delegates_api.router)
app.include_router(memory_api.router)
app_logger.info("Session management API routes loaded")

# Import and include model routes
# Conversation graph visualization
from app.routes.graph_routes import router as graph_router
app.include_router(graph_router)

# Headless diagram rendering (requires optional Playwright dependency)
from app.routes.diagram_routes import router as diagram_router
app.include_router(diagram_router)

# Model configuration routes
from app.routes.model_routes import router as model_router
app.include_router(model_router)

# Phase 3b extracted routes
from app.routes.folder_routes import router as folder_router
app.include_router(folder_router)

from app.routes.token_routes import router as token_router
app.include_router(token_router)

from app.routes.debug_routes import router as debug_router
app.include_router(debug_router)

from app.routes.diff_routes import router as diff_router
app.include_router(diff_router)

from app.routes.page_routes import router as page_router
app.include_router(page_router)

from app.routes.misc_routes import router as misc_router
app.include_router(misc_router)

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

async def _keepalive_wrapper(async_gen, interval: float = 15.0):
    """Wrap an async generator with periodic SSE keepalive comments.

    When the inner generator is busy (e.g. tool execution), the SSE
    connection can go idle for minutes. Browsers and proxies may treat
    an idle connection as dead and drop it — especially when the tab is
    backgrounded (screen saver, lid close, etc.).

    SSE spec allows lines starting with ':' as comments that clients
    silently ignore, so we periodically inject ': keepalive\\n\\n' to
    keep the TCP connection alive.
    """

    sentinel = object()

    pending_task = None

    while True:
        # Reuse an in-flight read task if one is still pending from a
        # previous keepalive timeout (wait_for cancels, so we must NOT
        # use it — we need the task to survive across iterations).
        if pending_task is None:
            async def _next(gen=async_gen, _s=sentinel):
                try:
                    return await gen.__anext__()
                except StopAsyncIteration:
                    return _s
            pending_task = asyncio.ensure_future(_next())

        try:
            done, _ = await asyncio.wait({pending_task}, timeout=interval)
        except asyncio.CancelledError:
            pending_task.cancel()
            raise

        if not done:
            # Timeout — no data within the interval, send keepalive.
            # The pending_task stays alive for the next iteration.
            yield ": keepalive\n\n"
            continue

        try:
            result = pending_task.result()
        except Exception as exc:  # Intentionally broad: wraps entire SSE stream
            # Any unhandled error must produce a client-visible error event, not a silent drop
            logger.error(f"_keepalive_wrapper: stream_chunks raised: {exc!r}", exc_info=True)
            yield f"data: {json.dumps({'error': str(exc), 'error_type': 'stream_error'})}\n\n"
            yield "data: {\"type\": \"stream_end\"}\n\n"
            break

        pending_task = None

        if result is sentinel:
            break

        yield result

async def stream_chunks(body):
    """Stream chunks from the agent executor."""
    logger.debug("stream_chunks: called")
    
    # Initialize diff validation hook
    from app.utils.diff_validation_hook import DiffValidationHook
    from app.config.app_config import ENABLE_DIFF_VALIDATION, AUTO_REGENERATE_INVALID_DIFFS, AUTO_ENHANCE_CONTEXT_ON_VALIDATION_FAILURE
    
    files = body.get("config", {}).get("files", [])
    conversation_id = body.get("conversation_id")
    project_root = body.get("config", {}).get("project_root") or body.get("project_root")
    system_prompt_addition = body.get("config", {}).get("systemPromptAddition", "")
    model_overrides = body.get("config", {}).get("modelOverrides", {})
    preferred_tool_ids = body.get("config", {}).get("preferredToolIds", [])
    
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
    
    # Check if we should use direct streaming
    if use_direct_streaming:
        logger.debug("stream_chunks: using StreamingToolExecutor")

        # Extract data from body for StreamingToolExecutor
        question = body.get("question", "")
        chat_history = body.get("chat_history", [])
        files = body.get("config", {}).get("files", [])
        conversation_id = body.get("conversation_id")
        project_root = body.get("config", {}).get("project_root") or body.get("project_root")
        
        if question:
            # Check for common connectivity-related errors early
            try:
                # Quick connectivity check before expensive operations
                state = ModelManager.get_state()
                if state.get('last_auth_error') and 'i/o timeout' in str(state.get('last_auth_error')):
                    yield f"data: {json.dumps({'error': 'Network connectivity issue detected. Please check your internet connection and try again.', 'error_type': 'connectivity'})}\n\n"
                    # Clean up stream before returning
                    if conversation_id:
                        await cleanup_stream(conversation_id)
                    return
            except (OSError, RuntimeError, asyncio.TimeoutError) as conn_check_error:
                logger.debug(f"Connectivity pre-check failed: {conn_check_error}")
            
            try:
                from app.streaming_tool_executor import StreamingToolExecutor
                
                chunk_count = 0
                last_diff_start_line = -1
                diff_counter = 0
                
                # Get current model state
                state = ModelManager.get_state()
                current_region = state.get('aws_region', 'us-east-1')
                aws_profile = state.get('aws_profile', 'default')
                endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                
                logger.debug(f"🔍 DIRECT_STREAMING_DEBUG: About to call build_messages_for_streaming with {len(files)} files")
                # Build messages with full context using the same function as LangChain path - use langchain format like 0.3.0
                logger.debug(f"🔍 CALLING_BUILD_MESSAGES: About to call build_messages_for_streaming")
                messages = build_messages_for_streaming(question, chat_history, files, conversation_id, use_langchain_format=True, system_prompt_addition=system_prompt_addition)
                logger.debug(f"🔍 DIRECT_STREAMING_PATH: Built {len(messages)} messages with full context")
                
                # Debug the system message content
                if messages and hasattr(messages[0], 'content'):
                    system_content_length = len(messages[0].content)
                    logger.debug(f"🔍 DIRECT_STREAMING_DEBUG: System message length = {system_content_length}")
                    logger.debug(f"🔍 DIRECT_STREAMING_DEBUG: System message preview = {messages[0].content[:200]}...")
                
                executor = StreamingToolExecutor(profile_name=aws_profile, region=current_region)
                logger.debug(f"🚀 DIRECT_STREAMING: Created StreamingToolExecutor with profile={aws_profile}, region={current_region}")
                
                # Apply per-request model overrides from active skills
                if model_overrides:
                    logger.info(f"🎯 SKILL_OVERRIDES: Applying model overrides: {model_overrides}")
                    if 'temperature' in model_overrides:
                        executor.temperature_override = float(model_overrides['temperature'])
                    if 'maxOutputTokens' in model_overrides:
                        executor.max_tokens_override = int(model_overrides['maxOutputTokens'])

                # Send initial heartbeat
                yield f"data: {json.dumps({'heartbeat': True, 'type': 'heartbeat'})}\n\n"
                
                # Get MCP tools — use process-level cache to avoid rebuilding wrappers each request
                global _mcp_tools_cache, _mcp_tools_cache_ts
                if time.time() - _mcp_tools_cache_ts > _MCP_TOOLS_CACHE_TTL:
                    try:
                        from app.mcp.enhanced_tools import create_secure_mcp_tools
                        _mcp_tools_cache = create_secure_mcp_tools()
                        _mcp_tools_cache_ts = time.time()
                    except (ImportError, OSError, RuntimeError) as e:
                        logger.warning("Failed to refresh MCP tools cache: %s", e)
                        _mcp_tools_cache = []
                mcp_tools = list(_mcp_tools_cache)
                
                # Filter tools by skill preferences if specified
                if preferred_tool_ids and mcp_tools:
                    preferred_set = set(preferred_tool_ids)
                    # Partition: preferred tools first, then the rest
                    preferred = [t for t in mcp_tools if t.name in preferred_set]
                    others = [t for t in mcp_tools if t.name not in preferred_set]
                    mcp_tools = preferred + others
                    logger.info(f"🎯 SKILL_TOOLS: Prioritized {len(preferred)} tools from active skills, {len(others)} others")

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
                    elif chunk.get('type') in ('processing', 'processing_state'):
                        # Forward processing/thinking heartbeats to frontend
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
                    
                    try:
                        validation_feedback = await asyncio.wait_for(
                            asyncio.to_thread(
                                validation_hook.validate_and_enhance,
                                content=accumulated_content,
                                model_messages=messages,
                                send_event=send_sse_event
                            ),
                            timeout=30,
                        )
                    except asyncio.TimeoutError:
                        logger.error("⏰ Diff validation timed out after 30s, skipping")
                        validation_feedback = None
                    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as e:
                        logger.error(f"❌ Diff validation failed: {e}")
                        validation_feedback = None
                    
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
                        
                        # Simple feedback - model will acknowledge naturally
                        enhanced_feedback = validation_feedback
                        messages.append(HumanMessage(content=enhanced_feedback))
                        
                        # Send a transition marker so the frontend can show a separator
                        separator_content = '\n\n---\n\n**Correcting failed diff(s):**\n\n'
                        yield f"data: {json.dumps({'type': 'validation_retry', 'content': separator_content})}\n\n"
                        
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
                    # Fire-and-forget: extract memories from the conversation
                    # in the background. Never blocks the stream completion.
                    try:
                        from app.utils.memory_extractor import run_post_conversation_extraction
                        # Resolve project name/path for memory scoping.
                        # project_root is already in scope from the request body.
                        _mem_project_name = None
                        _mem_project_path = project_root
                        if _mem_project_path:
                            try:
                                from app.storage.projects import ProjectStorage
                                _ps = ProjectStorage(get_ziya_home())
                                _proj = _ps.get_by_path(_mem_project_path)
                                _mem_project_name = _proj.name if _proj else None
                            except (ImportError, KeyError, AttributeError):
                                pass
                        # Build a lightweight message list from the LangChain
                        # messages used for this request (already in scope).
                        extraction_messages = []
                        for m in messages:
                            role = getattr(m, 'type', getattr(m, 'role', 'unknown'))
                            content = getattr(m, 'content', '')
                            if isinstance(content, str) and content.strip():
                                extraction_messages.append({"role": role, "content": content})
                        asyncio.create_task(
                            run_post_conversation_extraction(
                                extraction_messages, conversation_id,
                                project_name=_mem_project_name,
                                project_path=_mem_project_path)
                        )
                    except (ImportError, OSError, RuntimeError) as mem_err:
                        logger.debug(f"Memory extraction dispatch failed (non-fatal): {mem_err}")

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
                # BUT only if no content has been streamed yet.  If content was
                # already yielded (chunk_count > 0), falling through would cause
                # the LangChain path to replay the entire conversation from
                # scratch, duplicating everything in the frontend accumulator.
                if chunk_count > 0:
                    logger.warning(
                        f"🚀 DIRECT_STREAMING: ValueError after {chunk_count} chunks "
                        f"sent — NOT falling through to LangChain: {ve}"
                    )
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    if conversation_id:
                        await cleanup_stream(conversation_id)
                    return
                logger.debug(f"🚀 DIRECT_STREAMING: {ve} (pre-stream) - falling back to LangChain")
            except Exception as e:  # Intentionally broad: STE can raise API/auth/config errors
                # Falls back to LangChain path — must catch everything to avoid aborting
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
        messages = build_messages_for_streaming(question, chat_history, files, conversation_id, use_langchain_format=True, system_prompt_addition=system_prompt_addition)
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
        except (ImportError, OSError, RuntimeError) as e:
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
                except (ImportError, OSError, RuntimeError) as e:
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
        try:
            model_id_result = ModelManager.get_model_id()
            if isinstance(model_id_result, dict):
                current_model_id = list(model_id_result.values())[0]
            else:
                current_model_id = model_id_result
            
            # OpenAI models should use same message construction as other Bedrock models
            if current_model_id and ('openai' in current_model_id.lower() or os.environ.get("ZIYA_ENDPOINT") == "openai"):
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

                    # Build messages using same method as other Bedrock models
                    executor = StreamingToolExecutor()
                    messages = executor.build_messages(question, chat_history, files, conversation_id)

                    # Convert to LangChain format for OpenAI wrapper
                    langchain_messages = []
                    for msg in messages:
                        if msg["role"] == "system":
                            langchain_messages.append(SystemMessage(content=msg["content"]))
                        elif msg["role"] == "user":
                            langchain_messages.append(HumanMessage(content=msg["content"]))
                        elif msg["role"] == "assistant":
                            langchain_messages.append(AIMessage(content=msg["content"]))

                    messages = langchain_messages
                    logger.debug(f"Built {len(messages)} LangChain messages for OpenAI model")

                    # Load MCP tools for native function calling
                    mcp_tools = []
                    try:
                        from app.mcp.enhanced_tools import create_secure_mcp_tools
                        mcp_tools = create_secure_mcp_tools()
                        logger.info(f"OpenAI path: loaded {len(mcp_tools)} MCP tools")
                    except (ImportError, OSError, RuntimeError) as e:
                        logger.warning(f"Failed to get MCP tools for OpenAI: {e}")

                    # Use the model's own astream which has a built-in tool execution loop
                    model_instance = model.get_model()
                    async for chunk in model_instance.astream(messages, tools=mcp_tools if mcp_tools else []):
                        if isinstance(chunk, dict):
                            chunk_type = chunk.get('type', '')
                            if chunk_type == 'text' and chunk.get('content'):
                                yield f"data: {json.dumps({'content': chunk['content']})}\n\n"
                                accumulated_content += chunk['content']
                            elif chunk_type == 'tool_start':
                                yield f"data: {json.dumps({'tool_start': chunk})}\n\n"
                            elif chunk_type in ('tool_display', 'tool_execution'):
                                yield f"data: {json.dumps({'tool_result': chunk})}\n\n"
                            elif chunk_type == 'error':
                                yield f"data: {json.dumps({'error': chunk.get('content', 'Unknown error')})}\n\n"
                            elif chunk_type == 'stream_end':
                                break
                        elif hasattr(chunk, 'content') and chunk.content:
                            yield f"data: {json.dumps({'content': chunk.content})}\n\n"
                            accumulated_content += chunk.content

                    yield f"data: {json.dumps({'done': True})}\n\n"
                    return
                    
                except (ValueError, TypeError, KeyError, RuntimeError) as e:
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
                                                       use_langchain_format=True,
                                                       system_prompt_addition=system_prompt_addition)
                
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
                            except (ImportError, OSError, RuntimeError) as e:
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
                        
                    except Exception as e:  # Intentionally broad: wraps entire STE execution
                        # Must catch all to yield error event and avoid silent stream death
                        logger.error(f"🚀 DIRECT_STREAMING: Error in StreamingToolExecutor: {e}")
                        yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
                        return
                else:
                    # For non-Bedrock endpoints, skip StreamingToolExecutor and use LangChain path
                    logger.debug(f"🚀 DIRECT_STREAMING: Skipping StreamingToolExecutor for endpoint '{endpoint}' - using LangChain path")
        except (ImportError, KeyError, AttributeError, RuntimeError) as e:
            logger.error(f"🚀 DIRECT_STREAMING: Error checking model ID: {e}")
            # For Nova models, still try to use StreamingToolExecutor even if model ID check fails
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
            except (ImportError, KeyError, AttributeError, RuntimeError, ValueError):
                # Fall through to LangChain path
                pass
    
    # Check if this is a Nova model before falling back to LangChain
    try:
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
                        conversation_id=conversation_id,
                        system_prompt_addition=system_prompt_addition
                    )
                    
                    logger.debug(f"Built {len(messages)} messages for Nova StreamingToolExecutor")
                    
                    # DISABLED: Redundant Nova StreamingToolExecutor call - causes duplicate execution  
                    # async for chunk in executor.stream_with_tools(messages):
                    logger.info("🚀 DIRECT_STREAMING: Skipping redundant Nova StreamingToolExecutor call")
                    return
                    
                except Exception as e:  # Intentionally broad: Nova STE fallback to LangChain
                    logger.error(f"🚀 DIRECT_STREAMING: Error in Nova StreamingToolExecutor: {e}")
                    # Fall through to LangChain as last resort
                    pass
    except Exception:  # Intentionally broad: outermost STE/Nova guard
        # Ensures LangChain fallback path always executes
        pass
    
    # Fallback to LangChain for non-direct streaming
    logger.debug("🔍 STREAM_CHUNKS: Using LangChain mode")
    yield f"data: {json.dumps({'heartbeat': True, 'type': 'heartbeat'})}\n\n"
    
    # Track if we've successfully sent any data
    data_sent = False

    # Prepare messages for the model

    # Initialize variables that are always needed
    conversation_id = body.get("conversation_id")
    if not conversation_id:
        config_data = body.get("config", {})
        conversation_id = config_data.get("conversation_id")
    if not conversation_id:
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
        messages = build_messages_for_streaming(question, chat_history, files, conversation_id, use_langchain_format=True, system_prompt_addition=system_prompt_addition)
    
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
        model_instance = model.get_model()
        
        # Check if we have a Google function calling agent available
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
                
            except Exception as e:  # Intentionally broad: agent chain wraps LangChain + tools
                # Falls back to direct model approach
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
        mcp_tools = []
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
        
        # Allow tool calls to complete - only stop at the END of tool calls
        try:
            model_with_stop = model_instance.bind(stop=["</TOOL_SENTINEL>"])
        except Exception as e:  # Intentionally broad: model init can raise auth/config/API errors
            # Credential errors are detected and given specific messaging below
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
                buffered_content = ""  # Buffer for content after tool detection
                pending_tool_execution = False  # Flag to indicate we need to execute tools
                
                # DISABLED for Bedrock: LangChain streaming path - causes duplicate execution with StreamingToolExecutor
                # But ENABLED for non-Bedrock endpoints like Google
                endpoint = os.environ.get("ZIYA_ENDPOINT", "bedrock")
                if endpoint == "bedrock":
                    logger.info("🚀 DIRECT_STREAMING: LangChain path disabled for Bedrock - using StreamingToolExecutor only")
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    return
                
                # Stream from model for non-Bedrock endpoints (use simple streaming like 0.3.0)
                async for chunk in model_instance.astream(messages, tools=mcp_tools if mcp_tools else []):
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
                            if not content_str:
                                continue
                            if content_str:
                                current_response += content_str
                                ops = [{"op": "add", "path": "/streamed_output_str/-", "value": content_str}]
                                yield f"data: {json.dumps({'ops': ops})}\n\n"
                                chunk_count += 1
                        elif chunk.get('type') == 'tool_start':
                            yield f"data: {json.dumps({'tool_start': chunk})}\n\n"
                        elif chunk.get('type') == 'tool_display':
                            yield f"data: {json.dumps({'tool_result': chunk})}\n\n"
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
                        await asyncio.sleep(0)
                    else:
                        logger.debug(f"🔍 AGENT: Suppressed tool call content from frontend")
                    # Check for tool calls and execute when model has finished generating them
                    if pending_tool_execution or (TOOL_SENTINEL_OPEN in current_response and 
                                                  TOOL_SENTINEL_CLOSE in current_response) and not tool_executed:
                        
                        tools_handled_inline = False
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
                                
                            except (OSError, RuntimeError, asyncio.TimeoutError) as tool_error:
                                logger.error(f"🔍 STREAM: Tool execution error: {tool_error}")
                                error_msg = f"**Tool Error:** {str(tool_error)}"
                                yield f"data: {json.dumps({'content': error_msg})}\n\n"
                                tool_executed = True
                                tool_call_detected = False
                                pending_tool_execution = False

                            logger.debug(f"🔍 AGENT: Finished streaming loop for iteration {iteration}")
                                
                            # Only execute if tools weren't already handled inline
                            if not tools_handled_inline:
                                try:
                                    processed_response, tool_results = await execute_tools_and_update_conversation(
                                        current_response, processed_tool_calls, messages
                                    )
                                    if tool_results:
                                        tool_executed = True
                                except (OSError, RuntimeError, asyncio.TimeoutError) as tool_error:
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
                    except (OSError, RuntimeError, asyncio.TimeoutError) as tool_exec_error:
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

            except Exception as e:  # Intentionally broad: agent iteration error recovery loop
                # Classifies throttling, auth, validation errors with specific handling below
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
                                except (json.JSONDecodeError, TypeError, ValueError):
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
            
    except Exception as e:  # Intentionally broad: last-resort error handler for entire stream
        # Ensures the client always receives an error event
        logger.error(f"Unhandled exception in stream_chunks: {str(e)}", exc_info=True)
        # Check if this is a connectivity issue
        if any(indicator in str(e).lower() for indicator in ['i/o timeout', 'dial tcp', 'lookup', 'network', 'connection']):
            yield f"data: {json.dumps({'error': 'Network connectivity issue. Please check your internet connection and try again.', 'error_type': 'connectivity'})}\n\n"
        else:
            yield f"data: {json.dumps({'error': f'An unexpected error occurred: {str(e)[:100]}...', 'error_type': 'unexpected'})}\n\n"
        if conversation_id: # Ensure cleanup if conversation_id was set
            await cleanup_stream(conversation_id)

# Folder/cache management — delegated to app.services.folder_service
from app.services.folder_service import (
    _folder_cache, _cache_lock, _explicit_external_paths,
    invalidate_folder_cache, is_path_explicitly_allowed,
    add_file_to_folder_cache, update_file_in_folder_cache,
    remove_file_from_folder_cache, add_external_path_to_cache,
    add_directory_to_folder_cache, _schedule_broadcast,
    broadcast_file_tree_update, get_cached_folder_structure,
    collect_leaf_file_keys as _collect_leaf_file_keys,
    collect_documentation_file_keys as _collect_documentation_file_keys,
    restore_external_paths_for_project as _restore_external_paths_for_project,
    active_file_tree_connections,
    set_main_event_loop as _set_folder_service_event_loop,
)

# Re-export model route functions for backward compatibility
from app.routes.model_routes import get_config, get_available_models, get_current_model, get_model_capabilities

# Import scan progress from directory_util
# from app.utils.directory_util import get_scan_progress, cancel_scan, _scan_progress

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
        pass

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
        except (ImportError, ValueError, RuntimeError, KeyError) as e:
            logger.error(f"Error initializing model: {e}")
    
    # Run the server
    # Set process title using setproctitle if available - this persists through library calls
    port = args.port
    if has_setproctitle:
        setproctitle(f"Ziya : {port}")
        logger.info(f"Set process title to: Ziya : {port}")
    
    uvicorn_log_level = os.environ.get("ZIYA_LOG_LEVEL", "INFO").lower()
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level=uvicorn_log_level)
    except (KeyboardInterrupt, SystemExit):
        # Uvicorn re-raises KeyboardInterrupt after its own shutdown.
        # The lifespan handler has already persisted delegate state
        # and cleaned up MCP connections. Suppress the stack trace.
        print("\n✅ Ziya stopped.")
    except Exception:  # Intentionally broad: top-level server exit handler
        logger.exception("Server exited with error")

