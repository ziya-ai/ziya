"""
Integration module for Ziya AST capabilities.

This module provides functions to integrate AST capabilities with Ziya's core functionality.
"""

import os
import json
import logging
import importlib.util
from typing import Dict, List, Optional, Any, Tuple
from app.utils.logging_utils import logger

# Global enhancer instance
_enhancer = None
_ast_context = ""
_ast_token_count = 0
_ast_initialized = False
_resolution_estimates = {}

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
    
    Args:
        codebase_path: Path to the codebase
        exclude_patterns: Patterns to exclude
        max_depth: Maximum directory depth to traverse
        
    Returns:
        Dict containing initialization results
    """
    global _enhancer, _ast_context, _ast_token_count, _ast_initialized, _resolution_estimates
    
    logger.info(f"initialize_ast_capabilities called with codebase_path={codebase_path}, max_depth={max_depth}")
    
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
        # Create the enhancer
        ast_resolution = os.environ.get("ZIYA_AST_RESOLUTION", "medium")
        enhancer = ZiyaASTEnhancer(ast_resolution=ast_resolution)
        
        # Set the global enhancer variable
        _enhancer = enhancer
        
        # Process the codebase
        result = _enhancer.process_codebase(codebase_path, exclude_patterns or [], max_depth)
        
        # Calculate resolution estimates
        logger.info("Calculating AST resolution estimates...")
        _resolution_estimates = _enhancer.calculate_resolution_estimates()
        
        # Store context and token count
        if "ast_context" in result:
            _ast_context = result["ast_context"]
            # Calculate token count if not provided
            if "token_count" not in result and _ast_context:
                result["token_count"] = len(_ast_context) // 4  # Rough estimate
        if "token_count" in result:
            _ast_token_count = result["token_count"]
        
        # Mark as initialized
        _ast_initialized = True
        
        # Return a properly formatted result dictionary
        return {
            "initialized": True,
            "files_processed": result.get("files_processed", 0),
            "token_count": result.get("token_count", 0),
            "ast_context": result.get("ast_context", "")
        }
        
        logger.info(f"AST initialization returning: files_processed={result.get('files_processed', 0)}, token_count={result.get('token_count', 0)}")
    except Exception as e:
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
    return _ast_initialized and _enhancer is not None


def get_ast_context() -> str:
    """
    Get the AST context.
    
    Returns:
        AST context as a string
    """
    return _ast_context


def get_ast_token_count() -> int:
    """
    Get the AST context token count.
    
    Returns:
        AST context token count
    """
    return _ast_token_count


def get_resolution_estimates() -> Dict[str, Dict[str, Any]]:
    """
    Get the AST resolution estimates.
    
    Returns:
        Dictionary mapping resolution levels to their estimated sizes
    """
    return _resolution_estimates


def change_ast_resolution(new_resolution: str) -> None:
    """
    Change the AST resolution and re-index the codebase.
    
    Args:
        new_resolution: New resolution level
    """
    global _enhancer, _ast_context, _ast_token_count, _resolution_estimates
    
    if not _enhancer:
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
            _enhancer.ast_resolution = new_resolution
            
            # Clear context and token count
            _ast_context = ""
            _ast_token_count = 0
            
            # Update status to complete immediately
            _ast_indexing_status.update({
                'is_indexing': False,
                'completion_percentage': 100,
                'is_complete': True
            })
            
            logger.info("AST resolution set to disabled - no indexing required")
            return
        
        # Set environment variable for persistence
        os.environ['ZIYA_AST_RESOLUTION'] = new_resolution
        
        # Update the enhancer's resolution
        _enhancer.ast_resolution = new_resolution
        
        # Clear existing AST cache to force re-processing
        _enhancer.ast_cache.clear()
        _enhancer.query_engines.clear()
        _enhancer.project_ast = _enhancer.UnifiedAST() if hasattr(_enhancer, 'UnifiedAST') else None
        
        # Re-process the codebase with new resolution
        codebase_dir = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
        max_depth = int(os.environ.get("ZIYA_MAX_DEPTH", 15))
        
        logger.info(f"Re-indexing codebase with resolution: {new_resolution}")
        
        # Get exclude patterns
        from app.utils.directory_util import get_ignored_patterns
        gitignore_patterns = get_ignored_patterns(codebase_dir)
        
        # Re-process the codebase
        result = _enhancer.process_codebase(codebase_dir, gitignore_patterns, max_depth)
        
        # Update context and token count from the re-processing result
        new_context = result.get("ast_context", "")
        new_token_count = result.get("token_count", 0)
        
        # Update global variables
        _ast_context = new_context
        _ast_token_count = new_token_count
        
        # Recalculate resolution estimates
        logger.info("Recalculating AST resolution estimates...")
        _resolution_estimates = _enhancer.calculate_resolution_estimates()
        
        # Update status to complete
        _ast_indexing_status.update({
            'is_indexing': False,
            'completion_percentage': 100,
            'is_complete': True
        })
        
        logger.info(f"AST resolution changed successfully to {new_resolution}")
        logger.info(f"Files processed: {result.get('files_processed', 0)}")
        logger.info(f"New token count: {new_token_count}")
        
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
    if _enhancer is None:
        return {}
    
    return _enhancer.enhance_query_context(query, file_context)
