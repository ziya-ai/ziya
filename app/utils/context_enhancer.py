"""
Context enhancer for Ziya.

This module provides functions to enhance the context for the LLM with AST information.
"""

import os
import fnmatch
import importlib.util
import sys
from typing import Dict, Any, Optional

from app.utils.logging_utils import logger

# Add more detailed logging for AST dependencies
logger.info(f"Python version: {sys.version}")
logger.info(f"Python executable: {sys.executable}")
logger.info(f"Checking for AST dependencies...")

# Check if required packages are installed
cssutils_available = importlib.util.find_spec("cssutils") is not None
html5lib_available = importlib.util.find_spec("html5lib") is not None
ast_deps_available = cssutils_available and html5lib_available

logger.info(f"cssutils available: {cssutils_available}")
logger.info(f"html5lib available: {html5lib_available}")
logger.info(f"All AST dependencies available: {ast_deps_available}")

# Import AST capabilities if available
try:
    if ast_deps_available:
        logger.info("Attempting to import AST parser modules...")
        from app.utils.ast_parser import (
            initialize_ast_capabilities,
            is_ast_available, 
            get_ast_context, 
            get_ast_token_count,
            enhance_query_context as enhance_context
        )
        # Create an alias for backward compatibility
        initialize_ast = initialize_ast_capabilities
        AST_AVAILABLE = True
        logger.info("Successfully imported AST parser modules")
    else:
        AST_AVAILABLE = False
        logger.warning("AST dependencies not found. They will be installed automatically on next 'fbuild'.")
except ImportError as e:
    AST_AVAILABLE = False
    logger.warning(f"AST capabilities not available due to import error: {str(e)}")
    import traceback
    logger.warning(f"AST import traceback: {traceback.format_exc()}")

# Track AST initialization status
_ast_initialized = False
_ast_files_processed = 0
_ast_tokens_count = 0

def is_ast_enabled() -> bool:
    """
    Check if AST capabilities are enabled in the current environment.
    
    Returns:
        bool: True if AST capabilities are enabled, False otherwise
    """
    env_enabled = os.environ.get("ZIYA_ENABLE_AST") == "true"
    return env_enabled and AST_AVAILABLE


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
    global _ast_initialized, _ast_files_processed, _ast_tokens_count
    
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
        
        # Check if initialize_ast is defined
        if 'initialize_ast' not in globals():
            logger.error("initialize_ast function not found in globals")
            return {
                "initialized": False,
                "reason": "initialize_ast function not found"
            }
        
        # Print all available globals for debugging
        logger.info(f"Available globals: {[name for name in globals() if not name.startswith('_')]}")
        
        # Call initialize_ast with detailed logging
        logger.info("Calling initialize_ast function...")
        
        # Direct call to initialize_ast_capabilities to avoid any alias issues
        from app.utils.ast_parser import initialize_ast_capabilities
        result = initialize_ast_capabilities(codebase_path, exclude_patterns, max_depth)
        logger.info(f"initialize_ast_capabilities returned: {result}")
        
        # Update status
        if result and "files_processed" in result:
            _ast_initialized = True
            _ast_files_processed = result.get("files_processed", 0)
            _ast_tokens_count = result.get("token_count", 0)
            
            logger.info(f"AST initialization successful: {_ast_files_processed} files processed, {_ast_tokens_count} tokens")
            return {
                "initialized": True,
                "files_processed": _ast_files_processed,
                "token_count": _ast_tokens_count
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


def get_ast_indexing_status() -> Dict[str, Any]:
    """
    Get the current status of AST indexing.
    
    Returns:
        Dictionary with indexing status information
    """
    if not is_ast_enabled():
        return {
            "enabled": False,
            "initialized": False,
            "reason": "AST capabilities not enabled"
        }
    
    if not AST_AVAILABLE:
        return {
            "enabled": True,
            "initialized": False,
            "reason": "AST capabilities not available"
        }
    
    if not _ast_initialized:
        return {
            "enabled": True,
            "initialized": False,
            "reason": "AST not initialized yet"
        }
    
    return {
        "enabled": True,
        "initialized": True,
        "files_processed": _ast_files_processed,
        "token_count": _ast_token_count
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
    if not os.environ.get("ZIYA_ENABLE_AST") == "true":
        return {}
    
    if not AST_AVAILABLE or not is_ast_available():
        return {}
    
    try:
        return enhance_context(query, file_context)
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
    if not is_ast_enabled() or not _ast_initialized:
        return context
    
    try:
        # Get AST context
        ast_context = get_ast_context()
        if ast_context:
            context["ast_context"] = ast_context
            context["ast_token_count"] = get_ast_token_count()
    except Exception as e:
        logger.error(f"Error enhancing context with AST: {e}")
    
    return context
