"""
Context enhancer for Ziya.

This module provides functions to enhance the context for the LLM with AST information.
"""

import os
from typing import Dict, Any, Optional

from app.utils.logging_utils import logger

# Import AST capabilities if available
try:
    from app.utils.ast_parser.integration import (
        initialize_ast_capabilities, enhance_context, is_ast_available
    )
    AST_AVAILABLE = True
except ImportError:
    AST_AVAILABLE = False
    logger.warning("AST capabilities not available. Install the required packages to enable them.")


def initialize_ast(codebase_path: str, exclude_patterns: list, max_depth: int) -> bool:
    """
    Initialize AST capabilities for a codebase.
    
    Args:
        codebase_path: Path to the codebase
        exclude_patterns: Patterns to exclude
        max_depth: Maximum depth for folder traversal
        
    Returns:
        True if initialization was successful, False otherwise
    """
    if not os.environ.get("ZIYA_ENABLE_AST") == "true":
        return False
    
    if not AST_AVAILABLE:
        logger.warning("AST capabilities requested but not available")
        return False
    
    try:
        initialize_ast_capabilities(codebase_path, exclude_patterns, max_depth)
        logger.info("AST capabilities initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize AST capabilities: {e}")
        return False


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
