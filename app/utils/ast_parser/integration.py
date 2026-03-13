"""
Integration module for Ziya AST capabilities.

This module provides functions to integrate AST capabilities with Ziya's core functionality.
"""

import os
import json
import logging
import importlib.util
import threading
from typing import Dict, List, Optional, Any, Tuple, Set
from app.utils.logging_utils import logger

# Project-keyed enhancer instances: project_root -> enhancer
_enhancers: Dict[str, Any] = {}
_enhancer_lock = threading.Lock()
# Track which projects are currently being indexed (prevent double-start)
_indexing_in_progress: Set[str] = set()
_initialized_projects: Set[str] = set()

# Local copy of AST indexing status to avoid circular import
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


def _get_project_root() -> str:
    """Get the current project root, from request context or environment."""
    try:
        from app.context import get_project_root
        return os.path.abspath(get_project_root())
    except Exception:
        return os.path.abspath(os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd()))


def get_enhancer_for_project(project_root: Optional[str] = None):
    """Get the AST enhancer for a specific project root (or current project)."""
    root = os.path.abspath(project_root) if project_root else _get_project_root()
    return _enhancers.get(root)


# Legacy accessor — used by ast_tools.py and other callers that import _enhancer
# NOTE: _enhancer is no longer a module-level variable. Use get_current_enhancer() instead.


def get_current_enhancer():
    """Get the AST enhancer for the current request's project."""
    return get_enhancer_for_project()


def check_dependencies() -> bool:
    """
    Check if all required dependencies are installed.
    
    Returns:
        True if all dependencies are installed, False otherwise
    """
    required_packages = ["cssutils", "html5lib"]
    
    for package in required_packages:
        if importlib.util.find_spec(package) is None:
            logger.warning(f"Required package '{package}' is not installed")
            return False
    
    return True


def initialize_ast_capabilities(codebase_path: str, exclude_patterns: Optional[List[Tuple[str, str]]] = None,
                              max_depth: int = 15) -> Dict[str, Any]:
    """
    Initialize AST capabilities for the given codebase.

    Safe to call multiple times — subsequent calls for the same project_root
    return immediately.  Different project roots get separate enhancer instances.

    Args:
        codebase_path: Path to the codebase
        exclude_patterns: Patterns to exclude
        max_depth: Maximum directory depth to traverse
        
    Returns:
        Dict containing initialization results
    """
    abs_path = os.path.abspath(codebase_path)

    # Already indexed for this project?
    if abs_path in _indexing_in_progress:
        logger.debug(f"AST indexing already in progress for {abs_path}, skipping")
        return {
            "initialized": False,
            "files_processed": 0,
            "token_count": 0,
            "ast_context": "",
        }

    if abs_path in _initialized_projects:
        logger.debug(f"AST already initialized for {abs_path}, skipping")
        enhancer = _enhancers.get(abs_path)
        return {
            "initialized": True,
            "files_processed": len(enhancer.ast_cache) if enhancer else 0,
            "token_count": 0,
            "ast_context": "",
        }
    
    _indexing_in_progress.add(abs_path)

    # Check dependencies
    if not check_dependencies():
        logger.warning("AST dependencies not installed. AST capabilities will not be available.")
        return {
            "error": "Missing dependencies",
            "files_processed": 0,
            "ast_context": "",
            "token_count": 0
        }
    
    try:
        # Import ZiyaASTEnhancer here to avoid circular imports
        from .ziya_ast_enhancer import ZiyaASTEnhancer
        
        # Create enhancer if not already created
        ast_resolution = os.environ.get("ZIYA_AST_RESOLUTION", "medium")
        enhancer = ZiyaASTEnhancer(ast_resolution=ast_resolution)
        
        # Store keyed by project root
        with _enhancer_lock:
            _enhancers[abs_path] = enhancer
        
        # Process the codebase
        result = enhancer.process_codebase(codebase_path, exclude_patterns or [], max_depth)
        
        # Mark this project as initialized
        _initialized_projects.add(abs_path)
        
        _indexing_in_progress.discard(abs_path)
        # Return a properly formatted result dictionary
        return {
            "initialized": True,
            "files_processed": result.get("files_processed", 0),
            "token_count": result.get("token_count", 0),
            "ast_context": result.get("ast_context", "")
        }
        
    except Exception as e:
        _indexing_in_progress.discard(abs_path)
        logger.error(f"Error initializing AST capabilities: {str(e)}")
        import traceback
        logger.error(f"AST initialization traceback: {traceback.format_exc()}")
        return {
            "initialized": False,
            "error": str(e),
            "files_processed": 0,
            "ast_context": "",
            "token_count": 0
        }

def is_ast_available() -> bool:
    """
    Check if AST capabilities are available.
    
    Returns:
        True if AST capabilities are available, False otherwise
    """
    return len(_initialized_projects) > 0


def get_ast_context() -> str:
    """
    Get the AST context.
    
    Returns:
        AST context as a string
    """
    enhancer = get_current_enhancer()
    if enhancer is None:
        return ""
    return enhancer.generate_ast_context()


def get_ast_token_count() -> int:
    """
    Get the AST context token count.
    
    Returns:
        AST context token count
    """
    ctx = get_ast_context()
    return len(ctx) // 4 if ctx else 0


def get_resolution_estimates() -> Dict[str, Dict[str, Any]]:
    """
    Get the AST resolution estimates for the current project.
    """
    enhancer = get_current_enhancer()
    if enhancer is None:
        return {}
    return enhancer.calculate_resolution_estimates()


def change_ast_resolution(new_resolution: str) -> None:
    """
    Change the AST resolution and re-index the codebase.
    """
    enhancer = get_current_enhancer()
    
    if not enhancer:
        logger.error("AST enhancer not initialized")
        return
    
    # Reset indexing status to show progress
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
        logger.info(f"Changing AST resolution to: {new_resolution}")
        
        # Handle disabled case - no re-indexing needed
        if new_resolution == 'disabled':
            # Set environment variable for persistence
            os.environ['ZIYA_AST_RESOLUTION'] = new_resolution
            enhancer.ast_resolution = new_resolution
        
            # Update status to complete immediately
            _ast_indexing_status.update({
                'completion_percentage': 100,
                'is_complete': True
            })
            
            logger.info("AST resolution set to disabled - no indexing required")
            return
        
        # Set environment variable for persistence
        os.environ['ZIYA_AST_RESOLUTION'] = new_resolution
        
        # Update the enhancer's resolution
        enhancer.ast_resolution = new_resolution
        
        # Clear existing AST cache to force re-processing
        enhancer.ast_cache.clear()
        enhancer.query_engines.clear()
        enhancer.project_ast = enhancer.UnifiedAST() if hasattr(enhancer, 'UnifiedAST') else None
        
        # Re-process the codebase with new resolution
        codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
        max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
        
        logger.info(f"Re-indexing codebase with resolution: {new_resolution}")
        
        # Get exclude patterns
        from app.utils.directory_util import get_ignored_patterns
        gitignore_patterns = get_ignored_patterns(codebase_dir)
        
        # Re-process the codebase
        result = enhancer.process_codebase(codebase_dir, gitignore_patterns, max_depth)
        
        # Update status to complete
        _ast_indexing_status.update({
            'is_indexing': False,
            'completion_percentage': 100,
            'is_complete': True
        })
        
        logger.info(f"AST resolution changed successfully to {new_resolution}")
        logger.info(f"Files processed: {result.get('files_processed', 0)}")
        
    except Exception as e:
        logger.error(f"Error changing AST resolution: {e}")
        raise


def enhance_query_context(query: str, file_context: Optional[str] = None) -> Dict[str, Any]:
    """
    Enhance query context with AST information.
    
    Args:
        query: User query
        file_context: Optional file context
        
    Returns:
        Enhanced context
    """
    enhancer = get_current_enhancer()
    if enhancer is None:
        return {}
    return enhancer.enhance_query_context(query, file_context)
