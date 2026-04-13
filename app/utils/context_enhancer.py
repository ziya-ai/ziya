"""
Context enhancer for Ziya.

This module provides functions to enhance the context for the LLM with AST information.
"""

import os
import fnmatch
import importlib.util
import sys
from typing import Dict, Any, Optional
import threading

from app.utils.logging_utils import logger

# Add more detailed logging for AST dependencies
logger.info(f"Python version: {sys.version}")
logger.info(f"Python executable: {sys.executable}")
logger.info(f"Checking for AST dependencies...")

# Check if required packages are installed
logger.info("Starting AST dependency check...")
cssutils_available = importlib.util.find_spec("cssutils") is not None
html5lib_available = importlib.util.find_spec("html5lib") is not None
ast_deps_available = cssutils_available and html5lib_available

logger.info(f"AST dependency check: cssutils={cssutils_available}, html5lib={html5lib_available}, combined={ast_deps_available}")
logger.info(f"cssutils available: {cssutils_available}")
logger.info(f"html5lib available: {html5lib_available}")
logger.info(f"All AST dependencies available: {ast_deps_available}")

# Import AST capabilities if available
# Initialize AST_AVAILABLE to False by default
AST_AVAILABLE = False

initialize_ast_capabilities = None
is_ast_available = None
get_ast_context = None
get_ast_token_count = None
enhance_query_context = None

# AST indexing status tracking
_ast_indexing_status = {
    'is_indexing': False,
    'completion_percentage': 0,
    'is_complete': False,
    'indexed_files': 0,
    'total_files': 0,
    'elapsed_seconds': None,
    'error': None,
    'enabled': False
}

def reset_ast_indexing_status():
    """Reset AST indexing status to initial state for new indexing operation."""
    global _ast_indexing_status
    _ast_indexing_status.update({
        'is_indexing': True,
        'completion_percentage': 0,
        'is_complete': False,
        'indexed_files': 0,
        'total_files': 0,
        'elapsed_seconds': 0,
        'error': None,
        'enabled': True
    })

try:
    if ast_deps_available:
        logger.info("AST dependencies available, attempting to import AST parser modules...")
        
        from app.utils.ast_parser import (
            initialize_ast_capabilities,
            is_ast_available, 
            get_ast_context, 
            get_ast_token_count,
            enhance_query_context as enhance_context
        )
        
        AST_AVAILABLE = True
        logger.info("Successfully imported AST parser modules")
        logger.info(f"AST_AVAILABLE is now: {AST_AVAILABLE}")
    else:
        AST_AVAILABLE = False
        logger.warning("AST dependencies not found. They will be installed automatically on next 'fbuild'.")
except ImportError as e:
    AST_AVAILABLE = False
    logger.warning(f"AST capabilities not available due to import error: {str(e)}")
    import traceback
    logger.debug(f"AST import traceback: {traceback.format_exc()}")
    logger.warning(f"AST import traceback: {traceback.format_exc()}")

# Track AST initialization status
def is_ast_enabled() -> bool:
    """
    Check if AST capabilities are available.
    
    AST is now always enabled when dependencies are present (it runs as
    a background index and is queried on-demand via tools).

    Returns:
        bool: True if AST dependencies are importable
    """
    logger.debug(f"AST enablement check: AST_AVAILABLE={AST_AVAILABLE}")
    return AST_AVAILABLE
def initialize_ast_if_enabled():
    """Initialize AST capabilities if enabled via environment variable."""
    global _ast_indexing_status
    # In browser mode, don't eagerly index the server's startup directory.
    # The ProjectContextMiddleware will trigger indexing for the correct
    # project root when the first request with X-Project-Root arrives.
    # In CLI mode, ZIYA_USER_CODEBASE_DIR is set correctly before this runs.
    if not os.environ.get("ZIYA_USER_CODEBASE_DIR"):
        logger.info("AST: Deferring indexing until first project request (no ZIYA_USER_CODEBASE_DIR set)")
        _ast_indexing_status.update({'enabled': True})
        return
    
    if not is_ast_enabled():
        logger.info(f"AST capabilities not enabled or not available (ZIYA_ENABLE_AST={os.environ.get('ZIYA_ENABLE_AST', 'not set')}, AST_AVAILABLE={AST_AVAILABLE}, is_enabled={is_ast_enabled()})")
        return
    
    logger.info("AST capabilities enabled, starting initialization...")
    
    # Update status to indicate indexing has started
    codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())

    # Guard: don't start a second indexing run for the same project
    from app.utils.ast_parser.integration import _indexing_in_progress as _ast_in_progress, _initialized_projects
    abs_dir = os.path.abspath(codebase_dir)
    if abs_dir in _ast_in_progress or abs_dir in _initialized_projects:
        logger.debug(f"AST init already in progress or complete for {abs_dir}, skipping")
        return

    _ast_indexing_status.update({
        'is_indexing': True,
        'enabled': True,
        'completion_percentage': 0,
        'is_complete': False,
        'error': None
    })
    
    def _initialize_ast():
        """Background thread function to initialize AST."""
        try:
            max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
            
            # Get exclude patterns
            exclude_patterns = []
            # Get gitignore patterns from the directory utilities
            from app.utils.directory_util import get_ignored_patterns
            gitignore_patterns = get_ignored_patterns(codebase_dir)
            
            # Add additional common patterns that might not be in .gitignore
            additional_excludes = os.environ.get("ZIYA_ADDITIONAL_EXCLUDE_DIRS", "")
            if additional_excludes:
                additional_patterns = [p.strip() for p in additional_excludes.split(',') if p.strip()]
                gitignore_patterns.extend([(p, 'additional') for p in additional_patterns])
            
            logger.info(f"Starting AST initialization for {codebase_dir} with max_depth={max_depth}")
            
            # Update status to show we're starting
            _ast_indexing_status.update({
                'is_indexing': True,
                'completion_percentage': 5,
                'total_files': 0  # Will be updated as we discover files
            })
            
            # Initialize AST capabilities
            result = initialize_ast_capabilities(codebase_dir, gitignore_patterns, max_depth)
            
            # Update status based on result
            if result and result.get("files_processed", 0) > 0:
                _ast_indexing_status.update({
                    'enabled': True,
                    'is_indexing': False,
                    'completion_percentage': 100,
                    'is_complete': True,
                    'indexed_files': result.get("files_processed", 0),
                    'total_files': result.get("files_processed", 0),
                    'error': None
                })
                logger.info(f"AST initialization complete: {result.get('files_processed', 0)} files processed")

                _broadcast_ast_complete(result.get("files_processed", 0))
            else:
                error_msg = result.get("error", "No files processed") if result else "Unknown error during AST initialization"
                logger.error(f"AST initialization failed: {error_msg}")
                raise Exception(error_msg)
                
        except Exception as e:
            logger.error(f"Failed to initialize AST capabilities: {str(e)}")
            _ast_indexing_status.update({
                'enabled': True,  # Still enabled, just failed
                'is_indexing': False,
                'completion_percentage': 0,
                'is_complete': False,
                'error': str(e)
            })
        finally:
            _ast_in_progress.discard(abs_dir)
    
    # Start AST initialization in background thread
    ast_thread = threading.Thread(target=_initialize_ast, daemon=True)
    ast_thread.start()
    logger.info("AST initialization started in background thread")


def _broadcast_ast_complete(files_processed: int) -> None:
    """Push an ast_indexing_complete event to all ws/file-tree clients."""
    try:
        import asyncio
        from app.services.folder_service import broadcast_file_tree_update

        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        if loop and loop.is_running():
            loop.create_task(broadcast_file_tree_update("ast_indexing_complete", "", files_processed))
        else:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(broadcast_file_tree_update("ast_indexing_complete", "", files_processed))
            loop.close()
    except Exception as e:
        logger.debug(f"Could not broadcast AST completion event: {e}")



def get_ast_indexing_status() -> Dict[str, Any]:
    """Get the current AST indexing status."""
    status_copy = _ast_indexing_status.copy()
    status_copy['enabled'] = is_ast_enabled()
    return status_copy


def initialize_ast_context(codebase_path: str, exclude_patterns: list, max_depth: int) -> Dict[str, Any]:
    """
    Initialize AST capabilities for a codebase.
    
    Args:
        codebase_path: Path to the codebase
        exclude_patterns: Patterns to exclude
        max_depth: Maximum depth for folder traversal
        
    Returns:
        Dict with initialization results
    """
    
    logger.info(f"initialize_ast_context called with codebase_path={codebase_path}, max_depth={max_depth}")
    logger.info(f"AST enabled: {is_ast_enabled()}, AST available: {AST_AVAILABLE}")
    
    if not is_ast_enabled():
        logger.warning("AST capabilities not enabled")
        return {
            "initialized": False,
            "reason": "AST capabilities not enabled"
        }
    
    if not AST_AVAILABLE:
        logger.warning("AST capabilities requested but not available")
        return {
            "initialized": False,
            "reason": "AST capabilities not available"
        }
    
    try:
        # Initialize AST capabilities
        logger.info(f"Initializing AST capabilities for {codebase_path}")
        result = initialize_ast_capabilities(codebase_path, exclude_patterns, max_depth)
        logger.info(f"initialize_ast_capabilities returned: {result}")
        
        # Update status
        if result and "files_processed" in result:
            logger.info(f"AST initialization successful: {result.get('files_processed', 0)} files processed, {result.get('token_count', 0)} tokens")
            return {
                "initialized": True,
                "files_processed": result.get("files_processed", 0),
                "token_count": result.get("token_count", 0)
            }
        else:
            error_msg = result.get("error", "Unknown error") if result else "No result returned"
            logger.error(f"AST initialization failed: {error_msg}")
            return {
                "initialized": False,
                "reason": f"Initialization failed: {error_msg}"
            }
    except Exception as e:
        logger.error(f"Error initializing AST capabilities: {e}")
        import traceback
        logger.error(f"AST initialization traceback: {traceback.format_exc()}")
        return {
            "initialized": False,
            "reason": f"Error: {str(e)}"
        }


def enhance_query_context(query: str, file_context: Optional[str] = None) -> Dict[str, Any]:
    """
    Enhance the context for a query with AST information.
    
    Args:
        query: User query
        file_context: Optional file context
        
    Returns:
        Enhanced context information
    """
    if not is_ast_enabled():
        return {}
    
    if not AST_AVAILABLE or not is_ast_available():
        return {}
    
    try:
        return enhance_query_context(query, file_context)
    except Exception as e:
        logger.error(f"Error enhancing context: {e}")
        return {}


def enhance_context_with_ast(query: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enhance context with AST information.
    
    Args:
        query: User query
        context: Current context
        
    Returns:
        Enhanced context
    """
    if not is_ast_enabled():
        return context
    
    try:
        # Get AST context
        if get_ast_context:
            ast_context = get_ast_context()
        else:
            logger.warning("get_ast_context function not available")
            return context
            
        if ast_context:
            context["ast_context"] = ast_context
            context["ast_token_count"] = get_ast_token_count() if get_ast_token_count else 0
            logger.info(f"Added AST context: {len(ast_context)} chars, {context['ast_token_count']} tokens")
    except Exception as e:
        logger.error(f"Error enhancing context with AST: {e}")
    
    return context

# Removed: module-level initialization caused double-indexing because
# server.py also calls initialize_ast_if_enabled() explicitly.
# The server controls when initialization happens.

# Add token counting cache with LRU eviction
import functools

@functools.lru_cache(maxsize=1000)
def cached_token_count(content_hash: str, content: str) -> int:
    """Cached token counting to avoid recomputing for same content"""
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(content))
    except Exception:
        return len(content) // 4
