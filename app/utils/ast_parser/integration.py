"""
Integration module for Ziya AST capabilities.

This module provides functions to integrate AST capabilities with Ziya's core functionality.
"""

import os
import json
import logging
from typing import Dict, List, Optional, Any

from .ziya_ast_enhancer import ZiyaASTEnhancer
from app.utils.logging_utils import logger

# Global enhancer instance
_enhancer = None


def initialize_ast_capabilities(codebase_path: str, exclude_patterns: Optional[List[str]] = None, 
                              max_depth: int = 15) -> None:
    """
    Initialize AST capabilities for a codebase.
    
    Args:
        codebase_path: Path to the codebase root
        exclude_patterns: Patterns to exclude
        max_depth: Maximum directory depth to traverse
    """
    global _enhancer
    
    logger.info(f"Initializing AST capabilities for codebase: {codebase_path}")
    
    # Create enhancer if not exists
    if _enhancer is None:
        _enhancer = ZiyaASTEnhancer()
    
    # Process codebase
    _enhancer.process_codebase(codebase_path, exclude_patterns, max_depth)
    
    logger.info(f"AST capabilities initialized, processed {len(_enhancer.ast_cache)} files")


def enhance_context(query: str, file_context: Optional[str] = None) -> Dict[str, Any]:
    """
    Enhance context for a query with semantic information.
    
    Args:
        query: User query
        file_context: Optional file context
        
    Returns:
        Enhanced context information
    """
    global _enhancer
    
    if _enhancer is None:
        logger.warning("AST capabilities not initialized")
        return {}
    
    return _enhancer.enhance_query_context(query, file_context)


def get_code_summaries() -> Dict[str, Any]:
    """
    Get code summaries for the codebase.
    
    Returns:
        Dictionary with code summaries
    """
    global _enhancer
    
    if _enhancer is None:
        logger.warning("AST capabilities not initialized")
        return {}
    
    return _enhancer.generate_code_summaries()


def find_references(symbol_name: str) -> List[Dict[str, Any]]:
    """
    Find all references to a symbol across the codebase.
    
    Args:
        symbol_name: Name of the symbol to find
        
    Returns:
        List of references
    """
    global _enhancer
    
    if _enhancer is None:
        logger.warning("AST capabilities not initialized")
        return []
    
    return _enhancer.find_references(symbol_name)


def analyze_dependencies() -> Dict[str, List[str]]:
    """
    Analyze dependencies between files.
    
    Returns:
        Dictionary mapping files to their dependencies
    """
    global _enhancer
    
    if _enhancer is None:
        logger.warning("AST capabilities not initialized")
        return {}
    
    return _enhancer.analyze_dependencies()


def get_ast_for_file(file_path: str) -> Optional[Dict[str, Any]]:
    """
    Get the AST for a specific file.
    
    Args:
        file_path: Path to the file
        
    Returns:
        AST as a dictionary or None if not available
    """
    global _enhancer
    
    if _enhancer is None or file_path not in _enhancer.ast_cache:
        return None
    
    # Convert to dictionary for serialization
    ast = _enhancer.ast_cache[file_path]
    return json.loads(ast.to_json())


def is_ast_available() -> bool:
    """
    Check if AST capabilities are available.
    
    Returns:
        True if AST capabilities are initialized, False otherwise
    """
    global _enhancer
    return _enhancer is not None
