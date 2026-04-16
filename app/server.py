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
from starlette.websockets import WebSocket, WebSocketDisconnect

from fastapi import FastAPI, Request, HTTPException, APIRouter, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from app.agents.agent import model, create_agent_chain, create_agent_executor
from starlette.responses import StreamingResponse
from pydantic import BaseModel, Field

# Import configuration
import app.config.models_config as config
from app.config.app_config import DEFAULT_PORT
from app.agents.models import ModelManager
from app.config.models_config import TOOL_SENTINEL_OPEN, TOOL_SENTINEL_CLOSE, DEFAULT_MAX_OUTPUT_TOKENS
from botocore.exceptions import ClientError

import uvicorn

# Initialize extensions
from app.extensions import init_extensions
init_extensions()
from app.utils.logging_utils import logger
from app.utils.error_handlers import (
    create_json_response, create_sse_error_response, 
    is_streaming_request, ValidationError, handle_request_exception,
    handle_streaming_error
)
from app.utils.diff_utils import apply_diff_pipeline
from app.utils.diff_utils.pipeline.reverse_pipeline import apply_reverse_diff_pipeline
from app.utils.custom_exceptions import ValidationError
from app.utils.file_utils import read_file_content
from app.middleware import RequestSizeMiddleware, ModelSettingsMiddleware, ErrorHandlingMiddleware, HunkStatusMiddleware, StreamingMiddleware
from app.middleware.project_context import ProjectContextMiddleware
from app.utils.context_enhancer import initialize_ast_if_enabled
from app.middleware.security_headers import SecurityHeadersMiddleware
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
 
# Stream lifecycle tracking.  Previously `cleanup_stream` managed an
# `active_streams` dict, but streaming is now self-contained within
# `stream_chunks` (SSE generator) + the frontend's abort mechanism.
# These survive as thin stubs so call-sites in stream_chunks, diff_routes,
# and misc_routes don't crash with NameError.
active_streams: dict[str, Any] = {}

async def cleanup_stream(conversation_id: str) -> None:
    """Clean up any server-side state for a finished/aborted stream.

    Currently a no-op — stream lifecycle is managed by the SSE generator
    and the frontend abort controller.  Retained for call-site compatibility.
    """
    active_streams.pop(conversation_id, None)

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

# MCP tool objects (LangChain StructuredTool wrappers) are stateless config — cache them.
# Invalidated automatically after 30 s so tool additions/removals are picked up quickly.
_mcp_tools_cache: list = []
_mcp_tools_cache_ts: float = 0.0
_MCP_TOOLS_CACHE_TTL: float = 30.0

# Event loop reference for cross-thread async scheduling (set during lifespan startup)
_main_event_loop = None

# Use configuration from config module
# For model configurations, see app/config.py
    
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
    
    # Memory system background initialization — only when --memory is active
    if os.environ.get("ZIYA_ENABLE_MEMORY", "").lower() in ("true", "1", "yes"):
        asyncio.create_task(_initialize_memory_background())

    # Periodic cleanup of stale delegate plans and prompt cache
    asyncio.create_task(_periodic_memory_cleanup())

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
    if os.environ.get("ZIYA_ENABLE_MEMORY", "").lower() in ("true", "1", "yes"):
        logger.info("   🧠 Memory embedding backfill + knowledge organization")
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

async def _initialize_memory_background():
    """Background initialization of the memory system.

    Runs after server startup to handle:
    1. Embedding backfill — embed any memories that lack vectors
    2. Auto-organize — if memories exist but no mind-map, bootstrap structure

    Non-blocking, non-fatal.  If Bedrock is unavailable or the memory
    store is empty, silently returns.
    """
    await asyncio.sleep(2.0)

    try:
        from app.mcp.builtin_tools import is_builtin_category_enabled
        if not is_builtin_category_enabled("memory"):
            return
    except (ImportError, RuntimeError):
        return

    try:
        from app.storage.memory import get_memory_storage
        store = get_memory_storage()
        memories = store.list_memories(status="active")
        if not memories:
            return

        # Phase 1: Embedding backfill
        try:
            from app.services.embedding_service import (
                get_embedding_provider, get_embedding_cache,
                backfill_embeddings, NoopProvider,
            )
            provider = get_embedding_provider()
            if not isinstance(provider, NoopProvider):
                cache = get_embedding_cache()
                all_ids = [m.id for m in memories]
                missing = cache.missing_ids(all_ids)
                if missing:
                    logger.info(
                        f"🧠 Memory startup: {len(missing)}/{len(memories)} "
                        f"memories need embedding backfill"
                    )
                    to_embed = [(m.id, m.content) for m in memories if m.id in set(missing)]
                    count = await backfill_embeddings(to_embed)
                    logger.info(f"🧠 Memory startup: embedded {count} memories")
                else:
                    logger.debug(f"🧠 Memory startup: all {len(memories)} memories have embeddings")
        except Exception as e:
            logger.debug(f"🧠 Memory startup: embedding backfill skipped: {e}")

        # Phase 2: Auto-organize if no mind-map exists
        try:
            nodes = store.list_mindmap_nodes()
            if not nodes and len(memories) >= 10:
                logger.info(
                    f"🧠 Memory startup: {len(memories)} memories with no mind-map — "
                    f"triggering background organization"
                )
                from app.utils.memory_organizer import reorganize
                result = await reorganize(store)
                bootstrap = result.get("bootstrap", {})
                logger.info(
                    f"🧠 Memory startup: organized into "
                    f"{bootstrap.get('domains_created', 0)} domains, "
                    f"{bootstrap.get('memories_placed', 0)} memories placed"
                )
        except Exception as e:
            logger.warning(f"🧠 Memory startup: auto-organize failed (non-fatal): {e}")

    except Exception as e:
        logger.debug(f"🧠 Memory startup: initialization skipped: {e}")


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

async def _periodic_memory_cleanup():
    """Background task that periodically cleans up stale in-memory state.

    Runs every 30 minutes and sweeps:
    - DelegateManager: evicts terminal plans not accessed for 2+ hours
    - PromptCache: forces expired-entry cleanup
    - ThreadStateManager: prunes dead thread entries
    """
    await asyncio.sleep(60)  # Let startup complete

    while True:
        try:
            # Sweep stale delegate plans
            try:
                from app.agents.delegate_manager import _instances as dm_instances
                for mgr in dm_instances.values():
                    mgr.evict_stale_plans(max_age_seconds=7200)
            except (ImportError, RuntimeError) as e:
                logger.debug(f"Delegate plan cleanup skipped: {e}")

            # Force prompt cache cleanup
            try:
                from app.utils.prompt_cache import get_prompt_cache
                cache = get_prompt_cache()
                cache._cleanup_expired()
            except (ImportError, RuntimeError) as e:
                logger.debug(f"Prompt cache cleanup skipped: {e}")

        except Exception as e:
            logger.debug(f"Periodic cleanup error (non-fatal): {e}")

        await asyncio.sleep(1800)  # Every 30 minutes

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
    # UUID pattern for individual chat GETs: /chats/<uuid>  (re already imported at module top)
    _chat_get_re = re.compile(r'/chats/[0-9a-f]{8}-[0-9a-f]{4}-.*" [23]')
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
        # Check wrapper_class from model config — model names like "glm-4.7"
        # don't contain "openai" but use the OpenAIBedrock wrapper.
        is_bedrock_openai = ModelManager.get_model_config(os.environ.get("ZIYA_ENDPOINT", "bedrock"), current_model).get("wrapper_class") == "OpenAIBedrock" if current_model else False
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
            # Log the user's question at INFO level for operational visibility
            if question.strip():
                logger.info(f"👤 USER QUERY: {question}")

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
                        chunk['content'] = re.sub(r'<!-- REWIND_MARKER: [^>]+ -->\n*', '', chunk['content'])

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
                                # Find both fence variants; prefer 4-tick when it's at or before the
                                # 3-tick match (the 3-tick find is off-by-one inside a 4-tick fence).
                                pos3 = content.find('```diff')
                                pos4 = content.find('````diff')
                                if pos4 >= 0 and (pos3 < 0 or pos4 <= pos3):
                                    fence_pos = pos4
                                else:
                                    fence_pos = pos3
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
                            # Strip REWIND_MARKER HTML comments that leaked into content
                            cleaned = re.sub(r'<!-- REWIND_MARKER: [^>]+ -->\n*', '', content)
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
                # No content streamed yet — return error to client
                logger.warning(f"🚀 DIRECT_STREAMING: {ve} (pre-stream)")
                yield f"data: {json.dumps({'error': f'Model initialization error: {str(ve)[:200]}', 'error_type': 'ValueError'})}\\n\\n"
                yield f"data: {json.dumps({'done': True})}\\n\\n"
                return
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
        
        # No question provided — nothing to stream
        logger.warning("stream_chunks: No question in request body")
        yield f"data: {json.dumps({'error': 'No question provided', 'error_type': 'missing_input'})}\\n\\n"
        yield f"data: {json.dumps({'done': True})}\\n\\n"

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

