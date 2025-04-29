"""
Integration module for Ziya AST capabilities.

This module provides functions to integrate AST capabilities with Ziya's core functionality.
"""

import os
import json
import logging
import importlib.util
from typing import Dict, List, Optional, Any

from app.utils.logging_utils import logger

# Global enhancer instance
_enhancer = None
_ast_context = ""
_ast_token_count = 0
_ast_initialized = False


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


def initialize_ast_capabilities(codebase_path: str, exclude_patterns: Optional[List[str]] = None, 
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
    global _enhancer, _ast_context, _ast_token_count, _ast_initialized
    
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
        logger.info("Importing ZiyaASTEnhancer...")
        from .ziya_ast_enhancer import ZiyaASTEnhancer
        
        # Create enhancer if not already created
        if _enhancer is None:
            logger.info("Creating new ZiyaASTEnhancer instance")
            _enhancer = ZiyaASTEnhancer()
        else:
            logger.info("Using existing ZiyaASTEnhancer instance")
        
        # Process codebase
        logger.info(f"Processing codebase at {codebase_path}")
        result = _enhancer.process_codebase(codebase_path, exclude_patterns, max_depth)
        logger.info(f"Codebase processing result: {result}")
        
        # Store context and token count
        if "ast_context" in result:
            _ast_context = result["ast_context"]
            logger.info(f"AST context set, length: {len(_ast_context)}")
        if "token_count" in result:
            _ast_token_count = result["token_count"]
            logger.info(f"AST token count set: {_ast_token_count}")
        
        # Mark as initialized
        _ast_initialized = True
        logger.info("AST capabilities marked as initialized")
        
        # Return a properly formatted result dictionary
        return {
            "initialized": True,
            "files_processed": result.get("files_processed", 0),
            "token_count": result.get("token_count", 0),
            "ast_context": result.get("ast_context", "")
        }
        
    except Exception as e:
        logger.error(f"Error initializing AST capabilities: {str(e)}")
        import traceback
        logger.error(f"AST initialization traceback: {traceback.format_exc()}")
        return {
            "initialized": False,
            "error": str(e),
            "files_processed": 0,
            "token_count": 0
        }
        
        # Mark as initialized
        _ast_initialized = True
        
        return result
    except Exception as e:
        logger.error(f"Failed to initialize AST capabilities: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
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
